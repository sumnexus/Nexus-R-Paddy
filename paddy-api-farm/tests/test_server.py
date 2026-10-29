import json
import socket
import time
import unittest
import urllib.error
import urllib.request

import _helpers  # noqa: F401
from paddy.server import serve


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(port=0, manager='api', mode='approve', mock=True, block=False, runs_dir=None, static='/nonexistent')
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.stopping = True
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if cls.httpd.svc.mock:
            cls.httpd.svc.mock.stop()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return json.loads(r.read())

    def post(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(body or {}).encode(), method='POST', headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as ex:
            return ex.code, json.loads(ex.read())

    def test_1_health_config(self):
        h = self.get('/api/health')
        self.assertTrue(h['ok'])
        self.assertEqual(h['manager'], 'api')
        c = self.get('/api/config')
        self.assertEqual(c['rows'], 6)
        self.assertTrue(c['api']['key_present'])
        self.assertNotIn('not-a-real-key', json.dumps(c))

    def test_2_approval_flow(self):
        st, s = self.post('/api/sim/step', {'hours': 2})
        self.assertEqual(s['status'], 'awaiting_approval')
        pend = self.get('/api/decisions?status=pending_approval')['decisions']
        self.assertTrue(pend)
        st, r = self.post(f'/api/decisions/{pend[0]["id"]}/approve')
        self.assertEqual(st, 200)
        st, r = self.post(f'/api/decisions/{pend[1]["id"]}/reject', {'reason': 'not today'})
        self.assertEqual(r['decision']['status'], 'rejected_by_person')
        st, r = self.post(f'/api/decisions/{pend[1]["id"]}/approve')
        self.assertEqual(st, 409)
        for d in self.get('/api/decisions?status=pending_approval')['decisions']:
            self.post(f'/api/decisions/{d["id"]}/approve')
        st, s = self.post('/api/sim/step', {'hours': 3})
        self.assertGreaterEqual(s['stepped_hours'], 1)
        d = self.get(f'/api/decisions/{pend[0]["id"]}')
        self.assertIn('state', d['call'])
        self.assertEqual(d['call']['source'], 'mock')

    def test_3_views(self):
        f = self.get('/api/fields/A')
        self.assertEqual(len(f['squares']), 6)
        self.assertEqual(len(f['squares'][0]), 6)
        self.assertIn('depth_mm', f['squares'][0][0])
        self.assertIn('daily', self.get('/api/weather'))
        self.assertTrue(self.get('/api/models')['models'])
        self.assertIn('soil', self.get('/api/audit')['summary'])
        self.assertTrue(self.get('/api/runs')['runs'])
        self.assertGreater(len(self.get('/api/openapi.json')['paths']), 20)
        self.assertIn('fields', self.get('/api/farm'))

    def test_4_validation(self):
        self.assertEqual(self.post('/api/sim/speed', {'x': 'fast'})[0], 400)
        self.assertEqual(self.post('/api/events', {'kind': 'flood'})[0], 400)
        self.assertEqual(self.post('/api/sim/step', {'hours': 0})[0], 400)
        self.assertEqual(self.post('/api/sim/reset', {'season': 'summer'})[0], 400)
        self.assertEqual(self.post('/api/observations', {'kind': 'pest', 'field': 'C'})[0], 400)
        self.assertEqual(self.post('/api/decisions/d99999/approve')[0], 409)
        try:
            urllib.request.urlopen(self.base + '/api/nope')
            self.fail('expected 404')
        except urllib.error.HTTPError as ex:
            self.assertEqual(ex.code, 404)

    def test_5_events_notes_export(self):
        st, r = self.post('/api/events', {'kind': 'storm'})
        self.assertEqual(st, 200)
        st, r = self.post('/api/events', {'kind': 'dry', 'day': 40})
        self.assertEqual(r['scheduled']['day'], 40)
        st, r = self.post('/api/observations', {'kind': 'note', 'text': 'the east bund of A leaks', 'field': 'A'})
        self.assertEqual(st, 200)
        rid = self.get('/api/runs')['runs'][-1]['run_id']
        with urllib.request.urlopen(self.base + f'/api/runs/{rid}/export?format=csv') as r:
            self.assertTrue(r.read().startswith(b'id,call_id'))
        with urllib.request.urlopen(self.base + f'/api/runs/{rid}/export?format=json') as r:
            data = json.loads(r.read())
            self.assertIn('decisions', data)
            self.assertNotIn('not-a-real-key', json.dumps(data))

    def test_6_stream(self):
        s = socket.create_connection(('127.0.0.1', self.httpd.server_address[1]), timeout=5)
        s.sendall(b'GET /api/stream HTTP/1.1\r\nHost: x\r\n\r\n')
        data = ''
        end = time.time() + 8
        while '"type": "status"' not in data and time.time() < end:    # headers and the first event may arrive apart
            try:
                chunk = s.recv(4000).decode()
            except socket.timeout:
                break
            if not chunk:
                break
            data += chunk
        s.close()
        self.assertIn('text/event-stream', data)
        self.assertIn('"type": "status"', data)

    def test_6b_keep_alive_with_unread_bodies(self):
        """Several requests on one kept-alive connection, each POST with a body its route doesn't use: every
        request must still be read correctly (browsers and Node reuse connections)."""
        import http.client
        conn = http.client.HTTPConnection('127.0.0.1', self.httpd.server_address[1], timeout=30)
        try:
            for path, body in (('/api/sim/pause', {'ignored': 'x' * 50}), ('/api/decisions/d99999/approve', {}), ('/api/sim/pause', {}),
                               ('/api/nothing-here', {'a': 1})):
                conn.request('POST', path, body=json.dumps(body), headers={'Content-Type': 'application/json'})
                r = conn.getresponse()
                data = json.loads(r.read())
                self.assertIn(r.status, (200, 404, 409), (path, r.status, data))
            conn.request('GET', '/api/health')
            r = conn.getresponse()
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read())['ok'])
        finally:
            conn.close()

    def test_8_openapi_covers_every_endpoint(self):
        import re
        from paddy.server import ROUTES
        doc = self.get('/api/openapi.json')
        rid = self.get('/api/runs')['runs'][-1]['run_id']
        did = self.get('/api/decisions')['decisions'][0]['id']

        def concrete(path):
            return path.replace('{name}', 'A').replace('{rc}', '1,1').replace('/runs/{id}', f'/runs/{rid}').replace('{id}', did)
        documented = []
        for path, ops in doc['paths'].items():
            for method in ops:
                documented.append((method.upper(), concrete(path)))
        for method, rx, fn in ROUTES:
            self.assertTrue(any(m == method and rx.match(p) for m, p in documented), f'{method} {rx.pattern} is not in the OpenAPI document')
        for method, p in documented:
            self.assertTrue(p == '/api/stream' or any(m == method and rx.match(p) for m, rx, fn in ROUTES), f'{method} {p} is documented but not served')
            if method == 'GET' and p not in ('/api/stream',) and not p.endswith('/export'):
                with urllib.request.urlopen(self.base + p, timeout=30) as r:
                    self.assertEqual(r.status, 200, p)
                    json.loads(r.read())

    def test_9_compare_square_history_readings(self):
        st, r = self.post('/api/compare', {'managers': ['builtin', 'conventional', 'api'], 'seasons': 1})
        self.assertEqual(st, 200, r)
        import time
        for _ in range(240):
            j = self.get('/api/compare')
            if j['status'] != 'running':
                break
            time.sleep(1)
        self.assertEqual(j['status'], 'done', j.get('error'))
        self.assertIn('api', j['paired'])
        self.assertEqual(self.post('/api/compare', {'managers': ['nobody']})[0], 400)
        h = self.get('/api/fields/A/squares/2,3')
        self.assertEqual(h['sq'], 'A2,3')
        self.assertTrue(h['history'])
        st, r = self.post('/api/observations', {'kind': 'water', 'field': 'B', 'depth_mm': 33})
        self.assertEqual(st, 200)
        st, r = self.post('/api/observations', {'kind': 'soil', 'field': 'B', 'square': '2,2', 'ph': 5.1})
        self.assertEqual(st, 200)
        f = self.get('/api/fields/B')
        self.assertEqual(f['squares'][1][1]['soil']['ph'], 5.1)
        self.assertEqual(self.post('/api/observations', {'kind': 'soil', 'field': 'B', 'square': '2,2', 'ph': 14})[0], 400)
        self.assertEqual(self.get('/api/health')['api_status'], 'mock')

    def test_7_static_fallback_and_cors(self):
        req = urllib.request.Request(self.base + '/', headers={'Origin': 'http://localhost:5173'})
        with urllib.request.urlopen(req) as r:
            self.assertIn(b'not built yet', r.read())
        req = urllib.request.Request(self.base + '/api/health', headers={'Origin': 'http://localhost:5173'})
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.headers.get('Access-Control-Allow-Origin'), '*')


if __name__ == '__main__':
    unittest.main()


class RealApiNotSetUp(unittest.TestCase):
    """Without --mock and without a key: the website's comparison must refuse the API, and say what one season would cost."""

    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(port=0, manager='builtin', mode='auto', mock=False, block=False, runs_dir=None, static='/nonexistent')
        cls.base = f'http://127.0.0.1:{cls.httpd.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.stopping = True
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_compare_refuses_and_config_has_a_cost_estimate(self):
        with urllib.request.urlopen(self.base + '/api/config', timeout=30) as r:
            cfg = json.loads(r.read())
        self.assertEqual(cfg['api']['status'], 'not configured')
        self.assertFalse(cfg['api']['key_present'])
        c = cfg['cost_per_api_season']
        self.assertGreater(c['calls'], 200)
        self.assertGreater(c['usd'], 0)
        req = urllib.request.Request(self.base + '/api/compare', data=json.dumps({'managers': ['api', 'builtin'], 'seasons': 1, 'confirm_real_api': True}).encode(),
                                     method='POST', headers={'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=30)
        self.assertEqual(ctx.exception.code, 409)
        self.assertIn('not set up', json.loads(ctx.exception.read())['error'])

