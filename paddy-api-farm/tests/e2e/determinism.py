"""Determinism and exact replay.

    python tests/e2e/determinism.py            # → tests/results/determinism.json

1. The same case run twice, in separate processes, gives the same decisions and the same farm, for every
   decision maker that doesn't depend on a real model (built-in, conventional, the API through the stand-in).
2. The API through the stand-in makes exactly the built-in manager's decisions, and the farm follows exactly the
   same course (same decision and history fingerprints).
3. A recorded run replays exactly from its recorded replies, including one recorded against a stand-in that
   injects faults (rate limits, server errors, timeouts, malformed and unsafe replies), so retries, repair requests
   and fallbacks are part of what is replayed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BACKEND = os.path.join(ROOT, 'backend')
sys.path.insert(0, BACKEND)

CASES = [{'seed': 20261024, 'weatherYear': 0, 'season': 'kharif'}, {'seed': 7400011, 'weatherYear': 6001, 'season': 'rabi'},
         {'seed': 7400012, 'weatherYear': 6002, 'season': 'kharif'}]

CHILD = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from paddy.engine import Engine
from paddy.decisions.api_client import DecisionAPI
from paddy.decisions.mock_api import MockConfig, MockDecisionServer
case = json.loads(sys.argv[2]); mgr = sys.argv[3]; faults = sys.argv[4] if len(sys.argv) > 4 else ''; rec = sys.argv[5] if len(sys.argv) > 5 else None
srv = None
client = None
if mgr == 'api':
    srv = MockDecisionServer(MockConfig.parse(faults, seed=5, timeout_s=1.5)).start()
    client = DecisionAPI(base_url=srv.url, model='mock-builtin', key='k-determinism', timeout_s=1.0, backoff_base_s=0.01, retries=3,
                         max_calls_per_real_hour=10 ** 6, fallback='builtin')
e = Engine(manager=mgr, client=client, keep_states=False, record_dir=rec, **case)
e.run()
s = e.summary()
if rec:
    e.store.write_json('summary.json', s)
    e.store.write_json('config.json', {'cfg': e.cfg})
if srv:
    srv.stop()
print(json.dumps({k: s[k] for k in ('yield_t_ha', 'irrigation_mm', 'decisions', 'calls', 'calls_failed', 'fallbacks', 'decision_digest', 'history_digest')}))
'''


def child(case, mgr, faults='', rec=None):
    args = [sys.executable, '-c', CHILD, BACKEND, json.dumps(case), mgr, faults] + ([rec] if rec else [])
    out = subprocess.run(args, capture_output=True, text=True, timeout=900)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-2000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


def main():
    t0 = time.time()
    rep = {'repeat': [], 'api_equals_builtin': [], 'replay': []}
    ok = True
    for case in CASES:
        runs = {}
        for mgr in ('builtin', 'conventional', 'api'):
            a, b = child(case, mgr), child(case, mgr)
            same = a['decision_digest'] == b['decision_digest'] and a['history_digest'] == b['history_digest']
            ok &= same
            runs[mgr] = a
            rep['repeat'].append({'case': case, 'manager': mgr, 'identical': same, 'decision_digest': a['decision_digest'][:16],
                                  'history_digest': a['history_digest'][:16], 'yield_t_ha': a['yield_t_ha'], 'decisions': a['decisions']})
            print(f"repeat  {case['season']:<6} {case['seed']:<9} {mgr:<12} {'identical' if same else 'DIFFERENT'}  {a['decision_digest'][:12]} {a['history_digest'][:12]}", flush=True)
        eq = runs['api']['decision_digest'] == runs['builtin']['decision_digest'] and runs['api']['history_digest'] == runs['builtin']['history_digest']
        ok &= eq
        rep['api_equals_builtin'].append({'case': case, 'identical': eq})
        print(f"api(stand-in) = built-in  {case['season']:<6} {case['seed']:<9} {'yes' if eq else 'NO'}", flush=True)
    from paddy.decisions.api_client import ReplayClient
    from paddy.engine import Engine
    for case, faults in ((CASES[0], ''), (CASES[1], 'p429=0.06,p500=0.05,ptimeout=0.02,pmalformed=0.06,punsafe=0.1')):
        with tempfile.TemporaryDirectory() as d:
            rec = os.path.join(d, 'run')
            a = child(case, 'api', faults, rec)
            calls = [json.loads(line) for line in open(os.path.join(rec, 'calls.jsonl'), encoding='utf-8')]
            e = Engine(manager='api', client=ReplayClient(calls, fallback='builtin'), keep_states=False, **case)
            e.run()
            s = e.summary()
            same = s['decision_digest'] == a['decision_digest'] and s['history_digest'] == a['history_digest']
            ok &= same
            rep['replay'].append({'case': case, 'faults': faults or None, 'calls': a['calls'], 'calls_failed': a['calls_failed'],
                                  'fallbacks': a['fallbacks'], 'retried_or_repaired': sum(1 for c in calls if (c.get('attempts') or 1) > 1 or c.get('repaired')),
                                  'identical': same})
            print(f"replay  {case['season']:<6} {case['seed']:<9} faults={'yes' if faults else 'no '} calls {a['calls']}, failed {a['calls_failed']}, "
                  f"fallbacks {a['fallbacks']}: {'identical' if same else 'DIFFERENT'}", flush=True)
    rep['all_identical'] = bool(ok)
    rep['seconds'] = round(time.time() - t0)
    out = os.path.join(ROOT, 'tests', 'results', 'determinism.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(rep, fh, indent=1)
    print('ALL IDENTICAL' if ok else 'DIFFERENCES FOUND', f'({rep["seconds"]} s)')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
