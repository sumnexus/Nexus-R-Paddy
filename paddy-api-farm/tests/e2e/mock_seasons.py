"""End to end through HTTP: full seasons where every decision comes from the stand-in decision API over a real
socket, in auto mode and in approve mode (scripted approvals), with and without injected faults.

    python tests/e2e/mock_seasons.py --seasons 25        # 25 cases × 4 variants = 100 seasons (+25 built-in)

v2, the cases of set W in parts that each fit a time limit, then one report:

    python tests/e2e/mock_seasons.py --set W --start 0 --seasons 10 --out tests/results/e2e_mock_part0.json   # … --start 10, 20, …
    python tests/e2e/mock_seasons.py --merge tests/results/e2e_mock_part*.json --out tests/results/e2e_mock.json

Checks, per season: it finishes; failures end in a logged fallback; every unsafe suggestion is rejected; the key
never appears in a record; without faults, auto mode and approve-all mode make exactly the built-in manager's
decisions; and every rejection by the (scripted) farmer reaches the decision maker in the next call's state.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'backend'))

KEY = 'e2e-secret-key-0123456789abcdef'
FAULTS = dict(p429=0.04, p500=0.03, ptimeout=0.01, pmalformed=0.04, punsafe=0.08)


def cautious(rec, eng):
    """A careful farmer: no preventive sprays, no immature harvests, and every other scouting request turned down
    because they walk the field themselves."""
    msgs = ' '.join(rec['guardrail']['messages'])
    if rec['action'] == 'spray' and 'preventive' in msgs:
        return 'reject', 'no preventive sprays on my farm'
    if rec['action'] == 'harvest' and rec['params'].get('override_immature'):
        return 'reject', 'wait for the crop to ripen'
    if rec['action'] == 'scout' and int(rec['id'][1:]) % 2 == 0:
        return 'reject', 'I walk the field myself this morning'
    return 'approve', ''


def feedback_seen(e):
    """How many of the farmer's rejections reached the decision maker in the state of the next call."""
    calls = e.store.calls
    seen = 0
    rejected = [d for d in e.store.decisions if d['status'] == 'rejected_by_person']
    for d in rejected:
        nxt = next((c for c in calls if c['t'] > d['t'] and c.get('state')), None)
        if nxt is None:
            seen += 1          # the season ended first
            continue
        notes = ' '.join(r.get('note', '') for r in nxt['state'].get('recent_decisions') or [])
        seen += d['rejection_reason'] in notes
    return seen, len(rejected)


def one(job):
    case, variant, url = job
    from paddy.decisions.api_client import DecisionAPI
    from paddy.engine import Engine
    t0 = time.time()
    if variant == 'builtin':
        e = Engine(manager='builtin', keep_states=False, **case)
    else:
        faults = variant.endswith('faults')
        base = url['faults' if faults else 'clean']
        c = DecisionAPI(base_url=base, model='mock-builtin', key=KEY, timeout_s=1.5, backoff_base_s=0.02, retries=3, max_calls_per_real_hour=10 ** 6)
        mode = 'approve' if variant.startswith('approve') else 'auto'
        approver = None
        if mode == 'approve':
            approver = cautious if 'cautious' in variant else (lambda rec, eng: ('approve', ''))
        e = Engine(manager='api', client=c, mode=mode, approver=approver, keep_states=True, **case)
    e.run()
    s = e.summary()
    blob = json.dumps(e.store.calls, default=str) + json.dumps([e._public(d) for d in e.store.decisions], default=str)
    unsafe = [d for d in e.store.decisions if str(d.get('reason', '')).startswith('stand-in fault')]
    return {
        'case': case, 'variant': variant, 'finished': e.finished, 'yield_t_ha': s['yield_t_ha'], 'calls': s['calls'],
        'calls_failed': s['calls_failed'], 'fallbacks': s['fallbacks'], 'repaired': sum(1 for c in e.store.calls if c.get('repaired')),
        'retried': sum(1 for c in e.store.calls if (c.get('attempts') or 1) > 1 and c['ok']), 'decisions': s['decisions'],
        'status': s['decision_status'], 'unsafe': len(unsafe), 'unsafe_rejected': sum(1 for d in unsafe if d['status'] == 'rejected'),
        'rejected_by_person': s['decision_status'].get('rejected_by_person', 0), 'key_leaked': KEY in blob,
        'feedback_seen': feedback_seen(e)[0] if variant != 'builtin' else 0,
        'sources': sorted({c['source'] for c in e.store.calls}), 'digest': e.decision_digest(),
        'tokens_in': s['tokens_in'], 'tokens_out': s['tokens_out'], 'latency_ms_mean': s['latency_ms_mean'], 'wall_s': round(time.time() - t0, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seasons', type=int, default=25, help='cases per variant')
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--out', default=os.path.join(ROOT, 'tests', 'results', 'e2e_mock.json'))
    ap.add_argument('--set', default=None, choices=['W'], help='v2: the cases of set W (docs/DEFINITIONS-paddy-v2.md), with their events')
    ap.add_argument('--start', type=int, default=0, help='v2: first case of the set (to run a set in parts that each fit a time limit)')
    ap.add_argument('--merge', nargs='+', default=None, help='v2: combine the results of parts (JSON files written by --out) into one report')
    a = ap.parse_args()
    if a.merge:
        out, el = [], 0
        for f in a.merge:
            d = json.load(open(f))
            out += d['results']
            el += d['report'].get('elapsed_s', 0)
        return finish(out, el, a.out)
    from paddy.decisions.mock_api import MockConfig, MockDecisionServer
    from paddy.runs import cases
    clean = MockDecisionServer(MockConfig(seed=11, require_key=True)).start()
    faulty = MockDecisionServer(MockConfig(seed=12, require_key=True, timeout_s=2.0, **FAULTS)).start()
    url = {'clean': clean.url, 'faults': faulty.url}
    if a.set:
        from paddy import datasets as DS
        cs = []
        for i in range(a.start, a.start + a.seasons):
            c = DS.case(a.set, i)
            cs.append({'seed': c['seed'], 'weatherYear': c['weatherYear'], 'season': c['season'], 'events': c['events']})
    else:
        cs = cases(a.seasons, base_seed=7300000, first_year=5001)
    variants = ['builtin', 'auto', 'auto-faults', 'approve-all', 'approve-cautious-faults']
    jobs = [(c, v, url) for c in cs for v in variants]
    t0 = time.time()
    out = []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for k, r in enumerate(ex.map(one, jobs, chunksize=1)):
            out.append(r)
            if (k + 1) % 10 == 0:
                print(f'{k + 1}/{len(jobs)} {r["variant"]} {r["yield_t_ha"]:.2f} t/ha, {r["calls"]} calls, {r["wall_s"]} s', flush=True)
    clean.stop()
    faulty.stop()
    return finish(out, round(time.time() - t0), a.out)


def finish(out, elapsed_s, path):
    by = {}
    for r in out:
        by.setdefault(r['variant'], []).append(r)
    ref = {json.dumps(r['case'], sort_keys=True): r['digest'] for r in by['builtin']}
    rep = {'seasons_through_http': sum(len(v) for k, v in by.items() if k != 'builtin'), 'elapsed_s': elapsed_s, 'variants': {}}
    for v, rs in by.items():
        x = {'seasons': len(rs), 'finished': sum(r['finished'] for r in rs), 'mean_yield_t_ha': round(sum(r['yield_t_ha'] for r in rs) / len(rs), 3),
             'calls': sum(r['calls'] for r in rs), 'calls_failed': sum(r['calls_failed'] for r in rs), 'fallbacks': sum(r['fallbacks'] for r in rs),
             'repaired': sum(r['repaired'] for r in rs), 'retried_ok': sum(r['retried'] for r in rs), 'unsafe': sum(r['unsafe'] for r in rs),
             'unsafe_rejected': sum(r['unsafe_rejected'] for r in rs), 'rejected_by_person': sum(r['rejected_by_person'] for r in rs),
             'key_leaked': sum(r['key_leaked'] for r in rs), 'tokens_in': sum(r['tokens_in'] for r in rs), 'tokens_out': sum(r['tokens_out'] for r in rs),
             'feedback_seen': sum(r['feedback_seen'] for r in rs)}
        if v != 'builtin':
            x['identical_to_builtin'] = sum(1 for r in rs if ref.get(json.dumps(r['case'], sort_keys=True)) == r['digest'])
        rep['variants'][v] = x
    ok = all(x['finished'] == x['seasons'] for x in rep['variants'].values()) and all(x['key_leaked'] == 0 for x in rep['variants'].values()) \
        and all(x['unsafe'] == x['unsafe_rejected'] for x in rep['variants'].values()) \
        and rep['variants']['auto']['identical_to_builtin'] == rep['variants']['auto']['seasons'] \
        and rep['variants']['approve-all']['identical_to_builtin'] == rep['variants']['approve-all']['seasons'] \
        and rep['variants']['approve-cautious-faults']['rejected_by_person'] > 0 \
        and rep['variants']['approve-cautious-faults']['feedback_seen'] == rep['variants']['approve-cautious-faults']['rejected_by_person']
    rep['all_checks_passed'] = bool(ok)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'report': rep, 'results': out}, fh, indent=1, default=str)
    print(json.dumps(rep, indent=1))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
