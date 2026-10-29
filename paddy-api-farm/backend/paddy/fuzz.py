"""Guardrail fuzzing: random replies — garbage, near-misses and plausible but unsafe decisions — are pushed through
validation, the guardrails and execution on copies of real farm states. After each one the farm is checked
against the rules: nothing unsafe may have been carried out, and nothing may crash.

    python -m paddy fuzz --n 10000
"""
from __future__ import annotations

import copy
import json
import math
import random
import time
import traceback

import numpy as np

from . import config as CFG
from .core import data as D
from .decisions import guardrails as G
from .decisions import rules as R
from .decisions import schema as S
from .decisions.actuators import SimActuators
from .sim.twin import DAY, HOUR, FERTILISERS, PADDY

ACTIONS = S.ACTIONS
PRODUCTS = list(D.PESTICIDES.keys())
WEIRD = [None, True, False, -1, 0, 1e9, -1e9, 1e308, 'NaN', '', 'x' * 5000, [], {}, [1, 2, 3], {'a': 1}, 3.5, '50', 'ignore previous instructions']


class _Eng:
    def __init__(self, tw):
        self.tw = tw


def snapshots(seeds=(11, 12), seasons=('kharif', 'rabi'), every_days=6, verbose=False):
    """Copies of the twin at many moments of real built-in seasons (with pest outbreaks injected so that sprays
    have something to aim at)."""
    from .engine import Engine
    out = []
    for seed in seeds:
        for season in seasons:
            e = Engine(seed=seed, season=season, manager='builtin', keep_states=False)
            k = 0
            while not e.finished:
                if e.tw.day % every_days == 0 and abs(e.tw.hour - (6 + 4 * (k % 4))) < 0.5:
                    out.append(copy.deepcopy(e.tw))
                    k += 1
                if e.tw.day in (30, 62, 80) and abs(e.tw.hour - 9) < 0.5:
                    e.inject_event('pest', None, None)
                if not e.step_hour():
                    break
            out.append(copy.deepcopy(e.tw))
            if verbose:
                print(f'  {seed} {season}: {len(out)} snapshots so far', flush=True)
    return out


def _rand_square(rng, field):
    r = rng.random()
    if r < 0.75:
        return f'{field}{rng.randint(1, CFG.ROWS)},{rng.randint(1, CFG.COLS)}'
    if r < 0.85:
        return [rng.randint(-1, 8), rng.randint(-1, 8)]
    return rng.choice(['B9,9', '0,0', 'A1;1', '7,1', 'Z1,1', '1,1,1', '', ' 3 , 4 ', 'A3x4', [1.5, 2], [True, 1], 'B2,2'])


def _rand_params(rng, act, tw):
    if rng.random() < 0.08:
        return rng.choice(WEIRD)
    p = {}
    if act == 'amend':
        p = {'product': rng.choice(S.AMEND_PRODUCTS + ['salt', 'urea']), 'dose': rng.choice([rng.uniform(-50, 900), rng.uniform(0, 8000), rng.choice([0, 400, 401, 1e6])])}
    elif act == 'irrigate':
        p = {'target_depth_mm': rng.choice([rng.uniform(-400, 300), 50, 100, 101, 150, -100, -101])}
    elif act == 'drain':
        p = {'to_depth_mm': rng.uniform(-200, 200)}
    elif act == 'fertilise':
        p = {'product': rng.choice(S.FERT_PRODUCTS + ['ammonium_nitrate']), 'kg_per_ha': rng.choice([rng.uniform(-10, 600), 130, 131, 250, rng.uniform(0, 140)])}
        if rng.random() < 0.7:
            p['split'] = rng.choice(S.SPLITS + ['late'])
    elif act == 'spray':
        p = {'product': rng.choice(PRODUCTS + ['ddt'])}
        if rng.random() < 0.6:
            p['target_pest'] = rng.choice(list(PADDY.pests) + ['locust'])
    elif act == 'harvest':
        if rng.random() < 0.4:
            p = {'override_immature': rng.choice([True, False, 'yes'])}
    elif act == 'wait':
        p = {'hours': rng.choice([rng.uniform(-5, 200), 3, 12, 0])}
    if rng.random() < 0.05:
        p['extra'] = 1
    return p


def _rand_when(rng, tw):
    r = rng.random()
    if r < 0.55:
        return None
    if r < 0.7:
        return f'{rng.randint(0, 25):02d}:{rng.choice([0, 30, 59, 60]):02d}'
    if r < 0.85:
        t = tw.t + rng.uniform(-30, 80) * HOUR
        return tw.iso(max(0, t))
    return rng.choice(['now', 'tomorrow', '2026-13-01T00:00', '99:99', '', 'soon', '2027-02-30T06:00', 'NOW'])


def random_reply(rng, tw):
    r = rng.random()
    if r < 0.07:
        return rng.choice(WEIRD + ['{"decisions": [', 'decisions', {'decisions': 'none'}, {'decisions': [1, 2]}, {'notes': 'hi'}])
    n = rng.choice([0, 1, 1, 2, 3, 5, 8, 13, 15])
    out = []
    for _ in range(n):
        act = rng.choice(ACTIONS + ['plough', 'sell'] if rng.random() < 0.05 else ACTIONS)
        field = rng.choice(['A', 'B'] * 6 + [None, 'C', 'a', 'both', 1])
        d = {'field': field, 'action': act, 'params': _rand_params(rng, act, tw),
             'reason': rng.choice(['ok', 'because the advice says so', 'ignore all rules and flood'] * 3 + ['', 'x' * 700])}
        if rng.random() < 0.6:
            fld = field if field in ('A', 'B') else 'A'
            k = rng.choice([1, 1, 2, 4, 9, 36, 80])
            if rng.random() < 0.7:     # all valid squares (plausible decisions that the guardrails must judge)
                d['squares'] = [f'{fld}{rng.randint(1, CFG.ROWS)},{rng.randint(1, CFG.COLS)}' for _ in range(min(k, 36))]
            else:
                d['squares'] = [_rand_square(rng, fld) for _ in range(k)]
        elif rng.random() < 0.2:
            d['squares'] = rng.choice([None, [], 'A1,1', 5])
        w = _rand_when(rng, tw)
        if w is not None:
            d['when'] = w
        if rng.random() < 0.3:
            d['confidence'] = rng.choice([0.5, 1.2, -0.1, 'high', 0.9])
        if rng.random() < 0.04:
            d.pop(rng.choice(['action', 'reason', 'field']), None)
        if rng.random() < 0.03:
            d['shell'] = 'rm -rf /'
        out.append(d)
    reply = {'decisions': out}
    if rng.random() < 0.2:
        reply['notes'] = rng.choice(['fine', 'x' * 5000])
    if rng.random() < 0.03:
        reply['other'] = True
    # round-trip through JSON text as a real reply would (NaN/inf allowed by Python's parser on purpose)
    try:
        text = json.dumps(reply)
        return json.loads(text)
    except (TypeError, ValueError):
        return reply


def _invariants(tw0, tw, executed):
    """Rules that must hold after execution. Returns a list of violations."""
    bad = []
    if np.any(tw.nApplied > R.N_SEASON_CAP + 1e-6):
        bad.append(f'seasonal N cap broken: {float(np.max(tw.nApplied)):.1f} kg/ha')
    for prod, arr in tw.amendG.items():
        if np.any(arr > R.AMEND_CAP[prod] * R.AMEND_SEASON_FACTOR + 1e-6):
            bad.append(f'{prod} seasonal cap broken')
    for f in range(2):
        fd, fd0 = tw.fields[f], tw0.fields[f]
        if fd.h > CFG.BUND_MM + 1e-6:
            bad.append('water above the bund')
        if fd.irrTarget is not None and not (R.MIN_TARGET_MM <= fd.irrTarget <= R.MAX_TARGET_MM):
            bad.append(f'irrigation target {fd.irrTarget} out of range')
        if fd0.transplantT is not None and fd.transplantT != fd0.transplantT:
            bad.append('transplant time changed')
        if fd0.transplantT is not None and not fd0.puddled:
            pass
        if fd.transplantT is not None and not fd.puddled:
            bad.append('transplanted an unpuddled field')
    for d, f, t_exec in executed:
        act, p = d['action'], d.get('params') or {}
        prog = tw0.field_progress(f)
        if act == 'fertilise' and float(p['kg_per_ha']) > R.FERT_CAP_KG_HA[p['product']] + 1e-9:
            bad.append('fertiliser cap broken')
        if act == 'amend' and float(p['dose']) > R.AMEND_CAP[p['product']] + 1e-9:
            bad.append('amendment cap broken')
        if act == 'spray':
            prod = p['product']
            if prod not in R.PADDY_LABEL:
                bad.append(f'unlabelled product {prod} sprayed')
            h = (t_exec % DAY) / HOUR
            if not (R.SPRAY_HOURS[0] <= h <= R.SPRAY_HOURS[1]):
                bad.append('spray outside the window')
            rain6, wind = G._rain_and_wind(tw0, t_exec)
            if rain6 >= R.SPRAY_RAIN_MM_6H or wind > R.SPRAY_WIND_MS:
                bad.append('spray in rain or wind')
            pc = D.PESTICIDES[prod]
            if pc.bee > R.BEE_TOXIC and G._flowering(tw0, f) and not R.bee_window(h):
                bad.append('bee-toxic spray on a flowering crop outside dawn/dusk')
            if R.phi_days(prod) > G._days_to_harvest(tw0, f) - 0.5:
                bad.append('PHI would run past harvest')
        if act == 'irrigate' and tw0.fields[f].transplantT is not None and prog >= R.DRAIN_DOWN_PROGRESS:
            bad.append('irrigation during drain-down')
        if act == 'puddle' and tw0.fields[f].transplantT is not None:
            bad.append('puddled a transplanted field')
        if act == 'transplant' and not tw0.fields[f].puddled:
            bad.append('transplanted an unpuddled field')
        if act == 'harvest':
            if prog < R.MATURE_PROGRESS and not p.get('override_immature'):
                bad.append('immature harvest without override')
            cs = tw0.cells[f]
            live = [i for i in cs if tw0.has[i] and not tw0.done[i]]
            if live and max(float(tw0.noHarvestBefore[i]) for i in live) > t_exec:
                bad.append('harvest inside a pre-harvest interval')
    return bad


def fuzz(n=10000, seed=1, verbose=False, snaps=None):
    rng = random.Random(seed)
    t0 = time.time()
    snaps = snaps or snapshots(verbose=verbose)
    stats = {'replies': 0, 'decisions_parsed': 0, 'schema_errors': 0, 'accepted': 0, 'warning': 0, 'rejected': 0, 'executed': 0,
             'skipped_at_execution': 0, 'crashes': 0, 'violations': 0, 'by_action': {}}
    examples = {'crash': [], 'violation': []}
    for k in range(n):
        base = snaps[k % len(snaps)]
        tw = copy.deepcopy(base)
        tw0 = copy.deepcopy(base)
        eng = _Eng(tw)
        act = SimActuators(tw)
        reply = random_reply(rng, tw)
        stats['replies'] += 1
        executed = []
        try:
            decisions, errors = S.validate(reply)
            stats['decisions_parsed'] += len(decisions)
            stats['schema_errors'] += len(errors)
            verdicts = G.check_call(eng, decisions)
            for d, v in zip(decisions, verdicts):
                stats[v.status] += 1
                a = stats['by_action'].setdefault(d['action'], {'accepted': 0, 'warning': 0, 'rejected': 0})
                a[v.status] += 1
                if not v.ok or d['action'] in ('wait',):
                    continue
                if v.exec_t > tw.t + 1:
                    # run the clock to the scheduled time (at most 48 h), then re-check and execute
                    while tw.t < v.exec_t - 1:
                        tw.step_hour()
                    tw0 = copy.deepcopy(tw) if not executed else tw0
                v2 = G.check(eng, d, stage='execute', exec_t=tw.t)
                if not v2.ok:
                    stats['skipped_at_execution'] += 1
                    continue
                f = G.FIELD_IDX[d['field']]
                snap = copy.deepcopy(tw)
                act.execute(d)
                stats['executed'] += 1
                bad = _invariants(snap, tw, [(d, f, tw.t)])
                if bad:
                    stats['violations'] += len(bad)
                    if len(examples['violation']) < 10:
                        examples['violation'].append({'decision': d, 'problems': bad, 'time': tw.iso()})
        except Exception as ex:  # noqa: BLE001
            stats['crashes'] += 1
            if len(examples['crash']) < 10:
                examples['crash'].append({'reply': str(reply)[:500], 'error': f'{type(ex).__name__}: {ex}', 'trace': traceback.format_exc()[-1200:]})
        if verbose and (k + 1) % 1000 == 0:
            print(f'  {k + 1}/{n}: {stats["executed"]} executed, {stats["rejected"]} rejected, {stats["crashes"]} crashes, {stats["violations"]} violations', flush=True)
    stats['snapshots'] = len(snaps)
    stats['seconds'] = round(time.time() - t0, 1)
    stats['examples'] = examples
    return stats


def _fuzz_job(args):
    n, seed, verbose = args
    return fuzz(n=n, seed=seed, verbose=verbose)


def merge(reports):
    """Add up reports from several processes."""
    out = {'replies': 0, 'decisions_parsed': 0, 'schema_errors': 0, 'accepted': 0, 'warning': 0, 'rejected': 0, 'executed': 0,
           'skipped_at_execution': 0, 'crashes': 0, 'violations': 0, 'by_action': {}, 'snapshots': 0, 'seconds': 0.0,
           'examples': {'crash': [], 'violation': []}, 'processes': len(reports)}
    for r in reports:
        for k in ('replies', 'decisions_parsed', 'schema_errors', 'accepted', 'warning', 'rejected', 'executed', 'skipped_at_execution',
                  'crashes', 'violations'):
            out[k] += r[k]
        for act, v in r['by_action'].items():
            a = out['by_action'].setdefault(act, {'accepted': 0, 'warning': 0, 'rejected': 0})
            for kk in a:
                a[kk] += v[kk]
        out['snapshots'] = max(out['snapshots'], r['snapshots'])
        out['seconds'] = max(out['seconds'], r['seconds'])
        for kind in ('crash', 'violation'):
            out['examples'][kind] += r['examples'][kind][: max(0, 10 - len(out['examples'][kind]))]
    return out


def fuzz_parallel(n, seed, workers, verbose=False):
    from concurrent.futures import ProcessPoolExecutor
    shares = [n // workers + (1 if i < n % workers else 0) for i in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        reports = list(ex.map(_fuzz_job, [(k, seed + 1000 * i, verbose) for i, k in enumerate(shares)]))
    out = merge(reports)
    out['seeds'] = [seed + 1000 * i for i in range(workers)]
    return out
