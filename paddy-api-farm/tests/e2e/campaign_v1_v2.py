"""The v1 farm against the v2 farm on v1's own campaign cases (seeds 7,100,000–7,100,199, weather years 3,001–3,200,
no events), season by season: the same case under the same decision maker, v1's recorded result against v2's.

    python tests/e2e/campaign_v1_v2.py tests/results/v1/campaign200.json tests/results/campaign200_*.json \\
        --md tests/results/campaign_v1_v2.md

Paired mean differences (v2 − v1) with 95 % intervals (t) per decision maker, pooled and per season. These cases are
never used to train, select or score models (definitions addendum §4).
"""
from __future__ import annotations

import argparse
import json
import math

KEYS = [('yield_t_ha', 'Yield, t/ha', 3), ('irrigation_mm', 'Irrigation, mm', 0), ('pump_kwh', 'Pump energy, kWh', 2),
        ('n_kg_ha', 'Nitrogen, kg N/ha', 1), ('ai_g_ha', 'Insecticide, g a.i./ha', 1), ('bio_kg_ha', 'Bt, kg/ha', 2),
        ('sprayed_squares', 'Area sprayed, m²', 1), ('ch4_kg_ha', 'Methane, kg CH₄/ha', 1), ('rejected', 'Guardrail rejections', 2)]
T975 = {1: 12.71, 2: 4.30, 3: 3.18, 4: 2.78, 5: 2.57, 10: 2.23, 20: 2.09, 30: 2.04, 60: 2.00, 120: 1.98}


def t975(df):
    for k in sorted(T975):
        if df <= k:
            return T975[k]
    return 1.96


def val(r, k):
    if k == 'rejected':
        return float((r.get('verdicts') or {}).get('rejected', 0))
    v = r.get(k)
    return None if v is None else float(v)


def load(paths):
    out = {}
    for p in paths:
        for r in json.load(open(p))['results']:
            out[(r['case']['seed'], r['manager'])] = r
    return out


def paired(A, B, keys, k):
    d = [(val(B[x], k), val(A[x], k)) for x in keys]
    d = [b - a for b, a in d if a is not None and b is not None]
    n = len(d)
    if n < 2:
        return None
    m = sum(d) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in d) / (n - 1))
    h = t975(n - 1) * sd / math.sqrt(n)
    return n, m, m - h, m + h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('v1')
    ap.add_argument('v2', nargs='+')
    ap.add_argument('--md', default=None)
    a = ap.parse_args()
    A, B = load([a.v1]), load(a.v2)
    common = sorted(set(A) & set(B))
    managers = sorted({m for _, m in common}, key=['builtin', 'conventional', 'builtin-cf', 'api-no-advice'].index)
    L = [f'### v1 farm → v2 farm on v1\'s campaign cases ({len(common)} paired seasons)', '']
    for m in managers:
        ks = [k for k in common if k[1] == m]
        ma = lambda k: sum(val(A[x], k) or 0 for x in ks) / len(ks)
        mb = lambda k: sum(val(B[x], k) or 0 for x in ks) / len(ks)
        L += [f'**{m}** ({len(ks)} seasons; same decisions as v1 in {sum(A[x]["decision_digest"] == B[x]["decision_digest"] for x in ks)})', '',
              '| Mean per season | v1 | v2 | v2 − v1 (95 % interval) | kharif v2 − v1 | rabi v2 − v1 |', '|---|---:|---:|---:|---:|---:|']
        for k, lab, nd in KEYS:
            r = paired(A, B, ks, k)
            if not r:
                continue
            seas = []
            for s in ('kharif', 'rabi'):
                q = paired(A, B, [x for x in ks if A[x]['case']['season'] == s], k)
                seas.append('–' if not q else f'{q[1]:+.{nd}f} ({q[2]:+.{nd}f} … {q[3]:+.{nd}f})')
            L.append(f'| {lab} | {ma(k):,.{nd}f} | {mb(k):,.{nd}f} | {r[1]:+.{nd}f} ({r[2]:+.{nd}f} … {r[3]:+.{nd}f}) | {seas[0]} | {seas[1]} |')
        L.append('')
    txt = '\n'.join(L)
    print(txt)
    if a.md:
        with open(a.md, 'w') as fh:
            fh.write(txt + '\n')


if __name__ == '__main__':
    main()
