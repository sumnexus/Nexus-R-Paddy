import json
import os
import unittest

import _helpers  # noqa: F401
from paddy.decisions.adapters import AnthropicMessagesAdapter, ChatCompletionsAdapter, GenericJSONAdapter
from paddy.decisions.api_client import DecisionAPI, ReplayClient
from paddy.decisions.mock_api import MockConfig, MockDecisionServer
from paddy.decisions.records import redact
from paddy.decisions.state import build_state
from paddy.engine import Engine

KEY = 'sk-test-DO-NOT-LEAK-1234567890'


def state():
    e = Engine(seed=3, manager='api', keep_states=False)
    return build_state(e, ['scheduled'])


def client(url, **kw):
    base = dict(base_url=url, model='mock', key=KEY, timeout_s=2.0, backoff_base_s=0.01, retries=2, max_calls_per_real_hour=10 ** 6)
    base.update(kw)
    return DecisionAPI(**base)


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = state()

    def test_call_through_mock(self):
        with MockDecisionServer(MockConfig(seed=1, require_key=True)) as m:
            r = client(m.url).decide(self.state)
            self.assertTrue(r.ok, r.error)
            self.assertEqual(r.source, 'mock')
            self.assertGreater(r.tokens_in, 100)
            self.assertTrue(r.response['decisions'])
            self.assertEqual(m.stats.auth_seen, 1)

    def test_retries_then_failure(self):
        with MockDecisionServer(MockConfig(seed=1, p429=1.0)) as m:
            r = client(m.url, retries=2).decide(self.state)
            self.assertFalse(r.ok)
            self.assertEqual(r.attempts, 3)
            self.assertIn('429', r.error)

    def test_transient_500_recovers(self):
        # about a third of the requests fail; with six retries a call fails only if seven attempts in a row do
        # (0.3^7 ≈ 0.0002), so all calls get through, and some of them only after retrying. Twenty calls, so that a
        # run with no fault at all (0.7^20 ≈ 0.0008 for a given state) can't make the check pass vacuously.
        with MockDecisionServer(MockConfig(seed=4, p500=0.3)) as m:
            results = [client(m.url, retries=6).decide(self.state) for _ in range(20)]
            self.assertTrue(all(r.ok for r in results), [r.error for r in results])
            self.assertGreater(m.stats.faults['500'], 0)
            self.assertTrue(any(r.attempts > 1 for r in results))

    def test_repair_after_malformed(self):
        with MockDecisionServer(MockConfig(seed=1, pmalformed=1.0)) as m:
            r = client(m.url).decide(self.state)
            self.assertTrue(r.ok, r.error)
            self.assertTrue(r.repaired)

    def test_timeout(self):
        with MockDecisionServer(MockConfig(seed=1, ptimeout=1.0, timeout_s=1.0)) as m:
            r = client(m.url, timeout_s=0.3, retries=1).decide(self.state)
            self.assertFalse(r.ok)
            self.assertIn('timeout', r.error)

    def test_not_configured(self):
        r = DecisionAPI().decide(self.state)
        self.assertFalse(r.ok)
        self.assertIn('not configured', r.error)

    def test_real_hour_budget(self):
        with MockDecisionServer(MockConfig(seed=1)) as m:
            c = client(m.url, max_calls_per_real_hour=2)
            self.assertTrue(c.decide(self.state).ok)
            self.assertTrue(c.decide(self.state).ok)
            r = c.decide(self.state)
            self.assertFalse(r.ok)
            self.assertIn('real-hour', r.error)

    def test_key_never_shown(self):
        c = client('http://127.0.0.1:9')
        self.assertNotIn(KEY, repr(c))
        self.assertNotIn(KEY, json.dumps(c.describe()))
        os.environ['API_KEY'] = KEY
        try:
            self.assertNotIn(KEY, json.dumps(redact({'a': f'Authorization: Bearer {KEY}', 'b': [KEY], 'authorization': KEY})))
        finally:
            os.environ.pop('API_KEY', None)

    def test_adapters(self):
        msgs = [{'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'u'}]
        a = ChatCompletionsAdapter()
        self.assertEqual(a.url('https://x/v1'), 'https://x/v1/chat/completions')
        self.assertEqual(a.headers('k')['Authorization'], 'Bearer k')
        self.assertEqual(a.body(msgs, 'm', 0.2, True)['response_format'], {'type': 'json_object'})
        self.assertEqual(a.parse({'choices': [{'message': {'content': 'hi'}}], 'usage': {'prompt_tokens': 3, 'completion_tokens': 1}})[:3], ('hi', 3, 1))
        b = AnthropicMessagesAdapter()
        self.assertEqual(b.url('https://api.example.com'), 'https://api.example.com/v1/messages')
        self.assertEqual(b.headers('k')['x-api-key'], 'k')
        self.assertEqual(b.body(msgs, 'm', 0.2, True)['system'], 'sys')
        self.assertEqual(b.parse({'content': [{'type': 'text', 'text': 'ok'}], 'usage': {'input_tokens': 5, 'output_tokens': 2}})[:3], ('ok', 5, 2))
        g = GenericJSONAdapter()
        self.assertIn('schema', g.body(msgs, 'm', 0.2, True, state={'x': 1}))
        self.assertIn('decisions', g.parse({'decisions': []})[0])
        with self.assertRaises(ValueError):
            a.parse({'nope': 1})

    def test_anthropic_and_generic_through_mock(self):
        with MockDecisionServer(MockConfig(seed=2)) as m:
            r = client(m.url.replace('/v1', ''), adapter='anthropic_messages').decide(self.state)
            self.assertTrue(r.ok, r.error)
            r = client(m.url + '/decide', adapter='generic_json').decide(self.state)
            self.assertTrue(r.ok, r.error)

    def test_replay_client(self):
        calls = [{'ok': True, 'raw_response': '{"decisions": []}', 'source': 'mock'}, {'ok': False, 'error': 'x'}]
        rc = ReplayClient(calls)
        self.assertTrue(rc.decide({}).ok)
        self.assertFalse(rc.decide({}).ok)
        self.assertFalse(rc.decide({}).ok)


if __name__ == '__main__':
    unittest.main()
