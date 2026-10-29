"""The REST API (standard library HTTP server + NumPy), with server-sent events and the built website.

    python -m paddy serve --port 8000            # http://127.0.0.1:8000 (website) and /api/...
    python -m paddy serve --manager api --mock   # decisions from the stand-in API

One farm runs at a time. The clock runs in a background thread; every request takes the same lock, so what the
API returns is always one consistent moment of the simulation. The OpenAPI document is at /api/openapi.json.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import re
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

from . import __version__
from . import config as CFG
from .decisions import rules as R
from .decisions.records import decisions_csv, redact
from .engine import FIELD_IDX, MANAGER_NAMES, Engine
from .sim.twin import DAY, HOUR, PADDY, PEST_KEYS, stage_name

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_STATIC = os.path.normpath(os.path.join(HERE, '..', '..', 'frontend', 'dist'))
EVENT_KINDS = ('pest', 'dry', 'storm', 'cloudy')


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def _r(x, nd=2):
    if x is None:
        return None
    try:
        if not np.isfinite(x):
            return None
    except TypeError:
        return x
    return round(float(x), nd)


class FarmService:
    """The one farm the server runs, its clock and the event stream."""

    def __init__(self, manager='builtin', mode='auto', seed=20261024, weatherYear=0, season='kharif', client_factory=None,
                 runs_dir=None, speed=24.0, mock=None):
        self.lock = threading.RLock()
        self.client_factory = client_factory
        self.runs_dir = runs_dir
        self.speed = float(speed)              # simulated hours per real second
        self.work_rate = 30.0                  # simulated seconds per real second while the arms work (so they can be watched)
        self.running = False
        self.subscribers = []
        self.sub_lock = threading.Lock()
        self.finished_runs = []                # (summary, engine) of earlier runs, newest last
        self.future_events = []
        self.mock = mock
        self.baseline = None
        self._baseline_token = None
        self.compare_job = None
        self._last_tick_emit = 0.0
        self.thread = threading.Thread(target=self._loop, name='farm-clock', daemon=True)
        self.engine = None
        self.reset(seed=seed, weatherYear=weatherYear, season=season, manager=manager, mode=mode)
        self.thread.start()

    # ── engine lifecycle ──
    def reset(self, seed=None, weatherYear=None, season=None, manager=None, mode=None):
        with self.lock:
            old = self.engine
            if old is not None and old.store.decisions:
                self.finished_runs.append(old)
                self.finished_runs = self.finished_runs[-12:]
            cfg = dict(old.cfg) if old is not None else {}
            seed = int(seed if seed is not None else cfg.get('seed', 20261024))
            weatherYear = int(weatherYear if weatherYear is not None else cfg.get('weatherYear', 0))
            season = season or cfg.get('season', 'kharif')
            manager = manager or cfg.get('manager', 'builtin')
            mode = mode or cfg.get('mode', 'auto')
            if season not in CFG.SEASONS:
                raise ApiError(400, f'season must be one of {", ".join(CFG.SEASONS)}')
            if manager not in MANAGER_NAMES:
                raise ApiError(400, f'manager must be one of {", ".join(MANAGER_NAMES)}')
            if mode not in ('auto', 'approve'):
                raise ApiError(400, "mode must be 'auto' or 'approve'")
            client = self.client_factory() if (manager.startswith('api') and self.client_factory) else None
            run_dir = os.path.join(self.runs_dir, f'{time.strftime("%Y%m%d-%H%M%S")}-{manager}-{season}-{seed}') if self.runs_dir else None
            self.engine = Engine(seed=seed, weatherYear=weatherYear, season=season, manager=manager, mode=mode, client=client,
                                 record_dir=run_dir, listener=self._on_engine, record_frames=True)
            self.future_events = []
            self.running = False
            self._start_baseline(self.engine.cfg)
        self.broadcast('status', self.status())
        return self.status()

    def _on_engine(self, kind, data):
        if kind == 'tick':
            now = time.time()
            if now - self._last_tick_emit < 0.25:
                return
            self._last_tick_emit = now
            self.broadcast('tick', self.farm_brief())
            return
        self.broadcast(kind, data)

    def _loop(self):
        while True:
            if not self.running:
                time.sleep(0.05)
                continue
            t0 = time.time()
            steps = max(1, int(self.speed // 20))       # at high speed several hours per slice
            arm_wait = 0.0
            with self.lock:
                e = self.engine
                for k in range(steps):
                    self._fire_future_events()
                    ok = e.step_hour()
                    if not ok:
                        if e.finished:
                            self.running = False
                            self.broadcast('status', self.status())
                        elif e.awaiting_approval():
                            self.broadcast('status', self.status())
                        break
                    ah = getattr(e, 'last_arm_hour', None)
                    if ah:
                        # an hour with arm work: slow down so the website can show the arms doing it (the live view plays
                        # the hour's keyframes at work_rate), then carry on
                        arm_wait = min(45.0, max(0.0, ah['last'] - ah['t0']) / max(1.0, self.work_rate))
                        break
            if e.awaiting_approval():
                time.sleep(0.1)
                continue
            delay = max((k + 1) / max(0.01, self.speed), arm_wait) - (time.time() - t0)
            if delay > 0:
                time.sleep(delay)

    def _fire_future_events(self):
        e = self.engine
        keep = []
        for ev in self.future_events:
            if e.tw.day >= ev['day']:
                e.inject_event(ev['kind'], ev.get('field'), ev.get('pest'))
            else:
                keep.append(ev)
        self.future_events = keep

    # ── the event stream ──
    def subscribe(self):
        q = queue.Queue(maxsize=500)
        with self.sub_lock:
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self.sub_lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def broadcast(self, kind, data):
        msg = json.dumps({'type': kind, 'data': redact(data)}, default=str)
        with self.sub_lock:
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    # ── views ──
    def api_status(self):
        e = self.engine
        if not e.manager.startswith('api'):
            return 'not used'
        if self.mock:
            return 'mock'
        c = e.client
        if c is None or not getattr(c, 'configured', False):
            return 'not configured'
        if not c.key_present:
            return 'no key'
        if c.n_calls and c.n_calls > c.n_failed:
            return 'connected'
        return 'failing' if c.n_failed else 'configured'

    def status(self):
        e = self.engine
        st = 'finished' if e.finished else 'awaiting_approval' if e.awaiting_approval() else 'running' if self.running else 'paused'
        return {'status': st, 'run_id': e.run_id, 'time': e.tw.iso(), 'day': e.tw.day, 'season': e.tw.season, 'seed': e.cfg['seed'],
                'weatherYear': e.cfg['weatherYear'], 'manager': e.manager, 'mode': e.mode, 'speed': self.speed, 'work_rate': self.work_rate,
                'pending_approvals': len(e.pending), 'scheduled': len(e.queue), 'calls': e.n_calls,
                'calls_today': e.calls_by_day[e.tw.day], 'max_calls_per_day': e.max_calls_per_day, 'max_calls_per_season': e.max_calls_per_season,
                'api_status': self.api_status()}

    def farm_brief(self):
        e = self.engine
        tw = e.tw
        out = self.status()
        out['fields'] = {}
        for f in range(2):
            fd = tw.fields[f]
            prog = tw.field_progress(f)
            out['fields'][fd.name] = {'phase': _phase(tw, f), 'water_mm': _r(tw.water_tube(f), 0), 'progress': _r(prog, 3),
                                      'stage': stage_name(prog) if fd.transplantT is not None and fd.harvestT is None else None,
                                      'yield_t_ha': _r(fd.yieldTHa, 2), 'pumping': fd.irrTarget is not None, 'draining': fd.drainTarget is not None}
        return out

    def farm(self):
        e = self.engine
        tw = e.tw
        out = self.farm_brief()
        W = tw.weather
        out['weather'] = self.weather_now()
        for f in range(2):
            fd = tw.fields[f]
            cs = tw.cells[f]
            m = tw.methane(f)
            out['fields'][fd.name].update({
                'puddled': fd.puddled, 'transplanted': tw.iso(fd.transplantT)[:10] if fd.transplantT is not None else None,
                'harvested': tw.iso(fd.harvestT)[:10] if fd.harvestT is not None else None, 'abandoned': fd.abandoned,
                'plants': {'planted': int(np.sum(tw.has[cs])), 'alive': int(np.sum(tw.has[cs] & tw.alive[cs] & ~tw.done[cs]))},
                'irrigation_mm': _r(fd.irrMm, 0), 'irrigation_m3': _r(fd.waterM3, 2), 'rain_mm': _r(fd.rainMm, 0), 'pump_kwh': _r(fd.energyKwh, 2),
                'n_kg_ha': _r(fd.nKgHa, 1), 'p2o5_kg_ha': _r(fd.pKgHa, 1), 'k2o_kg_ha': _r(fd.kKgHa, 1), 'sprays': len(fd.sprays),
                'ai_g_ha': _r(fd.aiG / (len(cs) * CFG.SQUARE_M2 / 10000), 1), 'bio_kg_ha': _r(fd.bioKg / (len(cs) * CFG.SQUARE_M2 / 10000), 2),
                'sprayed_squares': int(sum(s['squares'] for s in fd.sprays)), 'ch4_kg_ha': _r(m.kgHa, 1), 'ch4_regime': m.regime,
                'pests_seen': int(np.sum((tw.oPestId[cs] >= 0) & (tw.oPest[cs] > 0))),
                'history': [{k: h.get(k) for k in ('day', 'date', f'{fd.name}_h', f'{fd.name}_prog', f'{fd.name}_health', f'{fd.name}_irr', f'{fd.name}_pest', f'{fd.name}_n')}
                            for h in e.history[-200:]],
            })
        fr = e.frame()
        out['t'] = e.tw.t
        hh = int(e.tw.t // HOUR)
        out['weather_hours'] = e.weather_hours(hh - 24, hh + 24)
        out['water_hours'] = e.water_hours(hh - 24, hh + 1)
        out['season_month'] = CFG.SEASONS.get(e.tw.season, 6)
        out['arms'] = fr.get('arms')
        out['power'] = fr.get('power')
        out['pumping'] = [n for n in ('A', 'B') if fr['fields'][n].get('pumping')]
        for name, x in fr['fields'].items():
            out['fields'][name]['squares'] = x['sq']
            out['fields'][name]['tube_mm'] = x['tube']
            out['fields'][name]['lvl'] = x['lvl']
            out['fields'][name]['awd_threshold_mm'] = e._awd_threshold(FIELD_IDX[name])
            out['fields'][name]['days_to_harvest'] = _r(max(0.0, (1 - x['progress']) * PADDY.durationDays), 0) if x['phase'] in ('growing', 'drain_down') else None
            if e.yield_at_transplant.get(FIELD_IDX[name]) is not None:
                out['fields'][name]['yield_forecast_t_ha'] = _r(e.yield_at_transplant[FIELD_IDX[name]] * PADDY.yieldTHa, 2)
        out['baseline'] = self.baseline_view()
        out['summary'] = e.summary_cache if e.finished else None
        out['events'] = e.store.events[-20:]
        out['api'] = e.client.describe() if getattr(e, 'client', None) is not None and hasattr(e.client, 'describe') else None
        out['mock'] = bool(self.mock)
        return out

    def baseline_view(self):
        b = self.baseline
        if not b:
            return None
        if b.get('status') != 'done':
            return {'status': b.get('status', 'running'), 'manager': 'conventional'}
        return {'status': 'done', 'manager': 'conventional', 'history': b['history'], 'summary': b['summary']}

    def _start_baseline(self, cfg):
        """Conventional practice on the same fields and weather, run in the background, for the charts."""
        token = object()
        self._baseline_token = token
        self.baseline = {'status': 'running'}

        def work():
            try:
                be = Engine(seed=cfg['seed'], weatherYear=cfg['weatherYear'], season=cfg['season'], manager='conventional', keep_states=False)
                while be.step_hour():
                    if self._baseline_token is not token:
                        return
                    time.sleep(0)
                s = be.summary()
                if self._baseline_token is token:
                    self.baseline = {'status': 'done', 'history': be.history,
                                     'summary': {k: s[k] for k in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'bio_kg_ha', 'sprayed_squares', 'ch4_kg_ha', 'pump_kwh')},
                                     'fields': {k: {kk: v[kk] for kk in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'sprays', 'sprayed_squares', 'ch4_kg_ha')}
                                                for k, v in s['fields'].items()}}
                    self.broadcast('baseline', {'status': 'done'})
            except Exception as ex:  # noqa: BLE001
                self.baseline = {'status': f'error: {type(ex).__name__}'}
        threading.Thread(target=work, name='baseline', daemon=True).start()

    def square_history(self, name, r, c):
        e = self.engine
        if name not in FIELD_IDX:
            raise ApiError(404, f'no field {name!r}')
        if not (1 <= r <= e.tw.R and 1 <= c <= e.tw.K):
            raise ApiError(404, 'no such square')
        k = (r - 1) * e.tw.K + (c - 1)
        out = []
        for fr in e.frames:
            x = fr['fields'][name]['sq']
            out.append({'time': fr['time'], 'day': fr['day'], 'depth_mm': x['depth'][k], 'progress': x['prog'][k], 'health': x['health'][k],
                        'pest': x['pest'][k], 'risk': x['risk'][k], 'state': x['state'][k]})
        return {'sq': f'{name}{r},{c}', 'history': out}

    def weather_now(self):
        tw = self.engine.tw
        W = tw.weather
        d, h = tw.day, tw.hour
        fc = []
        for k in range(3):
            x = W.forecast_day(d, k)
            fc.append({'date': tw.iso(tw.t + k * DAY)[:10], 'tmax': _r(x.tmax, 1), 'tmin': _r(x.tmin, 1), 'rain_mm': _r(x.rainMm, 1),
                       'rain_prob': _r(x.rainProb, 2), 'rh': _r(x.rh, 0), 'wind_ms': _r(x.wind, 1)})
        return {'time': tw.iso(), 'temp_c': _r(W.temp(d, h), 1), 'rh': _r(W.rh(d, h), 0), 'wind_ms': _r(W.wind(d, h), 1),
                'rain_today_mm': _r(W.rain_between(d, 0, h), 1), 'forecast': fc}

    def weather(self):
        e = self.engine
        out = self.weather_now()
        out['daily'] = [{'day': h['day'], 'date': h['date'], 'rain_mm': h['rain']} for h in e.history]
        out['events'] = [ev for ev in e.store.events if ev['kind'] in ('event',)][-20:]
        out['scheduled_events'] = list(self.future_events)
        return out

    def field(self, name):
        e = self.engine
        tw = e.tw
        if name not in FIELD_IDX:
            raise ApiError(404, f'no field {name!r} (fields are {", ".join(CFG.FIELDS)})')
        f = FIELD_IDX[name]
        fd = tw.fields[f]
        adv = e.adv
        soil = {s.i: s for s in adv.soil(f)}
        est = {}
        if fd.puddled and fd.transplantT is None and not fd.abandoned or (fd.transplantT is None and not fd.abandoned):
            est = {a.i: a.chance for a in adv.establishment(f)}
        risk = {}
        if fd.transplantT is not None and fd.harvestT is None:
            rs, _ = adv.pest_risk(f)
            for r in rs:
                risk[r.i] = max(risk.get(r.i, 0.0), r.risk)
        tube = tw.water_tube(f)
        squares = []
        for r in range(tw.R):
            row = []
            for c in range(tw.K):
                i = tw.sq_index(f, r, c)
                rd = tw.soil_reading(i)
                pk = int(tw.oPestId[i])
                row.append({
                    'sq': tw.sq_name(i), 'row': r + 1, 'col': c + 1,
                    'depth_mm': _r(tube - adv.elev_obs[i], 0),
                    'planted': bool(tw.has[i]), 'alive': bool(tw.has[i] and tw.alive[i] and not tw.done[i]),
                    'harvested': bool(tw.done[i] and tw.harvestKg[i] > 0), 'progress': _r(tw.frac[i], 3) if tw.has[i] else None,
                    'stage': stage_name(min(1.0, float(tw.frac[i]))) if tw.has[i] and tw.alive[i] and not tw.done[i] else None,
                    'health': _r(tw.oHealth[i], 2) if tw.has[i] else None,
                    'soil': {'code': soil[i].code, 'ph': _r(rd.ph, 2), 'ec': _r(rd.ec, 2), 'n': _r(rd.n, 0), 'p': _r(rd.p, 1), 'k': _r(rd.k, 0),
                             'oc': _r(rd.oc, 2), 'sand': _r(rd.sand, 0), 'clay': _r(rd.clay, 0), 'probed': i in tw.probe,
                             'doses': [{'product': d.product, 'dose': _r(d.dose, 0), 'unit': d.unit} for d in soil[i].doses]},
                    'pest': {'pest': PEST_KEYS[pk], 'severity': _r(tw.oPest[i], 3), 'days': _r(tw.obs_age(i), 1)} if pk >= 0 and tw.oPest[i] > 0 else None,
                    'risk': _r(risk.get(i), 3), 'establish': _r(est.get(i), 3),
                    'residue': _r(tw.residue[i], 2) if tw.residue[i] > 0 else None,
                    'n_applied': _r(tw.nApplied[i], 1), 'sprays': int(tw.sprayCount[i]),
                    'yield_kg': _r(tw.harvestKg[i], 3) if tw.done[i] else None,
                })
            squares.append(row)
        brief = self.farm()['fields'][name]
        brief['squares'] = squares
        brief['name'] = name
        brief['rows'] = tw.R
        brief['cols'] = tw.K
        return brief

    def decisions(self, field=None, status=None, action=None, since=None, limit=500):
        e = self.engine
        out = []
        for d in e.store.decisions:
            if field and d['field'] != field:
                continue
            if status and d['status'] != status:
                continue
            if action and d['action'] != action:
                continue
            if since:
                if since.startswith('d') and d['id'] <= since:
                    continue
                if not since.startswith('d') and d['created'] < since:
                    continue
            out.append(e._public(d))
        return out[-limit:]

    def decision(self, did):
        e = self.engine
        d = e.store.by_id.get(did)
        if d is None:
            raise ApiError(404, f'no decision {did!r}')
        call = next((c for c in e.store.calls if c['id'] == d['call_id']), None)
        return {'decision': e._public(d), 'call': call}

    def models(self):
        e = self.engine
        m = e.adv.m
        from . import modelround as MR
        paddy = getattr(m, 'paddy', None)
        pk = MR.pack_models_info(paddy) if paddy else []
        replaced = {x['replaces'] for x in pk if x.get('replaces')}
        out = []
        for mid, met in m.metrics.items():
            out.append({'id': mid, 'title': met.get('title'), 'task': met.get('task'), 'trees': met.get('trees'), 'nodes': met.get('nodes'),
                        'features': met.get('features'), 'metric': met.get('metric'),
                        'used_here': mid in ('soil', 'pest', 'yield', 'establish', 'spray', 'harvest') and mid not in replaced})
        note = ('The playground\'s round-4 forests, with the paddy forests of the v2 model round where listed. ' if pk else
                'The forests are the Agri Robotics Playground\'s round-4 forests, unchanged. ')
        return {'version': m.version, 'built': m.built, 'models': out, 'live': e.audit.summary(),
                'paddy': {'version': paddy.get('version'), 'built': paddy.get('built'), 'models': pk} if paddy else None,
                'note': note + '"live" is this run\'s checks with the fixed definitions (DEFINITIONS.md and the v2 addendum).'}


def _phase(tw, f):
    from .decisions.state import field_phase
    return field_phase(tw, f)


# ── HTTP ──
ROUTES = []


def route(method, pattern):
    rx = re.compile('^' + pattern + '$')

    def deco(fn):
        ROUTES.append((method, rx, fn))
        return fn
    return deco


MAX_BODY = 1_000_000


def _body(h):
    """The request's JSON body (already read off the socket by the handler, see Handler._dispatch)."""
    raw = getattr(h, 'raw_body', b'')
    if not raw:
        return {}
    try:
        b = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ApiError(400, 'the request body is not JSON') from None
    if not isinstance(b, dict):
        raise ApiError(400, 'the request body must be a JSON object')
    return b


@route('GET', r'/api/health')
def r_health(svc, h, q):
    with svc.lock:
        st = svc.status()
    return {'ok': True, 'version': __version__, 'server_time': time.strftime('%Y-%m-%dT%H:%M:%S'), **st, 'mock': bool(svc.mock)}


@route('GET', r'/api/config')
def r_config(svc, h, q):
    with svc.lock:
        e = svc.engine
        client = getattr(e, 'client', None)
        if client is None and svc.client_factory is not None:
            client = svc.client_factory()          # settings only: nothing is sent
        api = client.describe() if client is not None and hasattr(client, 'describe') else None
        if api is not None:
            api['status'] = svc.api_status() if e.manager.startswith('api') else (
                'mock' if svc.mock else 'not configured' if not client.configured else 'no key' if not client.key_present else 'configured')
    from .decisions.prompt import PROMPT_VERSION
    from .decisions.schema import ACTION_PARAMS
    return {'fields': list(CFG.FIELDS), 'rows': CFG.ROWS, 'cols': CFG.COLS, 'square_m2': CFG.SQUARE_M2, 'seasons': list(CFG.SEASONS),
            'managers': list(MANAGER_NAMES), 'modes': ['auto', 'approve'], 'decision_hours': list(CFG.DECISION_HOURS),
            'limits': {'max_calls_per_day': CFG.MAX_CALLS_PER_DAY, 'max_calls_per_season': CFG.MAX_CALLS_PER_SEASON,
                       'max_calls_per_real_hour': CFG.MAX_CALLS_PER_REAL_HOUR, 'max_decisions_per_call': CFG.MAX_DECISIONS_PER_CALL},
            'actions': {k: v.get('description') for k, v in ACTION_PARAMS.items()}, 'prompt_version': PROMPT_VERSION, 'api': api,
            'mock': bool(svc.mock), 'event_kinds': list(EVENT_KINDS), 'pests': list(PADDY.pests),
            'label': R.PADDY_LABEL, 'fert_caps_kg_ha': R.FERT_CAP_KG_HA, 'n_season_cap': R.N_SEASON_CAP,
            'cost_per_api_season': _cost_per_season(), 'layout': _layout(), 'lat': CFG.LAT, 'camera_hours': list(CFG.CAMERA_HOURS),
            'scan_sweep_s': 120, 'energy_cfg': _energy_cfg()}


def _energy_cfg():
    from .replay import _energy_cfg as f
    return f()


def _layout():
    from .robots.layout import layout_info
    return layout_info()


def _cost_per_season():
    """What one season on the real decision API would cost, before running it (prices from the environment)."""
    from . import runs
    pin_env, pout_env = os.environ.get('API_PRICE_IN_PER_M'), os.environ.get('API_PRICE_OUT_PER_M')
    try:
        pin, pout = float(pin_env or 3.0), float(pout_env or 15.0)
    except ValueError:
        pin, pout = 3.0, 15.0
    est = runs.cost_forecast(1, pin, pout)
    return {'calls': est['calls'], 'tokens_in': est['tokens_in'], 'tokens_out': est['tokens_out'], 'usd': round(est['usd'], 2),
            'price_in_per_m': pin, 'price_out_per_m': pout, 'prices_from_env': bool(pin_env and pout_env)}


@route('GET', r'/api/farm')
def r_farm(svc, h, q):
    with svc.lock:
        return svc.farm()


@route('GET', r'/api/fields/(?P<name>[A-Za-z])')
def r_field(svc, h, q, name):
    with svc.lock:
        return svc.field(name.upper())


@route('GET', r'/api/fields/(?P<name>[A-Za-z])/squares/(?P<r>\d+),(?P<c>\d+)')
def r_square(svc, h, q, name, r, c):
    with svc.lock:
        return svc.square_history(name.upper(), int(r), int(c))


@route('GET', r'/api/arms')
def r_arms(svc, h, q):
    """The arms now: their build, the farm layout, where each is, its jobs, and the keyframes of the last hour of arm work."""
    with svc.lock:
        e = svc.engine
        if e.arms is None:
            return {'arms': None}
        last = getattr(e, 'last_arm_hour', None)
        recent = {}
        if last:
            for k, v in e.arm_tracks.items():
                recent[str(k)] = [kf for kf in v[-4000:] if kf[0] >= last['t0']]
        return {'t': e.tw.t, 'snapshot': e.arms.snapshot(), 'status': e.arm_state(), 'last_hour': last, 'tracks': recent,
                'work_rate': svc.work_rate, 'totals': e.arms.totals()}


@route('GET', r'/api/weather')
def r_weather(svc, h, q):
    with svc.lock:
        return svc.weather()


@route('GET', r'/api/decisions')
def r_decisions(svc, h, q):
    with svc.lock:
        return {'decisions': svc.decisions(q.get('field'), q.get('status'), q.get('action'), q.get('since'), int(q.get('limit') or 500))}


@route('GET', r'/api/decisions/(?P<did>d\d+)')
def r_decision(svc, h, q, did):
    with svc.lock:
        return svc.decision(did)


@route('POST', r'/api/decisions/(?P<did>d\d+)/approve')
def r_approve(svc, h, q, did):
    with svc.lock:
        ok, msg = svc.engine.approve(did)
        if not ok:
            raise ApiError(409, msg)
        return {'ok': True, 'status': msg, 'decision': svc.engine._public(svc.engine.store.by_id[did])}


@route('POST', r'/api/decisions/(?P<did>d\d+)/reject')
def r_reject(svc, h, q, did):
    b = _body(h)
    reason = str(b.get('reason') or '')[:300]
    with svc.lock:
        ok, msg = svc.engine.reject(did, reason)
        if not ok:
            raise ApiError(409, msg)
        return {'ok': True, 'status': msg, 'decision': svc.engine._public(svc.engine.store.by_id[did])}


@route('POST', r'/api/sim/start')
def r_start(svc, h, q):
    with svc.lock:
        if svc.engine.finished:
            raise ApiError(409, 'the season is over; reset to start a new one')
        svc.running = True
    svc.broadcast('status', svc.status())
    return svc.status()


@route('POST', r'/api/sim/pause')
def r_pause(svc, h, q):
    with svc.lock:
        svc.running = False
    svc.broadcast('status', svc.status())
    return svc.status()


@route('POST', r'/api/sim/step')
def r_step(svc, h, q):
    b = _body(h)
    try:
        hours = int(b.get('hours', 1))
    except (TypeError, ValueError):
        raise ApiError(400, 'hours must be a whole number') from None
    if not 1 <= hours <= 24 * 60:
        raise ApiError(400, 'hours must be between 1 and 1440')
    done = 0
    with svc.lock:
        e = svc.engine
        for _ in range(hours):
            svc._fire_future_events()
            if not e.step_hour():
                break
            done += 1
    st = svc.status()
    st['stepped_hours'] = done
    svc.broadcast('status', st)
    return st


@route('POST', r'/api/sim/reset')
def r_reset(svc, h, q):
    b = _body(h)
    kw = {}
    for k in ('seed', 'weatherYear'):
        if k in b and b[k] is not None:
            try:
                kw[k] = int(b[k])
            except (TypeError, ValueError):
                raise ApiError(400, f'{k} must be a whole number') from None
    for k in ('season', 'manager', 'mode'):
        if b.get(k):
            kw[k] = str(b[k])
    return svc.reset(**kw)


@route('POST', r'/api/sim/speed')
def r_speed(svc, h, q):
    b = _body(h)
    try:
        x = float(b.get('x'))
    except (TypeError, ValueError):
        raise ApiError(400, 'x (simulated hours per second) must be a number') from None
    if not 0.1 <= x <= 5000:
        raise ApiError(400, 'x must be between 0.1 and 5000 simulated hours per second')
    svc.speed = x
    if b.get('work') is not None:
        try:
            w = float(b.get('work'))
        except (TypeError, ValueError):
            raise ApiError(400, 'work (simulated seconds per second while the arms work) must be a number') from None
        if not 1 <= w <= 3600:
            raise ApiError(400, 'work must be between 1 and 3600')
        svc.work_rate = w
    return svc.status()


@route('POST', r'/api/events')
def r_events(svc, h, q):
    b = _body(h)
    kind = b.get('kind')
    if kind not in EVENT_KINDS:
        raise ApiError(400, f'kind must be one of {", ".join(EVENT_KINDS)}')
    field = b.get('field')
    if field is not None and field not in FIELD_IDX:
        raise ApiError(400, f'field must be one of {", ".join(CFG.FIELDS)}')
    pest = b.get('pest')
    if pest is not None and pest not in PADDY.pests:
        raise ApiError(400, f'pest must be one of {", ".join(PADDY.pests)}')
    with svc.lock:
        e = svc.engine
        day = b.get('day')
        if day is not None:
            try:
                day = int(day)
            except (TypeError, ValueError):
                raise ApiError(400, 'day must be a whole number') from None
        if day is not None and day > e.tw.day:
            ev = {'kind': kind, 'day': day, 'field': field, 'pest': pest}
            svc.future_events.append(ev)
            return {'ok': True, 'scheduled': ev}
        r = e.inject_event(kind, field, pest)
        if not r.ok:
            raise ApiError(409, r.msg)
        return {'ok': True, 'message': r.msg}


@route('POST', r'/api/observations')
def r_observations(svc, h, q):
    """A person's own observation: a pest seen on a square, a water reading or a note. Pest reports go into the
    farm's observations (like scouting); notes reach the decision maker in the next state as farmer_notes."""
    b = _body(h)
    kind = b.get('kind')
    with svc.lock:
        e = svc.engine
        tw = e.tw
        if kind == 'note':
            text = str(b.get('text') or '').strip()[:300]
            if not text:
                raise ApiError(400, 'text is required')
            note = {'time': tw.iso(), 'field': b.get('field'), 'text': text}
            e.farmer_notes.append(note)
            e.note(f'farmer note: {text}', b.get('field'), 'observation')
            return {'ok': True, 'note': note}
        field = b.get('field')
        if field not in FIELD_IDX:
            raise ApiError(400, f'field must be one of {", ".join(CFG.FIELDS)}')
        f = FIELD_IDX[field]
        if kind == 'pest':
            from .decisions.schema import parse_square
            try:
                r, c = parse_square(b.get('square'), field)
            except ValueError as ex:
                raise ApiError(400, str(ex)) from None
            pest = b.get('pest')
            if pest not in PADDY.pests:
                raise ApiError(400, f'pest must be one of {", ".join(PADDY.pests)}')
            try:
                sev = float(b.get('severity'))
            except (TypeError, ValueError):
                raise ApiError(400, 'severity (0–1) is required') from None
            if not 0 <= sev <= 1:
                raise ApiError(400, 'severity must be between 0 and 1')
            i = tw.sq_index(f, r - 1, c - 1)
            if not (tw.has[i] and tw.alive[i] and not tw.done[i]):
                raise ApiError(409, f'there is no growing crop on {field}{r},{c}')
            from .sim.twin import PEST_IDX
            tw.oPestId[i] = PEST_IDX[pest] if sev > 0 else -1
            tw.oPest[i] = sev
            tw.oFirstT[i] = tw.t if tw.oFirstT[i] < 0 else tw.oFirstT[i]
            tw.oIdVote[i] = 2
            tw.oDetectT[i] = tw.t
            tw.version += 1
            e.note(f'farmer reports {pest.replace("_", " ")} at {sev:.0%} on {field}{r},{c}', field, 'observation')
            return {'ok': True}
        if kind == 'water':
            try:
                depth = float(b.get('depth_mm'))
            except (TypeError, ValueError):
                raise ApiError(400, 'depth_mm is required') from None
            if not -1000 <= depth <= 1000:
                raise ApiError(400, 'depth_mm must be between -1000 and 1000')
            e.tube_override[f] = (depth, tw.t)
            e.note(f'water reading of {depth:.0f} mm for field {field} replaces the tube for 6 hours (tube says {tw.water_tube(f):.0f} mm)', field, 'observation')
            return {'ok': True, 'override_hours': 6}
        if kind == 'soil':
            from .core.util import Obj
            from .decisions.schema import parse_square
            try:
                r, c = parse_square(b.get('square'), field)
            except ValueError as ex:
                raise ApiError(400, str(ex)) from None
            i = tw.sq_index(f, r - 1, c - 1)
            cur = tw.soil_reading(i)
            vals = {}
            for k, lo, hi in (('ph', 3, 10), ('ec', 0, 20), ('n', 0, 2000), ('p', 0, 500), ('k', 0, 2000), ('oc', 0, 10)):
                if b.get(k) is not None:
                    try:
                        v = float(b[k])
                    except (TypeError, ValueError):
                        raise ApiError(400, f'{k} must be a number') from None
                    if not lo <= v <= hi:
                        raise ApiError(400, f'{k} must be between {lo} and {hi}')
                    vals[k] = v
            if not vals:
                raise ApiError(400, 'give at least one of ph, ec, n, p, k, oc')
            rd = Obj({k: cur[k] for k in ('ph', 'n', 'p', 'k', 'oc', 'sand', 'clay', 'ec')})
            rd.update(vals)
            rd.t = tw.t
            tw.probe[i] = rd
            tw.version += 1
            e.note(f'soil reading for {field}{r},{c} from outside: ' + ', '.join(f'{k} {v:g}' for k, v in vals.items()), field, 'observation')
            return {'ok': True}
        raise ApiError(400, "kind must be 'pest', 'water', 'soil' or 'note'")


@route('GET', r'/api/models')
def r_models(svc, h, q):
    with svc.lock:
        return svc.models()


@route('GET', r'/api/audit')
def r_audit(svc, h, q):
    with svc.lock:
        e = svc.engine
        return {'summary': e.audit.summary(), 'samples': {k: v[-300:] for k, v in e.audit.samples.items()},
                'definitions': 'docs/DEFINITIONS.md (copied unchanged from the original project)'}


@route('GET', r'/api/runs')
def r_runs(svc, h, q):
    with svc.lock:
        out = []
        for e in svc.finished_runs + [svc.engine]:
            out.append({'run_id': e.run_id, 'config': e.cfg, 'finished': e.finished, 'time': e.tw.iso(), 'calls': e.n_calls,
                        'decisions': len(e.store.decisions), 'summary': e.summary_cache if e.finished else None, 'current': e is svc.engine})
        return {'runs': out}


def _find_run(svc, rid):
    for e in svc.finished_runs + [svc.engine]:
        if e.run_id == rid:
            return e
    raise ApiError(404, f'no run {rid!r}')


def export_run(e):
    return redact({'run_id': e.run_id, 'config': e.cfg, 'summary': e.summary_cache or e.summary(), 'decisions': [e._public(d) for d in e.store.decisions],
                   'calls': e.store.calls, 'events': e.store.events, 'history': e.history, 'version': __version__})


@route('GET', r'/api/runs/(?P<rid>[A-Za-z0-9_.\-]+)/export')
def r_export(svc, h, q, rid):
    fmt = q.get('format', 'json')
    with svc.lock:
        e = _find_run(svc, rid)
        if fmt == 'csv':
            return ('text/csv; charset=utf-8', decisions_csv([e._public(d) for d in e.store.decisions]).encode('utf-8'), f'{rid}-decisions.csv')
        if fmt != 'json':
            raise ApiError(400, "format must be 'json' or 'csv'")
        return ('application/json', json.dumps(export_run(e), default=str).encode('utf-8'), f'{rid}.json')


@route('POST', r'/api/compare')
def r_compare_start(svc, h, q):
    """Run the current season's case (and the next ones) under several managers, in the background."""
    b = _body(h)
    managers = b.get('managers') or ['builtin', 'conventional']
    if not isinstance(managers, list) or not managers or any(m not in MANAGER_NAMES for m in managers):
        raise ApiError(400, f'managers must be a list from {", ".join(MANAGER_NAMES)}')
    try:
        seasons = int(b.get('seasons', 1))
    except (TypeError, ValueError):
        raise ApiError(400, 'seasons must be a whole number') from None
    if not 1 <= seasons <= 20:
        raise ApiError(400, 'seasons must be between 1 and 20')
    uses_real = any(m.startswith('api') for m in managers) and not svc.mock
    if uses_real:
        c = svc.client_factory() if svc.client_factory else None
        if c is None or not c.configured or not c.key_present:
            raise ApiError(409, 'the decision API is not set up (API_BASE_URL, API_MODEL and API_KEY in the environment or .env); '
                                'leave the API out of the comparison or start the server with --mock')
        cost = _cost_per_season()
        n_api = sum(1 for m in managers if m.startswith('api'))
        if not b.get('confirm_real_api'):
            raise ApiError(409, f'this would call the real decision API about {cost["calls"] * seasons * n_api} times, roughly '
                                f'US${cost["usd"] * seasons * n_api:.2f} at US${cost["price_in_per_m"]:g} / US${cost["price_out_per_m"]:g} per million '
                                'tokens; send confirm_real_api: true to go ahead')
    with svc.lock:
        if svc.compare_job and svc.compare_job.get('status') == 'running':
            raise ApiError(409, 'a comparison is already running')
        cfg = dict(svc.engine.cfg)
        job = {'status': 'running', 'managers': managers, 'seasons': seasons, 'done': 0, 'total': seasons * len(managers), 'started': time.time()}
        svc.compare_job = job
    mock_url = svc.mock.url if svc.mock else None

    def work():
        from . import runs
        try:
            cs = runs.cases(seasons, base_seed=cfg['seed'], first_year=cfg['weatherYear'] or 1, seasons=(cfg['season'],))
            if seasons == 1:
                cs = [{'seed': cfg['seed'], 'weatherYear': cfg['weatherYear'], 'season': cfg['season']}]
            extra = {}
            if any(m.startswith('api') for m in managers):
                if mock_url:
                    extra['client_kw'] = dict(base_url=mock_url, model='mock-builtin', key='not-a-real-key', timeout_s=10, backoff_base_s=0.02,
                                              max_calls_per_real_hour=10 ** 6)
                else:
                    c = svc.client_factory()
                    extra['client_kw'] = dict(base_url=c.base_url, model=c.model, key=os.environ.get('API_KEY'), adapter=c.adapter_name,
                                              timeout_s=c.timeout_s, temperature=c.temperature, json_mode=c.json_mode, retries=c.retries, fallback=c.fallback)

            def prog(i, n, r):
                job['done'] = i
            res = runs.run_cases(cs, managers, workers=1, extra=extra, progress=prog)
            summ = runs.summarise(res)
            base = 'conventional' if 'conventional' in managers else managers[-1]
            paired = {}
            for m in managers:
                if m == base:
                    continue
                paired[m] = {k: runs.paired(res, m, base, k) for k in ('yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'ch4_kg_ha')}
            job.update(status='done', summary=summ, paired=paired, base=base,
                       results=[{k: r[k] for k in ('manager', 'case', 'yield_t_ha', 'irrigation_mm', 'n_kg_ha', 'ai_g_ha', 'ch4_kg_ha', 'calls', 'tokens_in', 'tokens_out')}
                                for r in res], finished=time.time())
        except Exception as ex:  # noqa: BLE001
            job.update(status='error', error=f'{type(ex).__name__}: {ex}')
        svc.broadcast('compare', {'status': job['status']})
    threading.Thread(target=work, name='compare', daemon=True).start()
    return {k: v for k, v in job.items()}


@route('GET', r'/api/compare')
def r_compare(svc, h, q):
    return svc.compare_job or {'status': 'none'}


@route('GET', r'/api/openapi\.json')
def r_openapi(svc, h, q):
    from .openapi import openapi
    return openapi()


class Handler(BaseHTTPRequestHandler):
    server_version = f'PaddyAPIFarm/{__version__}'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        if self.server.verbose:
            super().log_message(fmt, *args)

    def _cors(self):
        origin = self.headers.get('Origin')
        allowed = self.server.cors
        if origin and (allowed == '*' or origin in allowed):
            self.send_header('Access-Control-Allow-Origin', origin if allowed != '*' else '*')
            self.send_header('Vary', 'Origin')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, code, obj):
        body = json.dumps(obj, default=str, allow_nan=False).encode('utf-8') if not isinstance(obj, bytes) else obj
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header('Content-Length', '0')
        self.end_headers()

    def _dispatch(self, method):
        # Read the whole body first, whatever the route: on a kept-alive connection, unread body bytes would be
        # taken for the start of the next request.
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            self.close_connection = True
            return self._json(413 if n > MAX_BODY else 400, {'error': 'request body too large' if n > MAX_BODY else 'bad Content-Length'})
        self.raw_body = self.rfile.read(n) if n else b''
        u = urllib.parse.urlsplit(self.path)
        path = u.path.rstrip('/') or '/'
        q = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
        if path == '/api/stream' and method == 'GET':
            return self._stream()
        if path.startswith('/api/') or path == '/api':
            for m, rx, fn in ROUTES:
                mm = rx.match(path)
                if mm and m == method:
                    try:
                        res = fn(self.server.svc, self, q, **mm.groupdict())
                    except ApiError as ex:
                        return self._json(ex.status, {'error': ex.message})
                    except Exception as ex:  # noqa: BLE001
                        if self.server.verbose:
                            traceback.print_exc()
                        return self._json(500, {'error': f'internal error: {type(ex).__name__}'})
                    if isinstance(res, tuple):
                        ctype, body, fname = res
                        self.send_response(200)
                        self.send_header('Content-Type', ctype)
                        self.send_header('Content-Length', str(len(body)))
                        self.send_header('Content-Disposition', f'attachment; filename="{fname}"')
                        self._cors()
                        self.end_headers()
                        self.wfile.write(body)
                        return None
                    return self._json(200, res)
            allowed = [m for m, rx, fn in ROUTES if rx.match(path)]
            if allowed:
                return self._json(405, {'error': f'use {" or ".join(sorted(set(allowed)))} for {path}'})
            return self._json(404, {'error': f'no such endpoint: {method} {path}'})
        if method != 'GET':
            return self._json(405, {'error': 'only GET for files'})
        return self._static(path)

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def _static(self, path):
        root = self.server.static
        if not root or not os.path.isdir(root):
            body = (b'<!doctype html><meta charset="utf-8"><title>Paddy API Farm</title><p>The website is not built yet. '
                    b'Run <code>node frontend/build.mjs</code>, or use the API at <a href="/api/health">/api/health</a>.</p>')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        rel = urllib.parse.unquote(path).lstrip('/') or 'index.html'
        full = os.path.normpath(os.path.join(root, rel))
        if not full.startswith(os.path.normpath(root)) or not os.path.isfile(full):
            full = os.path.join(root, 'index.html')     # single-page app
        ctype = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        with open(full, 'rb') as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text/') or ctype.endswith('javascript') else ''))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        svc = self.server.svc
        q = svc.subscribe()
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self._cors()
        self.end_headers()
        try:
            with svc.lock:
                first = json.dumps({'type': 'status', 'data': svc.status()})
            self.wfile.write(f'data: {first}\n\n'.encode())
            self.wfile.flush()
            while not self.server.stopping:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(f'data: {msg}\n\n'.encode())
                except queue.Empty:
                    self.wfile.write(b': keep-alive\n\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            svc.unsubscribe(q)
            self.close_connection = True


class FarmServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, svc, static=None, cors='*', verbose=False):
        super().__init__(addr, Handler)
        self.svc = svc
        self.static = static if static is not None else DEFAULT_STATIC
        self.cors = cors
        self.verbose = verbose
        self.stopping = False


def serve(host='127.0.0.1', port=8000, manager='builtin', mode='auto', seed=20261024, season='kharif', weatherYear=0,
          mock=False, mock_faults='', static=None, runs_dir=None, verbose=False, cors='*', speed=24.0, block=True):
    mock_srv = None
    client_factory = None
    if mock:
        from .decisions.api_client import DecisionAPI
        from .decisions.mock_api import MockConfig, MockDecisionServer
        mock_srv = MockDecisionServer(MockConfig.parse(mock_faults, seed=1)).start()

        def client_factory():
            return DecisionAPI(base_url=mock_srv.url, model='mock-builtin', key='not-a-real-key', timeout_s=10, backoff_base_s=0.05,
                               max_calls_per_real_hour=10 ** 6)
    else:
        def client_factory():
            from .decisions.api_client import DecisionAPI
            return DecisionAPI.from_env()
    svc = FarmService(manager=manager, mode=mode, seed=seed, weatherYear=weatherYear, season=season, client_factory=client_factory,
                      runs_dir=runs_dir, speed=speed, mock=mock_srv)
    httpd = FarmServer((host, port), svc, static=static, cors=cors, verbose=verbose)
    if not block:
        th = threading.Thread(target=httpd.serve_forever, name='farm-http', daemon=True)
        th.start()
        return httpd
    print(f'Paddy API Farm {__version__} on http://{host}:{httpd.server_address[1]}  (manager {manager}, mode {mode}{", mock stand-in API" if mock else ""})')
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.stopping = True
        httpd.server_close()
        if mock_srv:
            mock_srv.stop()
    return httpd
