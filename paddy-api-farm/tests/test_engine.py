import json
import unittest

import _helpers  # noqa: F401
from paddy.decisions.api_client import DecisionAPI, ReplayClient
from paddy.decisions.mock_api import MockConfig, MockDecisionServer
from paddy.engine import CallResult, Engine

DAYS = 30


def run_days(e, days=DAYS):
    while e.tw.day < days and e.step_hour():
        pass
    return e


class FailingClient:
    fallback = 'hold'
    prompt_version = 'test'

    def decide(self, state):
        return CallResult(False, error='down for the test', source='api')


class EngineTests(unittest.TestCase):
    def test_deterministic(self):
        a = run_days(Engine(seed=5, manager='builtin', keep_states=False))
        b = run_days(Engine(seed=5, manager='builtin', keep_states=False))
        c = run_days(Engine(seed=6, manager='builtin', keep_states=False))
        self.assertEqual(a.decision_digest(), b.decision_digest())
        self.assertEqual(a.tw.fields[0].h, b.tw.fields[0].h)
        self.assertNotEqual(a.decision_digest(), c.decision_digest())

    def test_mock_api_equals_builtin_and_replay_is_exact(self):
        b = run_days(Engine(seed=7, manager='builtin', keep_states=False))
        with MockDecisionServer(MockConfig(seed=1)) as m:
            cl = DecisionAPI(base_url=m.url, model='mock', key='k-test', backoff_base_s=0.01, max_calls_per_real_hour=10 ** 6)
            a = run_days(Engine(seed=7, manager='api', client=cl, keep_states=True))
        self.assertEqual(a.decision_digest(), b.decision_digest())
        self.assertTrue(all(c['source'] == 'mock' for c in a.store.calls))
        r = run_days(Engine(seed=7, manager='api', client=ReplayClient(a.store.calls), keep_states=False))
        self.assertEqual(r.decision_digest(), a.decision_digest())

    def test_approve_mode(self):
        e = Engine(seed=8, manager='builtin', mode='approve', keep_states=False)
        self.assertFalse(e.step_hour())          # the 06:00 call leaves decisions waiting
        self.assertTrue(e.awaiting_approval())
        first, second = e.pending[0]['id'], e.pending[1]['id']
        ok, st = e.approve(first)
        self.assertTrue(ok)
        ok, st = e.reject(second, 'not now')
        self.assertTrue(ok)
        self.assertEqual(e.store.by_id[second]['status'], 'rejected_by_person')
        self.assertTrue(any('rejected by the farmer' in r['note'] for r in e.recent))
        for r in list(e.pending):
            e.approve(r['id'])
        self.assertTrue(e.step_hour())

    def test_scripted_approver(self):
        seen = []
        e = Engine(seed=8, manager='builtin', mode='approve', keep_states=False,
                   approver=lambda rec, eng: (seen.append(rec['action']) or ('reject', 'no') if rec['action'] == 'soil_check' else ('approve', '')))
        run_days(e, 3)
        self.assertIn('soil_check', seen)
        st = {d['status'] for d in e.store.decisions if d['action'] == 'soil_check'}
        self.assertEqual(st, {'rejected_by_person'})

    def test_fallbacks(self):
        e = run_days(Engine(seed=9, manager='api', client=FailingClient(), keep_states=False), 2)
        self.assertTrue(e.store.calls)
        self.assertTrue(all(c['fallback'] == 'hold' for c in e.store.calls))
        self.assertEqual(len(e.store.decisions), 0)
        e = run_days(Engine(seed=9, manager='api', client=FailingClient(), fallback='builtin', keep_states=False), 2)
        self.assertTrue(e.store.decisions)
        self.assertTrue(all(d['source'] == 'fallback-builtin' for d in e.store.decisions))

    def test_event_trigger_and_wait(self):
        e = run_days(Engine(seed=10, manager='builtin', keep_states=False), 30)
        n = e.n_calls
        r = e.inject_event('storm')
        self.assertTrue(r.ok)
        e.step_hour()
        self.assertGreater(e.n_calls, n)
        self.assertIn('event', e.store.calls[-1]['triggers'])

    def test_budget(self):
        e = run_days(Engine(seed=11, manager='builtin', keep_states=False, max_calls_per_day=1), 3)
        self.assertLessEqual(max(e.calls_by_day.values()), 1)
        self.assertGreater(e.n_skipped_calls, 0)

    def test_state_has_no_truth(self):
        e = run_days(Engine(seed=12, manager='builtin', keep_states=True), 40)
        text = json.dumps(e.store.calls[-1]['state'])
        for leak in ('"failed"', 'pestId', 'true_', '"elev"', 'failT'):
            self.assertNotIn(leak, text)


if __name__ == '__main__':
    unittest.main()
