"""Many seasons at once: campaigns, manager comparisons and their statistics.

A campaign runs the same list of (seed, weather year, season) cases under each manager, so every comparison is
paired: the same fields, soils and weather, only the decisions differ. Seasons run in parallel processes.
"""
from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

from . import config as CFG
from .sim.audit import wilson


def cases(n, base_seed=7000, seasons=('kharif', 'rabi'), first_year=1):
    """n cases: new seed and weather year each, seasons alternating."""
    out = []
    for i in range(n):
        out.append({'seed': base_seed + i, 'weatherYear': first_year + i, 'season': seasons[i % len(seasons)]})
    return out


def _run_one(args):
    case, manager, extra = args
    from .engine import Engine
    kw = dict(case)
    kw.update(extra or {})
    client = None
    if manager in ('api', 'api-no-advice'):
        from .decisions.api_client import DecisionAPI
        if 'client_kw' in kw:
            client = DecisionAPI(**kw.pop('client_kw'))
        else:                      # no API configured for this run: the stand-in, in this process (as the data sets do)
            from .datasets import make_client
            client = make_client()
    t0 = time.time()
    mname = manager
    if manager == 'builtin-cf':          # the AWD ablation: the built-in manager with continuous flooding
        mname = 'builtin'
        kw['builtin_water'] = 'continuous'
    e = Engine(manager=mname, client=client, keep_states=False,
               **{k: v for k, v in kw.items() if k in ('seed', 'weatherYear', 'season', 'mode', 'forecastMode', 'builtin_water')})
    e.run()
    s = e.summary()
    s['case'] = case
    s['manager'] = manager
    s['wall_total_s'] = round(time.time() - t0, 2)
    s['digest'] = e.decision_digest()
    s['history'] = e.history if (extra or {}).get('keep_history') else None
    return s


def run_cases(case_list, managers, workers=None, extra=None, progress=None):
    workers = workers or max(1, (os.cpu_count() or 2))
    jobs = [(c, m, extra) for c in case_list for m in managers]
    out = []
    if workers <= 1:
        for j in jobs:
            out.append(_run_one(j))
            if progress:
                progress(len(out), len(jobs), out[-1])
        return out
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(_run_one, jobs, chunksize=1):
            out.append(r)
            if progress:
                progress(len(out), len(jobs), r)
    return out


def _mean_ci(xs):
    xs = np.asarray([x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))], float)
    if not len(xs):
        return None
    m = float(np.mean(xs))
    if len(xs) < 2:
        return {'mean': m, 'n': 1}
    se = float(np.std(xs, ddof=1) / math.sqrt(len(xs)))
    return {'mean': m, 'lo': m - 1.96 * se, 'hi': m + 1.96 * se, 'sd': float(np.std(xs, ddof=1)), 'n': int(len(xs)),
            'p10': float(np.percentile(xs, 10)), 'p90': float(np.percentile(xs, 90))}


METRICS = ('yield_t_ha', 'irrigation_mm', 'pump_kwh', 'n_kg_ha', 'ai_g_ha', 'bio_kg_ha', 'sprayed_squares', 'ch4_kg_ha', 'calls', 'tokens_in', 'tokens_out')


def summarise(results):
    """Per manager: means with 95 % CIs, pooled forest checks, guardrail verdicts; per season type as well."""
    by = {}
    for r in results:
        by.setdefault(r['manager'], []).append(r)
    out = {}
    for m, rs in by.items():
        s = {'seasons': len(rs)}
        for k in METRICS:
            s[k] = _mean_ci([r.get(k) for r in rs])
        for season in ('kharif', 'rabi'):
            sub = [r for r in rs if r['case']['season'] == season]
            if sub:
                s[season] = {k: _mean_ci([r.get(k) for r in sub]) for k in ('yield_t_ha', 'irrigation_mm', 'ch4_kg_ha', 'n_kg_ha', 'ai_g_ha')}
                s[season]['seasons'] = len(sub)
        wp = [r['harvest_kg'] / r['irrigation_m3'] for r in rs if r.get('irrigation_m3')]
        s['water_productivity_kg_m3'] = _mean_ci(wp)
        aud = {}
        for r in rs:
            for k, v in (r.get('audit') or {}).items():
                if not v or not v.get('n'):
                    continue
                a = aud.setdefault(k, [0, 0])
                a[0] += round(v['acc'] * v['n'])
                a[1] += v['n']
        s['audit'] = {k: {'acc': h / n, 'n': n, 'ci': wilson(h, n)} for k, (h, n) in aud.items() if n}
        if 'pestPos' in s['audit'] and 'pestNeg' in s['audit']:
            s['audit']['pest']['balanced'] = (s['audit']['pestPos']['acc'] + s['audit']['pestNeg']['acc']) / 2
        # per-season-farm audit pass rates (the "farm-year" view of the original project)
        per = {}
        for r in rs:
            for k, v in (r.get('audit') or {}).items():
                if v and v.get('n', 0) >= 20:
                    per.setdefault(k, []).append(v['acc'])
        s['audit_seasons_at_95'] = {k: {'share': float(np.mean([a >= 0.95 for a in v])), 'seasons': len(v)} for k, v in per.items()}
        vc = {}
        for r in rs:
            for k, v in (r.get('verdicts') or {}).items():
                vc[k] = vc.get(k, 0) + v
        s['verdicts'] = vc
        s['forced_harvests'] = sum(1 for r in rs for f in r['fields'].values() if f.get('forced_harvest'))
        s['abandoned_fields'] = sum(1 for r in rs for f in r['fields'].values() if f.get('abandoned'))
        s['wall_s_per_season'] = _mean_ci([r.get('wall_total_s') for r in rs])
        out[m] = s
    return out


def paired(results, a, b, metric):
    """Mean of (a − b) over cases run under both managers, with a 95 % CI, and the relative change."""
    ra = {json.dumps(r['case'], sort_keys=True): r for r in results if r['manager'] == a}
    rb = {json.dumps(r['case'], sort_keys=True): r for r in results if r['manager'] == b}
    keys = sorted(set(ra) & set(rb))
    d = [ra[k][metric] - rb[k][metric] for k in keys if ra[k].get(metric) is not None and rb[k].get(metric) is not None]
    base = [rb[k][metric] for k in keys if rb[k].get(metric) is not None]
    out = _mean_ci(d) or {}
    if base and np.mean(base):
        out['relative'] = float(np.mean(d) / np.mean(base))
        se = out.get('sd', 0) / math.sqrt(max(1, len(d)))
        out['relative_lo'] = float((np.mean(d) - 1.96 * se) / np.mean(base))
        out['relative_hi'] = float((np.mean(d) + 1.96 * se) / np.mean(base))
    out['pairs'] = len(d)
    return out


def table(summary, managers=None):
    """A plain-text table of the main results."""
    managers = managers or list(summary)
    rows = [('', *managers)]

    def f(m, k, nd=2):
        x = summary[m].get(k)
        if not x:
            return '–'
        return f"{x['mean']:.{nd}f} ± {(x['hi'] - x['mean']) if 'hi' in x else 0:.{nd}f}"
    for k, nd, lab in (('yield_t_ha', 2, 'yield t/ha'), ('irrigation_mm', 0, 'irrigation mm'), ('pump_kwh', 1, 'pump kWh'), ('n_kg_ha', 0, 'N kg/ha'),
                       ('ai_g_ha', 1, 'pesticide a.i. g/ha'), ('bio_kg_ha', 2, 'Bt kg/ha'), ('sprayed_squares', 0, 'sprayed m²'), ('ch4_kg_ha', 0, 'CH4 kg/ha'), ('calls', 0, 'calls'),
                       ('tokens_in', 0, 'tokens in'), ('tokens_out', 0, 'tokens out')):
        rows.append((lab, *[f(m, k, nd) for m in managers]))
    w = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    return '\n'.join('  '.join(str(c).ljust(w[i]) for i, c in enumerate(r)) for r in rows)


# Measured with the stand-in over whole seasons (tests/results): about 290–300 calls a season (two scheduled calls a
# day plus triggers; 300 here to be safe). v2: the fixed instructions ≈ 2,700 tokens (paddy-prompt/1.6, 10,800
# characters ÷ 4) and the state with its message ≈ 2,650 (about 8,600 characters of JSON, escaped inside the
# request); the stand-in counted 5,200–5,350 input tokens a call (v1: 2,400 + 2,200, about 4,600 counted). Output
# tokens are an assumption for a real model: a few decisions per call with a short reason each.
CALLS_PER_SEASON = 300
PROMPT_TOKENS = 2700
STATE_TOKENS = 2650
TOKENS_OUT_PER_CALL = 300


def cost_forecast(api_seasons, price_in_per_m, price_out_per_m):
    """What a number of API seasons would cost, before running any."""
    calls = api_seasons * CALLS_PER_SEASON
    tin = PROMPT_TOKENS + STATE_TOKENS
    return {'calls_per_season': CALLS_PER_SEASON, 'calls': calls, 'prompt_tokens': PROMPT_TOKENS, 'state_tokens': STATE_TOKENS,
            'tokens_in_per_call': tin, 'tokens_out_per_call': TOKENS_OUT_PER_CALL, 'tokens_in': calls * tin, 'tokens_out': calls * TOKENS_OUT_PER_CALL,
            'usd': calls * (tin * price_in_per_m + TOKENS_OUT_PER_CALL * price_out_per_m) / 1e6}


def cost_estimate(summary_api, price_in_per_m, price_out_per_m):
    tin = summary_api['tokens_in']['mean'] if summary_api.get('tokens_in') else 0
    tout = summary_api['tokens_out']['mean'] if summary_api.get('tokens_out') else 0
    return {'tokens_in_per_season': tin, 'tokens_out_per_season': tout,
            'usd_per_season': tin / 1e6 * price_in_per_m + tout / 1e6 * price_out_per_m,
            'price_in_per_m': price_in_per_m, 'price_out_per_m': price_out_per_m}


__all__ = ['cases', 'run_cases', 'summarise', 'paired', 'table', 'cost_estimate', 'cost_forecast', 'CFG']
