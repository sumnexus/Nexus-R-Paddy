# Copied from the Agri Robotics Playground's Python port (agriplay/agro.py) for the Paddy API Farm; unchanged below.
"""Shared agronomy: phenology (GDD), crop coefficients, water stress, soil suitability
rules, nutrient sufficiency, pest dynamics, spray efficacy, establishment and the
"true" yield formula. The live farm and the synthetic training data both use these
functions, so the random forests learn the same world the robots work in.
"""
from __future__ import annotations

import math
import re

from .util import clamp, lerp, sigmoid, Obj, js_round

TREF = 24  # °C — typical Bengaluru mean; crop durations are calibrated to it
_exp, _log = math.exp, math.log


# ── phenology ──
def gdd_rate(T, c):
    """°C·day per day."""
    return max(0.0, min(T, c['tOpt'][1] + 3) - c['tBase'])


def total_gdd(c):
    return c['durationDays'] * max(1, min(TREF, c['tOpt'][1] + 3) - c['tBase'])


def stage_index(c, frac):
    st = c['stages']
    for k, s in enumerate(st):
        if frac < s['end']:
            return k
    return len(st) - 1


def stage_name(c, frac):
    return c['stages'][stage_index(c, frac)]['name']


FLOWER_RE = re.compile(r'flower|silk|tassel|panicle|ear-head|pegging', re.I)
_stage_cache = {}


def stage_flags(c):
    """Per-stage (flowering, weight) flags worked out once per crop from the stage names."""
    key = id(c['stages'])
    hit = _stage_cache.get(key)
    if hit is not None and hit[0] is c['stages']:
        return hit[1]
    flags = []
    for st in c['stages']:
        n = st['name']
        flower = bool(FLOWER_RE.search(n))
        if flower:
            w = 2.0
        elif re.search(r'fill|fruit|pod', n, re.I):
            w = 1.4
        elif re.search(r'establish|germin|emerg', n, re.I):
            w = 1.2
        else:
            w = 1.0
        flags.append((flower, w))
    if len(_stage_cache) > 50000:
        _stage_cache.clear()
    _stage_cache[key] = (c['stages'], flags)
    return flags


def is_flowering(c, frac):
    return stage_flags(c)[stage_index(c, frac)][0]


def stage_weight(c, frac):
    return stage_flags(c)[stage_index(c, frac)][1]


def kc(c, frac):
    """FAO-56 style crop coefficient curve."""
    ini_end = c['stages'][0]['end']
    mid_start = max(ini_end + 0.1, 0.38)
    mid_end = 0.88 if c['harvest']['type'] == 'multi' else 0.8
    k = c['kc']
    if frac < ini_end:
        return k['ini']
    if frac < mid_start:
        return lerp(k['ini'], k['mid'], (frac - ini_end) / (mid_start - ini_end))
    if frac < mid_end:
        return k['mid']
    return lerp(k['mid'], k['end'], clamp((frac - mid_end) / (1 - mid_end), 0, 1))


def canopy(c, frac):
    """Visual/biomass growth 0..1 (canopy size)."""
    st = c['stages']
    veg_end = min(0.75, st[min(2, len(st) - 1)]['end'] + 0.05)
    return clamp(0.06 + 0.94 * clamp(frac / veg_end, 0, 1) ** 0.85, 0, 1)


# ── water ──
def ks(theta, fc, wp, p):
    """FAO-56 water stress coefficient from volumetric water content."""
    taw = max(0.02, fc - wp)
    dep = (fc - theta) / taw
    if dep <= p:
        return 1.0
    return clamp((1 - dep) / (1 - p), 0, 1)


def depletion(theta, fc, wp):
    return clamp((fc - theta) / max(0.02, fc - wp), -1, 1.5)


def hydraulics(sand, clay, oc):
    """Pedotransfer (simple linear fits for tropical red soils), volumetric m³/m³."""
    silt = max(0.0, 100 - sand - clay)
    fc = clamp(0.07 + 0.0055 * clay + 0.0015 * silt + 0.025 * oc, 0.12, 0.42)
    wp = clamp(0.02 + 0.0042 * clay + 0.0005 * silt, 0.04, 0.26)
    sat = clamp(fc + 0.12 + 0.0008 * sand, fc + 0.08, 0.55)
    k_drain = 0.04 + 0.0022 * sand  # fraction of excess draining per hour
    return Obj(fc=fc, wp=wp, sat=sat, kDrain=k_drain, silt=silt)


# ── temperature response ──
def temp_factor(T, c):
    lo, hi = c['tOpt']
    tb = c['tBase']
    if T <= tb:
        return 0.0
    if T < lo:
        return clamp(0.3 + 0.7 * (T - tb) / (lo - tb), 0, 1)
    if T <= hi:
        return 1.0
    top = c['tMax'] + 5
    if T >= top:
        return 0.1
    return clamp(1 - 0.9 * (T - hi) / (top - hi), 0.1, 1)


# ── soil suitability ──
def _sand_dev(s, c):
    sand = s['sand']
    lo, hi = c['sand']
    return lo - sand if sand < lo else sand - hi if sand > hi else 0.0


def soil_factors(s, c):
    o0, o1 = c['ph']
    t0, t1 = c['phTol']
    ph = s['ph']
    f_ph = 1.0
    if ph < o0:
        f_ph = 0.35 * clamp(1 - (t0 - ph) / 0.8, 0, 1) if ph <= t0 else 0.35 + 0.65 * (ph - t0) / max(0.1, o0 - t0)
    elif ph > o1:
        f_ph = 0.35 * clamp(1 - (ph - t1) / 0.8, 0, 1) if ph >= t1 else 0.35 + 0.65 * (t1 - ph) / max(0.1, t1 - o1)
    f_ec = 1.0 if s['ec'] <= c['ecTol'] else clamp(1 - c['ecSlope'] * (s['ec'] - c['ecTol']), 0.05, 1)
    sd = _sand_dev(s, c)
    f_tex = clamp(1 - 0.018 * sd, 0.4, 1)
    f_wl = clamp(1 - (s['clay'] - 32) * 0.03 * (1 - c['wlTol']), 0.4, 1) if s['clay'] > 32 else 1.0
    return Obj(ph=f_ph, ec=f_ec, tex=f_tex, wl=f_wl, sandDev=sd, total=f_ph * f_ec * f_tex * f_wl)


def soil_total(s, c):
    """soil_factors(s, c).total without building the dict (hot path)."""
    o0, o1 = c['ph']
    t0, t1 = c['phTol']
    ph = s['ph']
    f_ph = 1.0
    if ph < o0:
        f_ph = 0.35 * clamp(1 - (t0 - ph) / 0.8, 0, 1) if ph <= t0 else 0.35 + 0.65 * (ph - t0) / max(0.1, o0 - t0)
    elif ph > o1:
        f_ph = 0.35 * clamp(1 - (ph - t1) / 0.8, 0, 1) if ph >= t1 else 0.35 + 0.65 * (t1 - ph) / max(0.1, t1 - o1)
    f_ec = 1.0 if s['ec'] <= c['ecTol'] else clamp(1 - c['ecSlope'] * (s['ec'] - c['ecTol']), 0.05, 1)
    f_tex = clamp(1 - 0.018 * _sand_dev(s, c), 0.4, 1)
    f_wl = clamp(1 - (s['clay'] - 32) * 0.03 * (1 - c['wlTol']), 0.4, 1) if s['clay'] > 32 else 1.0
    return f_ph * f_ec * f_tex * f_wl


def nutrient_sufficiency(n, p, k, c):
    npk = c['npk']
    sn = n / (0.6 * npk[0] + 150)
    sp = p / (0.12 * npk[1] + 14)
    sk = k / (0.5 * npk[2] + 110)
    return Obj(n=sn, p=sp, k=sk, min=min(sn, sp, sk))


def nutrient_factor(m):
    return clamp(clamp(m, 0, 1) ** 0.55, 0.25, 1)


def nutrient_targets(c):
    npk = c['npk']
    return Obj(n=1.0 * (0.6 * npk[0] + 150), p=1.05 * (0.12 * npk[1] + 14), k=1.05 * (0.5 * npk[2] + 110))


def _f1(x):
    return f'{x:.1f}'


def _f2(x):
    return f'{x:.2f}'


def suitability_rules(s, c):
    """Ground-truth agronomic rules: 0 unsuitable, 1 suitable after amendment, 2 suitable."""
    amend, why = [], []
    bad = False
    o0, o1 = c['ph']
    t0, t1 = c['phTol']
    ph = s['ph']
    if ph < t0 - 0.3:
        bad = True
        why.append(f'pH {_f1(ph)} is too acidic (tolerates {t0})')
    elif ph < o0:
        amend.append(Obj(kind='lime', why=f'pH {_f1(ph)} below {o0}'))
    if ph > t1 + 0.2:
        bad = True
        why.append(f'pH {_f1(ph)} is too alkaline (tolerates {t1})')
    elif ph > o1 + 0.25:
        amend.append(Obj(kind='sulphur', why=f'pH {_f1(ph)} above {o1}'))
    if s['ec'] > c['ecTol'] * 2.2:
        bad = True
        why.append(f"salinity {_f1(s['ec'])} dS/m vs tolerance {c['ecTol']}")
    elif s['ec'] > c['ecTol']:
        amend.append(Obj(kind='leach', why=f"salinity {_f1(s['ec'])} dS/m above {c['ecTol']}"))
    sd = _sand_dev(s, c)
    if sd > 20:
        bad = True
        why.append(f"texture ({js_round(s['sand'])}% sand) far from {c['sand'][0]}–{c['sand'][1]}%")
    elif sd > 8:
        amend.append(Obj(kind='compost', why=f"texture {js_round(s['sand'])}% sand"))
    if s['clay'] > 40 and c['wlTol'] < 0.3:
        bad = True
        why.append(f"heavy clay ({js_round(s['clay'])}%) waterlogs")
    if s['oc'] < 0.4 and not any(a.kind == 'compost' for a in amend):
        amend.append(Obj(kind='compost', why=f"organic carbon {_f2(s['oc'])}%"))
    ns = nutrient_sufficiency(s['n'], s['p'], s['k'], c)
    if ns.n < 0.75:
        amend.append(Obj(kind='N', why=f"N {js_round(s['n'])} kg/ha low"))
    if ns.p < 0.75:
        amend.append(Obj(kind='P', why=f"P {js_round(s['p'])} kg/ha low"))
    if ns.k < 0.75:
        amend.append(Obj(kind='K', why=f"K {js_round(s['k'])} kg/ha low"))
    label = 0 if bad else 1 if amend else 2
    return Obj(label=label, amend=amend, why=why)


def suitability_label(s, c):
    """suitability_rules(s, c).label without the explanations (hot path)."""
    o0, o1 = c['ph']
    t0, t1 = c['phTol']
    ph = s['ph']
    if ph < t0 - 0.3 or ph > t1 + 0.2 or s['ec'] > c['ecTol'] * 2.2:
        return 0
    sd = _sand_dev(s, c)
    if sd > 20 or (s['clay'] > 40 and c['wlTol'] < 0.3):
        return 0
    if ph < o0 or ph > o1 + 0.25 or s['ec'] > c['ecTol'] or sd > 8 or s['oc'] < 0.4:
        return 1
    ns = nutrient_sufficiency(s['n'], s['p'], s['k'], c)
    return 1 if (ns.n < 0.75 or ns.p < 0.75 or ns.k < 0.75) else 2


def amendment_doses(s, c):
    """Dose calculation for the planting arm (per 1 m² site)."""
    doses = []
    o0, o1 = c['ph']
    ph = s['ph']
    if ph < o0:
        d = min(1.0, o0 + 0.2 - ph)
        doses.append(Obj(kind='lime', grams=js_round(d * (150 + 6 * s['clay'])), dPh=d * 0.85))
    if ph > o1 + 0.25:
        d = min(0.8, ph - (o1 - 0.1))
        doses.append(Obj(kind='sulphur', grams=js_round(d * (40 + 1.5 * s['clay'])), dPh=-d * 0.7))
    sd = _sand_dev(s, c)
    if s['oc'] < 0.4 or sd > 8:
        doses.append(Obj(kind='compost', grams=1500))
    if s['ec'] > c['ecTol']:
        doses.append(Obj(kind='leach', mm=40))
    return doses


# Share of an applied fertiliser that shows up in the plant-available soil pool in the first season.
# Red laterite soils fix much of the phosphate; N losses are simulated separately through leaching.
FERT_RECOVERY = Obj(n=1.0, p=0.25, k=0.6)


def fert_to_pool(g):
    return Obj(n=g['n'] * 10 * FERT_RECOVERY.n, p=g['p'] * 10 * FERT_RECOVERY.p, k=g['k'] * 10 * FERT_RECOVERY.k)


def basal_fertilizer(s, c):
    """Soil-test-based basal dose (g/m² of N, P₂O₅, K₂O): fill the gap to the crop's target, plus a
    maintenance share of what the crop will remove when the soil isn't already high."""
    t = nutrient_targets(c)
    up_p = 0.12 * c['npk'][1]
    up_k = 0.25 * c['npk'][2]
    p = max(0.0, t.p - s['p']) + (0.6 * up_p if s['p'] < 1.5 * t.p else 0)
    k = max(0.0, t.k - s['k']) + (0.6 * up_k if s['k'] < 1.5 * t.k else 0)
    return Obj(n=max(0.0, (t.n - s['n']) * 0.8) / 10, p=p / FERT_RECOVERY.p / 10, k=k / FERT_RECOVERY.k / 10)


def top_dress_n(n_avail, c, frac):
    """In-season nitrogen top-dressing rule (kg N/ha to add, 0 if not needed)."""
    t = nutrient_targets(c).n
    if frac < 0.1 or frac > 0.8 or n_avail >= 0.85 * t:
        return 0.0
    return clamp(t - n_avail, 0.12 * c['npk'][0], 0.4 * c['npk'][0])


def blanket_basal(c):
    """The recommended blanket basal dose (half the N up front, all the P and K), g/m²."""
    return Obj(n=c['npk'][0] * 0.5 / 10, p=c['npk'][1] / 10, k=c['npk'][2] / 10)


def days_since_pick(pl, t):
    """Days since the last picking, or since fruiting began for the first one."""
    if pl.lastPickT is not None:
        return (t - pl.lastPickT) / 86400
    if pl.harvestStartT is not None:
        return (t - pl.harvestStartT) / 86400
    return 0.0


def leach_n(n, drained_mm, sand):
    return n * _exp(-0.0007 * drained_mm * (0.4 + sand / 100))


# ── pests ──
def pest_temp_f(pest, T):
    return _exp(-(((T - pest['tOpt']) / pest['tWidth']) ** 2))


def pest_rh_f(pest, rh):
    k = pest['rh']
    if k == 'humid':
        return 0.25 + 0.75 * clamp((rh - 55) / 35, 0, 1)
    if k == 'dry':
        return 0.25 + 0.75 * clamp((85 - rh) / 40, 0, 1)
    return 0.7


def in_window(pest, frac):
    w = pest['window']
    return w[0] <= frac <= w[1]


def pest_hazard(pest, c, T, rh, frac, nbr_sev, protection, mult=1.0):
    """New-infestation hazard per day for an uninfested plant."""
    return (pest['base'] * pest_temp_f(pest, T) * pest_rh_f(pest, rh) * (1 if in_window(pest, frac) else 0.12) *
            c['susceptibility'] * (1 + 10 * nbr_sev) * _exp(-3 * protection) * mult)


def pest_growth(pest, T, rh, frac):
    """Logistic growth rate per day of an existing infestation."""
    return (pest['r'] * (0.25 + 0.75 * pest_temp_f(pest, T)) * (0.5 + 0.5 * pest_rh_f(pest, rh)) *
            (1 if in_window(pest, frac) else 0.5) - 0.02)


def logistic_forward(s0, g):
    e = _exp(g)
    return s0 * e / (1 + s0 * (e - 1))


ETL = 0.08  # economic threshold severity


def true_pest_risk(f):
    """Probability that a site reaches the economic threshold within 7 days."""
    g = pest_growth(f['pest'], f['T'], f['rh'], f['frac'])
    if f['curSev'] > 0.005:
        kill = 3 * f['protection']
        s7 = logistic_forward(f['curSev'], (g - kill * 0.5) * 7)
        return clamp(sigmoid((s7 - ETL) / 0.02), 0.01, 0.99)
    H = pest_hazard(f['pest'], f['crop'], f['T'], f['rh'], f['frac'], f['nbrSev'], f['protection'], f.get('mult', 1)) * 7
    p_em = 1 - _exp(-H)
    need = _log((ETL / (1 - ETL)) / (0.02 / 0.98))  # log-growth needed 0.02 → ETL
    days = need / max(0.005, g)
    return clamp(p_em * clamp((7 - days / 2) / 7, 0, 1) + 0.01, 0.01, 0.99)


# ── spraying ──
def spray_efficacy(f):
    """f: {baseEff, wind, temp, rain6, uv, sunSin, cat, ageDays}"""
    wind = f['wind']
    wind_f = 1.0 if wind <= 2.5 else clamp(1 - 0.12 * (wind - 2.5), 0.35, 1)
    rain_f = 1 - 0.65 * (1 - _exp(-f['rain6'] / 4))
    temp = f['temp']
    temp_f = clamp(1 - 0.04 * (temp - 32), 0.6, 1) if temp > 32 else 0.85 if temp < 15 else 1.0
    uv_f = 1 - 0.35 * f['uv'] * max(0.0, f['sunSin'])
    old = 0.01 if f['cat'] in ('sucking', 'mite') else 0.025
    age_f = 1 - old * min(f['ageDays'], 14)
    return clamp(f['baseEff'] * wind_f * rain_f * temp_f * uv_f * age_f, 0, 1)


# ── establishment (best day to plant) ──
def establish_prob(f):
    """f: {maxRain3, sumRain3, tmax3, thetaRel, sand, transplant, tMaxCrop, wlTol}"""
    return establish_p(f['maxRain3'], f['tmax3'], f['thetaRel'], f['transplant'], f['tMaxCrop'], f['wlTol'])


def establish_p(max_rain3, tmax3, theta_rel, transplant, t_max_crop, wl_tol):
    p = 0.965
    if not transplant:
        p *= 1 - 0.55 * sigmoid((max_rain3 - 45) / 10)
    else:
        p *= 1 - 0.2 * sigmoid((max_rain3 - 70) / 12)
    if tmax3 > t_max_crop:
        p *= clamp(1 - 0.08 * (tmax3 - t_max_crop) * (1.5 if transplant else 0.7), 0.3, 1)
    if theta_rel > 1.12 and wl_tol < 0.5:
        p *= 0.72
    if theta_rel < 0.25:
        p *= 0.86
    return clamp(p, 0.02, 0.99)


# ── yield ──
def yield_fraction(acc, c, month_fit_sow):
    """acc: season accumulators kept on each plant."""
    water = clamp(1 - c['ky'] * (1 - (acc['ksW'] / acc['wW'] if acc['wW'] > 0 else 1)), 0, 1)
    temp = acc['tempF'] / acc['days'] if acc['days'] > 0 else 1
    heat = clamp(1 - 0.012 * acc['heatH'], 0.45, 1)
    nut = acc['nutF'] / acc['days'] if acc['days'] > 0 else 1
    season = 0.7 + 0.3 * month_fit_sow
    return clamp(acc['soil'] * water * temp * heat * nut * season * (1 - clamp(acc['damage'], 0, 0.95)), 0, 1.2)


def new_acc(soil_total_):
    return Obj(soil=soil_total_, wW=0.0, ksW=0.0, tempF=0.0, heatH=0.0, nutF=0.0, days=0.0, damage=0.0)
