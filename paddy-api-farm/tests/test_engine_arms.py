"""The engine with the arms (v2): decisions become arm jobs, each square lands at its real time, v1 stays reproducible."""
from __future__ import annotations

import json
import unittest

import _helpers  # noqa: F401
from paddy import config as CFG
from paddy.engine import Engine
from paddy.sim.twin import DAY, HOUR


def run_until(e, pred, max_hours=24 * 160):
    n = 0
    while n < max_hours and e.step_hour():
        n += 1
        if pred(e):
            return True
    return pred(e)


class EngineArmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = []
        e = Engine(seed=21, weatherYear=7, season='kharif', manager='builtin', keep_states=False, record_frames=True,
                   listener=lambda k, d: cls.events.append((k, json.loads(json.dumps(d, default=str)))))
        # run past the first top-dressing and the first sprays
        run_until(e, lambda e: e.tw.day >= 70)
        cls.e = e

    def decisions(self, action, status='executed'):
        return [d for d in self.e.store.decisions if d['action'] == action and d['status'] == status]

    def test_every_executed_decision_has_an_arm_plan(self):
        done = [d for d in self.e.store.decisions if d['status'] == 'executed' and d['action'] not in ('hold', 'wait')]
        self.assertGreater(len(done), 30)
        for d in done:
            p = d.get('arm_plan')
            self.assertIsNotNone(p, d['id'])
            for k in ('arm', 'job', 'start_t', 'end_t', 'busy_s', 'travel_m', 'energy_wh', 'min_clearance_m', 'collisions', 'tasks', 'route'):
                self.assertIn(k, p, (d['id'], k))
            self.assertEqual(p['collisions'], 0)
            self.assertGreaterEqual(p['start_t'], d['t'])                      # never before the decision
            self.assertGreaterEqual(p['end_t'], p['start_t'])
            for sq, t0, t1, st, ty in p['tasks']:
                if st == 'done':
                    self.assertTrue(p['start_t'] <= t0 <= t1 <= p['end_t'] + 1e-6, (d['id'], sq))

    def test_arm_roles(self):
        arm_of = {'transplant': 'planting', 'soil_check': 'planting', 'irrigate': 'watering', 'drain': 'watering', 'fertilise': 'watering',
                  'amend': 'watering', 'scout': 'pest', 'spray': 'pest', 'puddle': 'harvest', 'harvest': 'harvest'}
        for d in self.e.store.decisions:
            if d.get('arm_plan'):
                self.assertEqual(d['arm_plan']['arm'], arm_of[d['action']], d['id'])

    def test_status_goes_queued_executing_executed(self):
        seen = {}
        for k, d in self.events:
            if k == 'decision':
                seen.setdefault(d['id'], []).append(d['status'])
        some = [d['id'] for d in self.decisions('fertilise')][:3]
        self.assertTrue(some)
        for did in some:
            st = [x for i, x in enumerate(seen[did]) if i == 0 or x != seen[did][i - 1]]
            self.assertEqual(st[-3:], ['queued', 'executing', 'executed'], (did, st))

    def test_spray_squares_land_at_their_own_minute(self):
        sprays = self.decisions('spray')
        self.assertTrue(sprays, 'no spray in the first 70 days')
        for d in sprays:
            times = [t1 for _, _, t1, st, _ in d['arm_plan']['tasks'] if st == 'done']
            self.assertEqual(len(times), len(set(times)))                  # one square after another
            self.assertTrue(all(d['exec_t'] <= t for t in times))

    def test_irrigation_starts_when_the_gate_is_set(self):
        irr = self.decisions('irrigate')
        self.assertTrue(irr)
        for d in irr:
            gate_t = d['arm_plan']['tasks'][0][2]
            # from the time the irrigation is to start (v2: the built-in manager may schedule it for the hour the water
            # is expected to reach the AWD line): the arm has to get to the inlet first, within the hour
            self.assertGreater(gate_t, d['exec_t'])
            self.assertLess(gate_t - d['exec_t'], HOUR)
            self.assertIn('inlet open at', d['execution']['msg'])

    def test_state_says_which_arms_are_busy(self):
        from paddy.decisions.state import build_state
        st = build_state(self.e, ['scheduled'])
        self.assertEqual(set(st['arms']), {'planting', 'watering', 'pest', 'harvest'})
        e = Engine(seed=5, season='rabi', manager='builtin', keep_states=False)
        e.call(['scheduled'])                    # 06:00: soil checks and soaking handed to the arms, not yet carried out
        st = build_state(e, ['scheduled'])
        busy = {k: v for k, v in st['arms'].items() if v}
        self.assertTrue(busy)
        for v in busy.values():
            self.assertTrue(v['busy_until'] >= st['now']['time'])

    def test_season_power_and_arm_totals(self):
        s = self.e.summary()
        self.assertEqual(s['arms']['collisions'], 0)
        self.assertGreaterEqual(s['arms']['min_separation_m'], 0.0)
        self.assertGreater(s['arms']['busy_s'], 0)
        en = s['energy']
        self.assertAlmostEqual(en['load_kwh'], sum(en['by_category_kwh'].values()), delta=0.05)
        self.assertGreater(en['by_category_kwh'].get('pump', 0), 0)
        self.assertGreater(en['by_category_kwh'].get('move', 0), 0)

    def test_tracks_cover_the_jobs(self):
        tr = self.e.tracks_export()
        self.assertEqual(len(tr['arms']), 4)
        for k, kfs in tr['tracks'].items():
            ts = [kf[0] for kf in kfs]
            self.assertEqual(ts, sorted(ts), k)


class ApproveTests(unittest.TestCase):
    def test_pending_decision_shows_its_planned_path(self):
        e = Engine(seed=8, season='kharif', manager='builtin', mode='approve', keep_states=False)
        e.step_hour()
        self.assertTrue(e.pending)
        rec = next(r for r in e.pending if r.get('arm_preview'))
        pv = rec['arm_preview']
        self.assertTrue(pv['track'])
        self.assertTrue(pv['tasks'])
        self.assertEqual(pv['collisions'], 0)
        # approve only this one: the arm then does exactly what the preview showed
        for r in list(e.pending):
            if r is rec:
                e.approve(r['id'])
            else:
                e.reject(r['id'], 'test')
        run_until(e, lambda e: rec['status'] == 'executed', max_hours=6)
        self.assertEqual(rec['status'], 'executed')
        self.assertEqual([x[:3] for x in rec['arm_plan']['tasks']], [x[:3] for x in pv['tasks']])


class ReproducibilityTests(unittest.TestCase):
    def test_same_seed_same_season(self):
        a = Engine(seed=31, weatherYear=2, season='rabi', manager='builtin', keep_states=False)
        b = Engine(seed=31, weatherYear=2, season='rabi', manager='builtin', keep_states=False)
        for e in (a, b):
            run_until(e, lambda e: e.tw.day >= 40)
        self.assertEqual(a.decision_digest(), b.decision_digest())
        self.assertEqual(a.history_digest(), b.history_digest())
        self.assertEqual(a.arms.totals(), b.arms.totals())

    def test_arms_off_is_v1(self):
        """arms=False with v1's forests is v1's engine: the recorded v1 demo season (built-in decisions) comes out digest
        for digest."""
        e = Engine(seed=20261024, weatherYear=0, season='kharif', manager='builtin', keep_states=False, arms=False, models='v1')
        e.run()
        s = e.summary()
        self.assertEqual(s['decision_digest'][:16], '12a884b29cf1039f')
        self.assertEqual(s['history_digest'][:16], '48c45fe19c3537aa')


if __name__ == '__main__':
    unittest.main()
