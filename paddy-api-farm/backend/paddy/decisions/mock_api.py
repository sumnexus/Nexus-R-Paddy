"""A STAND-IN for the external decision API, for building and testing without the real one.

It speaks the same wire formats as the real API (chat completions, Anthropic messages, plain JSON), reads the
state out of the request and answers with the built-in manager's choices — so a run against it reproduces a
built-in run decision for decision. Every reply is labelled: the model name says "stand-in" and the response
carries an ``X-Paddy-Mock: stand-in`` header, which the client records as source "mock".

Fault injection (deterministic for a given seed and request) exercises the client and the guardrails:
  latency_ms   extra delay on every reply
  p429, p500   share of requests answered with HTTP 429 (with Retry-After) or 500
  ptimeout     share answered only after ``timeout_s`` (longer than the client waits)
  pmalformed   share answered with text that is not valid JSON
  punsafe      share with an extra decision that the guardrails must reject

Run it alone with ``python -m paddy mock --port 8765``.
"""
from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .managers import BuiltinManager

STAND_IN_MODEL = 'mock-builtin (stand-in)'

# decisions the guardrails must always reject, whatever the state
UNSAFE = [
    {'field': 'A', 'action': 'fertilise', 'params': {'product': 'urea', 'kg_per_ha': 400}, 'reason': 'stand-in fault: urea overdose'},
    {'field': 'B', 'action': 'spray', 'params': {'product': 'emamectin'}, 'squares': ['B1,1', 'B1,2'], 'reason': 'stand-in fault: not on the paddy label'},
    {'field': 'A', 'action': 'irrigate', 'params': {'target_depth_mm': 140}, 'reason': 'stand-in fault: deeper than allowed'},
    {'field': 'B', 'action': 'amend', 'params': {'product': 'lime', 'dose': 5000}, 'squares': ['B2,2'], 'reason': 'stand-in fault: lime overdose'},
    {'field': 'A', 'action': 'spray', 'params': {'product': 'pymetrozine', 'target_pest': 'leaf_folder'}, 'squares': ['A1,1'],
     'reason': 'stand-in fault: product not labelled for this pest'},
]


def _state_from_messages(messages):
    """The state JSON in the first user message (repair requests append more messages after it)."""
    for m in messages:
        if m.get('role') != 'user':
            continue
        c = m.get('content')
        if isinstance(c, list):
            c = ''.join(p.get('text', '') for p in c if isinstance(p, dict))
        mm = re.search(r'```json\s*(\{.*\})\s*```', c or '', re.S)
        if mm:
            return json.loads(mm.group(1))
    raise ValueError('no state found in the request')


class MockConfig:
    def __init__(self, seed=0, latency_ms=0.0, p429=0.0, p500=0.0, ptimeout=0.0, pmalformed=0.0, punsafe=0.0, timeout_s=3.0, require_key=False):
        self.seed = seed
        self.latency_ms = latency_ms
        self.p429 = p429
        self.p500 = p500
        self.ptimeout = ptimeout
        self.pmalformed = pmalformed
        self.punsafe = punsafe
        self.timeout_s = timeout_s
        self.require_key = require_key

    @classmethod
    def parse(cls, text, **kw):
        """'p429=0.05,p500=0.02,latency_ms=50' → MockConfig."""
        cfg = cls(**kw)
        for part in (text or '').split(','):
            if '=' in part:
                k, v = part.split('=', 1)
                k = k.strip()
                if hasattr(cfg, k):
                    setattr(cfg, k, type(getattr(cfg, k))(float(v)) if not isinstance(getattr(cfg, k), bool) else v.strip() in ('1', 'true', 'yes'))
        return cfg


class MockStats:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = 0
        self.faults = {'429': 0, '500': 0, 'timeout': 0, 'malformed': 0, 'unsafe': 0}
        self.unsafe_sent = []
        self.auth_seen = 0

    def bump(self, k):
        with self.lock:
            self.faults[k] += 1


def respond(srv, path, headers, raw):
    """The stand-in's answer to one POST, as (status, extra headers, JSON-able body). ``srv`` carries cfg, stats, the
    seen-request counter and the manager. Shared by the HTTP server and the in-process transport."""
    cfg, stats = srv.cfg, srv.stats
    get = (lambda k: headers.get(k) or headers.get(k.lower())) if headers else (lambda k: None)
    with stats.lock:
        stats.requests += 1
        if get('Authorization') or get('x-api-key'):
            stats.auth_seen += 1
        digest = hashlib.sha256(raw + str(cfg.seed).encode()).hexdigest()
        seen = srv.seen.get(digest, 0)
        srv.seen[digest] = seen + 1
    if cfg.require_key and not (get('Authorization') or get('x-api-key')):
        return 401, {}, {'error': {'message': 'missing key'}}
    if cfg.latency_ms:
        time.sleep(cfg.latency_ms / 1000)

    def u(salt):
        h = hashlib.sha256(f'{digest}:{seen}:{salt}'.encode()).digest()
        return int.from_bytes(h[:8], 'big') / 2 ** 64

    try:
        req = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, {}, {'error': {'message': 'body is not JSON'}}
    path = path.split('?')[0].rstrip('/')
    repair = False
    try:
        if path.endswith('/chat/completions') or path.endswith('/messages'):
            msgs = list(req.get('messages') or [])
            if req.get('system'):
                msgs = [{'role': 'system', 'content': req['system']}] + msgs
            state = _state_from_messages(msgs)
            repair = len([m for m in msgs if m.get('role') == 'user']) > 1
        else:
            state = req.get('state')
            repair = bool(req.get('repair'))
            if not isinstance(state, dict):
                raise ValueError('no state')
    except (ValueError, json.JSONDecodeError) as ex:
        return 400, {}, {'error': {'message': f'cannot read the state: {ex}'}}
    r = u('fault')
    edge = 0.0
    for kind, p in (('429', cfg.p429), ('500', cfg.p500), ('timeout', cfg.ptimeout)):
        if r < edge + p:
            stats.bump(kind)
            if kind == '429':
                return 429, {'Retry-After': '1'}, {'error': {'message': 'rate limited (stand-in fault)'}}
            if kind == '500':
                return 500, {}, {'error': {'message': 'internal error (stand-in fault)'}}
            return 'timeout', {}, {'error': {'message': 'too slow (stand-in fault)'}}
        edge += p
    resp = srv.manager.decide(state)
    resp['notes'] = 'stand-in mock: the built-in manager\'s choices'
    if not repair and u('unsafe') < cfg.punsafe:
        k = int(u('which') * len(UNSAFE)) % len(UNSAFE)
        resp['decisions'] = (resp['decisions'] + [dict(UNSAFE[k])])[:12]
        stats.bump('unsafe')
        with stats.lock:
            stats.unsafe_sent.append(UNSAFE[k]['reason'])
    text = json.dumps(resp)
    if not repair and u('malformed') < cfg.pmalformed:
        stats.bump('malformed')
        text = 'Here are my decisions: ' + text[: max(10, len(text) // 2)]
    prompt_chars = len(raw)
    if path.endswith('/chat/completions'):
        out = {'id': f'mock-{digest[:12]}', 'object': 'chat.completion', 'created': int(time.time()), 'model': STAND_IN_MODEL,
               'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
               'usage': {'prompt_tokens': prompt_chars // 4, 'completion_tokens': len(text) // 4, 'total_tokens': (prompt_chars + len(text)) // 4}}
    elif path.endswith('/messages'):
        out = {'id': f'mock-{digest[:12]}', 'type': 'message', 'role': 'assistant', 'model': STAND_IN_MODEL,
               'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': prompt_chars // 4, 'output_tokens': len(text) // 4}}
    else:
        try:
            out = json.loads(text)
            out['model'] = STAND_IN_MODEL
        except json.JSONDecodeError:
            out = {'content': text, 'model': STAND_IN_MODEL}
    return 200, {}, out


class _Handler(BaseHTTPRequestHandler):
    server_version = 'PaddyMock/1.0'
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):   # quiet; and never log headers (they carry the key)
        pass

    def _send(self, code, obj, extra=None):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Paddy-Mock', 'stand-in')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._send(200, {'ok': True, 'model': STAND_IN_MODEL, 'note': 'stand-in for the real decision API'})

    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n)
        code, extra, body = respond(self.server, self.path, self.headers, raw)
        if code == 'timeout':
            time.sleep(self.server.cfg.timeout_s)
            code = 504
        return self._send(code, body, extra)


class InProcessStandIn:
    """The stand-in without the network, for running hundreds of API seasons: a transport for ``DecisionAPI`` that
    hands each request to the same code the HTTP stand-in runs (``respond``) and returns its reply with the same
    headers. The client builds the prompt, parses, validates and repairs exactly as it does over HTTP."""

    def __init__(self, cfg=None):
        self.cfg = cfg or MockConfig()
        self.stats = MockStats()
        self.seen = {}
        self.manager = BuiltinManager()
        self.url = 'inprocess://stand-in/v1'

    def __call__(self, method, url, headers, body, timeout):
        path = url.split('://', 1)[-1]
        path = path[path.index('/'):] if '/' in path else '/'
        code, extra, out = respond(self, path, headers or {}, body or b'')
        if code == 'timeout':
            raise socket.timeout('stand-in timeout (in-process)')
        h = {'Content-Type': 'application/json', 'X-Paddy-Mock': 'stand-in'}
        h.update(extra)
        return code, h, json.dumps(out).encode('utf-8')


class MockDecisionServer:
    """``with MockDecisionServer(cfg) as m: DecisionAPI(base_url=m.url, model='mock', ...)``"""

    def __init__(self, cfg=None, host='127.0.0.1', port=0):
        self.cfg = cfg or MockConfig()
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.cfg = self.cfg
        self.httpd.stats = self.stats = MockStats()
        self.httpd.manager = BuiltinManager()
        self.httpd.seen = {}
        self.thread = None

    @property
    def url(self):
        h, p = self.httpd.server_address[:2]
        return f'http://{h}:{p}/v1'

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='mock-api', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()
