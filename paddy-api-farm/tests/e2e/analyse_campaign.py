"""Turn a campaign JSON (python -m paddy campaign … --out x.json) into the README's result tables.

    python tests/e2e/analyse_campaign.py tests/results/campaign200.json > tests/results/campaign200_summary.md
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(HERE)), 'backend'))

from paddy import runs  # noqa: E402

LABEL = {'builtin': 'Built-in (AWD)', 'builtin-cf': 'Built-in, continuous flooding', 'conventional': 'Conventional', 'api': 'API', 'api-no-advice': 'API, no advice'}
# Karnataka rice yield, RBI Handbook of Statistics on Indian States (Table 85), kg of rice per ha; ÷ 0.67 out-turn → paddy
KARNATAKA_RICE = {'2018-19': 3012, '2019-20': 3067, '2020-21': 3072, '2021-22': 3089, '2022-23': 3078}
OUTTURN = 0.67


def f(x, nd=2):
    return '–' if x is None else f'{x:,.{nd}f}'


def ci(x, nd=2):
    if not x:
        return '–'
    if 'hi' not in x:
        return f(x['mean'], nd)
    return f"{f(x['mean'], nd)} ± {f(x['hi'] - x['mean'], nd)}"


def main(path):
    d = json.load(open(path))
    res = d['results']
    S = runs.summarise(res)
    ms = [m for m in ('builtin', 'conventional', 'builtin-cf') if m in S] + [m for m in S if m not in ('builtin', 'conventional', 'builtin-cf')]
    out = []
    n = {m: S[m]['seasons'] for m in ms}
    out.append(f"### Campaign: {', '.join(f'{LABEL.get(m, m)} {n[m]}' for m in ms)} seasons\n")
    out.append('Every manager ran the same cases (new field seed and weather year each, kharif and rabi alternating). Means per season with 95% '
               'confidence intervals.\n')
    rows = [('Yield, t/ha (paddy)', 'yield_t_ha', 2), ('Irrigation, mm', 'irrigation_mm', 0), ('Pumping energy, kWh (72 m²)', 'pump_kwh', 1),
            ('Nitrogen, kg N/ha', 'n_kg_ha', 0), ('Insecticide, g a.i./ha (chemical and botanical)', 'ai_g_ha', 1),
            ('Bt (microbial), kg of product/ha', 'bio_kg_ha', 2), ('Area sprayed, m² of 72 (all sprays added up)', 'sprayed_squares', 0),
            ('Methane, kg CH₄/ha', 'ch4_kg_ha', 0), ('Decision calls', 'calls', 0)]
    out.append('| | ' + ' | '.join(LABEL.get(m, m) for m in ms) + ' |')
    out.append('|---|' + '---:|' * len(ms))
    for lab, k, nd in rows:
        out.append(f'| {lab} | ' + ' | '.join(ci(S[m].get(k), nd) for m in ms) + ' |')
    for season in ('kharif', 'rabi'):
        out.append(f'| Yield, {season} | ' + ' | '.join(ci(S[m].get(season, {}).get('yield_t_ha'), 2) for m in ms) + ' |')
        out.append(f'| Irrigation, {season}, mm | ' + ' | '.join(ci(S[m].get(season, {}).get('irrigation_mm'), 0) for m in ms) + ' |')
    out.append(f'| Water productivity, kg grain per m³ pumped | ' + ' | '.join(ci(S[m].get('water_productivity_kg_m3'), 2) for m in ms) + ' |')
    out.append('')
    out.append('**Paired differences** (same cases; mean difference, 95% interval, relative to the second manager):\n')
    out.append('| | Yield | Irrigation | Nitrogen | Insecticide a.i. | Methane |')
    out.append('|---|---:|---:|---:|---:|---:|')
    pairs = [('builtin', 'conventional'), ('builtin', 'builtin-cf'), ('builtin-cf', 'conventional')]
    for a, b in pairs:
        if a not in S or b not in S:
            continue
        cells = []
        for k, nd in (('yield_t_ha', 2), ('irrigation_mm', 0), ('n_kg_ha', 1), ('ai_g_ha', 1), ('ch4_kg_ha', 1)):
            p = runs.paired(res, a, b, k)
            rel = f" ({p['relative'] * 100:+.1f}%)" if p.get('relative') is not None else ''
            cells.append(f"{p['mean']:+,.{nd}f} [{p['lo']:+,.{nd}f}, {p['hi']:+,.{nd}f}]{rel}")
        out.append(f'| {LABEL[a]} − {LABEL[b]} | ' + ' | '.join(cells) + ' |')
    out.append('')
    # plausibility against published figures
    kar = sum(KARNATAKA_RICE.values()) / len(KARNATAKA_RICE) / 1000
    out.append('**Against published figures**\n')
    out.append(f'- Karnataka rice yield 2018-19 to 2022-23: {min(KARNATAKA_RICE.values()):,}–{max(KARNATAKA_RICE.values()):,} kg of rice per ha (RBI). '
               f'At a {OUTTURN:.0%} paddy-to-rice out-turn that is about {min(KARNATAKA_RICE.values()) / 1000 / OUTTURN:.1f}–{max(KARNATAKA_RICE.values()) / 1000 / OUTTURN:.1f} t/ha of paddy. '
               f"The twin gives {ci(S['conventional']['yield_t_ha'])} t/ha (conventional) and {ci(S['builtin']['yield_t_ha'])} t/ha (built-in); "
               f"kharif {ci(S['builtin']['kharif']['yield_t_ha'])}, rabi {ci(S['builtin']['rabi']['yield_t_ha'])} (built-in).")
    if 'builtin-cf' in S:
        w = runs.paired(res, 'builtin', 'builtin-cf', 'irrigation_mm')
        y = runs.paired(res, 'builtin', 'builtin-cf', 'yield_t_ha')
        c = runs.paired(res, 'builtin', 'builtin-cf', 'ch4_kg_ha')
        out.append(f"- AWD against continuous flooding, all else equal: water {w['relative'] * 100:+.1f}% "
                   f"[{w['relative_lo'] * 100:+.1f}, {w['relative_hi'] * 100:+.1f}], yield {y['relative'] * 100:+.1f}% "
                   f"[{y['relative_lo'] * 100:+.1f}, {y['relative_hi'] * 100:+.1f}], methane {c['relative'] * 100:+.1f}%. Published: mild AWD cuts water use by "
                   '23.4% without a significant yield loss (Carrijo et al. 2017); IRRI and TNAU cite up to 25–30% less water; IPCC scales methane by '
                   '0.55 for multiple drainage (−45%); Balaine et al. (2019) measured −41% for safe AWD.')
    out.append('')
    # forests
    v2 = any('awd' in (S[m].get('audit') or {}) for m in ms)
    out.append(('**The forests\' advice, checked as the definitions say** (docs/DEFINITIONS*.md, with the v2 addendum\'s time to AWD, planned '
                'spray and spray at the arm\'s minute; ' if v2 else '**The forests\' advice, checked with the original fixed definitions** (') +
               'all checks pooled over the campaign; Wilson 95% intervals):\n')
    keys = [('soil', 'Soil suitability'), ('establish', 'Transplanting day, chance within ±5 points'), ('establishDecision', 'Transplant / wait call'),
            ('pest', 'Pest risk, 7 days'), ('pestPos', '  recall, plants that did pass 8%'), ('pestNeg', '  recall, plants that did not'),
            ('yield', 'Yield forecast within ±10 points'), ('spray', 'Spray efficacy within ±5 points'),
            ('sprayPlan', 'Spray efficacy, planned (v2 target)' if v2 else 'Spray efficacy, planned (not a pass mark)'),
            ('harvest', 'Harvest readiness')] + ([('awd', 'Time to AWD (v2), within ±6 h or ±20 %')] if v2 else [])
    out.append('| Check | ' + ' | '.join(LABEL.get(m, m) for m in ms) + ' |')
    out.append('|---|' + '---:|' * len(ms))
    for k, lab in keys:
        cells = []
        for m in ms:
            a = S[m]['audit'].get(k)
            if not a:
                cells.append('–')
                continue
            extra = f" (balanced {a['balanced'] * 100:.1f}%)" if k == 'pest' and a.get('balanced') is not None else ''
            cells.append(f"{a['acc'] * 100:.1f}% [{a['ci'][0] * 100:.1f}–{a['ci'][1] * 100:.1f}], n={a['n']:,}{extra}")
        out.append(f'| {lab} | ' + ' | '.join(cells) + ' |')
    out.append('')
    out.append('Share of seasons in which each check reached 95% (checks with at least 20 cases in the season):\n')
    out.append('| Check | ' + ' | '.join(LABEL.get(m, m) for m in ms) + ' |')
    out.append('|---|' + '---:|' * len(ms))
    for k, lab in keys:
        cells = []
        for m in ms:
            a = S[m]['audit_seasons_at_95'].get(k)
            cells.append(f"{a['share'] * 100:.0f}% of {a['seasons']}" if a else '–')
        out.append(f'| {lab} | ' + ' | '.join(cells) + ' |')
    out.append('')
    gv = {m: S[m]['verdicts'] for m in ms}
    out.append('Guardrail verdicts over the campaign: ' + '; '.join(f"{LABEL.get(m, m)} {v.get('accepted', 0):,} accepted, {v.get('warning', 0):,} with a warning, "
                                                               f"{v.get('rejected', 0):,} rejected" for m, v in gv.items()) + '.')
    out.append(f"Forced harvests: {sum(S[m]['forced_harvests'] for m in ms)}; abandoned fields: {sum(S[m]['abandoned_fields'] for m in ms)}.")
    out.append(f"Simulation time: {', '.join(f'{LABEL.get(m, m)} {S[m]['wall_s_per_season']['mean']:.1f} s per season' for m in ms)} "
               '(wall time per season in each worker process; the machine had two cores, shared with other test runs).')
    print('\n'.join(out))


if __name__ == '__main__':
    main(sys.argv[1])
