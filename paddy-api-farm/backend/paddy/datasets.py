"""The v2 data sets: which seasons make up the training, test, live and A/B sets, and a resumable batch runner.

The sets are defined in docs/DEFINITIONS-paddy-v2.md (section 4), written before any v2 result:

    G  training            seed 8,100,000 + i, weather year 10,001 + i
    T  test                seed 8,200,000 + i, weather year 20,001 + i   (200 cases × 4 decision makers)
    L  live                seed 8,300,000 + i, weather year 30,001 + i   (500 cases × 4, run once after the freeze)
    H  A/B checks          seed 8,400,000 + i, weather year 40,001 + i
    W  HTTP seasons        seed 8,500,000 + i, weather year 45,001 + i

Case i is kharif for even i, rabi for odd i; with probability ½ (a hash of its seed) it has one to three events.

    python -m paddy batch --set G --start 0 --stop 4000 --workers 2 --minutes 100

writes one JSON summary and one .npz of feature rows per season under runs/datasets/<set>/<decision maker>/, skips
seasons already written (so a run killed by a restart just carries on), and stops taking new seasons when its time is up.
"""
from __future__ import annotations

import gzip
import json
import os
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

import numpy as np

from .core.util import hash01

SETS = {'G': (8_100_000, 10_001), 'T': (8_200_000, 20_001), 'L': (8_300_000, 30_001), 'H': (8_400_000, 40_001), 'W': (8_500_000, 45_001)}
SET_SIZE = {'G': 10_000, 'T': 200, 'L': 500, 'H': 2_000, 'W': 500}
MANAGERS4 = ('builtin', 'conventional', 'api-no-advice', 'builtin-cf')
MANAGER_LABEL = {'builtin': 'Built-in (AWD)', 'conventional': 'Conventional', 'api-no-advice': 'API without advice (stand-in)',
                 'builtin-cf': 'Built-in, continuous flooding'}
CLIMATE_YEARS = (90_001, 92_000)        # set K: weather only (the planned-spray ceiling's climatology)
# every v1 run (seeds, weather years), from the v1 records: they must never meet the v2 sets
V1_RUNS = [((7_100_000, 7_100_199), (3_001, 3_200)), ((7_300_000, 7_300_024), (5_001, 5_025)), ((5_100, 5_109), (101, 110)),
           ((7_400_011, 7_400_012), (6_001, 6_002)), ((20_261_024, 20_261_024), (0, 0))]
EVENT_KINDS = ('pest', 'storm', 'dry', 'cloudy')
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'runs', 'datasets')


def draw_events(seed):
    """Half the cases get one to three events (pest outbreak, heavy storm, dry spell, overcast week)."""
    u = lambda *k: hash01(seed, 'v2-events', *k)    # noqa: E731
    if u('any') >= 0.5:
        return []
    n = 1 + int(u('n') * 3) % 3
    out = []
    for k in range(n):
        kind = EVENT_KINDS[int(u('kind', k) * 4) % 4]
        lo, hi = (30, 100) if kind == 'pest' else (10, 120)
        ev = {'kind': kind, 'day': lo + int(u('day', k) * (hi - lo + 1)) % (hi - lo + 1), 'hour': 8}
        if kind == 'pest':
            ev['field'] = 'AB'[int(u('field', k) * 2) % 2]
        out.append(ev)
    return sorted(out, key=lambda e: (e['day'], e['kind']))


def case(set_name, i):
    s0, y0 = SETS[set_name]
    seed = s0 + i
    return {'set': set_name, 'i': int(i), 'seed': seed, 'weatherYear': y0 + i, 'season': 'kharif' if i % 2 == 0 else 'rabi',
            'events': draw_events(seed)}


def managers_for(set_name, i):
    """T, L, H, W: every case under all four decision makers (paired). G: one decision maker per case, in turn."""
    if set_name == 'G':
        return (MANAGERS4[i % 4],)
    return MANAGERS4


def overlap_report(sizes=None):
    """Shared seeds or weather years between any two sets, and between any set and any v1 run (must all be 0)."""
    sizes = dict(SET_SIZE, **(sizes or {}))
    spans = {k: ((s0, s0 + sizes[k] - 1), (y0, y0 + sizes[k] - 1)) for k, (s0, y0) in SETS.items()}
    spans['K (climate years)'] = ((-1, -2), CLIMATE_YEARS)
    for j, (sr, yr) in enumerate(V1_RUNS):
        spans[f'v1 run {j + 1}'] = (sr, yr)
    ov = lambda a, b: max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)    # noqa: E731
    names = list(spans)
    out = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            na, nb = names[a], names[b]
            if na.startswith('v1') and nb.startswith('v1'):
                continue
            out.append({'a': na, 'b': nb, 'shared_seeds': ov(spans[na][0], spans[nb][0]), 'shared_years': ov(spans[na][1], spans[nb][1])})
    return {'spans': {k: {'seeds': v[0], 'weather_years': v[1]} for k, v in spans.items()}, 'pairs': out,
            'total_shared': sum(p['shared_seeds'] + p['shared_years'] for p in out)}


# ── running one season ──
def make_client():
    """The API decision maker through the stand-in: the real client (prompt, adapter, parsing, validation, repair) with a
    transport that hands each request to the stand-in in this process."""
    from .decisions.api_client import DecisionAPI
    from .decisions.mock_api import InProcessStandIn
    sb = InProcessStandIn()
    return DecisionAPI(base_url=sb.url, model='mock', transport=sb, retries=2, backoff_base_s=0.01, max_calls_per_real_hour=10 ** 9)


def run_season(c, manager, rows=True, suite=None, extra=None, models=None):
    """One season of case c under one decision maker → (summary, rows). ``models``: see engine.model_suite."""
    from .engine import Engine
    # the data sets say which forests they ran: no pack named = the v1 forests (the "before" and the training set)
    kw = dict(seed=c['seed'], weatherYear=c['weatherYear'], season=c['season'], keep_states=False, events=c.get('events'), rows=rows,
              models=models or 'v1')
    kw.update(extra or {})
    m = manager
    if manager == 'builtin-cf':
        m = 'builtin'
        kw['builtin_water'] = 'continuous'
    client = make_client() if manager in ('api', 'api-no-advice') else None
    t0 = time.time()
    e = Engine(manager=m, client=client, **kw)
    if suite is not None:
        e.adv.m = suite
    e.run()
    s = e.summary()
    s['case'] = {k: c[k] for k in ('set', 'i', 'seed', 'weatherYear', 'season', 'events')}
    s['manager'] = manager
    s['models'] = models or 'v1'
    s['wall_total_s'] = round(time.time() - t0, 2)
    s['events_fired'] = [ev for ev in e.tw.events_log]
    return s, (e.audit.rows if rows else None)


def _pack_rows(rows):
    out = {}
    for k, lst in (rows or {}).items():
        if not lst:
            continue
        X = np.array([r[0] for r in lst], np.float32)
        y = np.array([r[1] for r in lst], np.float32)
        ne = max(len(r[2]) for r in lst)
        E = np.full((len(lst), ne), np.nan, np.float32)
        for j, r in enumerate(lst):
            E[j, :len(r[2])] = r[2]
        out[f'X_{k}'] = X
        out[f'y_{k}'] = y
        out[f'e_{k}'] = E
    return out


def season_path(out, set_name, manager, i):
    return os.path.join(out, set_name, manager, f'{i:05d}')


def _job(args):
    set_name, i, manager, out, rows, models_path, extra = args
    base = season_path(out, set_name, manager, i)
    if os.path.exists(base + '.json.gz'):
        return 'skip', set_name, i, manager, 0.0
    os.makedirs(os.path.dirname(base), exist_ok=True)
    t0 = time.time()
    try:
        s, R = run_season(case(set_name, i), manager, rows=rows, extra=extra, models=models_path)
    except Exception as ex:  # noqa: BLE001
        with open(base + '.error.txt', 'w') as fh:
            fh.write(traceback.format_exc())
        return 'error', set_name, i, manager, time.time() - t0
    if rows and R:
        tmp = base + '.tmp.npz'
        np.savez_compressed(tmp, **_pack_rows(R))
        os.replace(tmp, base + '.npz')
    tmp = base + '.tmp.json.gz'
    with gzip.open(tmp, 'wt', encoding='utf-8') as fh:
        json.dump(s, fh, default=str)
    os.replace(tmp, base + '.json.gz')       # the summary is written last: its presence means the season is complete
    return 'done', set_name, i, manager, time.time() - t0


def run_batch(set_name, start, stop, workers=2, minutes=100.0, out=DEFAULT_OUT, rows=True, managers=None, models_path=None,
              extra=None, log=print):
    """Run cases [start, stop) of a set; resumable; stops taking new seasons after ``minutes``."""
    jobs = []
    for i in range(start, stop):
        for m in (managers or managers_for(set_name, i)):
            if not os.path.exists(season_path(out, set_name, m, i) + '.json.gz'):
                jobs.append((set_name, i, m, out, rows, models_path, extra))
    t_end = time.time() + minutes * 60
    done = err = 0
    t0 = time.time()
    log(f'{set_name}[{start}:{stop}]: {len(jobs)} seasons to run with {workers} workers, {minutes:g} min budget')
    if not jobs:
        return 0, 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        it = iter(jobs)
        pending = set()
        for _ in range(workers):
            j = next(it, None)
            if j is not None:
                pending.add(ex.submit(_job, j))
        while pending:
            fin, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in fin:
                st, sn, i, m, dt = fut.result()
                if st == 'done':
                    done += 1
                elif st == 'error':
                    err += 1
                    log(f'  error: {sn} {i} {m}')
                if st != 'skip' and (done + err) % 10 == 0:
                    rate = (time.time() - t0) / max(1, done + err)
                    log(f'  {done} done, {err} errors, {rate:.1f} s per season (wall), {len(jobs) - done - err} left')
                if time.time() < t_end:
                    j = next(it, None)
                    if j is not None:
                        pending.add(ex.submit(_job, j))
    log(f'{set_name}: {done} seasons written, {err} errors, {time.time() - t0:.0f} s')
    return done, err


def load_rows(out, set_name, model, managers=None, idx=None):
    """Concatenate one model's rows from a set: X, y, extra, and per row (case i, decision maker, season)."""
    Xs, ys, Es, meta = [], [], [], []
    root = os.path.join(out, set_name)
    for m in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if managers and m not in managers:
            continue
        d = os.path.join(root, m)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.npz') or fn.endswith('.tmp.npz'):
                continue
            i = int(fn[:5])
            if idx is not None and i not in idx:
                continue
            if not os.path.exists(os.path.join(d, fn[:5] + '.json.gz')):
                continue
            z = np.load(os.path.join(d, fn))
            if f'X_{model}' not in z:
                continue
            X, y, E = z[f'X_{model}'], z[f'y_{model}'], z[f'e_{model}']
            Xs.append(X)
            ys.append(y)
            Es.append(E)
            meta += [(i, m, i % 2)] * len(y)
    if not Xs:
        return None
    ne = max(E.shape[1] for E in Es)
    Es = [np.pad(E, ((0, 0), (0, ne - E.shape[1])), constant_values=np.nan) for E in Es]
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(Es), np.array(meta, dtype=object)


def load_summaries(out, set_name, managers=None):
    res = []
    root = os.path.join(out, set_name)
    for m in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if managers and m not in managers:
            continue
        d = os.path.join(root, m)
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.json.gz') and not fn.endswith('.tmp.json.gz'):
                with gzip.open(os.path.join(d, fn), 'rt', encoding='utf-8') as fh:
                    res.append(json.load(fh))
    return res
