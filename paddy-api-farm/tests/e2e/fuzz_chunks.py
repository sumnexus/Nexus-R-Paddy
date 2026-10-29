"""Guardrail fuzzing in chunks that each fit a time limit, merged into one report (the same report
`python -m paddy fuzz --workers N` gives: each chunk is one worker's share with that worker's seed).

    python tests/e2e/fuzz_chunks.py --n 12000 --chunks 4 --chunk 0      # … --chunk 1, 2, 3
    python tests/e2e/fuzz_chunks.py --n 12000 --chunks 4 --merge --out tests/results/fuzz.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, 'backend'))
os.environ.setdefault('PADDY_NO_DOTENV', '1')

from paddy import fuzz as F  # noqa: E402


def part_path(k):
    return os.path.join(ROOT, 'tests', 'results', f'fuzz_part{k}.json')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=12000)
    ap.add_argument('--chunks', type=int, default=4)
    ap.add_argument('--chunk', type=int, default=None)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--merge', action='store_true')
    ap.add_argument('--out', default=os.path.join(ROOT, 'tests', 'results', 'fuzz.json'))
    a = ap.parse_args()
    shares = [a.n // a.chunks + (1 if i < a.n % a.chunks else 0) for i in range(a.chunks)]
    if a.merge:
        reports = [json.load(open(part_path(k))) for k in range(a.chunks)]
        out = F.merge(reports)
        out['seeds'] = [a.seed + 1000 * k for k in range(a.chunks)]
        out['seconds_total'] = round(sum(r['seconds'] for r in reports), 1)
        print(json.dumps(out, indent=1))
        with open(a.out, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, indent=1, default=str)
        return 0 if out['violations'] == 0 and out['crashes'] == 0 else 1
    k = a.chunk
    rep = F.fuzz(n=shares[k], seed=a.seed + 1000 * k, verbose=True)
    with open(part_path(k), 'w', encoding='utf-8') as fh:
        json.dump(rep, fh, indent=1, default=str)
    print(f'chunk {k}: {rep["replies"]} replies, {rep["crashes"]} crashes, {rep["violations"]} violations, {rep["seconds"]} s')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
