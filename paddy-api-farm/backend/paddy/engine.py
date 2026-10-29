"""The decision loop: the twin runs hour by hour; at decision points the state goes to the decision maker, its
reply is validated and checked by the guardrails, and accepted decisions are carried out (now, at the time the
decision asked for, or after a person approves them).

Decision points: 06:00 and 18:00 every simulated day, plus triggers — a new pest sighting, a field's water
tube reaching its AWD threshold, a heavy-rain forecast, the end of a wait the decision maker asked for, and an
event injected by the user. One call covers both fields. Calls are limited per simulated day, per season and per
real hour (the last one only matters for the real API).

Accepted decisions are carried out by the four arms (``paddy.robots``): each becomes a job for one arm, jobs for a busy
arm wait their turn, and the arms run in lock-step with the hourly twin. Each square's work is applied to the twin at
the minute the arm finishes it, before that hour's physics; pumping and draining start when the watering arm has set
the gate. ``arms=False`` restores v1's instant actions (for comparisons).
"""
from __future__ import annotations

import collections
import json
import math
import os
import time
import uuid

import numpy as np

from . import config as CFG
from .decisions import guardrails as G
from .decisions import rules as R
from .decisions import schema as S
from .decisions.actuators import SimActuators
from .decisions.managers import BuiltinManager, ConventionalManager
from .decisions.records import RunStore
from .decisions.state import build_state, field_phase
from .sim.advisor import Advisor
from .sim.audit import Audit
from .core import data as _D
from .core.util import Obj as Obj_
from .sim.twin import DAY, HOUR, PADDY, PEST_IDX, PEST_KEYS, POTENTIAL_KG_M2, Twin
from .core import agro as _A
from .core import climate as _C
from .robots import energy as EN
from .robots import layout as LAY
from .robots.system import ACTION_ARM, CODES as ARM_CODES, ArmSystem, ROLE_NAME

D_PESTICIDES = _D.PESTICIDES
D_PESTS = _D.PESTS
D_EFF = _D.pesticide_efficacy

MANAGER_NAMES = ('builtin', 'conventional', 'api', 'api-no-advice')
FIELD_IDX = {name: k for k, name in enumerate(CFG.FIELDS)}
STORM_MM = 64.5            # IMD "heavy rain": 64.5 mm or more in a day
GWP_CH4 = 27.0             # IPCC AR6 100-year global warming potential of non-fossil methane
EVENT_KINDS = ('pest', 'storm', 'dry', 'cloudy')
STRAW_PER_GRAIN = 1.0      # rice straw left per kg of grain (harvest index about 0.5); it feeds the biogas plant
SCAN_SWEEP_S = 120         # one camera pass: the scan bar crosses the 20 m frame in two minutes (the 3D view's sweep)


_SUITES = {}


def default_models():
    """What a farm runs when nothing is said: the shipped paddy pack when there is one (v2), else the v1 forests."""
    from . import modelround as MR
    return 'paddy' if os.path.exists(MR.PACK_NPZ) and os.path.exists(MR.PACK_META) else 'v1'


_PACK_SHA = {}


def pack_sha256(models):
    """The first 16 hex digits of a pack's SHA-256 ('paddy' = the shipped pack)."""
    from . import modelround as MR
    import hashlib
    path = MR.PACK_NPZ if models == 'paddy' else (models if str(models).endswith('.npz') else str(models) + '.npz')
    if path not in _PACK_SHA:
        try:
            with open(path, 'rb') as fh:
                _PACK_SHA[path] = hashlib.sha256(fh.read()).hexdigest()[:16]
        except OSError:
            _PACK_SHA[path] = None
    return _PACK_SHA[path]


def model_suite(models=None):
    """The forests a farm runs: the v1 forests ('v1'), plus the paddy forests of the v2 model round ('paddy' = the
    shipped pack in paddy/core/models/, or the path of another pack's .npz) loaded on top of them. None = the default
    (``default_models``: the shipped pack when there is one)."""
    from .core import models as M
    if models is None:
        models = default_models()
    if not models or models == 'v1':
        return M.ModelSuite.shipped().view()
    from . import modelround as MR
    key = str(models)
    if key not in _SUITES:
        if models == 'paddy':
            forests, meta = MR.load_pack()
        else:
            npz = models if str(models).endswith('.npz') else str(models) + '.npz'
            forests, meta = MR.load_pack(npz, npz[:-4] + '.json')
        if not forests:
            raise FileNotFoundError(f'no paddy forests at {models!r}')
        _SUITES[key] = (forests, meta)
    forests, meta = _SUITES[key]
    v = M.ModelSuite.shipped().view()
    v.m = dict(v.m)                 # the view shares the shipped dict: copy before adding
    v.m.update(forests)
    v.paddy = meta
    return v


def energy_config():
    """The playground's power plant sized for this farm's frame (13 × 6 m of fields, energy.size_for), with the
    biogas generator on."""
    cfg = EN.size_for(LAY.GRID_W * LAY.GRID_H * CFG.SQUARE_M2)
    cfg.update(biogas=True, sensingOn=True, trapsW=0)
    return cfg


class CallResult:
    """What a decision maker returned for one call."""

    def __init__(self, ok, response=None, raw=None, errors=None, source='api', model='', latency_ms=0.0, tokens_in=0, tokens_out=0,
                 attempts=1, repaired=False, http_status=None, error=None, request=None):
        self.ok = ok
        self.response = response
        self.raw = raw
        self.errors = errors or []
        self.source = source
        self.model = model
        self.latency_ms = latency_ms
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.attempts = attempts
        self.repaired = repaired
        self.http_status = http_status
        self.error = error
        self.request = request or {}


class Engine:
    def __init__(self, seed=20261024, weatherYear=0, season='kharif', manager='builtin', mode='auto', client=None, approver=None,
                 keep_states=True, forecastMode=None, run_id=None, record_dir=None, fallback=None, listener=None,
                 max_calls_per_day=None, max_calls_per_season=None, actuators=None, label=None, builtin_water='awd', record_frames=False,
                 arms=True, record_tracks=None, events=None, preview_arms=None, rows=False, models=None):
        if manager not in MANAGER_NAMES:
            raise ValueError(f'unknown manager {manager!r} (one of {", ".join(MANAGER_NAMES)})')
        if mode not in ('auto', 'approve'):
            raise ValueError("mode must be 'auto' or 'approve'")
        self.cfg = {'seed': int(seed), 'weatherYear': int(weatherYear or 0), 'season': season, 'manager': manager, 'mode': mode,
                    'forecastMode': forecastMode or 'default'}
        if builtin_water != 'awd':
            self.cfg['builtin_water'] = builtin_water
        if not arms:
            self.cfg['arms'] = False
        sched = []
        for e in events or []:
            if e.get('kind') in EVENT_KINDS:
                sched.append({'day': int(e['day']), 'hour': int(e.get('hour', 8)), 'kind': e['kind'], 'field': e.get('field'),
                              'pest': e.get('pest'), 'done': False})
        sched.sort(key=lambda e: (e['day'], e['hour']))
        if sched:
            self.cfg['events'] = [{k: v for k, v in e.items() if k != 'done' and v is not None} for e in sched]
        self.schedule = sched
        twcfg = {'seed': seed, 'weatherYear': weatherYear, 'season': season}
        if forecastMode == 'realistic':
            twcfg['forecastMode'] = 'realistic'
        self.tw = Twin(twcfg)
        conventional = manager == 'conventional'
        # which forests this farm runs, resolved and recorded (exact replay re-runs with the same ones)
        self.models = default_models() if models is None else models
        self.cfg['models'] = self.models if isinstance(self.models, str) else str(self.models)
        if self.cfg['models'] != 'v1':
            self.cfg['pack_sha256'] = pack_sha256(self.cfg['models'])
        self.adv = Advisor(self.tw, suite=model_suite(self.models),
                           policy={'soilCheck': not conventional, 'smartWater': not conventional, 'spotSpray': not conventional})
        self.audit = Audit(self.tw, self.adv, rows=rows)
        self.adv.keep_x = bool(rows)
        self.act = actuators or SimActuators(self.tw)
        self.manager = manager
        self.mode = mode
        self.advice = manager in ('builtin', 'api')          # conventional and api-no-advice decide without the forests
        self.client = client
        self.approver = approver
        self.fallback = fallback or (getattr(client, 'fallback', None) if client is not None else None) or 'hold'
        self.listener = listener
        self.builtin = BuiltinManager(water=builtin_water)
        self.conventional = ConventionalManager()
        self.run_id = run_id or f'{manager}-{season}-{seed}-{weatherYear}-{uuid.uuid4().hex[:6]}'
        self.label = label or self.run_id
        self.store = RunStore(self.run_id, record_dir, keep_states)
        if manager.startswith('api'):     # API_MAX_CALLS_PER_DAY / API_MAX_CALLS_PER_SEASON from the environment
            env = CFG.api_settings()
            max_calls_per_day = max_calls_per_day or env['max_calls_per_day']
            max_calls_per_season = max_calls_per_season or env['max_calls_per_season']
        self.max_calls_per_day = int(max_calls_per_day or CFG.MAX_CALLS_PER_DAY)
        self.max_calls_per_season = int(max_calls_per_season or CFG.MAX_CALLS_PER_SEASON)
        self.calls_by_day = collections.Counter()
        self.n_calls = 0
        self.n_skipped_calls = 0
        self.last_call_t = -1e18
        self.known_sightings = set()
        self.awd_fired = [False, False]
        self.storm_days = set()
        self.wake_at = None
        self.event_pending = []
        self.queue = []                       # decisions waiting for their time
        self.pending = []                     # decisions waiting for a person (approve mode)
        self.outcome_checks = []              # (due t, fn)
        self.outcomes = collections.deque(maxlen=20)
        self.recent = collections.deque(maxlen=12)
        self.spray_log = []
        self.splits = [[], []]
        self.yield_at_transplant = {}
        self.dec_seq = 0
        self.notes = collections.deque(maxlen=20)
        self.finished = False
        self.summary_cache = None
        self.wall_ms = 0.0
        self.history = []                     # daily snapshot for charts
        self._last_hist_day = -1
        self.forced = []
        self._resume_t = None
        self.close_look_t = {}                # square → when it was last looked at close up (scouting or spraying)
        self.record_frames = record_frames
        self.tube_override = {}                # field → (reading mm, when) from a person's observation
        self.frames = []                      # square-by-square pictures at every 06:00 and 18:00 (for the replay website)
        self.farmer_notes = []
        # power: the playground's plant (solar array, battery, biogas generator, grid for any shortfall)
        self.energy = EN.EnergySystem(energy_config())
        self._pump_l = [0.0, 0.0]
        # the arms (v2): every accepted decision that needs a machine becomes a job for one arm
        self.use_arms = bool(arms)
        self.record_tracks = record_frames if record_tracks is None else bool(record_tracks)
        self.preview_arms = (mode == 'approve') if preview_arms is None else bool(preview_arms)
        self.arms = ArmSystem(world=self, energy=self.energy, record=self.record_tracks, t0=self.tw.t) if self.use_arms else None
        self.arm_tracks = {k: [] for k in (1, 2, 3, 4)}
        self.arm_jobs = []                    # every finished job's summary (busy time, travel, energy, clearance)
        self._forced_jobs = set()
        self._job_rec = {}                    # arm job id → its decision record
        self.last_arm_hour = None
        self.tube_log = {}                    # (field, hour) → the tube reading the farm logged then (time-to-AWD slopes)
        self.water_flags = {}                 # (field, hour) → (gauge rain mm, irrigated, drained) in the hour from then
        self.level_log, self.level_log_h0 = [[], []], None   # true water level per field per hour (3D view only)
        self.power_log = {'from_hour': None, 'pv': [], 'soc': [], 'load': [], 'gen': [], 'biogas': []}   # the power plant per hour (3D view)

    # ── bookkeeping used by the state ──
    def recent_summaries(self):
        return list(self.recent)

    def scheduled_water(self, f):
        """v2: the earliest irrigation or drainage decided for later in field f (shown in the state, so that neither
        the built-in manager nor the API decides it again), or None."""
        name = CFG.FIELDS[f]
        for r in self.queue:
            if r['status'] == 'scheduled' and r.get('field') == name and r['action'] in ('irrigate', 'drain'):
                p = r.get('params') or {}
                return {'action': r['action'], 'at': self.tw.iso(r['exec_t']),
                        'target_mm': p.get('target_depth_mm', p.get('to_depth_mm')), 'decision': r['id']}
        return None

    def tube_reading(self, f):
        """The water level the farm knows: a person's reading from the last 6 hours if there is one, else the tube."""
        o = self.tube_override.get(f)
        if o is not None and self.tw.t - o[1] < 6 * HOUR:
            return o[0], 'farmer reading'
        return self.tw.water_tube(f), 'tube'

    def limits_info(self):
        d = self.tw.day
        return {'calls_left_today': max(0, self.max_calls_per_day - self.calls_by_day[d]),
                'calls_left_season': max(0, self.max_calls_per_season - self.n_calls),
                'max_decisions_per_call': CFG.MAX_DECISIONS_PER_CALL, 'decision_hours': list(CFG.DECISION_HOURS)}

    def track_record(self):
        s = self.audit.summary()
        out = {}
        for k in ('soil', 'establish', 'pest', 'spray', 'harvest', 'yield'):
            x = s.get(k)
            if x and x['n']:
                out[k] = f"{x['acc']:.0%} of {x['n']} checks this season"
        return out

    def emit(self, kind, data):
        if self.listener:
            try:
                self.listener(kind, data)
            except Exception:  # noqa: BLE001
                pass

    def note(self, text, field=None, kind='info'):
        ev = {'time': self.tw.iso(), 't': self.tw.t, 'kind': kind, 'field': field, 'text': text}
        self.store.add_event(ev)
        self.emit('event', ev)

    # ── triggers ──
    def _awd_threshold(self, f):
        tw = self.tw
        fd = tw.fields[f]
        if fd.transplantT is None or fd.harvestT is not None or fd.abandoned:
            return None
        prog = tw.field_progress(f)
        if prog >= R.DRAIN_DOWN_PROGRESS:
            return None
        return 0.0 if 0.5 <= prog < 0.78 else float(CFG.AWD_THRESHOLD_MM)

    def _sightings(self):
        tw = self.tw
        cells = np.nonzero(tw.valid & (tw.oPestId >= 0) & (tw.oPest > 0) & tw.has & ~tw.done)[0]
        return {(int(i), int(tw.oPestId[i])) for i in cells}

    def _triggers(self):
        tw = self.tw
        trig = []
        h = int(round(tw.hour)) % 24
        if h in CFG.DECISION_HOURS and abs(tw.t % HOUR) < 1e-6:
            trig.append('scheduled')
        if any(fd.transplantT is not None for fd in tw.fields):
            new = self._sightings() - self.known_sightings
            if new:
                trig.append('pest_sighting')
        for f in range(2):
            thr = self._awd_threshold(f)
            if thr is None:
                self.awd_fired[f] = False
                continue
            tube = tw.water_tube(f)
            if tube <= thr and not self.awd_fired[f]:
                trig.append(f'awd_threshold_{CFG.FIELDS[f]}')
                self.awd_fired[f] = True
            elif tube > thr + 30:
                self.awd_fired[f] = False
        W = tw.weather
        for k in range(2):
            fc = W.forecast_day(tw.day, k)
            if fc.rainMm >= STORM_MM and fc.rainProb >= 0.5 and (tw.day + k) not in self.storm_days:
                self.storm_days.add(tw.day + k)
                trig.append('storm_forecast')
                break
        if self.wake_at is not None and tw.t >= self.wake_at - 1:
            trig.append('wait_end')
            self.wake_at = None
        if self.event_pending:
            trig.append('event')
        return trig

    # ── one call to the decision maker ──
    def _decide(self, state):
        if self.manager == 'builtin':
            resp = self.builtin.decide(state)
            return CallResult(True, resp, json.dumps(resp), source='builtin', model='builtin')
        if self.manager == 'conventional':
            resp = self.conventional.decide(state)
            return CallResult(True, resp, json.dumps(resp), source='conventional', model='conventional')
        if self.client is None:
            return CallResult(False, error='no decision API client configured', source='api')
        return self.client.decide(state)

    def call(self, triggers):
        tw = self.tw
        d = tw.day
        if self.calls_by_day[d] >= self.max_calls_per_day or self.n_calls >= self.max_calls_per_season:
            self.n_skipped_calls += 1
            if 'scheduled' in triggers:
                self.note(f'call budget reached ({self.max_calls_per_day}/day, {self.max_calls_per_season}/season): no call at {tw.iso()}', kind='budget')
            return None
        self.calls_by_day[d] += 1
        self.n_calls += 1
        self.last_call_t = tw.t
        self.known_sightings = self._sightings()
        self.event_pending = []
        t0 = time.perf_counter()
        state = build_state(self, triggers, advice=self.advice)
        res = self._decide(state)
        call_id = f'c{self.n_calls:05d}'
        fallback_used = None
        decisions, errors = [], list(res.errors)
        if res.ok and res.response is not None:
            decisions, errs = S.validate(res.response)
            errors += errs
        if not res.ok:
            fallback_used = self.fallback
            if self.fallback == 'builtin':
                resp = self.builtin.decide(state)
                decisions, errs = S.validate(resp)
                errors += errs
            self.note(f'decision API failed ({res.error}); fallback: {self.fallback}', kind='fallback')
        source = res.source if res.ok else f'fallback-{self.fallback}'
        verdicts = G.check_call(self, decisions)
        rec_call = {'id': call_id, 'run_id': self.run_id, 'time': tw.iso(), 't': tw.t, 'triggers': triggers, 'source': source, 'model': res.model,
                    'ok': res.ok, 'error': res.error, 'http_status': res.http_status, 'attempts': res.attempts, 'repaired': res.repaired,
                    'latency_ms': round(res.latency_ms, 1), 'tokens_in': res.tokens_in, 'tokens_out': res.tokens_out,
                    'schema_errors': errors, 'n_decisions': len(decisions), 'fallback': fallback_used,
                    'prompt_version': getattr(self.client, 'prompt_version', None) if self.manager.startswith('api') else None,
                    'request': res.request, 'state': state, 'raw_response': res.raw,
                    'notes': (res.response or {}).get('notes') if isinstance(res.response, dict) else None}
        self.store.add_call(rec_call)
        made = []
        for dd, v in zip(decisions, verdicts):
            made.append(self._new_decision(dd, v, call_id, source))
        for e in errors[:6]:
            self.recent.append({'time': tw.iso(), 'call': call_id, 'status': 'invalid', 'note': e[:200]})
        self.wall_ms += (time.perf_counter() - t0) * 1000
        self.emit('call', {k: rec_call[k] for k in ('id', 'time', 'triggers', 'source', 'ok', 'latency_ms', 'tokens_in', 'tokens_out', 'n_decisions', 'fallback')})
        return made

    def _new_decision(self, d, v, call_id, source):
        tw = self.tw
        self.dec_seq += 1
        did = f'd{self.dec_seq:05d}'
        sq = [f'{d["field"]}{r},{c}' for r, c in d['squares']] if d.get('squares') else None
        rec = {'id': did, 'call_id': call_id, 'run_id': self.run_id, 'created': tw.iso(), 't': tw.t, 'source': source,
               'field': d.get('field'), 'action': d['action'], 'params': d.get('params') or {}, 'squares': sq, 'when': d.get('when', 'now'),
               'exec_t': v.exec_t, 'exec_at': tw.iso(v.exec_t), 'reason': d.get('reason'), 'confidence': d.get('confidence'),
               'guardrail': v.as_dict(), 'status': 'proposed', 'execution': None, 'outcome': None, '_d': d}
        if d['action'] == 'spray' and v.ok:
            rec['_planned_kill'] = rec['planned_kill'] = self._planned_kill(d, v.exec_t)
            f = FIELD_IDX.get(d.get('field'))
            sq = [tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
            times = self._spray_times(rec, d, f, v.exec_t)
            preds, xs = self.adv.planned_spray(f, d['params']['product'], sq, v.exec_t, d['params'].get('target_pest'), rec['_planned_kill'],
                                               times=times)
            rec['_plan_sq'] = {int(i): (p, x) for i, p, x in zip(sq, preds, xs)}
            rec['_plan_meta'] = list(getattr(self.adv, 'last_plan_meta', []) or [])
            if preds and preds[0] is not None:
                rec['planned_kill'] = round(float(np.mean(preds)), 4)
        self.store.add_decision(rec)
        if not v.ok:
            rec['status'] = 'rejected'
            self._feedback(rec, 'rejected: ' + '; '.join(v.messages))
            self.store.flush_decision(self._public(rec))
        elif self.mode == 'approve' and d['action'] not in ('hold', 'wait'):
            rec['status'] = 'pending_approval'
            if self.use_arms and self.preview_arms and d['action'] in ACTION_ARM:
                rec['arm_preview'] = self.arm_preview(rec)
            self.pending.append(rec)
            if self.approver is not None:
                verdict, why = self.approver(self._public(rec), self)
                if verdict == 'approve':
                    self.approve(did, by='script')
                else:
                    self.reject(did, why or 'rejected by script', by='script')
        else:
            self._accept(rec)
        self.emit('decision', self._public(rec))
        return rec

    def _spray_times(self, rec, d, f, exec_t):
        """v2: the minute the pest arm's own plan reaches each square of a spray (a dry run of the arm system, as for the
        approve-mode preview: nothing on the farm changes); a spray decided for later is shifted to start then.
        {} without the arms."""
        if not self.use_arms or self.arms is None:
            return {}
        job = self._job_for(rec, d, f, 'spray', {}, preview=True)
        if job is None:
            return {}
        pv = self.arms.preview(job)
        tasks = (pv.summary or {}).get('tasks') or []
        shift = max(0.0, exec_t - self.tw.t)
        return {int(tk[0]): float(tk[2]) + shift for tk in tasks if isinstance(tk[0], (int, np.integer)) and tk[0] >= 0 and tk[2] is not None}

    def _planned_kill(self, d, exec_t):
        """The spray forest's prediction, when the spray is decided, for the hour it will be applied."""
        tw = self.tw
        f = FIELD_IDX.get(d.get('field'))
        pid = d['params'].get('target_pest')
        if not pid:
            seen = [PEST_KEYS[int(tw.oPestId[tw.sq_index(f, r - 1, c - 1)])] for r, c in d['squares'] if tw.oPestId[tw.sq_index(f, r - 1, c - 1)] >= 0]
            pid = max(set(seen), key=seen.count) if seen else None
        if not pid:
            return None
        for o in self.adv.spray_options(f, pid, max_products=8):
            if o.product == d['params']['product']:
                for t, e, _w, _r in o.slots:
                    if abs(t - exec_t) < 1:
                        return round(e, 4)
        return None

    def _accept(self, rec):
        if rec['action'] == 'wait':
            hrs = float(rec['params'].get('hours', 12))
            self.wake_at = self.tw.t + hrs * HOUR
            rec['status'] = 'executed'
            rec['execution'] = {'at': self.tw.iso(), 'ok': True, 'msg': f'next call requested at {self.tw.iso(self.wake_at)}'}
            self._feedback(rec, rec['execution']['msg'])
            self.store.flush_decision(self._public(rec))
            return
        if rec['exec_t'] <= self.tw.t + 1:
            self._execute(rec)
        else:
            rec['status'] = 'scheduled'
            self.queue.append(rec)
            self.queue.sort(key=lambda r: (r['exec_t'], r['id']))

    def approve(self, did, by='user'):
        rec = self.store.by_id.get(did)
        if rec is None or rec['status'] != 'pending_approval':
            return False, 'not awaiting approval'
        self.pending = [r for r in self.pending if r['id'] != did]
        rec['approved_by'] = by
        rec['exec_t'] = max(rec['exec_t'], self.tw.t)
        rec['exec_at'] = self.tw.iso(rec['exec_t'])
        self._accept(rec)
        self.emit('decision', self._public(rec))
        return True, rec['status']

    def reject(self, did, reason='', by='user'):
        rec = self.store.by_id.get(did)
        if rec is None or rec['status'] != 'pending_approval':
            return False, 'not awaiting approval'
        self.pending = [r for r in self.pending if r['id'] != did]
        rec['status'] = 'rejected_by_person'
        rec['rejected_by'] = by
        rec['rejection_reason'] = (reason or '')[:300]
        self._feedback(rec, f'rejected by the farmer: {reason}' if reason else 'rejected by the farmer')
        self.store.flush_decision(self._public(rec))
        self.emit('decision', self._public(rec))
        return True, rec['status']

    def _feedback(self, rec, msg):
        self.recent.append({'time': self.tw.iso(), 'field': rec['field'], 'action': rec['action'], 'status': rec['status'], 'note': msg[:200]})

    @staticmethod
    def _public(rec):
        return {k: v for k, v in rec.items() if not k.startswith('_')}

    # ── carrying out a decision ──
    def _execute(self, rec):
        tw = self.tw
        d = rec['_d']
        v = G.check(self, d, stage='execute', exec_t=tw.t)
        if not v.ok:
            rec['status'] = 'skipped'
            rec['execution'] = {'at': tw.iso(), 'ok': False, 'msg': '; '.join(v.messages)}
            self._feedback(rec, 'not carried out: ' + '; '.join(v.messages))
            self.store.flush_decision(self._public(rec))
            self.emit('decision', self._public(rec))
            return
        if v.messages and rec['guardrail']['verdict'] == 'accepted':
            rec['guardrail']['verdict'] = 'warning'
        for m in v.messages:
            if m not in rec['guardrail']['messages']:
                rec['guardrail']['messages'].append(m)
        f = FIELD_IDX.get(d.get('field'))
        act = d['action']
        pre = {}
        if act == 'transplant':
            sq = None if d.get('squares') is None else [tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
            self.audit.on_establish_check(f, sq)
            if tw.fields[f].transplantT is None:
                yfo = self.adv.yield_forecast(f)
                yf = yfo.today
                x = extra = None
                if self.audit.rows is not None:
                    from .core import models as _M
                    x = _M._feat_yield(yfo.ctx)
                    cs = tw.cells[f]
                    rd = [tw.soil_reading(i) for i in cs]
                    extra = [1.0 if tw.season == 'rabi' else 0.0, tw.date_info().doy, tw.seedling_age(), f, tw.water_tube(f),
                             float(np.mean([r.ph for r in rd])), float(np.mean([r.ec for r in rd])), float(np.mean([r.n for r in rd])),
                             float(np.mean([r.oc for r in rd])), len(sq) if sq is not None else len(cs)]
                self.audit.on_transplant(f, yf, x, extra)
                self.yield_at_transplant[f] = yf
            pre['squares'] = sq if sq is not None else [int(i) for i in tw.cells[f] if not tw.has[i] or tw.failed[i]]
        if act == 'spray':
            sq = [tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
            pre['pests'] = {i: int(tw.oPestId[i]) for i in sq}
            pre['true_pests'] = {i: int(tw.pestId[i]) for i in sq}
            pid = d['params'].get('target_pest')
            if not pid:
                seen = [PEST_KEYS[p] for p in pre['pests'].values() if p >= 0]
                pid = max(set(seen), key=seen.count) if seen else None
            pre['pid'] = pid
            pre['pred'] = None
            if pid:
                for o in self.adv.spray_options(f, pid, max_products=8, hours=0):
                    if o.product == d['params']['product']:
                        pre['pred'] = o.nowEfficacy
            pre['max_sev'] = max([float(tw.oPest[i]) for i in sq if tw.oPestId[i] >= 0] + [0.0])
            pre['planned'] = rec.get('_planned_kill')
        if self.use_arms and act in ACTION_ARM:
            job = self._job_for(rec, d, f, act, pre)
            if job is None:
                rec['status'] = 'failed'
                rec['execution'] = {'at': tw.iso(), 'ok': False, 'msg': 'nothing to do on those squares'}
                self._feedback(rec, rec['execution']['msg'])
                self.store.flush_decision(self._public(rec))
                self.emit('decision', self._public(rec))
                return
            rec['_pre'] = pre
            rec['_warn'] = list(v.messages)
            rec['arm_job'] = job.id
            rec['status'] = 'queued'
            self._job_rec[job.id] = rec
            self.arms.submit(job, tw.t)
            self.store.flush_decision(self._public(rec))
            self.emit('decision', self._public(rec))
            return
        res = self.act.execute(d)
        rec['status'] = 'executed' if res.get('ok') else 'failed'
        rec['execution'] = {'at': tw.iso(), 'ok': bool(res.get('ok')), 'msg': res.get('msg', '')}
        if v.messages:
            rec['execution']['warnings'] = list(v.messages)
        self._after(rec, d, f, act, res, pre)
        self._feedback(rec, res.get('msg', ''))
        self.store.flush_decision(self._public(rec))
        self.emit('decision', self._public(rec))

    def _audit_spray(self, d, res, pid, planned):
        """Spray-efficacy check on every sprayed square: the forest's prediction from what the farm knows at that
        moment (measured wind, temperature, humidity and sun, the hourly rain forecast, the pest as the close-up look
        identified it and its tracked age) against the share actually killed."""
        tw = self.tw
        looks = res.get('looks') or []
        effs = dict(res.get('effs') or [])
        if not looks:
            return
        prod = d['params']['product']
        pc = D_PESTICIDES[prod]
        ctxs, actual = [], []
        rain_cache = {}
        for lk in looks:
            i, seen, age = lk[0], lk[1], lk[2]
            # v2: each square has the time the arm sprayed it and the station's readings then; v1: one time for all
            cond = lk[3] if len(lk) > 3 else (res.get('conditions') or {})
            t_ = lk[4] if len(lk) > 4 else tw.t
            rain_h = rain_cache.get(t_)
            if rain_h is None:
                rain_h = rain_cache[t_] = [tw.weather.forecast_hour(t_, t_ + q * HOUR).rain for q in range(6)]
            p = seen or pid
            ctxs.append(Obj_(hour=cond.get('hour', tw.hour), wind=cond['wind'], temp=cond['temp'], rh=cond['rh'], rain6=sum(rain_h), rainH=rain_h,
                             uv=pc.uv, baseEff=D_EFF(prod, p) if p else 0.5, cat=D_PESTS[p].cat if p else 'larva', ageDays=float(age),
                             sunSin=cond['sunSin']))
            actual.append(effs[int(i)])
        preds = self.adv.m.spray_eff_batch(ctxs)
        # what decided each square's kill, kept with the rows for the planned-spray ceiling: the true pest (index, −1 for
        # none), its true age and base efficacy, and the conditions at the minute it was sprayed (wind, temperature,
        # humidity, the rain of the next six hours, the sun); then the pest the look saw (index) and its tracked age
        truth_x = []
        for lk, c in zip(looks, ctxs):
            tr = lk[5] if len(lk) > 5 else (-1, 0.0, 0.5)
            cond = lk[3] if len(lk) > 3 else {}
            seen = lk[1]
            truth_x.append([float(tr[0]), float(tr[1]), float(tr[2]), float(cond.get('wind', 0)), float(cond.get('temp', 0)), float(cond.get('rh', 0)),
                            float(cond.get('rain6', 0)), float(cond.get('sunSin', 0)), float(PEST_IDX.get(seen, -1)) if seen else -1.0, float(lk[2])])
        plan_sq = res.get('plan_sq')
        if plan_sq:
            planned = [plan_sq.get(int(lk[0]), (None, None))[0] for lk in looks]
            pm = list(res.get('plan_meta') or [])
            plan_rows = [(plan_sq[int(lk[0])][1], [int(lk[0])] + tx + pm) if int(lk[0]) in plan_sq else None for lk, tx in zip(looks, truth_x)]
        else:
            plan_rows = None
        xs = None
        if self.audit.rows is not None:
            from .core import models as _M
            xs = [_M._feat_spray_full(c) for c in ctxs]
        self.audit.on_spray(list(zip(preds, actual)), planned, xs=xs, plan_rows=plan_rows, extra=truth_x)

    def _outcome(self, rec, text, data=None):
        rec['outcome'] = dict(data or {}, text=text, time=self.tw.iso())
        self.outcomes.append({'time': self.tw.iso(), 'field': rec['field'], 'decision': rec['id'], 'action': rec['action'], 'text': text})
        self.store.flush_decision(self._public(rec))
        self.emit('decision', self._public(rec))

    def _after(self, rec, d, f, act, res, pre):
        tw = self.tw
        if not res.get('ok'):
            return
        if act == 'soil_check':
            sq = [int(i) for i in tw.cells[f]] if d.get('squares') is None else [tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
            self.audit.on_soil_check(f, sq)
            codes = {s.i: s.code for s in self.adv.soil(f)}
            n = collections.Counter(codes[i] for i in sq)
            self._outcome(rec, f'probed {len(sq)} squares: {n.get("S", 0)} suitable, {n.get("A", 0)} need amendment, {n.get("U", 0)} unsuitable (soil forest)')
        elif act == 'transplant':
            sq = pre['squares']
            due = tw.t + 6 * DAY

            def check(rec=rec, sq=sq, f=f):
                alive = sum(1 for i in sq if tw.alive[i])
                self._outcome(rec, f'{alive} of {len(sq)} transplanted squares established after 6 days', {'established': alive, 'planted': len(sq)})
            self.outcome_checks.append((due, check))
        elif act == 'fertilise':
            p = d['params']
            n = R_FERT_N.get(p['product'], 0) * float(p['kg_per_ha'])
            if n > 0:
                share = 1.0 if d.get('squares') is None else len(d['squares']) / len(tw.cells[f])
                self.splits[f].append({'split': p.get('split', 'other'), 'n': n * share, 'date': tw.iso()[:10]})
            elif p.get('split') == 'basal':
                self.splits[f].append({'split': 'basal', 'n': 0.0, 'date': tw.iso()[:10]})
            lost = res.get('lostNKgHa')
            self._outcome(rec, f'applied; about {lost:.1f} kg N/ha lost to the air' if lost else 'applied', {'lost_n_kg_ha': lost})
        elif act == 'spray':
            kills = res.get('kills') or []
            pid = pre.get('pid')
            res['plan_sq'] = rec.get('_plan_sq')
            res['plan_meta'] = rec.get('_plan_meta')
            self._audit_spray(d, res, d['params'].get('target_pest'), pre.get('planned'))
            for i in pre['pests']:
                self.close_look_t[int(i)] = max(self.close_look_t.get(int(i), -1e18), tw.t)
            self.spray_log.append({'time': tw.iso(), 'field': d['field'], 'product': d['params']['product'], 'pest': pid,
                                   'squares': len(d['squares']), 'max_sev': round(pre.get('max_sev', 0.0), 3)})
            mean_k = float(np.mean([e for _, e in kills])) if kills else None
            txt = f'{len(kills)} infested squares hit, mean kill {mean_k:.0%}' if kills else 'no infested squares among the targets'
            if pre.get('pred') is not None:
                txt += f' (forest predicted {pre["pred"]:.0%})'
            self._outcome(rec, txt, {'kill': mean_k, 'predicted': pre.get('pred'), 'infested': len(kills)})
        elif act == 'harvest':
            self.audit.on_harvest(f)
            fd = tw.fields[f]
            self.energy.add_residue((fd.harvestKg or 0.0) * STRAW_PER_GRAIN)
            self._outcome(rec, f'{fd.harvestKg:.1f} kg from field {fd.name} ({fd.yieldTHa:.2f} t/ha)', {'kg': fd.harvestKg, 't_ha': fd.yieldTHa})
        elif act == 'scout':
            sq = [tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
            for i in sq:
                self.close_look_t[int(i)] = max(self.close_look_t.get(int(i), -1e18), tw.t)
            self._outcome(rec, f'{res.get("found", 0)} of {res.get("squares", 0)} squares have pests')
        elif act in ('irrigate', 'drain'):
            start = tw.water_tube(f)

            def check(rec=rec, f=f, start=start, act=act):
                fd = tw.fields[f]
                busy = fd.irrTarget is not None if act == 'irrigate' else fd.drainTarget is not None
                if busy and tw.t - rec['t'] < 24 * HOUR:
                    return False
                self._outcome(rec, f'water level {start:.0f} → {tw.water_tube(f):.0f} mm' + (' (still going after 24 h)' if busy else ''))
                return True
            self.outcome_checks.append((tw.t + HOUR, check))
        elif act in ('amend', 'puddle'):
            self._outcome(rec, res.get('msg', 'done'))

    # ── the arms (v2) ──
    def _canopy_h(self, i):
        tw = self.tw
        if not (tw.has[i] and tw.alive[i] and not tw.done[i]):
            return 0.0
        return float(PADDY.visual['height']) * _A.canopy(PADDY, min(1.0, float(tw.frac[i])))

    def _job_for(self, rec, d, f, act, pre, preview=False):
        """The arm job that carries out decision d: its squares (or water gate), each square's load for the arm's
        magazine and the height the tool works at."""
        tw = self.tw
        p = d.get('params') or {}
        sqs = None if d.get('squares') is None else [tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
        allf = [int(i) for i in tw.cells[f]]
        demand = tool_y = None
        if act in ('irrigate', 'drain'):
            targets = [('inlet' if act == 'irrigate' else 'outlet', f)]
        elif act == 'transplant':
            targets = list(pre['squares']) if pre.get('squares') is not None else [i for i in (sqs or allf) if not tw.has[i] or tw.failed[i]]
            demand = [1.0] * len(targets)
        elif act == 'harvest':
            targets = [i for i in allf if tw.has[i] and not tw.done[i]]
            demand = [POTENTIAL_KG_M2 * CFG.SQUARE_M2 * max(0.2, tw.yield_fraction(i, f)) if tw.alive[i] else 0.0 for i in targets]
            tool_y = [max(0.15, 0.6 * self._canopy_h(i)) for i in targets]
        elif act == 'puddle':
            targets = allf
        elif act == 'soil_check':
            targets = sqs if sqs is not None else allf
        elif act in ('scout', 'spray'):
            targets = sqs or []
            tool_y = [max(0.15, 0.6 * self._canopy_h(i)) for i in targets]
            if act == 'spray':
                demand = [D_PESTICIDES[p['product']].mlPerSite / 1000] * len(targets)
        else:   # fertilise, amend: granules or water from above the crop
            targets = sqs if sqs is not None else allf
            tool_y = [max(0.3, self._canopy_h(i) + 0.15) for i in targets]
            if act == 'fertilise':
                demand = [float(p['kg_per_ha']) / 10 / 1000 * CFG.SQUARE_M2] * len(targets)
            elif p.get('product') != 'leach':
                demand = [float(p['dose']) / 1000 * CFG.SQUARE_M2] * len(targets)
        if not targets:
            return None
        n = len(targets)
        label = f"{act.replace('_', ' ')} {d.get('field')}" + (f' ({n} squares)' if n > 1 else '')
        return self.arms.make_job(act, f, targets, params=p, decision=rec['id'] if rec else None, label=label, demand=demand,
                                  tool_y=tool_y, preview=preview)

    def arm_preview(self, rec):
        """A pending decision's planned arm path (a dry run of the arm system; nothing on the farm changes)."""
        d = rec['_d']
        f = FIELD_IDX.get(d.get('field'))
        act = d['action']
        pre = {}
        if act == 'transplant':
            sq = None if d.get('squares') is None else [self.tw.sq_index(f, r - 1, c - 1) for r, c in d['squares']]
            pre['squares'] = [i for i in (sq if sq is not None else [int(x) for x in self.tw.cells[f]]) if not self.tw.has[i] or self.tw.failed[i]]
        job = self._job_for(rec, d, f, act, pre, preview=True)
        if job is None:
            return None
        pv = self.arms.preview(job)
        s = pv.summary or {}
        return {'arm': ROLE_NAME[job.arm], 'stops': pv.plan.stops if pv.plan else None, 'route': pv.plan.route if pv.plan else None,
                'start_t': s.get('start'), 'end_t': s.get('end'), 'busy_s': s.get('busy_s'), 'travel_m': s.get('travel_m'),
                'energy_wh': s.get('energy_wh'), 'min_clearance_m': s.get('min_clearance_m'), 'collisions': s.get('collisions'),
                'tasks': s.get('tasks'), 'track': pv.track}

    def arm_job_may_start(self, job, t):
        """A job that waited more than an hour for its arm is checked by the guardrails again before it starts."""
        rec = self._job_rec.get(job.id)
        if rec is None or t - job.submitted < HOUR:
            return True, ''
        v = G.check(self, rec['_d'], stage='execute', exec_t=self.tw.t)
        return v.ok, '; '.join(v.messages)

    def arm_job_cancelled(self, job, t, why):
        rec = self._job_rec.pop(job.id, None)
        if rec is None:
            return
        rec['status'] = 'skipped'
        rec['execution'] = {'at': self.tw.iso(t), 'ok': False, 'msg': f'not carried out after waiting for the {ROLE_NAME[job.arm]} arm: {why}'}
        self._feedback(rec, rec['execution']['msg'])
        self.store.flush_decision(self._public(rec))
        self.emit('decision', self._public(rec))

    def arm_task_valid(self, job, task, t):
        """Is this square still worth the arm's visit? (A square harvested or replanted meanwhile is skipped.)"""
        if task.cell < 0:
            return True
        tw = self.tw
        i = task.cell
        k = job.kind
        if k == 'harvest':
            return bool(tw.has[i] and not tw.done[i])
        if tw.fields[job.field].harvestT is not None:
            return False
        if k == 'transplant':
            return bool(not tw.has[i] or tw.failed[i])
        return True

    def arm_task_done(self, job, task, t):
        """The arm has finished one square (or set a gate) at time t: apply it to the twin now."""
        tw = self.tw
        k, f, i, p = job.kind, job.field, task.cell, job.params
        if k == 'soil_check':
            tw.probe_square(i, t)
            job.results.append(i)
        elif k == 'transplant':
            job.results.append((i, tw.transplant_square(f, i, t)))
        elif k == 'irrigate':
            tw.act_irrigate(f, float(p['target_depth_mm']), t=t)
            job.results.append(t)
        elif k == 'drain':
            tw.act_drain(f, float(p['to_depth_mm']), t=t)
            job.results.append(t)
        elif k == 'amend':
            job.results.append((i, tw.amend_square(f, i, p['product'], float(p['dose']))))
        elif k == 'puddle':
            tw.puddle_square(f, i)
            job.results.append(i)
        elif k == 'fertilise':
            depth = float(tw.fields[f].h - tw.elev[i])
            job.results.append((i, tw.fertilise_square(f, i, p['product'], float(p['kg_per_ha']), depth)))
        elif k == 'scout':
            found = tw.scout_square(f, i, t)
            self.close_look_t[int(i)] = t
            job.results.append((i, found))
        elif k == 'spray':
            look, cond, eff, infested, truth = tw.spray_square(f, i, p['product'], p.get('target_pest'), t)
            self.close_look_t[int(i)] = t
            job.results.append((i, look, dict(cond), eff, infested, t, truth))
        elif k == 'harvest':
            kg = tw.harvest_square(f, i, t)
            job.results.append((i, kg))
            return {'kg': kg}
        return None

    def arm_job_started(self, job, t):
        rec = self._job_rec.get(job.id)
        if rec is None:
            return
        rec['status'] = 'executing'
        rec['arm_plan'] = {'arm': ROLE_NAME[job.arm], 'job': job.id, 'start_t': round(t, 1), 'start': self.tw.iso(t),
                           'planned_s': round(job.plan.makespan, 1), 'stops': job.plan.stops, 'route': job.plan.route}
        self.emit('decision', self._public(rec))

    def _arm_res(self, job, t):
        """What the job did, in the shape the v1 actuators reported it (for the outcome checks and the audit)."""
        tw = self.tw
        k, f, p = job.kind, job.field, job.params
        fd = tw.fields[f]
        n = len(job.results)
        if k == 'soil_check':
            return {'ok': n > 0, 'squares': n, 'msg': f'probed {n} squares'}
        if k == 'transplant':
            return {'ok': n > 0, 'squares': n, 'msg': f'transplanted {n} squares in field {fd.name}' if n else 'nothing to transplant'}
        if k == 'irrigate':
            return {'ok': n > 0, 'msg': f'inlet open at {tw.iso(job.results[0])[11:16]}: pumping field {fd.name} towards {float(p["target_depth_mm"]):.0f} mm' if n else 'gate not set'}
        if k == 'drain':
            return {'ok': n > 0, 'msg': f'outlet open at {tw.iso(job.results[0])[11:16]}: draining field {fd.name} to {float(p["to_depth_mm"]):.0f} mm' if n else 'gate not set'}
        if k == 'amend':
            return {'ok': n > 0, 'squares': n, 'msg': f'{p["product"]} on {n} squares'}
        if k == 'puddle':
            if n:
                tw.puddle_finish(f)
            return {'ok': n > 0, 'msg': f'field {fd.name} puddled' if n else 'not puddled'}
        if k == 'fertilise':
            lost = sum(x[1] for x in job.results)
            return {'ok': n > 0, 'squares': n, 'lostNKgHa': lost / max(1, n), 'msg': f'{float(p["kg_per_ha"]):.0f} kg/ha {p["product"]} on {n} squares'}
        if k == 'scout':
            return {'ok': n > 0, 'squares': n, 'found': sum(1 for x in job.results if x[1]), 'msg': f'scouted {n} squares'}
        if k == 'spray':
            if n:
                tw.spray_record(f, p['product'], n, job.results[0][5])
            kills = [(int(x[0]), float(x[3])) for x in job.results if x[4]]
            looks = [(x[1][0], x[1][1], x[1][2], x[2], x[5], x[6]) for x in job.results]
            effs = [(int(x[0]), float(x[3])) for x in job.results]
            msg = (f'{p["product"]} on {n} squares (mean kill {np.mean([e for _, e in kills]) * 100:.0f}% on {len(kills)} infested)' if kills
                   else f'{p["product"]} on {n} squares (none infested)')
            return {'ok': n > 0, 'squares': n, 'kills': kills, 'looks': looks, 'effs': effs, 'msg': msg}
        if k == 'harvest':
            r = tw.harvest_finish(f, t)
            return {'ok': True, 'kg': r.kg, 'msg': r.msg}
        return {'ok': False, 'msg': f'unknown job {k}'}

    def arm_job_done(self, job, t):
        tw = self.tw
        s = dict(job.summary)
        s.update(kind=job.kind, field=CFG.FIELDS[job.field] if job.field is not None else None, decision=job.decision, job=job.id)
        self.arm_jobs.append({k: v for k, v in s.items() if k != 'tasks'})
        rec = self._job_rec.pop(job.id, None)
        if rec is None:
            if job.kind == 'harvest':          # the engine's forced harvest
                r = tw.harvest_finish(job.field, t)
                self.forced.append(tw.fields[job.field].name)
                self.audit.on_harvest(job.field)
                self.energy.add_residue(r.kg * STRAW_PER_GRAIN)
                self.note(f'forced harvest (the decision maker never harvested): {r.msg}', tw.fields[job.field].name, 'season')
            return
        f = job.field
        res = self._arm_res(job, t)
        rec['status'] = 'executed' if res.get('ok') else 'failed'
        rec['execution'] = {'at': tw.iso(t), 't': round(t, 1), 'ok': bool(res.get('ok')), 'msg': res.get('msg', '')}
        if job.skipped:
            rec['execution']['skipped_squares'] = job.skipped
        if rec.get('_warn'):
            rec['execution']['warnings'] = rec['_warn']
        plan = rec.setdefault('arm_plan', {'arm': ROLE_NAME[job.arm], 'job': job.id})
        plan.update({k: s[k] for k in ('end', 'busy_s', 'travel_m', 'energy_wh', 'refills', 'min_clearance_m', 'collisions', 'near_misses',
                                       'traffic_wait_s', 'made_way', 'tasks')})
        plan['end_t'] = round(job.end, 1)
        plan.pop('end', None)
        self._after(rec, rec['_d'], f, rec['action'], res, rec.get('_pre') or {})
        self._feedback(rec, res.get('msg', ''))
        self.store.flush_decision(self._public(rec))
        self.emit('decision', self._public(rec))

    def arm_state(self):
        """For the decision maker: which arms are busy, until about when, and how many jobs wait for them."""
        if self.arms is None:
            return None
        out = {}
        for name, st in self.arms.arm_status().items():
            out[name] = None if st is None else {'jobs': st.jobs, 'busy_until': self.tw.iso(st.busy_until)}
        return out

    def _energy_hour(self, t_h, irr0, busy_s):
        """Close one hour of the power plant: solar from the hour's irradiance, the pump's water, the frame's sensing,
        the computer, and the arms' loads (added by the arm system as they worked)."""
        tw = self.tw
        W = tw.weather
        d, h0 = int(t_h // DAY), (t_h % DAY) / HOUR
        ghi = sum(W.ghi(d, h0 + (k + 0.5) / 6) for k in range(6)) / 6
        temp = W.temp(d, h0 + 0.5)
        night = _C.sun_elevation(tw.date_info(t_h).doy, h0 + 0.5) <= 0
        litres = sum(max(0.0, tw.fields[k].irrMm - irr0[k]) * len(tw.cells[k]) * CFG.SQUARE_M2 for k in range(2))
        if litres > 0:
            self.energy.add_pump_water(litres)
        if busy_s > 0:
            self.energy.add_load('compute', self.energy.cfg['compute']['busyW'], busy_s)
        hr_end = int(round(((t_h + HOUR) % DAY) / HOUR)) % 24
        # the playground's scale: duty 1 = a sweep every 20 minutes, so one pass in the hour is a third
        duty = (1.0 / 3.0) if hr_end in CFG.CAMERA_HOURS else 0.0
        self.energy.step(HOUR, ghi, temp, night, t_h + HOUR, {'scanDuty': duty, 'busy': False})
        if hr_end == 0:
            self.energy.close_day(d)

    # ── the clock ──
    def awaiting_approval(self):
        return self.mode == 'approve' and bool(self.pending)

    def step_hour(self):
        """Advance one hour. Returns False when the season is over or decisions await approval."""
        if self.finished:
            return False
        if self.awaiting_approval():
            return False
        tw = self.tw
        t0 = time.perf_counter()
        if self._resume_t == tw.t:
            # this hour's decision point already happened; it was waiting for approvals: go on with the physics
            self._resume_t = None
            return self._physics(t0)
        slot = int(round(tw.t / HOUR))
        for f in range(2):
            self.tube_log[(f, slot)] = tw.water_tube(f)
        if self.record_frames:
            # the true water level of each field at every whole hour, for the 3D view (display only)
            if self.level_log_h0 is None:
                self.level_log_h0 = slot
            for f in range(2):
                self.level_log[f].append(round(float(tw.fields[f].h), 1))
        self.tube_log.pop((0, slot - 49), None)
        self.tube_log.pop((1, slot - 49), None)
        # 0. scheduled events (the data sets' outbreaks, storms, dry spells and overcast weeks)
        for e in self.schedule:
            if not e['done'] and (tw.day > e['day'] or (tw.day == e['day'] and int(round(tw.hour)) % 24 >= e['hour'])):
                e['done'] = True
                self.inject_event(e['kind'], e.get('field'), e.get('pest'))
        # 1. decisions whose time has come
        due = [r for r in self.queue if r['exec_t'] <= tw.t + 1]
        if due:
            self.queue = [r for r in self.queue if r['exec_t'] > tw.t + 1]
            for r in due:
                if r['status'] == 'scheduled':
                    self._execute(r)
        # 2. outcome checks
        if self.outcome_checks:
            keep = []
            for due_t, fn in self.outcome_checks:
                if tw.t >= due_t:
                    if fn() is False:
                        keep.append((tw.t + HOUR, fn))
                else:
                    keep.append((due_t, fn))
            self.outcome_checks = keep
        # 3. decision point?
        trig = self._triggers()
        scheduled = 'scheduled' in trig
        if scheduled:
            self._audit_point()
        if int(round(tw.hour)) % 24 == 8:
            self.audit.pest_check()
        if trig and (scheduled or tw.t - self.last_call_t >= CFG.TRIGGER_MIN_GAP_H * HOUR - 1):
            self.call(trig)
        if scheduled and self.record_frames:
            self.frames.append(self.frame())
        if self.awaiting_approval():
            self._resume_t = tw.t
            self.wall_ms += (time.perf_counter() - t0) * 1000
            return False
        return self._physics(t0)

    def _physics(self, t0):
        tw = self.tw
        t_h = tw.t
        irr0 = [fd.irrMm for fd in tw.fields]
        dr0 = [fd.drainedM3 for fd in tw.fields]
        busy_s = 0.0
        # 4. the arms work through this hour; each square they finish is applied to the twin as they go
        self.last_arm_hour = None
        if self.arms is not None:
            self.arms.advance_to(t_h + HOUR)
            busy_s, self.arms.busy_s = self.arms.busy_s, 0.0
            if self.record_tracks:
                hour = self.arms.flush_tracks()
                ts = [kf[0] for v in hour.values() for kf in v]
                for k, v in hour.items():
                    self.arm_tracks[k].extend(v)
                if ts:
                    # what the live website needs to show this hour's arm work: every keyframe, and the span it covers
                    self.last_arm_hour = {'t0': t_h, 't1': t_h + HOUR, 'first': min(ts), 'last': max(ts), 'busy_s': round(busy_s, 1)}
                    if self.listener:
                        self.emit('arms', dict(self.last_arm_hour, tracks={str(k): v for k, v in hour.items()}))
        # 5. physics
        tw.step_hour()
        # what the farm itself knows of the hour just gone (its rain gauge, its pump and outlets), kept 2 days for the
        # time-to-AWD features
        slot = int(round(t_h / HOUR))
        d0, h0 = int(t_h // DAY), (t_h % DAY) / HOUR
        g = tw.weather.rain_between(d0, h0, h0 + 1.0)
        e0 = tw.weather.et0_between(d0, h0, h0 + 1.0)          # the station's reference evapotranspiration for the hour
        for f in range(2):
            fd = tw.fields[f]
            area = len(tw.cells[f]) * CFG.SQUARE_M2
            # (gauge rain mm, pumped?, drained?, pumped mm, drained mm, ET0 mm): the gauge, the pump's and the outlet's
            # meters and the station
            self.water_flags[(f, slot)] = (g, fd.irrMm > irr0[f] + 1e-9, fd.drainedM3 > dr0[f] + 1e-12, fd.irrMm - irr0[f],
                                           (fd.drainedM3 - dr0[f]) * 1000.0 / area, e0)
            self.water_flags.pop((f, slot - 50), None)
        self._energy_hour(t_h, irr0, busy_s)
        if self.record_frames:
            E = self.energy
            pl = self.power_log
            if pl['from_hour'] is None:
                pl['from_hour'] = int(round(t_h / HOUR))
            pl['pv'].append(round(E.pvKw, 3)); pl['soc'].append(round(E.soc, 3)); pl['load'].append(round(E.loadKw, 3))
            pl['gen'].append(1 if E.genOn else 0); pl['biogas'].append(round(E.biogasM3, 2))
        self.audit.tick()
        self._season_rules()
        if tw.day != self._last_hist_day:
            self._snapshot()
        self.wall_ms += (time.perf_counter() - t0) * 1000
        self.emit('tick', {'time': tw.iso(), 't': tw.t})
        if tw.season_over():
            self._finish()
            return False
        return True

    def _audit_point(self):
        tw = self.tw
        for f in range(2):
            fd = tw.fields[f]
            if fd.puddled and fd.transplantT is None and not fd.abandoned:
                self.audit.on_establish_check(f)
            if fd.transplantT is not None and fd.harvestT is None and tw.field_progress(f) >= 0.96:
                self.audit.on_harvest_assess(f)
            thr = self._awd_threshold(f)
            if thr is not None and fd.irrTarget is None and fd.h > thr:
                pred, x = self.adv.hours_to_awd(f, thr, self.tube_log, self.water_flags)
                self.audit.on_awd_check(f, thr, pred, x if self.audit.rows is not None else None)

    def _season_rules(self):
        tw = self.tw
        for f in range(2):
            fd = tw.fields[f]
            if fd.transplantT is None and not fd.abandoned and tw.day >= CFG.TRANSPLANT_DEADLINE_DAY:
                fd.abandoned = True
                self.note(f'field {fd.name} was never transplanted: abandoned for the season', fd.name, 'season')
            if fd.transplantT is not None and fd.harvestT is None and not fd.abandoned:
                cs = tw.cells[f]
                alive = cs[tw.alive[cs] & ~tw.done[cs]]
                if (len(alive) and tw.field_progress(f) >= 1.35) or tw.day >= CFG.SEASON_MAX_DAYS - 1 or (not len(alive) and tw.t - fd.transplantT > 20 * DAY):
                    if self.use_arms:
                        busy = any(j.kind == 'harvest' and j.field == f and j.status in ('queued', 'active') for j in self.arms.jobs.values())
                        if f not in self._forced_jobs and not busy:
                            self._forced_jobs.add(f)
                            job = self._job_for(None, {'field': fd.name, 'action': 'harvest', 'params': {}}, f, 'harvest', {})
                            if job is not None:
                                job.label = f'forced harvest {fd.name}'
                                self.arms.submit(job, tw.t)
                            else:
                                r = tw.harvest_finish(f, tw.t)
                                self.forced.append(fd.name)
                                self.audit.on_harvest(f)
                        continue
                    r = tw.act_harvest(f)
                    self.forced.append(fd.name)
                    self.audit.on_harvest(f)
                    self.energy.add_residue(r.kg * STRAW_PER_GRAIN)
                    self.note(f'forced harvest (the decision maker never harvested): {r.msg}', fd.name, 'season')

    def _snapshot(self):
        tw = self.tw
        self._last_hist_day = tw.day
        row = {'day': tw.day, 'date': tw.iso()[:10]}
        W = tw.weather
        row['rain'] = round(W.day(max(0, tw.day - 1)).rain, 1) if tw.day > 0 else 0.0
        for f in range(2):
            fd = tw.fields[f]
            k = fd.name
            row[f'{k}_h'] = round(fd.h, 1)
            row[f'{k}_prog'] = round(tw.field_progress(f), 3)
            row[f'{k}_irr'] = round(fd.irrMm, 1)
            cs = tw.cells[f]
            live = cs[tw.alive[cs] & ~tw.done[cs]]
            row[f'{k}_health'] = round(float(np.mean(tw.health[live])), 3) if len(live) else None
            row[f'{k}_pest'] = round(float(np.mean(tw.oPest[cs])), 4)
            row[f'{k}_n'] = round(fd.nKgHa, 1)
        self.history.append(row)

    def frame(self):
        """Both fields square by square, as the farm sees them (for the replay website)."""
        tw, adv = self.tw, self.adv
        out = {'t': tw.t, 'time': tw.iso(), 'day': tw.day, 'fields': {}}
        W = tw.weather
        d, h = tw.day, tw.hour
        out['weather'] = {'temp_c': round(W.temp(d, h), 1), 'rh': round(W.rh(d, h)), 'wind_ms': round(W.wind(d, h), 1),
                          'rain_today_mm': round(W.rain_between(d, 0, h), 1),
                          'forecast': [{'tmax': round(x.tmax, 1), 'tmin': round(x.tmin, 1), 'rain_mm': round(x.rainMm, 1), 'rain_prob': round(x.rainProb, 2)}
                                       for x in (W.forecast_day(d, k) for k in range(3))]}
        for f in range(2):
            fd = tw.fields[f]
            cs = [tw.sq_index(f, r, c) for r in range(tw.R) for c in range(tw.K)]
            tube = tw.water_tube(f)
            soil = {s_.i: s_.code for s_ in adv.soil(f)}
            est = {}
            if fd.transplantT is None and not fd.abandoned:
                est = {a.i: a.chance for a in adv.establishment(f)}
            risk = {}
            if fd.transplantT is not None and fd.harvestT is None:
                rs, _ = adv.pest_risk(f)
                for r_ in rs:
                    risk[r_.i] = max(risk.get(r_.i, 0.0), r_.risk)
            prog = tw.field_progress(f)
            m = tw.methane(f)
            t_first = fd.transplantT
            out['fields'][fd.name] = {
                'phase': field_phase(tw, f), 'tube': round(tube), 'progress': round(prog, 3), 'pumping': fd.irrTarget is not None,
                'draining': fd.drainTarget is not None, 'irr_mm': round(fd.irrMm), 'rain_mm': round(fd.rainMm), 'n_kg_ha': round(fd.nKgHa, 1),
                'sprays': len(fd.sprays), 'ch4': round(m.kgHa, 1), 'yield': round(fd.yieldTHa, 2) if fd.yieldTHa is not None else None,
                'sq': {
                    'depth': [round(tube - adv.elev_obs[i]) for i in cs],
                    'prog': [round(float(tw.frac[i]), 3) if tw.has[i] else None for i in cs],
                    'state': ''.join('H' if tw.done[i] and tw.harvestKg[i] > 0 else 'L' if tw.has[i] and tw.alive[i] and not tw.done[i]
                                     else 'D' if tw.has[i] else '.' for i in cs),
                    'health': [round(float(tw.oHealth[i]), 2) if tw.has[i] else None for i in cs],
                    'soil': ''.join(soil[i] for i in cs),
                    'ph': [round(tw.soil_reading(i).ph, 1) for i in cs],
                    'pest': [round(float(tw.oPest[i]), 3) if tw.oPestId[i] >= 0 and tw.oPest[i] > 0 else 0 for i in cs],
                    'pid': [int(tw.oPestId[i]) if tw.oPest[i] > 0 else -1 for i in cs],
                    'risk': [round(risk[i], 3) if i in risk else None for i in cs],
                    'est': [round(est[i], 3) if i in est else None for i in cs],
                    # the physical world, for the 3D view: each square's true soil height (mm, the survey's 'depth' above is
                    # the farm's estimate) and whether the square was gap-filled after the field's first transplanting
                    'elev': [round(float(tw.elev[i])) for i in cs],
                    'gf': ''.join('1' if (t_first is not None and tw.has[i] and tw.plantedT[i] > t_first + 43200) else '0' for i in cs),
                },
                'lvl': round(float(fd.h), 1),
            }
        if self.arms is not None:
            out['arms'] = self.arms.snapshot()
        out['power'] = self.power_now()
        return out

    def water_hours(self, h0=None, h1=None):
        """The true water level of both fields (mm above the mean soil surface) at every whole hour the run has seen, from
        hour h0 to h1 (display only: the 3D view draws the water at this height)."""
        base = self.level_log_h0 or 0
        n = len(self.level_log[0])
        a = 0 if h0 is None else max(0, int(h0) - base)
        b = n if h1 is None else max(a, min(n, int(h1) - base))
        return {'from_hour': base + a, 'A': self.level_log[0][a:b], 'B': self.level_log[1][a:b]}

    def weather_hours(self, h0, h1):
        """Hourly weather for the 3D view from hour h0 to h1 (hours since the season's first midnight): rain in the hour
        (mm), cloud (0–1), wind (m/s) and temperature (°C) as they were (the twin's weather, events included)."""
        W = self.tw.weather
        rain, cloud, wind, temp = [], [], [], []
        for hh in range(max(0, int(h0)), int(h1)):
            d, h = divmod(hh, 24)
            rain.append(round(W.rain_between(d, h, h + 1.0), 2))
            cloud.append(round(W.cloud(d, h + 0.5), 2))
            wind.append(round(W.wind(d, h + 0.5), 1))
            temp.append(round(W.temp(d, h + 0.5), 1))
        return {'from_hour': max(0, int(h0)), 'rain': rain, 'cloud': cloud, 'wind': wind, 'temp': temp}

    def power_now(self):
        E = self.energy
        T = E.totals
        return {'pv_kw': round(E.pvKw, 3), 'load_kw': round(E.loadKw, 3), 'soc': round(E.soc, 3), 'gen_on': bool(E.genOn),
                'biogas_m3': round(E.biogasM3, 2), 'pv_kwh': round(T['pvKwh'], 2), 'load_kwh': round(T['loadKwh'], 2),
                'grid_kwh': round(T['gridKwh'], 2), 'gen_kwh': round(T['genKwh'], 2)}

    def tracks_export(self):
        """Everything the 3D view needs to replay the arms: their build, every keyframe, every job."""
        from .robots.arms import DEFS
        return {'arms': [{k: d[k] for k in ('id', 'name', 'title', 'role', 'color', 'radius', 'scale', 'reach', 'capacity', 'capKind')} for d in DEFS],
                'codes': ARM_CODES,
                'tracks': {str(k): v for k, v in self.arm_tracks.items()}, 'jobs': self.arm_jobs}

    def run(self, max_hours=None, wall_limit_s=None):
        """Run until the season ends (or approval is needed, or a limit is hit)."""
        n = 0
        t0 = time.time()
        while self.step_hour():
            n += 1
            if max_hours is not None and n >= max_hours:
                break
            if wall_limit_s is not None and time.time() - t0 > wall_limit_s:
                break
        return self

    def inject_event(self, kind, field=None, pest=None):
        f = FIELD_IDX.get(field) if isinstance(field, str) else field
        r = self.tw.trigger_event(kind, f, pest)
        if r.ok:
            self.event_pending.append(kind)
            self.note(r.msg, kind='event')
        return r

    def _finish(self):
        if self.finished:
            return
        for r in self.queue:
            if r['status'] == 'scheduled':
                r['status'] = 'expired'
                self.store.flush_decision(self._public(r))
        self.queue = []
        for jid, r in list(self._job_rec.items()):
            if r['status'] in ('queued', 'executing'):
                r['status'] = 'expired'
                r['execution'] = {'at': self.tw.iso(), 'ok': False, 'msg': 'the season ended before the arm finished'}
                self.store.flush_decision(self._public(r))
        self._job_rec = {}
        for due_t, fn in self.outcome_checks:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        self.outcome_checks = []
        self.audit.tick_awd(final=True)
        self.finished = True
        self.summary_cache = self.summary()
        self.store.write_json('summary.json', self.summary_cache)
        self.store.write_json('history.json', self.history)
        if self.record_frames:
            self.store.write_json('frames.json', self.frames)
        if self.record_tracks and self.arms is not None:
            self.store.write_json('arm_tracks.json', self.tracks_export())
        if self.record_frames:
            self.store.write_json('weather_hourly.json', self.weather_hours(0, (self.tw.day + 2) * 24))
            self.store.write_json('water_hourly.json', self.water_hours())
            self.store.write_json('power_hourly.json', self.power_log)
        self.emit('finished', self.summary_cache)

    # ── results ──
    def summary(self):
        tw = self.tw
        fields = {}
        tot_kg = 0.0
        for f in range(2):
            fd = tw.fields[f]
            m = tw.methane(f)
            area_ha = len(tw.cells[f]) * CFG.SQUARE_M2 / 10000
            kg = fd.harvestKg or 0.0
            tot_kg += kg
            fields[fd.name] = {
                'yield_t_ha': round(fd.yieldTHa, 3) if fd.yieldTHa is not None else 0.0,
                'harvest_kg': round(kg, 2), 'transplanted': tw.iso(fd.transplantT)[:10] if fd.transplantT is not None else None,
                'harvested': tw.iso(fd.harvestT)[:10] if fd.harvestT is not None else None, 'abandoned': fd.abandoned,
                'forced_harvest': fd.name in self.forced,
                'irrigation_mm': round(fd.irrMm, 1), 'irrigation_m3': round(fd.waterM3, 3), 'rain_mm': round(fd.rainMm, 1),
                'percolation_mm': round(fd.percMm, 1), 'et_mm': round(fd.etMm, 1), 'overflow_mm': round(fd.overflowMm, 1),
                'drained_m3': round(fd.drainedM3, 3), 'pump_kwh': round(fd.energyKwh, 3),
                'water_productivity_kg_m3': round(kg / fd.waterM3, 3) if fd.waterM3 > 0 else None,
                'n_kg_ha': round(fd.nKgHa, 1), 'p2o5_kg_ha': round(fd.pKgHa, 1), 'k2o_kg_ha': round(fd.kKgHa, 1),
                'sprays': len(fd.sprays), 'spray_l': round(fd.sprayL, 3), 'ai_g_ha': round(fd.aiG / area_ha, 2),
                'bio_kg_ha': round(fd.bioKg / area_ha, 3),
                'sprayed_squares': int(sum(s['squares'] for s in fd.sprays)),
                'ch4_kg_ha': round(m.kgHa, 1), 'ch4_regime': m.regime, 'drain_periods': m.drainPeriods, 'flooded_days': round(fd.floodedH / 24, 1),
                'ch4_sf_organic': round(m.sfOrganic, 3),
                'co2e_t_ha': round(m.kgHa * GWP_CH4 / 1000, 2), 'cultivation_days': round(m.days, 1),
                'established_share': (round(float(np.mean(~tw.failed[tw.cells[f]][tw.has[tw.cells[f]]])), 3)
                                      if fd.transplantT is not None and tw.has[tw.cells[f]].any() else None),
                'planted_squares': int(np.sum(tw.has[tw.cells[f]])),
            }
        decs = self.store.decisions
        status = collections.Counter(d['status'] for d in decs)
        verdicts = collections.Counter(d['guardrail']['verdict'] for d in decs)
        actions = collections.Counter(d['action'] for d in decs if d['status'] == 'executed')
        calls = self.store.calls
        out = {
            'run_id': self.run_id, 'config': dict(self.cfg), 'season_days': tw.day, 'finished': self.finished,
            'yield_t_ha': round(sum(fields[k]['yield_t_ha'] for k in fields) / 2, 3), 'harvest_kg': round(tot_kg, 2),
            'fields': fields,
            'irrigation_mm': round(sum(fields[k]['irrigation_mm'] for k in fields) / 2, 1),
            'irrigation_m3': round(sum(fields[k]['irrigation_m3'] for k in fields), 3),
            'pump_kwh': round(sum(fields[k]['pump_kwh'] for k in fields), 3),
            'n_kg_ha': round(sum(fields[k]['n_kg_ha'] for k in fields) / 2, 1),
            'ai_g_ha': round(sum(fields[k]['ai_g_ha'] for k in fields) / 2, 2),
            'bio_kg_ha': round(sum(fields[k]['bio_kg_ha'] for k in fields) / 2, 3),
            'sprayed_squares': sum(fields[k]['sprayed_squares'] for k in fields),
            'ch4_kg_ha': round(sum(fields[k]['ch4_kg_ha'] for k in fields) / 2, 1),
            'calls': len(calls), 'calls_skipped_budget': self.n_skipped_calls,
            'calls_failed': sum(1 for c in calls if not c['ok']), 'fallbacks': sum(1 for c in calls if c.get('fallback')),
            'tokens_in': int(sum(c.get('tokens_in') or 0 for c in calls)), 'tokens_out': int(sum(c.get('tokens_out') or 0 for c in calls)),
            'latency_ms_mean': round(float(np.mean([c['latency_ms'] for c in calls if c.get('latency_ms')])), 1) if any(c.get('latency_ms') for c in calls) else None,
            'triggers': dict(collections.Counter(t.split('_A')[0].split('_B')[0] if t.startswith('awd') else t for c in calls for t in c['triggers'])),
            'decisions': len(decs), 'decision_status': dict(status), 'verdicts': dict(verdicts), 'executed_actions': dict(actions),
            'schema_errors': sum(len(c.get('schema_errors') or []) for c in calls),
            'audit': self.audit.summary(),
            'energy': self.energy_summary(),
            'wall_s': round(self.wall_ms / 1000, 2),
            'decision_digest': self.decision_digest(), 'history_digest': self.history_digest(),
        }
        if self.arms is not None:
            out['arms'] = self.arms_summary()
        return out

    def energy_summary(self):
        E = self.energy
        T = E.totals
        return {'pv_kwh': round(T['pvKwh'], 2), 'load_kwh': round(T['loadKwh'], 2), 'grid_kwh': round(T['gridKwh'], 2),
                'gen_kwh': round(T['genKwh'], 2), 'curtailed_kwh': round(T['curtailedKwh'], 2), 'battery_in_kwh': round(T['battInKwh'], 2),
                'battery_out_kwh': round(T['battOutKwh'], 2), 'self_sufficiency': round(E.self_sufficiency(), 4),
                'by_category_kwh': {k: round(v, 3) for k, v in sorted(E.byCat.items())}, 'battery_end_soc': round(E.soc, 3),
                'biogas_m3_left': round(E.biogasM3, 2), 'pv_kwp': E.cfg['pvKwp'], 'battery_kwh': E.cfg['battKwh']}

    def arms_summary(self):
        """Busy time, travel, energy, closest approach and collisions: per arm, per kind of job, and overall."""
        tot = self.arms.totals()
        by = {}
        for j in self.arm_jobs:
            b = by.setdefault(j['kind'], {'jobs': 0, 'squares': 0, 'busy_s': 0.0, 'travel_m': 0.0, 'energy_wh': 0.0, 'min_clearance_m': None,
                                          'collisions': 0, 'near_misses': 0, 'traffic_wait_s': 0.0})
            b['jobs'] += 1
            b['squares'] += j['done']
            b['busy_s'] = round(b['busy_s'] + j['busy_s'], 1)
            b['travel_m'] = round(b['travel_m'] + j['travel_m'], 2)
            b['energy_wh'] = round(b['energy_wh'] + j['energy_wh'], 2)
            b['traffic_wait_s'] = round(b['traffic_wait_s'] + j['traffic_wait_s'], 1)
            b['collisions'] += j['collisions']
            b['near_misses'] += j['near_misses']
            if j['min_clearance_m'] is not None and (b['min_clearance_m'] is None or j['min_clearance_m'] < b['min_clearance_m']):
                b['min_clearance_m'] = j['min_clearance_m']
        clear = [j['min_clearance_m'] for j in self.arm_jobs if j['min_clearance_m'] is not None]
        return {'jobs': len(self.arm_jobs), 'busy_s': round(sum(j['busy_s'] for j in self.arm_jobs), 1),
                'travel_m': round(sum(j['travel_m'] for j in self.arm_jobs), 1), 'energy_wh': round(sum(j['energy_wh'] for j in self.arm_jobs), 1),
                'min_clearance_m': round(min(clear), 3) if clear else None, 'collisions': tot['violations'], 'near_misses': tot['nearMisses'],
                'min_separation_m': tot['minSep'], 'detours': tot['detours'], 'made_way': tot['yields'], 'holds': tot['holds'],
                'steps': tot['steps'], 'per_arm': tot['arms'], 'by_kind': by}

    def history_digest(self):
        """A fingerprint of the farm's physical course: the daily history of both fields (water, growth, health, pests,
        inputs) and the final yields."""
        import hashlib
        h = hashlib.sha256(json.dumps([{k: v for k, v in x.items() if not k.startswith('_')} for x in self.history], sort_keys=True, default=str).encode())
        h.update(json.dumps([self.tw.fields[f].harvestKg for f in range(2)], default=str).encode())
        return h.hexdigest()

    def decision_digest(self):
        """A compact, order-preserving fingerprint of every decision and its fate (for determinism checks)."""
        import hashlib
        h = hashlib.sha256()
        for d in self.store.decisions:
            h.update(json.dumps([d['created'], d['field'], d['action'], d['params'], d['squares'], d['exec_at'], d['status']], sort_keys=True).encode())
        return h.hexdigest()


R_FERT_N = {'urea': 0.46, 'dap': 0.18, 'mop': 0.0, 'ssp': 0.0, 'complex_10_26_26': 0.10}
