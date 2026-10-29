"""Guardrails: every decision, whoever made it, is checked before anything happens.

Each decision gets a verdict:
  accepted  — carried out as asked;
  warning   — carried out, with the concern recorded and fed back to the decision maker;
  rejected  — not carried out; the reason is fed back at the next decision point.

Decisions are checked twice: when they arrive (``check_call``) and again just before they run (``check``
with ``stage='execute'``), because a decision scheduled for later may meet different weather.
The numbers live in ``rules.py``; README.md explains each one.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .. import config as CFG
from ..core import agro as A
from ..core import data as D
from ..sim.twin import DAY, HOUR, PADDY, PEST_IDX, PEST_KEYS, FERTILISERS
from . import rules as R

FIELD_IDX = {name: k for k, name in enumerate(CFG.FIELDS)}


class Verdict:
    __slots__ = ('status', 'messages', 'exec_t', 'decision')

    def __init__(self, decision, exec_t):
        self.status = 'accepted'
        self.messages = []
        self.exec_t = exec_t
        self.decision = decision

    def warn(self, msg):
        if self.status == 'accepted':
            self.status = 'warning'
        self.messages.append(msg)

    def reject(self, msg):
        self.status = 'rejected'
        self.messages.append(msg)

    @property
    def ok(self):
        return self.status != 'rejected'

    def as_dict(self):
        return {'verdict': self.status, 'messages': list(self.messages)}


_HHMM = re.compile(r'^\s*(\d{1,2}):(\d{2})\s*$')
_ISO = re.compile(r'^\s*(\d{4})-(\d{2})-(\d{2})[T ](\d{1,2}):(\d{2})(?::\d{2})?\s*$')


def parse_when(tw, when):
    """'now' | 'HH:MM' (the next time the clock shows it, within 24 h) | 'YYYY-MM-DDTHH:MM' → (sim time, error).
    The twin runs in one-hour steps, so times are rounded to the nearest hour (never earlier than now)."""
    w = (when or 'now').strip().lower()
    if w in ('now', 'asap', 'immediately', ''):
        return tw.t, None
    m = _HHMM.match(w)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh > 23 or mm > 59:
            return None, f'"when" {when!r} is not a clock time'
        day0 = tw.t - (tw.t % DAY)
        t = day0 + hh * HOUR + mm * 60
        if t < tw.t - 1:
            t += DAY
    else:
        m = _ISO.match(when)
        if not m:
            return None, f'"when" {when!r} is not "now", "HH:MM" or "YYYY-MM-DDTHH:MM"'
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        di0 = tw.date_info(0)
        import datetime as _dt
        try:
            target = _dt.datetime(y, mo, d, hh, mm)
        except ValueError:
            return None, f'"when" {when!r} is not a valid date'
        start = _dt.datetime(di0.y, di0.m, di0.d)
        t = (target - start).total_seconds()
        if t < tw.t - 1800:
            return None, f'"when" {when!r} is in the past (now {tw.iso()})'
    t = max(tw.t, round(t / HOUR) * HOUR)
    if t - tw.t > R.WHEN_MAX_H * HOUR:
        return None, f'"when" {when!r} is more than {R.WHEN_MAX_H} h ahead'
    return t, None


def _squares_idx(tw, f, squares):
    if squares is None:
        return [int(i) for i in tw.cells[f]]
    return [tw.sq_index(f, r - 1, c - 1) for r, c in squares]


def _flowering(tw, f):
    return any(tw.alive[i] and not tw.done[i] and A.is_flowering(PADDY, min(1.0, float(tw.frac[i]))) for i in tw.cells[f])


def _rain_and_wind(tw, t):
    W = tw.weather
    fh = W.forecast_hour(tw.t, t)
    rain6 = sum(W.forecast_hour(tw.t, t + q * HOUR).rain for q in range(6))
    return rain6, fh.wind


def _days_to_harvest(tw, f):
    cs = tw.cells[f]
    live = cs[tw.alive[cs] & ~tw.done[cs]]
    if not len(live):
        return math.inf
    return max(0.0, (1 - float(np.median(tw.frac[live]))) * PADDY.durationDays)


def check(eng, d, stage='decide', exec_t=None):
    """One decision → Verdict. ``d`` is a normalised decision from schema.validate."""
    tw = eng.tw
    if exec_t is None:
        exec_t, err = parse_when(tw, d.get('when'))
        if err:
            v = Verdict(d, tw.t)
            v.reject(err)
            return v
    v = Verdict(d, exec_t)
    act = d['action']
    p = d.get('params') or {}
    if act == 'wait':
        hrs = float(p.get('hours', 12))
        if hrs < 0.5 or hrs > R.WAIT_MAX_H:
            v.warn(f'wait of {hrs:g} h is outside 0.5–{R.WAIT_MAX_H} h; using {min(R.WAIT_MAX_H, max(0.5, hrs)):g} h')
            p['hours'] = min(R.WAIT_MAX_H, max(0.5, hrs))
        return v
    f = FIELD_IDX.get(d.get('field'))
    if f is None:
        v.reject(f'unknown field {d.get("field")!r}')
        return v
    fd = tw.fields[f]
    cs = tw.cells[f]
    if fd.harvestT is not None and act != 'hold':
        v.reject(f'field {fd.name} is already harvested')
        return v
    if fd.abandoned and act != 'hold':
        v.reject(f'field {fd.name} was abandoned for this season')
        return v
    prog = tw.field_progress(f)
    planted = fd.transplantT is not None
    sq = _squares_idx(tw, f, d.get('squares'))
    hour = (exec_t % DAY) / HOUR
    depth = tw.water_tube(f)

    if act == 'hold' or act == 'soil_check' or act == 'scout':
        if act == 'scout' and not planted:
            v.warn('nothing is planted yet, so scouting finds nothing')
        return v

    if act == 'amend':
        prod, dose = p['product'], float(p['dose'])
        cap = R.AMEND_CAP[prod]
        unit = 'mm' if prod == 'leach' else 'g/m²'
        if dose <= 0:
            v.reject(f'{prod} dose must be positive')
        elif dose > cap:
            v.reject(f'{prod} {dose:g} {unit} is above the largest single application ({cap} {unit})')
        else:
            done = max(float(tw.amendG[prod][i]) for i in sq)
            if done + dose > cap * R.AMEND_SEASON_FACTOR:
                v.reject(f'{prod}: {done:g} {unit} already applied on some of these squares; {dose:g} more would pass the seasonal '
                         f'{cap * R.AMEND_SEASON_FACTOR:g} {unit}')
        if v.ok and planted:
            v.warn('amendments act slowly once the crop is in; the time to amend is before puddling')
        if v.ok and prod == 'leach':
            add = dose * len(sq) / len(cs)
            if tw.level_after_adding(depth, add) > CFG.BUND_MM - 10:     # the tube reading, with a margin for its error
                v.reject(f'leaching {dose:g} mm would overflow the {CFG.BUND_MM} mm bund')
        return v

    if act == 'puddle':
        if planted:
            v.reject('the field is already transplanted; puddling would destroy the crop')
        elif depth < R.PUDDLE_MIN_MM:
            v.reject(f'puddling needs at least {R.PUDDLE_MIN_MM} mm of standing water (the tube reads {depth:.0f} mm); irrigate first')
        elif fd.puddled:
            v.warn('the field is already puddled')
        return v

    if act == 'transplant':
        if not fd.puddled:
            v.reject('transplant only into a puddled field (puddle it first)')
            return v
        if tw.day >= CFG.TRANSPLANT_DEADLINE_DAY:
            v.reject(f'too late: transplanting closes on day {CFG.TRANSPLANT_DEADLINE_DAY} of the season')
            return v
        if planted:
            gap = [i for i in sq if tw.has[i] and not tw.alive[i] and not tw.done[i]]
            if exec_t - fd.transplantT > R.GAP_FILL_DAYS * DAY:
                v.reject(f'the field was transplanted on {tw.iso(fd.transplantT)[:10]}; gap-filling is only allowed within {R.GAP_FILL_DAYS} days')
                return v
            if not gap:
                v.reject('the field is already transplanted (only squares whose seedlings died can be gap-filled)')
                return v
            v.warn(f'gap-filling {len(gap)} square(s) whose seedlings died')
            d['squares'] = [(tw.sq_rc(i)[1] + 1, tw.sq_rc(i)[2] + 1) for i in gap]
        age = tw.seedling_age(exec_t)
        if age > R.OLD_SEEDLINGS_DAYS:
            v.warn(f'seedlings are {age:.0f} days old: older seedlings establish and tiller less')
        if depth > R.TRANSPLANT_DEEP_MM:
            v.warn(f'{depth:.0f} mm of standing water: young seedlings may drown (20–30 mm is best)')
        elif depth < R.TRANSPLANT_DRY_MM:
            v.warn(f'the field has dried out (tube {depth:.0f} mm): seedlings may dry out')
        return v

    if act == 'irrigate':
        tgt = float(p['target_depth_mm'])
        if tgt > R.MAX_TARGET_MM:
            v.reject(f'target {tgt:g} mm is deeper than the farm allows ({R.MAX_TARGET_MM} mm; the bund is {CFG.BUND_MM} mm)')
        elif tgt < R.MIN_TARGET_MM:
            v.reject(f'target {tgt:g} mm is below {R.MIN_TARGET_MM} mm; the pump can only raise the water level')
        elif planted and prog >= R.DRAIN_DOWN_PROGRESS:
            v.reject(f'the field is in its pre-harvest drain-down (progress {prog:.2f}): no irrigation')
        else:
            if tgt <= depth + 2:
                v.warn(f'the water level ({depth:.0f} mm) is already at or above the target; nothing to pump')
            need = (tgt - depth) if depth >= 0 else (-depth * CFG.SPECIFIC_YIELD + tgt)
            hrs = max(0.0, need) * len(cs) * CFG.SQUARE_M2 / 1000 / CFG.PUMP_M3_PER_H
            if hrs > 20:
                v.warn(f'reaching {tgt:g} mm needs about {hrs:.0f} h of pumping')
            W = tw.weather
            rain24 = sum(W.forecast_day(tw.day, k).rainMm * W.forecast_day(tw.day, k).rainProb for k in range(1))
            if rain24 >= 30:
                v.warn(f'about {rain24:.0f} mm of rain is expected today; irrigation may be wasted or overflow')
        return v

    if act == 'drain':
        to = float(p['to_depth_mm'])
        if to < 0:
            v.warn('the outlet can only drain standing water; draining to 0 mm')
            p['to_depth_mm'] = 0.0
            to = 0.0
        if depth <= to + 1:
            v.warn(f'the water level ({depth:.0f} mm) is already at or below {to:g} mm')
        if planted and 0.55 <= prog < 0.75:
            v.warn('the crop is around flowering, when it is most sensitive to water stress')
        if not planted and fd.puddled:
            v.warn('a puddled field that dries out before transplanting may need puddling again')
        return v

    if act == 'fertilise':
        prod, kg = p['product'], float(p['kg_per_ha'])
        cap = R.FERT_CAP_KG_HA[prod]
        if kg <= 0:
            v.reject('the amount must be positive')
            return v
        if kg > cap:
            v.reject(f'{kg:g} kg/ha of {prod} is above the largest single application ({cap} kg/ha); split it')
            return v
        n_add = kg * FERTILISERS[prod][0]
        n_max = max(float(tw.nApplied[i]) for i in sq)
        if n_add > 0 and n_max + n_add > R.N_SEASON_CAP:
            v.reject(f'nitrogen: {n_max:.0f} kg N/ha already applied on some of these squares; {n_add:.0f} more would pass the seasonal cap of {R.N_SEASON_CAP} kg N/ha')
            return v
        if n_add > 0 and n_max + n_add > R.N_SEASON_WARN:
            v.warn(f'nitrogen reaches {n_max + n_add:.0f} kg N/ha this season (the recommendation is 100–120)')
        if planted and prog >= R.LATE_N_PROGRESS and n_add > 0:
            v.warn('nitrogen after flowering is mostly wasted')
        if prod in ('urea', 'dap') and depth > 50:
            v.warn(f'{depth:.0f} mm of standing water: more of the urea is lost to the air; top-dress into 0–50 mm')
        return v

    if act == 'spray':
        prod = p['product']
        pest = p.get('target_pest')
        if not R.labelled_for(prod):
            v.reject(f'{prod} is not on the paddy label list (simulated list: {", ".join(R.PADDY_LABEL)})')
            return v
        if pest and not R.labelled_for(prod, pest):
            v.reject(f'{prod} is not labelled for {pest.replace("_", " ")} on paddy (labelled for {", ".join(R.PADDY_LABEL[prod]["pests"])})')
            return v
        if not planted:
            v.reject('nothing is planted yet')
            return v
        if not (R.SPRAY_HOURS[0] <= hour <= R.SPRAY_HOURS[1]):
            v.reject(f'{hour:05.2f} h is outside the spraying window {R.SPRAY_HOURS[0]:g}–{R.SPRAY_HOURS[1]:g} h')
            return v
        rain6, wind = _rain_and_wind(tw, exec_t)
        if rain6 >= R.SPRAY_RAIN_MM_6H:
            v.reject(f'{rain6:.1f} mm of rain is forecast in the 6 h after {tw.iso(exec_t)[11:]}; the spray would wash off')
            return v
        if wind > R.SPRAY_WIND_MS:
            v.reject(f'wind {wind:.1f} m/s is forecast at {tw.iso(exec_t)[11:]}; above {R.SPRAY_WIND_MS:g} m/s the spray drifts')
            return v
        pc = D.PESTICIDES[prod]
        dth = _days_to_harvest(tw, f)
        phi = R.phi_days(prod)
        if phi > dth - 0.5:
            v.reject(f'{prod} has a {phi}-day pre-harvest interval on rice but the field is about {dth:.0f} days from harvest')
            return v
        sev = max([float(tw.oPest[i]) for i in sq if tw.oPestId[i] >= 0] + [0.0])
        if pc.bee > R.BEE_TOXIC and _flowering(tw, f):
            if not R.bee_window(hour):
                v.reject(f'{prod} is bee-toxic and the crop is flowering: only at dawn (05:30–07:00) or dusk (17:30–19:30)')
                return v
            if sev < R.BEE_SEVERE:
                v.reject(f'{prod} is bee-toxic and the crop is flowering: only against a severe infestation (≥ {R.BEE_SEVERE:.2f}; highest seen {sev:.2f})')
                return v
        live = [i for i in sq if tw.has[i] and tw.alive[i] and not tw.done[i]]
        if not live:
            v.reject('none of these squares has a living crop')
            return v
        seen = [i for i in sq if tw.oPestId[i] >= 0 and tw.oPest[i] > 0]
        if not seen:
            v.warn('no pest is seen on these squares (a preventive spray)')
        elif pest is None:
            pests = {PEST_KEYS[int(tw.oPestId[i])] for i in seen}
            if not any(R.labelled_for(prod, x) for x in pests):
                v.reject(f'{prod} is not labelled for the pest seen here ({", ".join(sorted(x.replace("_", " ") for x in pests))})')
                return v
        rep = [i for i in sq if tw.lastMoa[i] == pc.moa and exec_t - tw.lastMoaT[i] < R.MOA_REPEAT_DAYS * DAY]
        if rep:
            v.warn(f'same IRAC group ({pc.moa}) as a spray on {len(rep)} of these squares in the last {R.MOA_REPEAT_DAYS} days: rotate modes of action')
        most = max(int(tw.sprayCount[i]) for i in sq)
        if most + 1 > R.SPRAYS_CAP:
            v.reject(f'some of these squares have had {most} sprays this season (limit {R.SPRAYS_CAP})')
            return v
        if most + 1 > R.SPRAYS_WARN:
            v.warn(f'some of these squares have had {most} sprays this season')
        return v

    if act == 'harvest':
        if not planted:
            v.reject('nothing is planted')
            return v
        nhb = max(float(tw.noHarvestBefore[i]) for i in cs if tw.has[i] and not tw.done[i]) if any(tw.has[i] and not tw.done[i] for i in cs) else 0
        if nhb > exec_t:
            v.reject(f'pre-harvest interval not over: no harvest before {tw.iso(nhb)}')
            return v
        if prog < R.MATURE_PROGRESS:
            if p.get('override_immature'):
                v.warn(f'harvesting an immature crop (progress {prog:.2f}): grain will be lost')
            else:
                v.reject(f'the crop is not mature (progress {prog:.2f}, about {_days_to_harvest(tw, f):.0f} days to go); set override_immature to force it')
                return v
        W = tw.weather
        rain2 = W.rain_between(tw.day, 0, tw.hour) + W.day(max(0, tw.day - 1)).rain
        if rain2 >= R.WET_RAIN_2D_MM:
            v.warn(f'{rain2:.0f} mm of rain in the last two days: wet grain and some loss')
        if depth > 20:
            v.warn(f'{depth:.0f} mm of standing water at harvest: some grain is lost (drain first)')
        return v

    v.reject(f'unknown action {act!r}')
    return v


def check_call(eng, decisions):
    """All decisions of one reply: per-decision checks plus the checks across them (too many decisions, two
    decisions that contradict each other, the same decision twice)."""
    out = []
    seen = {}
    for k, d in enumerate(decisions):
        if k >= CFG.MAX_DECISIONS_PER_CALL:
            v = Verdict(d, eng.tw.t)
            v.reject(f'more than {CFG.MAX_DECISIONS_PER_CALL} decisions in one reply')
            out.append(v)
            continue
        v = check(eng, d)
        if v.ok:
            fld, act = d.get('field'), d['action']
            key = (fld, act, repr(d.get('params')), repr(d.get('squares')), v.exec_t)
            if key in seen:
                v.reject(f'duplicate of decision {seen[key] + 1}')
            else:
                seen[key] = k
                for j, (d2, v2) in enumerate(zip(decisions[:k], out)):
                    if not v2.ok or d2.get('field') != fld or v2.exec_t != v.exec_t:
                        continue
                    pair = {act, d2['action']}
                    if pair == {'irrigate', 'drain'}:
                        v.reject(f'contradicts decision {j + 1} ({d2["action"]} the same field at the same time)')
                        break
                    if act == d2['action'] and act in ('irrigate', 'drain', 'transplant', 'puddle', 'harvest'):
                        v.reject(f'decision {j + 1} already does "{act}" in this field at the same time')
                        break
        out.append(v)
    return out
