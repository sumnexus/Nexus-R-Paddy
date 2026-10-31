"""The client for the external decision API.

    from paddy.decisions.api_client import api      # built from the environment at import time
    result = api.decide(state)                     # → engine.CallResult

Settings come from the environment (or a git-ignored .env file): API_BASE_URL, API_MODEL, API_KEY, and optionally
API_ADAPTER (chat_completions | anthropic_messages | generic_json), API_TIMEOUT_S, API_MAX_CALLS_PER_DAY,
API_MAX_CALLS_PER_SEASON, API_MAX_CALLS_PER_REAL_HOUR, API_TEMPERATURE, API_JSON_MODE, API_RETRIES, API_FALLBACK,
API_PRICE_IN_PER_M and API_PRICE_OUT_PER_M (US$ per million tokens, for cost estimates).

The key is read from the environment only, sent only in the request header, and never written anywhere: not in
logs, records, exports, error messages or ``repr``. Without a key or URL the client is simply "not configured"
and every call falls back.

What happens on a call: the state goes out with the versioned prompt; timeouts, HTTP 429 and 5xx are retried with
exponential backoff (honouring Retry-After); a reply that isn't usable JSON gets one repair request quoting the
schema errors; if that fails too, the engine falls back (API_FALLBACK = hold or builtin) and logs it.
"""
from __future__ import annotations

import collections
import json
import os
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from .. import config as CFG
from . import prompt as P
from . import schema as S
from .adapters import ADAPTERS
from .records import redact

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}


def _short(text, n=300):
    t = (text or '').strip().replace('\n', ' ')
    return t[:n] + ('…' if len(t) > n else '')


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def urllib_transport(method, url, headers, body, timeout):
    """One HTTP request with the standard library → (status, headers, body bytes). Network errors raise.
    Requests to this machine (the mock) never go through a proxy; others follow the usual proxy settings."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    opener = _DIRECT if host in ('127.0.0.1', 'localhost', '::1') else urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as ex:
        return ex.code, dict(ex.headers or {}), ex.read() if hasattr(ex, 'read') else b''


class DecisionAPI:
    def __init__(self, base_url='', model='', key=None, adapter='chat_completions', timeout_s=60.0, temperature=0.2, json_mode=True,
                 retries=3, fallback='hold', max_calls_per_real_hour=CFG.MAX_CALLS_PER_REAL_HOUR, price_in_per_m=None, price_out_per_m=None,
                 transport=None, backoff_base_s=1.0, max_tokens=16000):
        if adapter not in ADAPTERS:
            raise ValueError(f'unknown adapter {adapter!r} (one of {", ".join(ADAPTERS)})')
        if fallback not in ('hold', 'builtin'):
            raise ValueError("fallback must be 'hold' or 'builtin'")
        self.base_url = (base_url or '').strip()
        self.model = (model or '').strip()
        self.__key = key or None
        self.adapter_name = adapter
        self.adapter = ADAPTERS[adapter]()
        self.timeout_s = float(timeout_s)
        self.temperature = float(temperature)
        self.json_mode = bool(json_mode)
        self.retries = int(retries)
        self.fallback = fallback
        self.max_calls_per_real_hour = int(max_calls_per_real_hour)
        self.price_in_per_m = price_in_per_m
        self.price_out_per_m = price_out_per_m
        self.transport = transport or urllib_transport
        self.backoff_base_s = float(backoff_base_s)
        self.max_tokens = int(max_tokens)
        self.prompt_version = P.PROMPT_VERSION
        self._recent = collections.deque()
        self.n_calls = 0
        self.n_failed = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self._rng = random.Random(1)

    @classmethod
    def from_env(cls, **over):
        CFG.load_dotenv()
        s = CFG.api_settings()
        price_in = os.environ.get('API_PRICE_IN_PER_M')
        price_out = os.environ.get('API_PRICE_OUT_PER_M')
        kw = dict(base_url=s['base_url'], model=s['model'], key=os.environ.get('API_KEY') or None, adapter=s['adapter'],
                  timeout_s=s['timeout_s'], temperature=s['temperature'], json_mode=s['json_mode'], retries=s['retries'],
                  fallback=s['fallback'] if s['fallback'] in ('hold', 'builtin') else 'hold',
                  max_calls_per_real_hour=s['max_calls_per_real_hour'],
                  price_in_per_m=float(price_in) if price_in else None, price_out_per_m=float(price_out) if price_out else None)
        kw.update(over)
        return cls(**kw)

    # ── description without the key ──
    @property
    def key_present(self):
        return bool(self.__key)

    @property
    def configured(self):
        return bool(self.base_url and (self.model or self.adapter_name == 'generic_json'))

    def describe(self):
        return {'base_url': self.base_url, 'model': self.model, 'adapter': self.adapter_name, 'key_present': self.key_present,
                'configured': self.configured, 'timeout_s': self.timeout_s, 'temperature': self.temperature, 'json_mode': self.json_mode,
                'retries': self.retries, 'fallback': self.fallback, 'max_calls_per_real_hour': self.max_calls_per_real_hour,
                'prompt_version': self.prompt_version, 'price_in_per_m': self.price_in_per_m, 'price_out_per_m': self.price_out_per_m}

    def __repr__(self):
        return f'DecisionAPI(base_url={self.base_url!r}, model={self.model!r}, adapter={self.adapter_name!r}, key={"set" if self.key_present else "not set"})'

    __str__ = __repr__

    def cost_usd(self, tokens_in, tokens_out):
        if self.price_in_per_m is None or self.price_out_per_m is None:
            return None
        return tokens_in / 1e6 * self.price_in_per_m + tokens_out / 1e6 * self.price_out_per_m

    # ── one HTTP exchange with retries ──
    def _post(self, messages, state, log):
        url = self.adapter.url(self.base_url)
        body = json.dumps(self.adapter.body(messages, self.model, self.temperature, self.json_mode, self.max_tokens, state)).encode('utf-8')
        headers = self.adapter.headers(self.__key)
        attempt = 0
        while True:
            attempt += 1
            t0 = time.perf_counter()
            status, rh, data, err = None, {}, b'', None
            try:
                status, rh, data = self.transport('POST', url, headers, body, self.timeout_s)
            except (socket.timeout, TimeoutError):
                err = f'timeout after {self.timeout_s:g} s'
            except urllib.error.URLError as ex:
                reason = getattr(ex, 'reason', ex)
                err = 'timeout' if isinstance(reason, (socket.timeout, TimeoutError)) else f'connection error: {type(reason).__name__}'
            except (ConnectionError, OSError) as ex:
                err = f'connection error: {type(ex).__name__}'
            ms = (time.perf_counter() - t0) * 1000
            entry = {'attempt': attempt, 'status': status, 'latency_ms': round(ms, 1)}
            if err:
                entry['error'] = err
            elif status != 200:
                entry['error'] = f'HTTP {status}: {_short(data.decode("utf-8", "replace"), 200)}'
            log.append(redact(entry))
            if status == 200 and not err:
                return status, rh, data, None
            retryable = err is not None or status in RETRY_STATUS
            if not retryable or attempt > self.retries:
                return status, rh, data, entry.get('error')
            wait = self.backoff_base_s * (2 ** (attempt - 1)) * (0.5 + 0.5 * self._rng.random())
            ra = None
            for k, v in (rh or {}).items():
                if k.lower() == 'retry-after':
                    try:
                        ra = float(v)
                    except ValueError:
                        ra = None
            if ra is not None:
                wait = max(wait, min(ra, 60.0) * (self.backoff_base_s if self.backoff_base_s < 1 else 1.0))
            time.sleep(min(wait, 60.0))

    def _interpret(self, status, data):
        """HTTP body → (reply text, tokens in, tokens out, model, error)."""
        try:
            obj = json.loads(data.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, None, None, '', 'the HTTP body is not JSON'
        try:
            text, tin, tout, model = self.adapter.parse(obj)
        except ValueError as ex:
            return None, None, None, '', str(ex)
        return text, tin, tout, model, None

    # ── the call the engine makes ──
    def decide(self, state):
        from ..engine import CallResult
        req_meta = {'url': self.adapter.url(self.base_url) if self.base_url else None, 'model': self.model, 'adapter': self.adapter_name,
                    'prompt_version': self.prompt_version, 'temperature': self.temperature, 'json_mode': self.json_mode, 'attempts': []}
        if not self.configured:
            return CallResult(False, error='decision API not configured (set API_BASE_URL and API_MODEL)', source='api', request=req_meta)
        now = time.time()
        while self._recent and now - self._recent[0] > 3600:
            self._recent.popleft()
        if len(self._recent) >= self.max_calls_per_real_hour:
            return CallResult(False, error=f'real-hour call budget reached ({self.max_calls_per_real_hour} calls/hour)', source='api', request=req_meta)
        self._recent.append(now)
        self.n_calls += 1
        t0 = time.perf_counter()
        messages = P.build_messages(state)
        tin_total = tout_total = 0
        estimated = False
        source = 'api'
        model = self.model
        raw = None
        repaired = False
        errors = []
        for round_ in (0, 1):
            status, rh, data, err = self._post(messages, state, req_meta['attempts'])
            if any(k.lower() == 'x-paddy-mock' for k in (rh or {})):
                source = 'mock'
            if err:
                self.n_failed += 1
                return CallResult(False, raw=raw, errors=errors, source=source, model=model, latency_ms=(time.perf_counter() - t0) * 1000,
                                  tokens_in=tin_total, tokens_out=tout_total, attempts=len(req_meta['attempts']), repaired=repaired,
                                  http_status=status, error=redact(err), request=req_meta)
            text, tin, tout, m, perr = self._interpret(status, data)
            model = m or model
            if tin is None:
                tin, estimated = sum(len(x['content']) for x in messages) // 4, True
            if tout is None:
                tout, estimated = len(text or '') // 4, True
            tin_total += int(tin)
            tout_total += int(tout)
            raw = text if text is not None else _short(data.decode('utf-8', 'replace'), 2000)
            problems = []
            obj = None
            if perr:
                problems.append(perr)
            else:
                try:
                    obj = S.extract_json(text)
                except (json.JSONDecodeError, ValueError) as ex:
                    problems.append(f'the reply is not valid JSON ({type(ex).__name__}: {str(ex)[:120]})')
            if obj is not None:
                decisions, errs = S.validate(obj)
                if decisions or not errs:
                    self.tokens_in += tin_total
                    self.tokens_out += tout_total
                    req_meta['tokens_estimated'] = estimated
                    return CallResult(True, response=obj, raw=redact(raw), errors=errs, source=source, model=model,
                                      latency_ms=(time.perf_counter() - t0) * 1000, tokens_in=tin_total, tokens_out=tout_total,
                                      attempts=len(req_meta['attempts']), repaired=repaired, http_status=status, request=req_meta)
                problems += errs
            errors += problems
            if round_ == 0:
                messages = P.repair_messages(messages, raw, problems)
                repaired = True
        self.n_failed += 1
        self.tokens_in += tin_total
        self.tokens_out += tout_total
        return CallResult(False, raw=redact(raw), errors=errors, source=source, model=model, latency_ms=(time.perf_counter() - t0) * 1000,
                          tokens_in=tin_total, tokens_out=tout_total, attempts=len(req_meta['attempts']), repaired=True, http_status=200,
                          error='the reply could not be used after one repair request', request=req_meta)


class ReplayClient:
    """Plays back recorded replies, in order, for an exact replay of an earlier run (no network)."""

    def __init__(self, calls, fallback='hold'):
        self.calls = [c for c in calls]
        self.i = 0
        self.fallback = fallback
        self.prompt_version = calls[0].get('prompt_version') if calls else P.PROMPT_VERSION

    def decide(self, state):
        from ..engine import CallResult
        if self.i >= len(self.calls):
            return CallResult(False, error='replay: no more recorded replies', source='replay')
        c = self.calls[self.i]
        self.i += 1
        if not c.get('ok'):
            return CallResult(False, raw=c.get('raw_response'), errors=c.get('schema_errors') or [], source=c.get('source', 'replay'),
                              error=c.get('error') or 'recorded failure', request={'replay': True})
        raw = c.get('raw_response')
        try:
            obj = S.extract_json(raw)
        except (json.JSONDecodeError, ValueError):
            return CallResult(False, raw=raw, error='replay: recorded reply is not JSON', source='replay')
        return CallResult(True, response=obj, raw=raw, source=c.get('source', 'replay'), model=c.get('model', ''),
                          tokens_in=c.get('tokens_in') or 0, tokens_out=c.get('tokens_out') or 0, request={'replay': True})


def _make():
    try:
        return DecisionAPI.from_env()
    except Exception:  # noqa: BLE001 — a bad setting must not stop the package from importing
        return DecisionAPI()


api = _make()
