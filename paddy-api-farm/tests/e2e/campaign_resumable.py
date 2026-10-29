"""The validation campaign in resumable chunks (each finished season is appended to a JSON-lines file, so a chunk cut
short by a time limit or a restart loses at most the seasons in flight), then written out as the same JSON that
`python -m paddy campaign` writes.

    python tests/e2e/campaign_resumable.py --managers builtin,conventional,builtin-cf,api-no-advice --seasons 200 \\
        --base-seed 7100000 --first-year 3001 --workers 2 --minutes 105 --parts tests/results/campaign200.parts.jsonl
    python tests/e2e/campaign_resumable.py ... --finish --out tests/results/campaign200.json

v1's campaign cases (seeds 7,100,000–7,100,199, weather years 3,001–3,200, kharif and rabi alternating, no events) are
run again with the final build only to compare the v1 and v2 farms season by season (definitions addendum §4).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
os.environ.setdefault('PADDY_NO_DOTENV', '1')

from paddy import __version__, runs  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--managers', default='builtin,conventional,builtin-cf,api-no-advice')
    ap.add_argument('--seasons', type=int, default=200)
    ap.add_argument('--base-seed', type=int, default=7100000)
    ap.add_argument('--first-year', type=int, default=3001)
    ap.add_argument('--workers', type=int, default=2)
    ap.add_argument('--minutes', type=float, default=105)
    ap.add_argument('--parts', required=True)
    ap.add_argument('--finish', action='store_true')
    ap.add_argument('--out', default=None)
    a = ap.parse_args()
    managers = a.managers.split(',')
    cs = runs.cases(a.seasons, base_seed=a.base_seed, first_year=a.first_year)
    done = {}
    if os.path.exists(a.parts):
        with open(a.parts, encoding='utf-8') as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue                      # a line cut off by a restart
                done[(r['case']['seed'], r['manager'])] = r
    if a.finish:
        res = [done[(c['seed'], m)] for c in cs for m in managers if (c['seed'], m) in done]
        missing = len(cs) * len(managers) - len(res)
        if missing:
            raise SystemExit(f'{missing} seasons still missing')
        S = runs.summarise(res)
        print(runs.table(S, managers))
        with open(a.out, 'w', encoding='utf-8') as fh:
            json.dump({'cases': cs, 'managers': managers, 'summary': S, 'results': res, 'version': __version__,
                       'elapsed_s': sum(r.get('wall_total_s') or 0 for r in res)}, fh, default=str)
        print(f'written to {a.out} ({len(res)} seasons)')
        return 0
    jobs = [(c, m, {'keep_history': False}) for c in cs for m in managers if (c['seed'], m) not in done]
    print(f'{len(jobs)} seasons to run ({len(done)} done) with {a.workers} workers, {a.minutes:g} min budget', flush=True)
    t0 = time.time()
    n = 0
    with ProcessPoolExecutor(max_workers=a.workers) as ex, open(a.parts, 'a', encoding='utf-8') as out:
        pending = set()
        it = iter(jobs)
        for _ in range(a.workers):
            j = next(it, None)
            if j is not None:
                pending.add(ex.submit(runs._run_one, j))
        while pending:
            fut = next(as_completed(pending))
            pending.remove(fut)
            r = fut.result()
            out.write(json.dumps(r, default=str) + '\n')
            out.flush()
            n += 1
            if n % 10 == 0:
                print(f'{n} done, {(time.time() - t0) / n:.1f} s per season (wall)', flush=True)
            if time.time() - t0 < a.minutes * 60:
                j = next(it, None)
                if j is not None:
                    pending.add(ex.submit(runs._run_one, j))
    print(f'{n} seasons written in {time.time() - t0:.0f} s; {len(jobs) - n} left', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
