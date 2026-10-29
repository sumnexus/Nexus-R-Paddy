"""The state sent to the decision maker at every decision point.

Only what the farm can know goes in: sensor readings (water tubes, the weather station and rain gauge, the soil
scan and probe readings, camera sightings), forecasts as issued, the farm's own records (what it applied and when)
and, unless the run is ``api-no-advice``, the forests' advice. True values the twin uses internally (the real
soil, the real pest severity, whether a seedling will die) never go in. The same JSON is what the mock API and
the built-in manager decide from, so the built-in manager is a pure function of this state.
"""
from __future__ import annotations

import math

import numpy as np

from .. import config as CFG
from ..core import agro as A
from ..core import data as D
from ..sim.twin import DAY, HOUR, PADDY, PEST_KEYS, PC_KEYS, stage_name
from . import rules as R

STATE_VERSION = 'paddy-state/1.3'
SOIL_LEGEND = 'S suitable, A needs amendment, U unsuitable (rows top to bottom, columns left to right)'


def _r(x, nd=1):
    if x is None:
        return None
    v = round(float(x), nd)
    return int(v) if nd == 0 else v


def _grid(tw, f, fn, fmt):
    rows = []
    for r in range(tw.R):
        rows.append(' '.join(fmt(fn(tw.sq_index(f, r, c))) for c in range(tw.K)))
    return rows


def field_phase(tw, f):
    fd = tw.fields[f]
    if fd.abandoned:
        return 'abandoned'
    if fd.harvestT is not None:
        return 'harvested'
    if fd.transplantT is None:
        return 'puddled' if fd.puddled else 'land_prep'
    return 'drain_down' if tw.field_progress(f) >= R.DRAIN_DOWN_PROGRESS else 'growing'


def days_to_harvest(tw, f):
    cs = tw.cells[f]
    live = cs[tw.alive[cs] & ~tw.done[cs]]
    if not len(live):
        return math.inf
    return max(0.0, (1 - float(np.median(tw.frac[live]))) * PADDY.durationDays)


def soil_code(rd):
    """A compact code from the readings alone (no forest): the most limiting of salinity, pH and nitrogen."""
    if rd.ec > PADDY.ecTol:
        return 's'
    if rd.ph < PADDY.ph[0]:
        return 'a'
    if rd.ph > PADDY.ph[1] + 0.25:
        return 'k'
    if rd.n < 200:
        return 'l'
    return 'n'


def soil_section(tw, adv, f, advice, detail):
    cs = tw.cells[f]
    rd = [tw.soil_reading(i) for i in cs]
    avg = {k: _r(np.mean([x[k] for x in rd]), 2 if k in ('ph', 'ec', 'oc') else 0) for k in ('ph', 'ec', 'n', 'p', 'k', 'oc', 'sand', 'clay')}
    out = {'codes': _grid(tw, f, lambda i: soil_code(tw.soil_reading(i)), lambda v: v).copy(), 'avg': avg,
           'probed_squares': sum(1 for i in cs if int(i) in tw.probe)}
    out['codes'] = [row.replace(' ', '') for row in out['codes']]
    ages = [(tw.t - tw.probe[int(i)].t) / DAY for i in cs if int(i) in tw.probe and tw.probe[int(i)].get('t') is not None]
    if ages:
        out['probe_age_days'] = _r(min(ages), 1)
    if detail:
        out['ph'] = _grid(tw, f, lambda i: tw.soil_reading(i).ph, lambda v: f'{v:.1f}')
        out['ec'] = _grid(tw, f, lambda i: tw.soil_reading(i).ec, lambda v: f'{v:.1f}')
        out['n'] = _grid(tw, f, lambda i: tw.soil_reading(i).n, lambda v: f'{v:.0f}')
    return out


def pest_section(eng, f):
    tw = eng.tw
    cs = tw.cells[f]
    sight = []
    for i in cs:
        pk = int(tw.oPestId[i])
        if pk >= 0 and tw.oPest[i] > 0 and tw.has[i] and not tw.done[i]:
            x = {'sq': tw.sq_name(i), 'pest': PEST_KEYS[pk], 'sev': _r(tw.oPest[i], 3), 'days': _r(tw.obs_age(i), 1)}
            lt = eng.close_look_t.get(int(i))
            if lt is not None and tw.t - lt < 3 * DAY:
                x['close_look_h_ago'] = _r((tw.t - lt) / HOUR, 0)
            sight.append(x)
    sight.sort(key=lambda x: -x['sev'])
    prot = {}
    for i in cs:
        pc = int(tw.residuePc[i])
        if pc >= 0 and tw.residue[i] >= 0.1:
            prot[PC_KEYS[pc]] = prot.get(PC_KEYS[pc], 0) + 1
    out = {'sightings': sight[:24], 'n_sightings': len(sight)}
    if len(sight) > 24:
        out['more_sightings'] = len(sight) - 24
    if prot:
        out['protected_squares'] = prot
    ann = [o for o in tw.announced if o['field'] == CFG.FIELDS[f]]
    if ann:
        out['outbreak_reports'] = ann[-3:]
    sprays = [s for s in eng.spray_log if s['field'] == CFG.FIELDS[f]][-5:]
    if sprays:
        out['recent_sprays'] = sprays
    return out


def advice_section(eng, f, phase):
    tw, adv = eng.tw, eng.adv
    out = {}
    soil = adv.soil(f)
    codes = {s.i: s.code for s in soil}
    out['soil'] = {'grid': [''.join(codes[tw.sq_index(f, r, c)] for c in range(tw.K)) for r in range(tw.R)]}
    if phase in ('land_prep', 'puddled'):
        doses = []
        for s in soil:
            for d in s.doses:
                doses.append(f'{s.sq} {d.product} {d.dose:.0f}')
        if doses:
            out['soil']['suggested_amendments'] = doses[:40]
        est = adv.establishment(f)
        ch = [a.chance for a in est]
        low = sorted(est, key=lambda a: a.chance)[:6]
        out['establishment'] = {'mean': _r(np.mean(ch), 3), 'min': _r(min(ch), 3), 'plant_at': R_EST_PLANT_AT,
                                'lowest': [f'{a.sq} {a.chance:.2f}' for a in low]}
        yf = adv.yield_forecast(f)
        out['yield'] = {'if_transplanted_today': _r(yf.today, 3), 'best_day_in_days': yf.bestDay, 'best': _r(yf.best, 3)}
    elif phase in ('growing', 'drain_down'):
        if eng.yield_at_transplant.get(f) is not None:
            out['yield'] = {'forecast_at_transplanting': _r(eng.yield_at_transplant[f], 3)}
        risks, _ = adv.pest_risk(f)
        if risks:
            top = sorted(risks, key=lambda r: -r.risk)
            out['pest_risk'] = {'farm_acts_at': _r(adv.pest_act_at, 3),
                                'at_risk': [f'{r.sq} {r.pest} {r.risk:.2f}' for r in top if r.risk >= 0.3][:36],
                                'checked_plants': len(risks), 'max_risk': _r(top[0].risk, 3)}
        seen = sorted({PEST_KEYS[int(tw.oPestId[i])] for i in tw.cells[f] if tw.oPestId[i] >= 0 and tw.oPest[i] > 0})
        if seen:
            sp = {}
            for pid in seen:
                opts = adv.spray_options(f, pid)
                sp[pid] = [{'product': o.product, 'best_safe_time': o.bestSafeTime, 'kill_at_best_safe_time': _r(o.bestSafeEfficacy, 2),
                            'kill_now': _r(o.nowEfficacy, 2), 'candidate_hours': _candidates(tw, o), 'phi_days': o.phiDays,
                            'bee_toxicity': o.beeToxicity, 'irac': o.moa}
                           for o in opts[:3]]
            out['spray'] = {'options': sp}
        if tw.field_progress(f) >= 0.9:
            h = adv.harvest(f)
            out['harvest'] = {'ready_share': _r(h.readyShare, 3), 'mean_score': _r(h.meanScore, 3)}
        thr = eng._awd_threshold(f) if hasattr(eng, '_awd_threshold') else None
        if thr is not None and tw.fields[f].irrTarget is None and tw.water_tube(f) > thr:
            hrs, _ = adv.hours_to_awd(f, thr, getattr(eng, 'tube_log', None), getattr(eng, 'water_flags', None))
            src = 'forest' if 'awd' in getattr(adv.m, 'm', {}) else 'water-balance estimate'
            out['water'] = {'hours_to_awd_threshold': _r(hrs, 0), 'threshold_mm': _r(thr, 0), 'from': src}
    return out


R_EST_PLANT_AT = 0.72


def _candidates(tw, o, n=3):
    """The spray forest's predicted kill for the best few hours that satisfy the rain, wind, daylight and bee rules."""
    safe = [(t, e) for t, e, w, r6 in o.slots if r6 < R.SPRAY_RAIN_MM_6H and w <= R.SPRAY_WIND_MS
            and (not o.duskOnlyIfFlowering or R.bee_window((t % DAY) / HOUR))]
    best = sorted(safe, key=lambda x: -x[1])[:n]
    return [f'{tw.iso(t)[11:16]} {e:.2f}' + ('' if t - tw.t < DAY - 1 and tw.iso(t)[:10] == tw.iso()[:10] else ' (tomorrow)') for t, e in sorted(best)]


def field_state(eng, f, advice):
    tw = eng.tw
    fd = tw.fields[f]
    cs = tw.cells[f]
    phase = field_phase(tw, f)
    planted = int(np.sum(tw.has[cs]))
    alive = int(np.sum(tw.has[cs] & tw.alive[cs] & ~tw.done[cs]))
    dead = int(np.sum(tw.has[cs] & ~tw.alive[cs] & ~tw.done[cs]))
    prog = tw.field_progress(f)
    out = {'phase': phase, 'puddled': bool(fd.puddled)}
    nmax = float(np.max(tw.nApplied[cs]))
    if fd.transplantT is not None:
        out['transplanted'] = tw.iso(fd.transplantT)[:10]
        out['days_after_transplanting'] = _r((tw.t - fd.transplantT) / DAY, 1)
    if alive:
        out['stage'] = stage_name(prog)
        out['progress'] = _r(prog, 3)
        out['maturity_estimate'] = tw.iso(tw.t + max(0.0, (1 - prog)) * PADDY.durationDays * DAY)[:10]
        out['days_to_maturity'] = _r(max(0.0, (1 - prog)) * PADDY.durationDays, 1)   # as the pre-harvest-interval rule counts it
    if planted:
        live = cs[tw.has[cs] & tw.alive[cs] & ~tw.done[cs]]
        out['plants'] = {'planted': planted, 'alive': alive, 'dead': dead,
                         'health': _r(np.mean(tw.oHealth[live]), 2) if len(live) else None}
        if dead and phase in ('growing',):
            out['plants']['dead_squares'] = [tw.sq_name(i) for i in cs if tw.has[i] and not tw.alive[i] and not tw.done[i]][:36]
    if hasattr(eng, 'tube_reading'):
        dep, src = eng.tube_reading(f)
    else:
        dep, src = tw.water_tube(f), 'tube'
    loc = [dep - eng.adv.elev_obs[i] for i in cs]
    out['water'] = {'tube_mm': _r(dep, 0), 'square_depth_range_mm': [_r(min(loc), 0), _r(max(loc), 0)],
                    'target_mm': _r(fd.irrTarget, 0) if fd.irrTarget is not None else None,
                    'pumping': fd.irrTarget is not None, 'draining': fd.drainTarget is not None}
    if src != 'tube':
        out['water']['source'] = src
    sched = eng.scheduled_water(f) if hasattr(eng, 'scheduled_water') else None
    if sched:
        out['water']['scheduled'] = {k: sched[k] for k in ('action', 'at', 'target_mm')}
    if phase in ('growing',) and prog < R.DRAIN_DOWN_PROGRESS:
        out['water']['awd_threshold_mm'] = 0 if 0.5 <= prog < 0.78 else CFG.AWD_THRESHOLD_MM
    out['soil'] = soil_section(tw, eng.adv, f, advice, detail=phase in ('land_prep', 'puddled'))
    if phase in ('land_prep', 'puddled'):
        am = {}
        for prod, arr in tw.amendG.items():
            sq = [tw.sq_name(i) for i in cs if arr[i] > 0]
            if sq:
                am[prod] = sq
        if am:
            out['amended_squares'] = am
    splits = {}
    for x in eng.splits[f]:
        k = (x['split'], x['date'])
        splits[k] = splits.get(k, 0.0) + x['n']
    out['inputs'] = {'n_kg_ha': _r(fd.nKgHa, 1), 'p2o5_kg_ha': _r(fd.pKgHa, 1), 'k2o_kg_ha': _r(fd.kKgHa, 1),
                     'splits': [[k[0], _r(v, 1), k[1]] for k, v in splits.items()],
                     'irrigation_m3': _r(fd.waterM3, 2), 'irrigation_mm': _r(fd.irrMm, 0), 'rain_mm': _r(fd.rainMm, 0),
                     'pump_kwh': _r(fd.energyKwh, 2), 'sprays': len(fd.sprays), 'spray_l': _r(fd.sprayL, 2)}
    if nmax > fd.nKgHa + 0.5:
        out['inputs']['n_max_square_kg_ha'] = _r(nmax, 1)
    if planted and phase in ('growing', 'drain_down'):
        out['pests'] = pest_section(eng, f)
        nhb = float(np.max(tw.noHarvestBefore[cs]))
        if nhb > tw.t:
            out['phi_no_harvest_before'] = tw.iso(nhb)
    if advice:
        out['advice'] = advice_section(eng, f, phase)
    return out


def weather_section(tw):
    W = tw.weather
    d, h = tw.day, tw.hour
    fc = []
    for k in range(3):
        x = W.forecast_day(d, k)
        fc.append({'date': tw.iso(tw.t + k * DAY)[:10], 'tmax': _r(x.tmax), 'tmin': _r(x.tmin), 'rain_mm': _r(x.rainMm), 'rain_prob': _r(x.rainProb, 2),
                   'rh': _r(x.rh, 0), 'wind_ms': _r(getattr(x, 'wind', None) if getattr(x, 'wind', None) is not None else 0, 1)})
    nxt = [W.forecast_hour(tw.t, tw.t + q * HOUR) for q in range(12)]
    rain24 = W.rain_between(d, 0, h) + W.rain_between(d - 1, h, 24) if d > 0 else W.rain_between(d, 0, h)
    return {'station': {'temp_c': _r(W.temp(d, h)), 'rh': _r(W.rh(d, h), 0), 'wind_ms': _r(W.wind(d, h))},
            'rain_last_24h_mm': _r(rain24), 'next_12h': {'rain_mm': _r(sum(x.rain for x in nxt)), 'max_wind_ms': _r(max(x.wind for x in nxt))},
            'forecast': fc}


def build_state(eng, trigger, advice=True):
    tw = eng.tw
    di = tw.date_info()
    st = {
        'v': STATE_VERSION,
        'farm': {'fields': list(CFG.FIELDS), 'grid': f'{tw.R}x{tw.K} squares of {CFG.SQUARE_M2:g} m2 per field'},
        'now': {'time': tw.iso(), 'day_of_season': tw.day, 'season': tw.season, 'trigger': trigger, 'month': di.m},
        'weather': weather_section(tw),

        'pump': {'m3_per_h': CFG.PUMP_M3_PER_H, 'shared_by': [tw.fields[k].name for k in range(2) if tw.fields[k].irrTarget is not None]},
        'fields': {CFG.FIELDS[f]: field_state(eng, f, advice) for f in range(2)},
        'recent_decisions': eng.recent_summaries()[-8:],
        'outcomes': [{k: v for k, v in o.items() if k != 'decision'} for o in list(eng.outcomes)[-6:]],
        'limits': eng.limits_info(),
    }
    arms = eng.arm_state() if hasattr(eng, 'arm_state') else None
    if arms is not None:
        # which arms are busy and until about when (null = parked and free); decisions for a busy arm wait their turn
        st['arms'] = arms
    notes = getattr(eng, 'farmer_notes', None)
    if notes:
        st['farmer_notes'] = [{'time': n['time'], 'field': n.get('field'), 'text': n['text'][:300]} for n in notes[-3:]]
    if any(tw.fields[f].transplantT is None and not tw.fields[f].abandoned for f in range(2)):
        st['nursery'] = {'seedling_age_days': _r(tw.seedling_age(), 1)}
    if advice:
        st['advice_track_record'] = eng.track_record()
    return st
