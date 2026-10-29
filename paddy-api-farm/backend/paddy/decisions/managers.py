"""Decision makers that run inside the farm: the built-in manager and a conventional-practice baseline.

Both are pure functions of the state JSON (``state.build_state``) and answer in exactly the JSON format the
decision API must use, so their replies go through the same validation and guardrails as the API's. The mock
decision API answers with ``BuiltinManager``'s choices, which is why a run against the mock reproduces a
built-in run decision for decision.

BuiltinManager — the farm's own policy, following the original playground's farm manager where it applies:
  * probe the soil on day 0 and apply the amendments the soil-suitability advice suggests, once per square;
  * soak, puddle with the basal fertiliser, and transplant when the establishment forest gives at least 72 %
    (waiting at most until the seedlings are 26 days old);
  * safe alternate wetting and drying (AWD): ponded for the first 10 days, then re-flood to 50 mm whenever the
    water tube reads 15 cm below the surface (at a decision point already from −140 mm, since decision points are
    12 h apart — v2: in that margin only if the time-to-AWD forest expects the line before the next decision
    point); ponded from panicle initiation to the end of flowering; drained for harvest at 90 % of the crop's
    thermal time;
  * the recommended dose (100:50:50 kg N:P₂O₅:K₂O per ha in kharif, 120:60:60 in rabi), nitrogen in three splits
    (50 % basal, 25 % at tillering, 25 % at panicle initiation) adjusted to the soil test, top-dressed into shallow
    water;
  * the original pest rules (spray when severity reaches the 8 % action threshold, when three plants carry
    established colonies, or at 4 % with the pest-risk forest flagging a plant), the original product choice
    (labelled, PHI, pollinators, biological first at low pressure, IRAC rotation) and the spray forest's best
    safe hour;
  * harvest when the harvest-readiness forest calls at least 60 % of plants ready and the grain is dry.
Without the forests' advice (``api-no-advice`` runs against the mock) it falls back to plain rules on the readings.

ConventionalManager — common farmer practice: no soil test, fixed transplanting date, continuous flooding,
blanket fertiliser on a calendar into standing water, calendar sprays plus a spray when damage is obvious,
harvest at maturity.
"""
from __future__ import annotations

import datetime as _dt

from ..core import data as D
from . import rules as R

PADDY = next(c for c in D.CROPS if c.id == 'paddy')
EST_PLANT_AT = 0.72
MAX_SEEDLING_WAIT = 26          # days: transplant anyway once seedlings are this old
# Recommended doses for transplanted medium-duration paddy (UAS Bengaluru): 100:50:50 kg N:P₂O₅:K₂O per ha in kharif,
# 120:60:60 in summer (rabi)
POP = {'kharif': (100, 50, 50), 'rabi': (120, 60, 60)}


def pop(st):
    return POP.get(st['now'].get('season'), POP['kharif'])


def _t(iso):
    return _dt.datetime.fromisoformat(iso)


def _hours_between(a, b):
    return (_t(b) - _t(a)).total_seconds() / 3600


def _dec(field, action, reason, params=None, squares=None, when='now', confidence=None):
    d = {'field': field, 'action': action, 'params': params or {}, 'reason': reason}
    if squares is not None:
        d['squares'] = squares
    if when != 'now':
        d['when'] = when
    if confidence is not None:
        d['confidence'] = round(float(confidence), 2)
    return d


def _sq(field, r, c):
    return f'{field}{r + 1},{c + 1}'


def _dims(st):
    g = st['farm']['grid'].split()[0]
    r, c = g.split('x')
    return int(r), int(c)


def _all_squares(st, name):
    rows, cols = _dims(st)
    return [_sq(name, r, c) for r in range(rows) for c in range(cols)]


def _grid_vals(rows):
    return [[float(x) for x in row.split()] for row in rows]


# Gap filling: TNAU's advice is to fill the gaps 7–10 days after transplanting, from the same nursery.
GAP_FILL_DAT = (7.0, 10.0)
# Near the drain-down a re-flood only wets the soil up to the surface: standing water put on now would just be
# drained off a few days later (the drain-down starts at 0.9; 0.86 is about five days earlier).
LATE_REFLOOD_PROGRESS = 0.86


def gap_fill(name, F, how='from the same nursery'):
    dead = (F.get('plants') or {}).get('dead_squares') or []
    dat = F.get('days_after_transplanting') or 0
    if dead and GAP_FILL_DAT[0] <= dat <= GAP_FILL_DAT[1]:
        return [_dec(name, 'transplant', f'gap filling: {len(dead)} square(s) lost their seedlings; replant them {how} '
                     f'({dat:.0f} days after transplanting)', squares=list(dead))]
    return []


# The managers' own caution on waiting periods: the guardrail rejects a spray whose pre-harvest interval would run
# past the expected harvest; the managers keep two days in hand so that rounding in the state never makes them
# propose one the guardrail will reject.
PHI_MARGIN_DAYS = 2.0


def _days_to_harvest(F):
    if F.get('progress') is None:
        return None
    return max(0.0, (1 - F['progress']) * PADDY.durationDays)


def _split_done(F, name):
    return any(s[0] == name for s in F['inputs']['splits'])


def _n_factor(F):
    """Soil-test adjustment of the nitrogen dose from the field's mean available-N reading (kg/ha)."""
    n = F['soil']['avg']['n']
    if n is None:
        return 1.0
    return 1.2 if n < 200 else 0.8 if n > 320 else 1.0


def basal_decisions(name, n_factor=1.0, reason_extra='', dose=POP['kharif']):
    """50 % of the nitrogen, all the phosphate and half the potash before transplanting."""
    pn, pp, pk = dose
    n = 0.5 * pn * n_factor
    dap = pp / 0.46                          # 108.7 kg DAP gives 50 kg P₂O₅ and 19.6 kg N
    urea = max(0.0, (n - dap * 0.18) / 0.46)
    mop = 0.5 * pk / 0.60
    why = f'basal dose at puddling: {n:.0f} kg N, {pp} kg P2O5, {0.5 * pk:.0f} kg K2O per ha{reason_extra}'
    out = [_dec(name, 'fertilise', why, {'product': 'dap', 'kg_per_ha': round(dap), 'split': 'basal'})]
    if urea >= 5:
        out.append(_dec(name, 'fertilise', why, {'product': 'urea', 'kg_per_ha': round(urea), 'split': 'basal'}))
    out.append(_dec(name, 'fertilise', why, {'product': 'mop', 'kg_per_ha': round(mop), 'split': 'basal'}))
    return out


class BuiltinManager:
    name = 'builtin'

    def __init__(self, water='awd', awd_timing='auto'):
        if water not in ('awd', 'continuous'):
            raise ValueError("water must be 'awd' or 'continuous'")
        if awd_timing not in ('auto', 'tube'):
            raise ValueError("awd_timing must be 'auto' or 'tube'")
        self.water = water          # 'continuous' only for the AWD ablation in the campaigns
        # v2: with the time-to-AWD forest's advice in the state ('from': 'forest'), re-flood when it expects the water
        # to reach the AWD line before the next decision point; 'tube' keeps v1's rule on the tube reading alone
        self.awd_timing = awd_timing

    def decide(self, st):
        out, notes = [], []
        for name, F in st['fields'].items():
            ph = F['phase']
            if ph in ('harvested', 'abandoned'):
                continue
            if ph in ('land_prep', 'puddled'):
                out += self._before_transplant(st, name, F, notes)
            else:
                out += self._growing(st, name, F, notes)
        return {'decisions': out[:12], 'notes': '; '.join(notes) if notes else 'built-in manager'}

    # ── before transplanting ──
    def _before_transplant(self, st, name, F, notes):
        out = []
        adv = F.get('advice')
        soil = F['soil']
        water = F['water']
        tube = water['tube_mm']
        if soil['probed_squares'] == 0:
            out.append(_dec(name, 'soil_check', 'first look at the soil: probe every square before deciding amendments'))
        else:
            out += self._amendments(name, F, adv)
        if F['phase'] == 'land_prep':
            if tube < R.PUDDLE_MIN_MM + 5:
                if not water['pumping']:
                    out.append(_dec(name, 'irrigate', 'soak the field for puddling (50 mm of standing water)', {'target_depth_mm': 50}))
            elif not any(d['action'] == 'amend' and d['field'] == name for d in out):
                out.append(_dec(name, 'puddle', f'{tube:.0f} mm of standing water: puddle to seal the plough pan and level the field'))
                out += basal_decisions(name, _n_factor(F), '; adjusted to the soil test', pop(st))
            return out
        # puddled: transplant when the seedlings are likely to establish
        age = st['nursery']['seedling_age_days']
        if tube > R.TRANSPLANT_DEEP_MM:
            if not water['draining']:
                out.append(_dec(name, 'drain', f'{tube:.0f} mm is too deep for young seedlings: lower to 30 mm', {'to_depth_mm': 30}))
            if age < MAX_SEEDLING_WAIT:
                return out
        if tube < 10 and not water['pumping']:
            out.append(_dec(name, 'irrigate', 'bring the puddled field to 30 mm for transplanting', {'target_depth_mm': 30}))
            if tube < R.TRANSPLANT_DRY_MM and age < MAX_SEEDLING_WAIT:
                return out
        if not _split_done(F, 'basal'):
            out += basal_decisions(name, _n_factor(F), '; adjusted to the soil test', pop(st))
        squares = self._plantable(name, F)
        if adv and adv.get('establishment'):
            e = adv['establishment']
            if e['mean'] >= EST_PLANT_AT:
                out.append(_dec(name, 'transplant', f"establishment forest gives {e['mean']:.0%} (≥ {EST_PLANT_AT:.0%}): transplant {age:.0f}-day-old seedlings",
                                squares=squares, confidence=e['mean']))
            elif age >= MAX_SEEDLING_WAIT:
                out.append(_dec(name, 'transplant', f"establishment only {e['mean']:.0%} but the seedlings are {age:.0f} days old: transplant now rather than later",
                                squares=squares, confidence=e['mean']))
            else:
                notes.append(f"{name}: waiting to transplant (establishment {e['mean']:.0%} < {EST_PLANT_AT:.0%})")
        else:
            fc = st['weather']['forecast']
            heavy = max(x['rain_mm'] * x['rain_prob'] for x in fc[:2]) >= 30
            hot = max(x['tmax'] for x in fc[:3]) >= PADDY.tMax
            if (heavy or hot) and age < MAX_SEEDLING_WAIT:
                notes.append(f'{name}: waiting to transplant ({"heavy rain" if heavy else "heat"} forecast)')
            else:
                out.append(_dec(name, 'transplant', f'puddled field at {tube:.0f} mm and no heavy rain or heat in the forecast: transplant', squares=squares))
        return out

    def _plantable(self, name, F):
        adv = F.get('advice')
        if not adv:
            return None
        grid = adv['soil']['grid']
        bad = [(r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch == 'U']
        if not bad:
            return None
        return [_sq(name, r, c) for r, row in enumerate(grid) for c, ch in enumerate(row) if ch != 'U']

    def _amendments(self, name, F, adv):
        done = F.get('amended_squares') or {}
        groups = {}
        if adv and adv['soil'].get('suggested_amendments'):
            for a in adv['soil']['suggested_amendments']:
                sq, prod, dose0 = a.split()
                if sq in done.get(prod, []):
                    continue
                step = {'lime': 50, 'gypsum': 50, 'sulphur': 10, 'compost': 500, 'leach': 10}[prod]
                dose = max(step, round(float(dose0) / step) * step)
                dose = min(dose, R.AMEND_CAP[prod])
                groups.setdefault((prod, dose), []).append(sq)
        elif not adv and F['soil'].get('ph'):
            ph = _grid_vals(F['soil']['ph'])
            ec = _grid_vals(F['soil']['ec'])
            clay = F['soil']['avg']['clay'] or 30
            for r in range(len(ph)):
                for c in range(len(ph[r])):
                    s = _sq(name, r, c)
                    if ph[r][c] < PADDY.ph[0] and s not in done.get('lime', []):
                        d = min(1.0, PADDY.ph[0] + 0.2 - ph[r][c])
                        groups.setdefault(('lime', min(R.AMEND_CAP['lime'], max(50, round(d * (150 + 6 * clay) / 50) * 50))), []).append(s)
                    if ph[r][c] > PADDY.ph[1] + 0.25 and s not in done.get('sulphur', []):
                        d = min(0.8, ph[r][c] - (PADDY.ph[1] - 0.1))
                        groups.setdefault(('sulphur', min(R.AMEND_CAP['sulphur'], max(10, round(d * (40 + 1.5 * clay) / 10) * 10))), []).append(s)
                    if ec[r][c] > PADDY.ecTol and s not in done.get('leach', []):
                        groups.setdefault(('leach', 40), []).append(s)
        out = []
        for (prod, dose), sqs in sorted(groups.items()):
            unit = 'mm' if prod == 'leach' else 'g/m²'
            why = {'lime': 'acid soil', 'sulphur': 'alkaline soil', 'gypsum': 'sodic soil', 'compost': 'low organic matter or coarse texture',
                   'leach': 'salinity above the paddy tolerance'}[prod]
            out.append(_dec(name, 'amend', f'{why}: {prod} {dose:g} {unit} on {len(sqs)} square(s)', {'product': prod, 'dose': dose}, squares=sqs))
        return out[:6]

    # ── the growing crop ──
    def _growing(self, st, name, F, notes):
        out = []
        adv = F.get('advice') or {}
        water = F['water']
        tube = water['tube_mm']
        prog = F.get('progress')
        dat = F.get('days_after_transplanting') or 0
        fc = st['weather']['forecast']
        rain_today = fc[0]['rain_mm'] * fc[0]['rain_prob']
        if prog is None:
            return out
        # harvest
        if prog >= R.MATURE_PROGRESS and not F.get('phi_no_harvest_before'):
            h = adv.get('harvest')
            wet = st['weather']['rain_last_24h_mm'] >= R.WET_RAIN_2D_MM or rain_today >= 5
            ready = (h['ready_share'] >= 0.6) if h else prog >= 1.0
            if (ready and not wet) or prog >= 1.12:
                why = (f"harvest-readiness forest calls {h['ready_share']:.0%} of plants ready" if h else f'the crop has reached maturity (progress {prog:.2f})')
                if wet:
                    why += '; harvesting despite the rain because the crop is overdue'
                return [_dec(name, 'harvest', why, confidence=h['mean_score'] if h else None)]
        out += gap_fill(name, F)
        # water: safe AWD
        if prog >= R.DRAIN_DOWN_PROGRESS:
            if tube > 5 and not water['draining']:
                out.append(_dec(name, 'drain', 'drain-down for harvest: the grain is filling and the field must be dry at harvest', {'to_depth_mm': 0}))
        elif not water['pumping'] and self.water == 'continuous':
            if tube < 30:
                out.append(_dec(name, 'irrigate', 'continuous flooding (comparison run): keep 30–50 mm', {'target_depth_mm': 50}))
        elif not water['pumping']:
            aw = (adv.get('water') or {}) if self.awd_timing == 'auto' else {}
            hrs = aw.get('hours_to_awd_threshold') if aw.get('from') == 'forest' else None
            planned = (water.get('scheduled') or {}).get('action') == 'irrigate'
            # v2: in the margin above the line where v1 re-floods at once (decision points are 12 h apart, so it acts
            # a little early), the time-to-AWD forest decides: re-flood now if it expects the line before the next
            # decision point, otherwise let the field keep drying — the tube's own trigger at the line, or the next
            # decision point, re-floods it in time. At or past the line, and without the forest, v1's rule.
            far = hrs is not None and hrs > AWD_LOOKAHEAD_H
            line = aw.get('threshold_mm', AWD_LINE)
            if dat < 10:
                if tube < 20:
                    out.append(_dec(name, 'irrigate', 'young transplants need standing water for their first 10 days', {'target_depth_mm': 40}))
            elif 0.5 <= prog < 0.78:
                if tube < 15:
                    out.append(_dec(name, 'irrigate', 'from about a week before flowering to a week after it the crop is kept ponded (most sensitive to stress)',
                                   {'target_depth_mm': 50}))
            elif planned:
                pass                                          # an irrigation already decided for later: not twice
            elif tube <= R_AWD_REFLOOD and far and tube > AWD_LINE:
                notes.append(f'{name}: water tube at {tube:.0f} mm; the time-to-AWD forest expects {line:.0f} mm in about {hrs:.0f} h, '
                             f'after the next decision point: keep drying (the tube re-floods it at the line)')
            elif tube <= R_AWD_REFLOOD:
                lead = f'water tube at {tube:.0f} mm, near the safe-AWD limit of -150 mm'
                if hrs is not None and tube > AWD_LINE:
                    lead += f'; the time-to-AWD forest expects the line in about {hrs:.0f} h, before the next decision point'
                if prog >= LATE_REFLOOD_PROGRESS:
                    out.append(_dec(name, 'irrigate', f'{lead}; the drain-down for harvest is a few days away, so wet the soil up to the surface only',
                                    {'target_depth_mm': 0}))
                else:
                    out.append(_dec(name, 'irrigate', f'{lead}: re-flood to 50 mm', {'target_depth_mm': 50}))
        # nitrogen top-dressing
        nf = _n_factor(F)
        pn, pp, pk = pop(st)
        if not _split_done(F, 'basal') and dat < 15:
            out += basal_decisions(name, nf, ' (not applied before transplanting)', pop(st))
        elif not _split_done(F, 'tillering') and dat >= 18 and prog < 0.45:
            if tube <= 50 or dat >= 26:
                n = 0.25 * pn * nf
                out.append(_dec(name, 'fertilise', f'tillering top-dressing: {n:.0f} kg N/ha into shallow water', {'product': 'urea', 'kg_per_ha': round(n / 0.46), 'split': 'tillering'}))
        elif not _split_done(F, 'panicle_initiation') and prog >= 0.45 and prog < R.LATE_N_PROGRESS:
            if tube <= 50 or prog >= 0.52:
                n = 0.25 * pn * nf
                out.append(_dec(name, 'fertilise', f'panicle-initiation top-dressing: {n:.0f} kg N/ha', {'product': 'urea', 'kg_per_ha': round(n / 0.46), 'split': 'panicle_initiation'}))
                out.append(_dec(name, 'fertilise', f'panicle-initiation potash: {0.5 * pk:.0f} kg K2O/ha', {'product': 'mop', 'kg_per_ha': round(0.5 * pk / 0.6), 'split': 'panicle_initiation'}))
        # pests
        out += self._pests(st, name, F, adv, notes)
        return out

    def _pests(self, st, name, F, adv, notes):
        P = F.get('pests') or {}
        sights = P.get('sightings') or []
        if not sights:
            return []
        out = []
        now = st['now']['time']
        dth = _days_to_harvest(F) or 0
        prog = F.get('progress') or 0
        flowering = 0.6 <= min(1.0, prog) < 0.7
        sprays = P.get('recent_sprays') or []
        risk = (adv.get('pest_risk') or {})
        act_at = risk.get('farm_acts_at', 0.55)
        scout = []
        scout_why = []
        for pid in sorted({s['pest'] for s in sights}):
            mine = [s for s in sights if s['pest'] == pid]
            top = max(s['sev'] for s in mine)
            established = sum(1 for s in mine if s['sev'] >= 0.02)
            risky = [{'sq': x.split()[0], 'pest': x.split()[1], 'risk': float(x.split()[2])} for x in risk.get('at_risk', [])]
            risky = [x for x in risky if x['pest'] == pid and x['risk'] >= act_at]
            last = next((s for s in reversed(sprays) if s.get('pest') == pid), None)
            if last and _hours_between(last['time'], now) < 72 and top < 1.5 * (last.get('max_sev') or 0):
                continue
            act = top >= 0.08 or established >= 3 or (top >= 0.04 and len(risky) >= 1)
            if not act:
                if top < 0.03:
                    new = [s['sq'] for s in mine if s['days'] >= 0.5 and 'close_look_h_ago' not in s]
                    if new:
                        scout += new
                        scout_why.append(f'{pid.replace("_", " ")} at up to {top:.1%}')
                continue
            choice = self._choose_product(pid, top, dth, flowering, sprays, adv)
            if choice is None:
                notes.append(f'{name}: {pid.replace("_", " ")} at {top:.0%} but no labelled product fits the PHI, bee and weather rules now')
                continue
            prod, when, kill, why = choice
            targets = [s['sq'] for s in mine if s['sev'] >= 0.01] or [s['sq'] for s in mine]
            for x in risky:
                if x['sq'] not in targets:
                    targets.append(x['sq'])
            if top >= 0.08:
                trig = f'severity {top:.0%} is above the 8% action threshold'
            elif established >= 3:
                trig = f'{established} plants carry established colonies'
            else:
                trig = f'the pest-risk forest gives {len(risky)} plant(s) a real chance of passing 8% within a week'
            kill_txt = f'; spray forest predicts {kill:.0%} kill' if kill is not None else ''
            out.append(_dec(name, 'spray', f'{pid.replace("_", " ")}: {trig}; {why}{kill_txt}', {'product': prod, 'target_pest': pid},
                            squares=targets[:36], when=when, confidence=kill))
        if scout:
            out.append(_dec(name, 'scout', f'cameras see {" and ".join(scout_why)}, below any action level: a close look to confirm it before deciding',
                            squares=sorted(set(scout))[:12]))
        return out

    def _choose_product(self, pid, sev, dth, flowering, sprays, adv):
        recent_moa = [D.PESTICIDES[s['product']].moa for s in sprays[-2:] if s.get('product') in D.PESTICIDES]
        opts = (adv.get('spray') or {}).get('options', {}).get(pid) if adv else None
        cands = []
        if opts is not None:
            for o in opts:
                pc = D.PESTICIDES[o['product']]
                if o['best_safe_time'] is None:
                    continue
                if R.phi_days(o['product']) > dth - PHI_MARGIN_DAYS:
                    continue
                if flowering and pc.bee > R.BEE_TOXIC and sev < R.BEE_SEVERE:
                    continue
                score = o['kill_at_best_safe_time']
                rank = D.PESTICIDE_KIND_RANK.get(pc.kind, 2)
                if sev < 0.15:
                    score -= 0.12 * rank
                if pc.kind == 'chemical' and pc.moa in recent_moa:
                    score -= 0.15
                cands.append((score, o['product'], o['best_safe_time'], o['kill_at_best_safe_time'], pc))
        else:
            for prod in R.products_for(pid):
                pc = D.PESTICIDES[prod]
                eff = D.pesticide_efficacy(prod, pid)
                if eff < 0.3 or R.phi_days(prod) > dth - PHI_MARGIN_DAYS or (flowering and pc.bee > R.BEE_TOXIC and sev < R.BEE_SEVERE):
                    continue
                score = eff - (0.12 * D.PESTICIDE_KIND_RANK.get(pc.kind, 2) if sev < 0.15 else 0)
                cands.append((score, prod, 'now', None, pc))
        if not cands:
            return None
        cands.sort(key=lambda x: -x[0])
        score, prod, when, kill, pc = cands[0]
        why = f'{prod} ({pc.kind}, IRAC {pc.moa}, PHI {R.phi_days(prod)} d)'
        if sev < 0.15 and D.PESTICIDE_KIND_RANK.get(pc.kind, 2) == 0:
            why += ', biological first at low pressure'
        return prod, when, kill, why


AWD_LINE = -150           # safe AWD: re-flood at 15 cm below the surface (CTCN/IRRI, TNAU)
R_AWD_REFLOOD = -140      # v1: re-flood a little before the tube reaches -150 mm (decision points are 12 h apart)
AWD_LOOKAHEAD_H = 12      # v2: … unless the time-to-AWD forest expects the line only after the next decision point


class ConventionalManager:
    """Common practice: fixed calendar, continuous flooding, blanket inputs."""
    name = 'conventional'
    SPRAY_DAT = (20, 35, 50, 65)
    SPRAY_PRODUCT = ('chlorantraniliprole', 'pymetrozine', 'chlorantraniliprole', 'pymetrozine')

    @staticmethod
    def _label_fit(pid, F):
        """What a farmer reads off the labels: the usual product for the pest if its waiting period fits, else the next."""
        dth = _days_to_harvest(F) or 0
        prog = F.get('progress') or 0
        flowering = 0.6 <= min(1.0, prog) < 0.7
        order = {'brown_planthopper': ['imidacloprid', 'pymetrozine', 'neem']}.get(pid, ['chlorantraniliprole', 'neem', 'bt'])
        for prod in order:
            if R.phi_days(prod) > dth - PHI_MARGIN_DAYS:
                continue
            if flowering and D.PESTICIDES[prod].bee > R.BEE_TOXIC:
                continue
            return prod
        return None

    def decide(self, st):
        out = []
        hour = int(st['now']['time'][11:13])
        for name, F in st['fields'].items():
            ph = F['phase']
            water = F['water']
            tube = water['tube_mm']
            if ph in ('harvested', 'abandoned'):
                continue
            if ph == 'land_prep':
                if tube < 25:
                    if not water['pumping']:
                        out.append(_dec(name, 'irrigate', 'flood the field for puddling', {'target_depth_mm': 50}))
                else:
                    out.append(_dec(name, 'puddle', 'puddle the flooded field'))
                    out += basal_decisions(name, 1.0, ' (blanket recommendation)', pop(st))
                continue
            if ph == 'puddled':
                if st['nursery']['seedling_age_days'] >= 21:
                    out.append(_dec(name, 'transplant', 'transplant 21-day-old seedlings on the planned date'))
                elif tube < 30 and not water['pumping']:
                    out.append(_dec(name, 'irrigate', 'keep the puddled field flooded', {'target_depth_mm': 50}))
                continue
            prog = F.get('progress') or 0
            dat = F.get('days_after_transplanting') or 0
            out += gap_fill(name, F, how='with seedlings left in the nursery')
            if prog >= 1.0 and not F.get('phi_no_harvest_before') and hour == 6:
                out.append(_dec(name, 'harvest', 'the crop is mature'))
                continue
            if prog >= R.DRAIN_DOWN_PROGRESS:
                if tube > 5 and not water['draining']:
                    out.append(_dec(name, 'drain', 'drain the field before harvest', {'to_depth_mm': 0}))
            elif tube < 30 and not water['pumping']:
                out.append(_dec(name, 'irrigate', 'keep 5 cm of standing water (continuous flooding)', {'target_depth_mm': 50}))
            pn, pp, pk = pop(st)
            if not _split_done(F, 'tillering') and dat >= 21:
                out.append(_dec(name, 'fertilise', 'tillering top-dressing on the calendar', {'product': 'urea', 'kg_per_ha': round(0.25 * pn / 0.46), 'split': 'tillering'}))
            if not _split_done(F, 'panicle_initiation') and dat >= 45:
                out.append(_dec(name, 'fertilise', 'panicle-initiation top-dressing on the calendar', {'product': 'urea', 'kg_per_ha': round(0.25 * pn / 0.46), 'split': 'panicle_initiation'}))
                out.append(_dec(name, 'fertilise', 'panicle-initiation potash on the calendar', {'product': 'mop', 'kg_per_ha': round(0.5 * pk / 0.6), 'split': 'panicle_initiation'}))
            n_done = F['inputs']['sprays']
            k = sum(1 for x in self.SPRAY_DAT if dat >= x)
            if n_done < k and k <= len(self.SPRAY_DAT) and 6 <= hour <= 16:
                prod = self.SPRAY_PRODUCT[min(n_done, len(self.SPRAY_PRODUCT) - 1)]
                target = 'brown_planthopper' if prod == 'pymetrozine' else 'yellow_stem_borer'
                out.append(_dec(name, 'spray', f'calendar spray {n_done + 1} of {len(self.SPRAY_DAT)} over the whole field', {'product': prod, 'target_pest': target},
                                squares=_all_squares(st, name)))
            else:
                sights = (F.get('pests') or {}).get('sightings') or []
                bad = [s for s in sights if s['sev'] >= 0.15]
                if bad and 6 <= hour <= 16:
                    pid = bad[0]['pest']
                    prod = self._label_fit(pid, F)
                    if prod:
                        out.append(_dec(name, 'spray', f'visible {pid.replace("_", " ")} damage: spray the whole field', {'product': prod, 'target_pest': pid},
                                        squares=_all_squares(st, name)))
        return {'decisions': out[:12], 'notes': 'conventional practice'}


MANAGERS = {'builtin': BuiltinManager, 'conventional': ConventionalManager}
