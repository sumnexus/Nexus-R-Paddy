"""Print the README's result tables from the files in tests/results/ (so the numbers are copied, not retyped).

    python tests/e2e/results_md.py > tests/results/RESULTS.md
"""
from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RES = os.path.join(ROOT, 'tests', 'results')
sys.path.insert(0, os.path.join(ROOT, 'backend'))


def load(name):
    p = os.path.join(RES, name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding='utf-8') as fh:
        return json.load(fh) if name.endswith('.json') else fh.read()


def n(x, nd=0):
    return f'{x:,.{nd}f}'


def fuzz_md(d):
    k = d.get('processes', 1)
    if k > 1 and d.get('seeds'):
        # v2: run in chunks that each fit the two-hour job limit (tests/e2e/fuzz_chunks.py), merged into one report
        how = (f"`python tests/e2e/fuzz_chunks.py --n {d['replies']} --chunks {k} --chunk i` for i = 0 … {k - 1}, then `--merge` (the "
               f"split `python -m paddy fuzz --workers {k}` makes: {k} shares of {n(d['replies'] / k)} replies with seeds "
               f"{', '.join(str(x) for x in d['seeds'])}, each run as its own job under the two-hour limit; "
               f"{d.get('seconds_total', d['seconds']) / 3600:.1f} hours in all) pushed")
    else:
        how = f"`python -m paddy fuzz --n {d['replies']}` pushed"
    out = ['### Guardrail fuzzing\n',
           f"{how} {n(d['replies'])} random replies through validation, the guardrails and "
           f"execution, each on a copy of a real farm state ({d['snapshots']} moments of four built-in seasons, kharif and rabi, with "
           'pest outbreaks added so sprays have targets). The replies mix garbage (not JSON, wrong types, NaN, 5,000-character '
           'strings, "ignore previous instructions"), near-misses (squares off the grid, unknown products and pests, out-of-range '
           'times) and plausible but unsafe decisions (doses over the caps, unlabelled products, sprays in rain, wind, darkness or '
           'on flowering rice with bee-toxic products, harvests inside a waiting period, irrigation over the bund or during the '
           'drain-down). Decisions scheduled for later were run to their time and checked again. After every execution the farm '
           'was checked against the rules.\n',
           '| | |', '|---|---:|',
           f"| Replies | {n(d['replies'])} |",
           f"| Decisions that passed the schema | {n(d['decisions_parsed'])} |",
           f"| Schema problems reported (decisions dropped or fields removed) | {n(d['schema_errors'])} |",
           f"| Accepted / with a warning / rejected by the guardrails | {n(d['accepted'])} / {n(d['warning'])} / {n(d['rejected'])} |",
           f"| Carried out | {n(d['executed'])} |",
           f"| Stopped by the second check at execution time | {n(d['skipped_at_execution'])} |",
           f"| **Crashes** | **{d['crashes']}** |",
           f"| **Rule violations found in the farm afterwards** | **{d['violations']}** |", '']
    rows = sorted(d['by_action'].items(), key=lambda kv: -sum(kv[1].values()))
    out += ['| Action | accepted | warning | rejected |', '|---|---:|---:|---:|']
    out += [f"| {a} | {n(v['accepted'])} | {n(v['warning'])} | {n(v['rejected'])} |" for a, v in rows]
    out.append('')
    return '\n'.join(out)


def e2e_md(d):
    rep = d['report']
    V = rep['variants']
    label = {'builtin': 'Built-in manager (reference, no HTTP)', 'auto': 'API through the stand-in, auto', 'auto-faults': 'API through a faulty stand-in, auto',
             'approve-all': 'API through the stand-in, approve mode, every decision approved',
             'approve-cautious-faults': 'API through a faulty stand-in, approve mode, a cautious farmer'}
    cases = {json.dumps(r['case'], sort_keys=True) for r in d.get('results', [])}
    if any('events' in r['case'] for r in d.get('results', [])):
        # v2: the cases of set W (docs/DEFINITIONS-paddy-v2.md), in parts of 10 cases that each fit the two-hour job limit
        nc = len(cases)
        ev = sum(1 for c in cases if json.loads(c).get('events'))
        kh = sum(1 for c in cases if json.loads(c).get('season') == 'kharif')
        how = (f"`python tests/e2e/mock_seasons.py --set W --start k --seasons 10 --workers 1` for k = 0, 10, …, {nc - 10}, then `--merge`: "
               f"the {nc} cases of set W (seeds and weather years of their own, {kh} kharif and {nc - kh} rabi, {ev} with events — outbreaks, "
               f"storms, dry spells, overcast weeks), each under every variant below: {rep['seasons_through_http']} seasons")
        took = f"{rep['elapsed_s'] / 3600:.1f} hours of wall time (one worker per part, on two cores shared with other runs)."
    else:
        how = f"`python tests/e2e/mock_seasons.py --seasons {V['builtin']['seasons']}`: {rep['seasons_through_http']} seasons"
        took = f"{rep['elapsed_s'] // 60} minutes."
    out = ['### Whole seasons through HTTP\n',
           f"{how} where every decision came "
           'over a real socket from the stand-in API (which requires the key: every request carried a test key), plus the built-in '
           'manager on the same cases for reference. The faulty stand-in answered 4 % of requests with 429 (Retry-After), 3 % with '
           '500, timed out on 1 %, sent malformed JSON 4 % of the time and slipped an unsafe decision into 8 % of its replies. '
           'The cautious farmer turned down preventive sprays, immature harvests and every other scouting request, with a reason '
           f"each time. {took}\n",
           '| | Seasons | Finished | Mean yield t/ha | Calls | Retried and then fine | Repaired | Failed → fallback | Unsafe suggestions → rejected | Rejected by the farmer → reason seen in the next call | Same decisions as the built-in manager | Key found in a record |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for k in ('builtin', 'auto', 'approve-all', 'auto-faults', 'approve-cautious-faults'):
        if k not in V:
            continue
        x = V[k]
        same = f"{x['identical_to_builtin']} of {x['seasons']}" if 'identical_to_builtin' in x else '–'
        out.append(f"| {label[k]} | {x['seasons']} | {x['finished']} | {x['mean_yield_t_ha']:.2f} | {n(x['calls'])} | {n(x['retried_ok'])} | {n(x['repaired'])} | "
                   f"{n(x['calls_failed'])} → {n(x['fallbacks'])} | {n(x['unsafe'])} → {n(x['unsafe_rejected'])} | {n(x['rejected_by_person'])} → {n(x.get('feedback_seen', 0))} | {same} | {x['key_leaked']} |")
    out.append('')
    out.append(('All checks passed. ' if rep.get('all_checks_passed') else 'SOME CHECKS FAILED. ') +
               'Without faults the API through the stand-in made exactly the built-in decisions, in auto and in approve mode. '
               "With faults the decision lists differ (each rejected unsafe suggestion is a decision record too, and the cautious "
               "farmer turned decisions down), yet every season finished, every unsafe suggestion was stopped, and the farmer's "
               'reasons reached the decision maker.')
    out.append('')
    return '\n'.join(out)


def det_md(d):
    out = ['### Determinism and exact replay\n', '`python tests/e2e/determinism.py`:\n',
           '| Case | Decision maker | Run twice in separate processes | Decision fingerprint | Farm fingerprint |', '|---|---|---|---|---|']
    for r in d['repeat']:
        c = r['case']
        out.append(f"| {c['season']} {c['seed']}, weather year {c['weatherYear']} | {r['manager']} | {'identical' if r['identical'] else 'DIFFERENT'} | "
                   f"`{r['decision_digest']}` | `{r['history_digest']}` |")
    out.append('')
    same = sum(1 for r in d['api_equals_builtin'] if r['identical'])
    out.append(f"The API through the stand-in made exactly the built-in manager's decisions, and the farm followed exactly the same "
               f"course, in {same} of {len(d['api_equals_builtin'])} cases (the fingerprints above match row for row).\n")
    out += ['| Recorded run | Calls | Retried or repaired | Failed | Replayed from the records |', '|---|---:|---:|---:|---|']
    for r in d['replay']:
        c = r['case']
        out.append(f"| {c['season']} {c['seed']}{', stand-in with faults' if r['faults'] else ''} | {r['calls']} | {r['retried_or_repaired']} | "
                   f"{r['calls_failed']} | {'identical decisions and farm' if r['identical'] else 'DIFFERENT'} |")
    out.append('')
    return '\n'.join(out)


def compare_md(d):
    from paddy import runs
    S = d['summary']
    ms = d['managers']
    lab = {'api': 'API (stand-in)', 'api-no-advice': 'API without advice (stand-in)', 'builtin': 'Built-in', 'conventional': 'Conventional'}
    out = ['### The strategy comparison\n',
           f"`python -m paddy compare --managers {','.join(ms)} --seasons {S[ms[0]]['seasons']} --mock` (the same {S[ms[0]]['seasons']} cases for each, "
           'kharif and rabi alternating). It prints the cost estimate first; with the stand-in nothing is spent.\n',
           '| Mean per season (± 95 % interval) | ' + ' | '.join(lab.get(m, m) for m in ms) + ' |', '|---|' + '---:|' * len(ms)]
    for k, nd, title in (('yield_t_ha', 2, 'Yield, t/ha'), ('irrigation_mm', 0, 'Irrigation, mm'), ('n_kg_ha', 0, 'Nitrogen, kg N/ha'),
                         ('ai_g_ha', 1, 'Insecticide, g a.i./ha'), ('bio_kg_ha', 2, 'Bt, kg/ha'), ('sprayed_squares', 0, 'Area sprayed, m²'),
                         ('ch4_kg_ha', 0, 'Methane, kg CH₄/ha'), ('calls', 0, 'Decision calls'), ('tokens_in', 0, 'Input tokens (stand-in count)'),
                         ('tokens_out', 0, 'Output tokens (stand-in count)')):
        cells = []
        for m in ms:
            x = S[m].get(k)
            cells.append('–' if not x or (k.startswith('tokens') and not x['mean']) else
                         f"{x['mean']:,.{nd}f} ± {(x['hi'] - x['mean']) if 'hi' in x else 0:,.{nd}f}")
        out.append(f'| {title} | ' + ' | '.join(cells) + ' |')
    out.append('')
    if d.get('mock_identical'):
        out.append(f"Stand-in check: the API through the stand-in made exactly the built-in decisions in {d['mock_identical']['same']} of "
                   f"{d['mock_identical']['of']} seasons.\n")
    if 'api-no-advice' in S and 'api' in S:
        ti, tn = S['api']['tokens_in']['mean'], S['api-no-advice']['tokens_in']['mean']
        out.append('Without the forests\' advice the stand-in falls back on the built-in manager\'s plain rules: almost the same yield, a '
                   'little less spraying, and a little less methane because it applies no compost (only the soil advice suggests it), '
                   f'and its calls carry {100 * (1 - tn / ti):.0f} % fewer input tokens. With a real model, these two columns are the test of whether the '
                   'advice helps it.\n')
    for m, ce in (d.get('cost') or {}).items():
        out.append(f"- {lab.get(m, m)}: {ce['tokens_in_per_season']:,.0f} input and {ce['tokens_out_per_season']:,.0f} output tokens per season by the "
                   f"stand-in's count ≈ US${ce['usd_per_season']:.2f} per season at US${ce['price_in_per_m']:g} / US${ce['price_out_per_m']:g} per million.")
    out.append('')
    return '\n'.join(out)


def _grid(lines, tags):
    """Checks repeated for every viewport → one row each with a tick per viewport; the rest as a list."""
    rows, once = {}, []
    for x in lines:
        ok = x.startswith('ok')
        body = x[5:]
        tag, _, what = body.partition(': ')
        if tag in tags:
            key = re.sub(r' \(.*\)$', '', what)
            rows.setdefault(key, {})[tag] = 'yes' if ok else '**no**'
        else:
            once.append(('' if ok else '**FAILED** ') + body)
    out = []
    shared = {k: v for k, v in rows.items() if len(v) == len(tags)}
    first = {k: v for k, v in rows.items() if len(v) != len(tags)}
    if shared:
        out += ['| Check | ' + ' | '.join(t.split('-', 1)[1].replace('-', ', ') if t.count('-') > 1 else t.replace('-', ' ') for t in tags) + ' |',
                '|---|' + '---|' * len(tags)]
        out += [f'| {k} | ' + ' | '.join(v.get(t, '–') for t in tags) + ' |' for k, v in shared.items()]
        out.append('')
    for k, v in first.items():
        for t, res in v.items():
            out.append(f'- {t}: {k}' + ('' if res == 'yes' else ' — FAILED'))
    return out, once


def ui_md(text):
    lines = [x for x in text.splitlines() if x.startswith(('ok', 'FAIL'))]
    fails = [x for x in lines if x.startswith('FAIL')]
    tail = [x for x in text.splitlines() if x.strip()][-1]
    secs = re.search(r'in (\d+) s', tail)
    tail = f'in {secs.group(1)} s' if secs else tail
    tags = ['desktop-light', 'desktop-dark', 'phone-light', 'phone-dark']
    grid, once = _grid(lines, tags)
    detail = [x for x in lines if x[5:].startswith('desktop-light: ') and not any(x[5:].endswith(k) or k in x for k in ())]
    first_pass = [x[5:].split(': ', 1)[1] for x in lines if x[5:].startswith('desktop-light: ')]
    shared_keys = set()
    for row in grid:
        m = re.match(r'\| (.+?) \|', row)
        if m:
            shared_keys.add(m.group(1))
    extra = [w for w in first_pass if re.sub(r' \(.*\)$', '', w) not in shared_keys]
    out = ['### The website in a browser\n',
           f'`node tests/e2e/ui.js` (Playwright, Chromium) against `serve --manager api --mode approve --mock`: {len(lines) - len(fails)} checks '
           f'passed, {len(fails)} failed ({tail}).\n',
           'From the page itself, on desktop:\n'] + [f'- {w}' for w in extra] + ['']
    out += ['On every viewport (desktop 1440 px and phone 400 px, light and dark):\n'] + [g for g in grid if g.startswith('|')] + ['']
    out += [f'- {re.sub(r"[.](?=[A-Z])", ". ", w)}' for w in once] + ['']
    return '\n'.join(out)


def replay_md(text):
    lines = [x for x in text.splitlines() if x.startswith(('ok', 'FAIL'))]
    fails = [x for x in lines if x.startswith('FAIL')]
    tags = ['replay-desktop-light', 'replay-phone-dark']
    rows = {}
    for x in lines:
        tag, _, what = x[5:].partition(': ')
        rows.setdefault(re.sub(r' \(.*\)$', '', what), {})[tag] = 'yes' if x.startswith('ok') else '**no**'
    out = [f'The published replay page, tested without any server (`node tests/e2e/replay_ui.js`): {len(lines) - len(fails)} checks passed, '
           f'{len(fails)} failed.\n', '| Check | desktop, light | phone, dark |', '|---|---|---|']
    out += [f'| {k} | ' + ' | '.join(v.get(t, '–') for t in tags) + ' |' for k, v in rows.items()]
    out.append('')
    return '\n'.join(out)


def farm3d_md(text):
    """v2: tests/e2e/farm3d_ui.js on the replay page (and, with a live server, approve mode)."""
    lines = [x for x in text.splitlines() if x.startswith(('ok', 'FAIL'))]
    fails = [x for x in lines if x.startswith('FAIL')]
    fps = [x for x in text.splitlines() if x.startswith('frame rates')]
    tags = ['desktop-light', 'desktop-dark', 'phone-light', 'phone-dark']
    grid, once = _grid(lines, tags)
    out = ['### The 3D farm in a browser\n',
           f'`node tests/e2e/farm3d_ui.js` (Playwright, Chromium with software WebGL 2): {len(lines) - len(fails)} checks passed, '
           f'{len(fails)} failed.\n', 'On every viewport (desktop 1440 px and phone 400 px, light and dark):\n']
    out += [g for g in grid if g.startswith('|')] + ['']
    out += [f'- {w}' for w in once] + ['']
    cj = load('farm3d_controls.json')
    if cj:
        out += [f"Camera controls (`node tests/e2e/farm3d_controls.js`, on the replay page): {cj['passed']} checks passed, "
                f"{cj['failed']} failed. Each gesture is judged by what it does to the picture "
                "(where the view's test hook projects squares A1,1, A1,6, A6,1, A6,6 and B6,6 on the screen): the corner-to-corner "
                "span (zoom), its bearing (orbit), the shift of its middle (pan) and field A's height over width (tilt). On the "
                "desktop each gesture starts from the overview preset; the touch gestures follow one another on a 400 px phone "
                "page, sent as real multi-touch input through the DevTools protocol.\n",
                '| Check | Result | What the picture did |', '|---|---|---|']
        out += [f"| {r['name']} | {'pass' if r['ok'] else '**FAIL**'} | {r['detail']} |" for r in cj['results']]
        out.append('')
    fj = load('farm3d_fps.json')
    if fj:
        out += [f"Frame rates (`node tests/e2e/farm3d_fps.js`; {fj['machine']}): each setting warmed up for 12 s, then the view's own counter "
                'read ten times, once a second.\n', '| Setting | Median fps | Readings | Render | Triangles, draw calls |', '|---|---:|---|---|---:|']
        out += [f"| {r['setting']} | {r['median_fps']:g} | {', '.join(f'{x:g}' for x in r['reads'])} | {'light' if r['light_render'] else 'full'} | "
                f"{r['triangles']:,}, {r['draws']} |" for r in fj['results']]
        out.append('')
    elif fps:
        out += [fps[0].replace('frame rates (software WebGL in this container): ', 'Frame rates (software WebGL 2 in this container, no GPU): ') + '.', '']
    return '\n'.join(out)


def main():
    # v2: the strategy comparison of v1 (compare10.json) is kept in tests/results/v1/; the campaign replaces it
    parts = []
    for name, fn in (('fuzz.json', fuzz_md), ('e2e_mock.json', e2e_md), ('determinism.json', det_md), ('ui_test.txt', ui_md),
                     ('farm3d_ui_test.txt', farm3d_md), ('replay_ui_test.txt', replay_md)):
        d = load(name)
        parts.append(fn(d) if d is not None else f'*({name} missing)*\n')
    print('\n'.join(parts))


if __name__ == '__main__':
    main()
