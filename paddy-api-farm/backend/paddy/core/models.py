# Copied from the Agri Robotics Playground's Python port (agriplay/models.py) for the Paddy API Farm; unchanged below.
"""The seven random-forest decision models.

Each model turns what the farm knows at that moment (sensor readings, forecasts, its own
soil-water estimate) into features, several of them agronomic "priors" (the farm's own
formulas evaluated on what it knows). The forests were trained offline on 100,000s of
simulated cases and recorded farm-years; ``ModelSuite`` loads and runs them.

Feature definitions are the web version's, one for one, so the shipped forests work
unchanged. Contexts are dicts with the web version's keys (``rainH``, ``tMaxCrop``…).
"""
from __future__ import annotations

import math

import numpy as np

from . import agro as A
from . import climate as C
from . import data as D
from .forests import load_pack
from .rf import RandomForest
from .util import RNG, clamp, Obj

_exp, _log, _sin, _cos, _sqrt = math.exp, math.log, math.sin, math.cos, math.sqrt
SQRT2 = math.sqrt(2)
LN2 = math.log(2)


# ── crop samplers (jitter real crops + invent plausible ones so models generalise to imported crops) ──
def jitter_crop(c, r: RNG):
    def j(v, s=0.08):
        return v * (1 + r.normal(0, s))
    o = Obj(c)
    o.ph = [c['ph'][0] + r.normal(0, 0.15), c['ph'][1] + r.normal(0, 0.15)]
    o.phTol = [min(o.ph[0] - 0.2, c['phTol'][0] + r.normal(0, 0.15)), max(o.ph[1] + 0.2, c['phTol'][1] + r.normal(0, 0.15))]
    s0 = clamp(c['sand'][0] + r.normal(0, 4), 5, 80)
    o.sand = [s0, clamp(c['sand'][1] + r.normal(0, 4), s0 + 15, 95)]
    o.ecTol = max(0.6, j(c['ecTol'], 0.12))
    o.npk = [max(10, j(v)) for v in c['npk']]
    o.wlTol = clamp(c['wlTol'] + r.normal(0, 0.06), 0, 1)
    return o


def random_crop(r: RNG):
    base = r.pick(D.CROPS)
    o = jitter_crop(base, r)
    lo, w = r.range(4.8, 6.6), r.range(0.8, 1.8)
    o.ph = [lo, lo + w]
    o.phTol = [lo - r.range(0.3, 0.9), lo + w + r.range(0.3, 0.9)]
    s0 = r.range(10, 55)
    o.sand = [s0, min(95, s0 + r.range(20, 45))]
    o.ecTol = r.range(0.8, 5)
    o.npk = [r.range(20, 220), r.range(30, 140), r.range(20, 180)]
    o.wlTol = r.random()
    return o


def sample_soil(r: RNG, wide=True):
    sand = r.range(15, 92) if wide else r.range(35, 80)
    clay = r.range(4, 52) if wide else r.range(8, 34)
    if sand + clay > 96:
        clay = max(3, 96 - sand)
    return Obj(ph=r.range(4.3, 8.9) if wide else r.range(5.0, 7.8), n=r.range(90, 420), p=r.range(4, 60), k=r.range(70, 420),
               oc=r.range(0.15, 1.3), sand=sand, clay=clay,
               ec=r.range(0.1, 1.5) if r.random() < 0.7 else r.range(1.5, 5 if wide else 3))


# ── M1 soil suitability ──
SOIL_F = ['pH', 'pH − crop optimum low', 'crop optimum high − pH', 'pH − crop tolerance low', 'crop tolerance high − pH', 'EC dS/m',
          'EC ÷ crop tolerance', 'sand %', 'sand outside preferred range', 'clay %', 'crop waterlogging tolerance', 'organic carbon %',
          'N sufficiency', 'P sufficiency', 'K sufficiency']


def feat_soil(s, c):
    sand = s['sand']
    lo, hi = c['sand']
    sd = lo - sand if sand < lo else sand - hi if sand > hi else 0.0
    npk = c['npk']
    return [s['ph'], s['ph'] - c['ph'][0], c['ph'][1] - s['ph'], s['ph'] - c['phTol'][0], c['phTol'][1] - s['ph'], s['ec'], s['ec'] / c['ecTol'],
            sand, sd, s['clay'], c['wlTol'], s['oc'], s['n'] / (0.6 * npk[0] + 150), s['p'] / (0.12 * npk[1] + 14), s['k'] / (0.5 * npk[2] + 110)]


# ── M2 pest risk ──
PEST_F = ['mean temperature °C', 'relative humidity %', 'rain next 7 days mm', 'crop progress', 'inside pest window', 'neighbour severity',
          'current severity', 'residue protection', 'crop susceptibility', 'pest temperature fit', 'pest humidity fit', 'pest growth rate',
          'pest emergence rate']


def feat_pest(f):
    ps = f['pest']
    return [f['T'], f['rh'], f['rain7'], f['frac'], 1 if A.in_window(ps, f['frac']) else 0, f['nbrSev'], f['curSev'], f['protection'],
            f['crop']['susceptibility'], A.pest_temp_f(ps, f['T']), A.pest_rh_f(ps, f['rh']), ps['r'], ps['base'] * 100]


# ── M3 yield: the climate-normal outlook for a sowing ──
def erf(x):
    t = 1 / (1 + 0.3275911 * abs(x))
    y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * _exp(-x * x)
    return y if x >= 0 else -y


def exp_pos(mu, sd):
    """Expected positive part of a normal variable: E[max(0, X)] for X ~ N(mu, sd)."""
    z = mu / sd
    return mu * 0.5 * (1 + erf(z / SQRT2)) + sd * _exp(-z * z / 2) / _sqrt(2 * math.pi)


_season_cache = {}


def expected_season(c, sow_doy):
    """Climate-normal season outlook for a crop sown on day-of-year ``sow_doy`` (cached per crop object).

    Development is thermal-time driven like the live farm, so a cool-season sowing flowers later."""
    key = (id(c), sow_doy)
    hit = _season_cache.get(key)
    if hit is not None and hit[0] is c:
        return hit[1]
    r = _compute_season(c, sow_doy)
    if len(_season_cache) > 200000:
        _season_cache.clear()
    _season_cache[key] = (c, r)
    return r


def _compute_season(c, sow_doy):
    tot = A.total_gdd(c)
    tf = rain = heat = 0.0
    n = 0
    gdd = 0.0
    d = 0
    while d < 420 and gdd < tot:
        cd = C.clim_day(((sow_doy - 1 + d) % 365) + 1)
        frac = gdd / tot
        tf += A.temp_factor(cd.tmean, c)
        n += 1
        rain += cd.rain
        if A.is_flowering(c, frac):
            heat += 4 * exp_pos(cd.tmax - c['tMax'], 1.6)  # degree-hours above the crop's limit
        gdd += A.gdd_rate(cd.tmean, c)
        d += 1
    return Obj(tempF=tf / max(1, n), heat=heat, rainRatio=(rain * 0.75) / max(150, c['waterNeedMm']), days=n)


# ── M4 establishment ──
EST_F = ['max daily rain next 3 d', 'total rain next 3 d', 'max temperature next 3 d', 'soil moisture (relative)', 'sand %', 'transplanted',
         'crop heat limit °C', 'crop waterlogging tolerance']


def feat_establish(f):
    return [f['maxRain3'], f['sumRain3'], f['tmax3'], f['thetaRel'], f['sand'], 1 if f['transplant'] else 0, f['tMaxCrop'], f['wlTol']]


# ── M6 spray efficacy ──
SPRAY_F = ['hour (sin)', 'hour (cos)', 'wind m/s', 'temperature °C', 'humidity %', 'rain next 6 h mm', 'product UV sensitivity',
           'product base efficacy', 'caterpillar', 'sucking pest', 'mite', 'leaf miner', 'pest age days', 'sun height']


def feat_spray(f):
    a = 2 * math.pi * f['hour'] / 24
    cat = f['cat']
    return [_sin(a), _cos(a), f['wind'], f['temp'], f['rh'], f['rain6'], f['uv'], f['baseEff'], 1 if cat == 'larva' else 0,
            1 if cat == 'sucking' else 0, 1 if cat == 'mite' else 0, 1 if cat == 'miner' else 0, f['ageDays'], f['sunSin']]


# ── M7 harvest readiness ──
HARV_F = ['crop progress', 'days past maturity', 'multi-pick crop', 'days since last pick ÷ interval', 'rain last 2 days mm', 'humidity %',
          'plant health', 'fruit load']


def feat_harvest(f):
    return [f['frac'], f['daysPastMat'], 1 if f['multi'] else 0, f['pickRatio'], f['rain2'], f['rh'], f['health'], f['fruitLoad']]


def true_harvest_ready(f, c):
    if f['multi']:
        return f['frac'] >= c['harvest']['start'] and f['pickRatio'] >= 0.9 and f['fruitLoad'] > 0.25
    return f['frac'] >= 1 and ((f['rain2'] < 6 and f['rh'] < 90) or f['daysPastMat'] > 8)


# ── priors and helpers ──
def logit(x):
    v = clamp(x, 1e-4, 1 - 1e-4)
    return _log(v / (1 - v))


def exp_rain_day(x):
    """Expected rain for one forecast day, as the farm reads it."""
    return x['rainMm'] if x['rainProb'] > 0.5 else x['rainMm'] * x['rainProb']


STRESS_HMAX = 120


def stress_walk(f, rain):
    """Stress prior: walk the hours, removing crop ET (forecast ET₀ × Kc, by the sun's daily curve) from the water
    left before stress; with ``rain``, forecast rain refills it (afternoon showers in the wet months, midday
    otherwise), never above field capacity."""
    taw = f['tawMm']
    cap = f['p'] * taw
    left = (f['p'] - f['dep']) * taw
    if left <= 0:
        return 0.0
    w = C.et_weights(f['doy'])
    m = (f.get('month') or 6) - 1
    rs = 13 if 3 <= m <= 10 else 10
    fcs = f['fcs']
    nf = len(fcs)
    h = f['hour']
    hours = 0.0
    kc = f['kc']
    guard = 0
    while guard < 400 and hours < 240:
        day = math.floor((hours + f['hour']) / 24)
        fc = fcs[min(day, nf - 1)]
        hi = math.floor(h) % 24
        step = min(1, math.floor(h) + 1 - h)
        et = kc * fc['et0'] * w[hi] * step
        r = 0.0
        if rain and rs <= hi < rs + 6:
            r = exp_rain_day(fc) / 6 * step
        if et > left + r:
            return hours + step * (left + r) / max(1e-9, et)
        left = min(cap, left - et + r)
        hours += step
        h += step
        guard += 1
    return 240.0


def hourly_rain(rain_h, hour, gap_fill):
    """Hourly forecast rain (mm/h sampled each hour from now) → rain in each hour from now (trapezoid).
    gap_fill: a gap between two forecast rain hours of the same day is taken as part of one spell."""
    n = len(rain_h)
    v = list(rain_h)
    if gap_fill:
        q = 0
        while q < n:
            day = math.floor((hour + q) / 24)
            e = q
            while e < n and math.floor((hour + e) / 24) == day:
                e += 1
            a = b = -1
            s = 0.0
            k = 0
            for i in range(q, e):
                if v[i] > 0:
                    if a < 0:
                        a = i
                    b = i
                    s += v[i]
                    k += 1
            if k > 1:
                for i in range(a, b + 1):
                    if not (v[i] > 0):
                        v[i] = s / k
            q = e
    return [(v[q] + (v[q + 1] if q + 1 < n else v[q])) / 2 for q in range(n)]


_RAIN_CUTS = (12, 24, 36, 48, 72)


def rain_summary(rh, hour, _cache={}):
    """What a water-stress check's hourly forecast says, worked out once per check (every plant shares it)."""
    key = (id(rh), hour)
    hit = _cache.get('last')
    if hit is not None and hit[0] == key and hit[1] is rh:
        return hit[2]
    hr = hourly_rain(rh, hour, False)
    hf = hourly_rain(rh, hour, True)
    sums = [0.0] * 5
    wet0 = wet1 = 0
    first = 99
    for q in range(len(rh)):
        for b, x in enumerate(_RAIN_CUTS):
            if q < x:
                sums[b] += hr[q]
                break
        if rh[q] > 0:
            if q < 24:
                wet0 += 1
            else:
                wet1 += 1
            if first == 99:
                first = q
    out = {'hour': hour, 'hr': hr, 'hf': hf, 'sums': sums, 'wet0': wet0, 'wet1': wet1, 'first': first}
    _cache['last'] = (key, rh, out)
    return out


def walk_w(f, rain, tail_from):
    """Hour-by-hour water left before the stress limit: forecast ET (daily ET₀ × the sun's curve × Kc), the given
    hourly rain (and, after ``tail_from`` hours, the daily forecast's expected rain spread over the afternoon), water
    above field capacity draining at the soil's rate. Stops at STRESS_HMAX."""
    taw = f['tawMm']
    top = f['p'] * taw
    mx = top + (f.get('satMm') or 0)
    W = min((f['p'] - f['dep']) * taw, mx)
    if W <= 0:
        return 0.0
    fcs = f['fcs']
    nf = len(fcs)
    w = C.et_weights(f['doy'])
    keep = _exp(-(f.get('kd') or 0.1))
    most = 0.0
    for x in fcs:
        most = max(most, x['et0'])
    kc = f['kc']
    # more water than five days of the highest forecast crop water use: no stress within the horizon
    if min(W, top) > kc * most * (STRESS_HMAX / 24 + 1):
        return float(STRESS_HMAX)
    m = (f.get('month') or 6) - 1
    rs = 13 if 3 <= m <= 10 else 10
    hour = f['hour']
    nr = len(rain) if rain is not None else 0
    for q in range(STRESS_HMAX):
        t = hour + q
        day = math.floor((t + 0.5) / 24)
        fl = math.floor(t)
        h0 = fl % 24
        fr = t - fl
        fc = fcs[min(day, nf - 1)]
        et = kc * fc['et0'] * (w[h0] * (1 - fr) + w[(h0 + 1) % 24] * fr)
        r = 0.0
        if rain is not None and q < nr and not (tail_from > 0 and q >= tail_from):
            r = rain[q]
        elif tail_from > 0 and q >= tail_from and rs <= h0 < rs + 6:
            r = exp_rain_day(fc) / 6
        if et > W + r:
            return q + (W + r) / et
        W += r - et
        if W > top:
            W = top + (W - top) * keep
        if W > mx:
            W = mx
    return float(STRESS_HMAX)


# ── batch versions (every plant of one check shares the forecasts): same arithmetic, all plants at once ──
def _walk_w_batch(shared, taw, p, dep, kc, sat_mm, kd, rain, tail_from):
    top = p * taw
    mx = top + sat_mm
    W = np.minimum((p - dep) * taw, mx)
    n = len(W)
    res = np.full(n, np.nan)
    res[W <= 0] = 0.0
    fcs = shared['fcs']
    nf = len(fcs)
    w = C.et_weights(shared['doy'])
    keep = np.array([_exp(-(v or 0.1)) for v in kd])
    most = 0.0
    for x in fcs:
        most = max(most, x['et0'])
    nocheck = np.isnan(res) & (np.minimum(W, top) > kc * most * (STRESS_HMAX / 24 + 1))
    res[nocheck] = STRESS_HMAX
    active = np.isnan(res)
    m = (shared.get('month') or 6) - 1
    rs = 13 if 3 <= m <= 10 else 10
    hour = shared['hour']
    nr = len(rain) if rain is not None else 0
    for q in range(STRESS_HMAX):
        if not active.any():
            break
        t = hour + q
        day = math.floor((t + 0.5) / 24)
        fl = math.floor(t)
        h0 = fl % 24
        fr = t - fl
        fc = fcs[min(day, nf - 1)]
        # kc * fc.et0 * (…) in the same order as the scalar walk
        et = kc * fc['et0'] * (w[h0] * (1 - fr) + w[(h0 + 1) % 24] * fr)
        r = 0.0
        if rain is not None and q < nr and not (tail_from > 0 and q >= tail_from):
            r = rain[q]
        elif tail_from > 0 and q >= tail_from and rs <= h0 < rs + 6:
            r = exp_rain_day(fc) / 6
        hit = active & (et > W + r)
        if hit.any():
            res[hit] = q + (W[hit] + r) / et[hit]
            active &= ~hit
        W = W + r - et
        over = W > top
        W = np.where(over, top + (W - top) * keep, W)
        W = np.minimum(W, mx)
    res[active] = STRESS_HMAX
    return res


def _stress_walk_batch(shared, taw, p, dep, kc):
    cap = p * taw
    left = (p - dep) * taw
    n = len(left)
    res = np.full(n, np.nan)
    res[left <= 0] = 0.0
    active = np.isnan(res)
    w = C.et_weights(shared['doy'])
    m = (shared.get('month') or 6) - 1
    rs = 13 if 3 <= m <= 10 else 10
    fcs = shared['fcs']
    nf = len(fcs)
    hour = shared['hour']
    h = hour
    hours = 0.0
    guard = 0
    while guard < 400 and hours < 240 and active.any():
        day = math.floor((hours + hour) / 24)
        fc = fcs[min(day, nf - 1)]
        hi = math.floor(h) % 24
        step = min(1, math.floor(h) + 1 - h)
        et = kc * fc['et0'] * w[hi] * step
        r = exp_rain_day(fc) / 6 * step if rs <= hi < rs + 6 else 0.0
        hit = active & (et > left + r)
        if hit.any():
            res[hit] = hours + step * (left[hit] + r) / np.maximum(1e-9, et[hit])
            active &= ~hit
        left = np.minimum(cap, left - et + r)
        hours += step
        h += step
        guard += 1
    res[active] = 240.0
    return res


def stress_features_batch(ctxs):
    """Stress features for many plants of one check (they share fcs, rainH, hour, doy, month): (N, 33) array."""
    f0 = ctxs[0]
    fc = f0['fcs']
    rh = f0.get('rainH') or []
    R = rain_summary(rh, f0['hour'])
    hr, hf = R['hr'], R['hf']
    arr = lambda k, d=0.0: np.array([c.get(k, d) if c.get(k) is not None else d for c in ctxs], np.float64)
    dep, p, kc, taw, sand = arr('dep'), arr('p'), arr('kc'), arr('tawMm'), arr('sand')
    sat_mm = np.array([c.get('satMm') or 0 for c in ctxs], np.float64)
    kd_raw = [c.get('kd') for c in ctxs]
    esd = np.array([c.get('eSd') or 0 for c in ctxs], np.float64)
    left = (p - dep) * taw
    dry = _walk_w_batch(f0, taw, p, dep, kc, sat_mm, kd_raw, None, 0)
    sw = _stress_walk_batch(f0, taw, p, dep, kc)
    need = dry < STRESS_HMAX
    w_hr = dry.copy()
    w_hf = dry.copy()
    w_tail = dry.copy()
    if need.any():
        ix = np.nonzero(need)[0]
        sub = lambda a: a[ix]
        kd_sub = [kd_raw[i] for i in ix]
        w_hr[ix] = _walk_w_batch(f0, sub(taw), sub(p), sub(dep), sub(kc), sub(sat_mm), kd_sub, hr, 0)
        w_hf[ix] = _walk_w_batch(f0, sub(taw), sub(p), sub(dep), sub(kc), sub(sat_mm), kd_sub, hf, 0)
        w_tail[ix] = _walk_w_batch(f0, sub(taw), sub(p), sub(dep), sub(kc), sub(sat_mm), kd_sub, hr, 24)
    # rain before dry-weather stress: hr summed over q < dry (q < len(rh))
    csum = np.concatenate([[0.0], np.cumsum(hr)]) if len(hr) else np.zeros(1)
    nq = np.minimum(np.ceil(dry), len(rh)).astype(np.int64)
    before = csum[nq]
    n = len(ctxs)
    shared_cols = [exp_rain_day(fc[0]), exp_rain_day(fc[1]), exp_rain_day(fc[2]), fc[0]['rainProb'], fc[1]['rainProb'], fc[2]['rainProb'],
                   fc[0]['et0'], fc[1]['et0'], fc[0]['tmax']]
    X = np.empty((n, 33))
    X[:, 0], X[:, 1], X[:, 2], X[:, 3], X[:, 4] = dep, p, kc, taw, sand
    X[:, 5] = f0['hour']
    X[:, 6] = esd
    X[:, 7] = left
    X[:, 8] = kc * fc[0]['et0']
    X[:, 9], X[:, 10], X[:, 11], X[:, 12], X[:, 13] = dry, sw, w_hr, w_hf, w_tail
    X[:, 14:23] = shared_cols
    X[:, 23:28] = R['sums']
    X[:, 28], X[:, 29], X[:, 30] = R['wet0'], R['wet1'], R['first']
    X[:, 31] = before
    X[:, 32] = [c.get('kd') or 0.1 for c in ctxs]
    return X


def pest_prior(f):
    """The camera's severity grown 7 days by the forecast weather (beyond the 3-day forecast, the climate normals
    for the date), with the residue decaying and the crop developing."""
    if not ((f.get('curSev') or 0) > 0):
        return 0.0
    ps = f['pest']
    fcs = f.get('fc') or []
    sev = best = f['curSev']
    res = f.get('residue') or 0.0
    gdd = f['gdd']
    tot = f.get('totGDD') or 1
    pesticide = f.get('pesticide')
    half = D.PESTICIDES[pesticide]['halfLife'] if pesticide else 3
    eff = D.pesticide_efficacy(pesticide, f.get('pestId')) if pesticide else 0
    decay = _exp(-LN2 / half)
    crop = f['crop']
    for d in range(7):
        x = fcs[d] if d < len(fcs) else None
        if x:
            T = (x[0] + x[1]) / 2
            rh = x[2]
        elif f.get('doy'):
            doy = ((f['doy'] - 1 + d) % 365) + 1
            cd = C.clim_day(doy)
            T = cd.tmean
            rh = C.N.rh[min(11, math.floor((doy - 1) / 30.42))]
        else:
            y = fcs[-1] if fcs else None
            T = (y[0] + y[1]) / 2 if y else f['T']
            rh = y[2] if y else f['rh']
        frac = gdd / tot
        g = (ps['r'] * (0.25 + 0.75 * A.pest_temp_f(ps, T)) * (0.5 + 0.5 * A.pest_rh_f(ps, rh)) *
             (1 if A.in_window(ps, frac) else 0.5) - 0.02 - 1.2 * res * eff)
        sev = A.logistic_forward(sev, g)
        if sev > best:
            best = sev
        res *= decay
        if res < 0.02:
            res = 0.0
        gdd += A.gdd_rate(T, crop)
    return best


def per_issue(fcs, p1, p2):
    """The planting check's forecasts issue by issue (−1 when missing)."""
    out = []
    for j in range(3):
        lst = [fcs[j] if j < len(fcs) else None, p1[j + 1] if p1 else None, p2[j + 2] if p2 and j + 2 <= 3 else None]
        if j == 2:
            lst = lst[:2]
        for x in lst:
            if x:
                out += [x['rainMm'], x['rainProb'], x['tmax']]
            else:
                out += [-1, -1, -1]
    return out


def _ep(f, max_rain3, tmax3=None):
    return A.establish_p(max_rain3, f['tmax3'] if tmax3 is None else tmax3, f['thetaRel'], f['transplant'], f['tMaxCrop'], f['wlTol'])


def hourly_heat(f, fcs, rain_max3):
    th = f.get('tempH') or []
    h0 = 12 if f.get('hour') is None else f['hour']
    hi = [-99.0, -99.0, -99.0]
    for q in range(len(th)):
        day = math.floor((h0 + q) / 24)
        if day > 2:
            break
        if th[q] > hi[day]:
            hi[day] = th[q]
    if not len(th):
        for j in range(3):
            hi[j] = fcs[j]['tmax'] if j < len(fcs) and fcs[j] else -99
    t_so_far = f.get('tSoFar')
    if t_so_far is not None and t_so_far > hi[0]:
        hi[0] = t_so_far
    hot = max(hi)
    return [hi[0], hi[1], hi[2], hot - f['tMaxCrop'], hi[1] - fcs[1]['tmax'] if len(fcs) > 1 and fcs[1] else 0, _ep(f, rain_max3, hot)]


def issue_spread(f, fcs, today):
    wet, dry, hot, cool = [], [], [], []
    p1, p2 = f.get('prev1'), f.get('prev2')
    for j in range(3):
        lst = [fcs[j] if j < len(fcs) else None]
        if p1 and p1[j + 1]:
            lst.append(p1[j + 1])
        if p2 and j + 2 <= 3 and p2[j + 2]:
            lst.append(p2[j + 2])
        xs = [x for x in lst if x]
        if not xs:
            wet.append(0.0)
            dry.append(0.0)
            hot.append(-99.0)
            cool.append(-99.0)
            continue
        r = [exp_rain_day(x) for x in xs]
        tm = [x['tmax'] for x in xs]
        floor = (f.get('rainSoFar') or 0) if j == 0 else 0
        wet.append(max(floor, *r))
        dry.append(max(floor, min(r)))
        hot.append(max(tm))
        cool.append(min(tm))
    p_w = _ep(f, max(wet))
    p_d = _ep(f, max(dry))
    hottest = max(hot)
    hd = hot.index(hottest)
    return [wet[0], dry[0], wet[1], dry[1], wet[2], dry[2], p_w, p_d, hottest - f['tMaxCrop'], (cool[hd] if hd >= 0 else -99) - f['tMaxCrop']]


def rain_hour_edges(f, d_sum, d_max):
    so = f.get('rainSoFar') or 0
    lo = [so + max(0.0, d_sum[0] - d_max[0]), max(0.0, d_sum[1] - d_max[1]), max(0.0, d_sum[2] - d_max[2])]
    hi = [so + d_sum[0] + d_max[0], d_sum[1] + d_max[1], d_sum[2] + d_max[2]]
    p_lo = _ep(f, max(lo))
    p_hi = _ep(f, max(hi))
    return [p_lo, p_hi, p_lo - p_hi, max(hi) - max(lo)]


def _feat_establish_full(f):
    prior = A.establish_prob(f)
    fcs = f.get('fc') or []
    a = 2 * math.pi * (f.get('doy') or 180) / 365
    today = (f.get('rainSoFar') or 0) + (f.get('rainRest') or 0)
    rh = f.get('rainH') or []
    h0 = 12 if f.get('hour') is None else f['hour']
    d_sum = [0.0, 0.0, 0.0]
    d_max = [0.0, 0.0, 0.0]
    wet1 = 0
    for q in range(len(rh)):
        day = math.floor((h0 + q) / 24)
        if day > 2:
            break
        v = rh[q]
        d_sum[day] += v
        if v > d_max[day]:
            d_max[day] = v
        if day == 1 and v > 0:
            wet1 += 1
    hourly = _ep(f, max(today, d_sum[1], d_sum[2]))
    p1, p2 = f.get('prev1'), f.get('prev2')
    tmax_all, spread_hot, rain_max, rain_sum, likely = -99.0, 0.0, 0.0, 0.0, 0
    for j in range(3):
        lst = [(fcs[j] if j < len(fcs) else None, j)]
        if p1 and p1[j + 1]:
            lst.append((p1[j + 1], j + 1))
        if p2 and j + 2 <= 3 and p2[j + 2]:
            lst.append((p2[j + 2], j + 2))
        wsum = tm = rm = 0.0
        lo, hi = 99.0, -99.0
        for x, lead in lst:
            if not x:
                continue
            wgt = 1 / (0.5 + 0.35 * lead) ** 2
            wsum += wgt
            tm += wgt * x['tmax']
            rm += wgt * exp_rain_day(x)
            lo = min(lo, x['tmax'])
            hi = max(hi, x['tmax'])
            if x['rainProb'] > 0.5:
                likely += 1
        if not wsum:
            continue
        tm /= wsum
        rm /= wsum
        if tm > tmax_all:
            tmax_all = tm
            spread_hot = hi - lo
        v = max(rm, today) if j == 0 else rm
        rain_max = max(rain_max, v)
        rain_sum += v
    all_issues = _ep(f, rain_max, tmax_all)
    out = feat_establish(f)
    out += [prior, f['tmax3'] - f['tMaxCrop'], max(x['rainProb'] for x in fcs) if fcs else 0, sum(x['rainMm'] for x in fcs), _sin(a), _cos(a),
            f.get('eSd') or 0, h0, f.get('rainSoFar') or 0, f.get('rainRest') or 0, today - (exp_rain_day(fcs[0]) if fcs else 0),
            -99 if f.get('tSoFar') is None else f['tSoFar'], d_sum[1], d_sum[2], d_max[0], d_max[1], d_max[2], wet1, hourly,
            tmax_all, tmax_all - f['tMaxCrop'], spread_hot, rain_max, rain_sum, likely, all_issues, (1 if p1 else 0) + (1 if p2 else 0)]
    out += per_issue(fcs, p1, p2)
    out += hourly_heat(f, fcs, max(today, d_sum[1], d_sum[2]))
    out += rain_hour_edges(f, d_sum, d_max)
    out += issue_spread(f, fcs, today)
    return out


def _feat_stress_one(f):
    return stress_features_batch([f])[0].tolist()


def _feat_pest_full(f):
    pr = pest_prior(f)
    ps = f['pest']
    fcs = f.get('fc') or []
    g = 0.0
    inw = 1 if A.in_window(ps, f['frac']) else 0.5
    for x in fcs:
        T = (x[0] + x[1]) / 2
        g += ps['r'] * (0.25 + 0.75 * A.pest_temp_f(ps, T)) * (0.5 + 0.5 * A.pest_rh_f(ps, x[2])) * inw - 0.02
    pesticide = f.get('pesticide')
    eff = D.pesticide_efficacy(pesticide, f.get('pestId')) if pesticide else 0
    cur = f.get('curSev') or 0
    since = f.get('sinceSpray')
    return feat_pest(f) + [pr, logit(pr) - logit(A.ETL) if pr > 0 else -10, logit(cur) if cur > 0 else -10, f.get('nbr2') or 0,
                           min(60, 60 if since is None else since), 1 if f.get('outbreak') else 0, f.get('residue') or 0, eff,
                           g / len(fcs) if fcs else 0, f.get('bedSeen') or 0, f.get('bedMax') or 0, f.get('bedShare') or 0,
                           f.get('otherSev') or 0, f.get('vote') or 0]


def _feat_yield(f):
    c = f['crop']
    e = f['exp']
    season = 0.7 + 0.3 * f['monthFit']
    heat = clamp(1 - 0.012 * e['heat'], 0.45, 1)
    return [f['sfMean'], f['sfMin'], f['sfBed'], f['monthFit'], e['tempF'], e['heat'], e['rainRatio'], c['ky'], c['durationDays'] / 100,
            f['unsuit'], f['suit'], 1 if f.get('soilCheck') else 0, 1 if f.get('smartWater') else 0, 1 if f.get('spotSpray') else 0,
            1 if c.get('ponded') else 0, 1 if c['harvest']['type'] == 'multi' else 0, 1 if c.get('establishment') == 'transplant' else 0,
            f['sfMean'] * season * e['tempF'] * heat, f['nCells'] if f.get('nCells') is not None else 24,
            c['wlTol'] if c.get('wlTol') is not None else 0.3, c['tMax']]


def _feat_harvest_full(f):
    return feat_harvest(f) + [f['frac'] - (f['start'] if f['multi'] else 1)]


def _feat_spray_full(f):
    rh = f.get('rainH') or []
    return feat_spray(f) + [A.spray_efficacy(f), max(rh) if len(rh) else f['rain6'], sum(1 for v in rh if v > 0)]


def _pest_skip(f):
    return not ((f.get('curSev') or 0) > 0) and not ((f.get('otherSev') or 0) > 0) and not ((f.get('nbr2') or 0) > 0)


def _pest_post(f, p):
    if not ((f.get('curSev') or 0) > 0) and (f.get('announced') or 0) > 0:
        return max(p, f['announced'])
    return p


_EST_NAMES = (EST_F + ['establishment formula on the forecast', 'forecast heat above the crop limit °C', 'highest rain chance next 3 d',
                       'forecast rain next 3 d mm (unweighted)', 'season (sin)', 'season (cos)', 'estimate uncertainty', 'hour of the check',
                       'rain so far today (gauge) mm', 'hourly forecast rain rest of today mm', 'today so far + rest vs daily forecast',
                       'highest temperature so far today °C', 'hourly forecast rain tomorrow mm', 'hourly forecast rain day 3 mm',
                       'heaviest forecast hour today mm', 'heaviest forecast hour tomorrow mm', 'heaviest forecast hour day 3 mm',
                       'forecast rainy hours tomorrow', 'formula with the hourly forecast rain', 'max temperature next 3 d, all issues',
                       'heat above the crop limit, all issues', 'spread of the issues for the hottest day', 'max daily rain next 3 d, all issues',
                       'rain next 3 d, all issues', 'issues calling rain likely', 'formula on all issues', 'earlier issues available']
              + [f'{kind} {day}, {by}' + (' mm' if kind == 'rain' else '')
                 for day, j in (('today', 0), ('tomorrow', 1), ('day 3', 2))
                 for by in ['issued today', 'issued yesterday', 'issued 2 days ago'][:2 if j == 2 else 3]
                 for kind in ('rain', 'rain chance', 'max temperature')]
              + ['hourly forecast high today °C', 'hourly forecast high tomorrow °C', 'hourly forecast high day 3 °C',
                 'hottest day by the hourly forecast, above the crop limit °C', 'hourly high tomorrow minus the daily forecast °C',
                 'formula with the hourly rain and temperatures', 'formula, one rain hour fewer each day', 'formula, one rain hour more each day',
                 'spread of the two', 'heaviest day ± one rain hour, range mm', 'wettest call today mm', 'driest call today mm',
                 'wettest call tomorrow mm', 'driest call tomorrow mm', 'wettest call day 3 mm', 'driest call day 3 mm', 'formula on the wettest calls',
                 'formula on the driest calls', 'hottest call above the crop limit °C', 'coolest call for the hottest day above the limit °C'])

V2 = {
    'soil': Obj(type='cls', classes=['Unsuitable', 'Needs amendment', 'Suitable'], features=SOIL_F, feat=lambda f: feat_soil(f['s'], f['crop'])),
    'harvest': Obj(type='cls', classes=['Wait', 'Ready'], features=HARV_F + ['progress past picking start or maturity'], feat=_feat_harvest_full),
    'spray': Obj(type='reg', features=SPRAY_F + ['efficacy formula on what the farm knows', 'wettest forecast hour mm', 'forecast rainy hours'],
                 feat=_feat_spray_full),
    'establish': Obj(type='reg', features=_EST_NAMES, feat=_feat_establish_full),
    'stress': Obj(type='reg', cap=72, feat=_feat_stress_one, features=[
        'depletion (filtered estimate)', 'crop depletion limit p', 'Kc', 'available water mm', 'sand %', 'hour', 'estimate uncertainty',
        'water left before stress mm', 'crop water use today mm', 'hours to stress if dry (forecast ET)', 'hours to stress with daily forecast rain',
        'hours to stress with hourly forecast rain', 'same, rain spells filled in', 'same, hourly rain for 24 h then daily', 'expected rain today mm',
        'expected rain tomorrow mm', 'expected rain day 3 mm', 'rain chance today', 'rain chance tomorrow', 'rain chance day 3', 'ET₀ today',
        'ET₀ tomorrow', 'max temperature today', 'hourly forecast rain 0–12 h', '12–24 h', '24–36 h', '36–48 h', '48–72 h',
        'forecast rainy hours 0–24 h', 'forecast rainy hours 24–72 h', 'hours to first forecast rain', 'forecast rain before dry-weather stress mm',
        'drainage rate']),
    'pest': Obj(type='cls', classes=['Stays below 8 %', 'Reaches 8 %'], feat=_feat_pest_full, skip=_pest_skip, post=_pest_post,
                features=PEST_F + ['severity grown 7 days by the forecast', 'its log-odds above the threshold', 'camera severity log-odds',
                                   'neighbour severity (2 squares)', 'days since this plant was sprayed', 'outbreak in the bed', 'spray residue (sensor)',
                                   'product efficacy on this pest', 'pest growth per day under the forecast', 'plants in the bed seen with this pest',
                                   'highest severity of it in the bed', "its share of the bed's sightings", 'another pest seen on this plant',
                                   'camera label confidence']),
    'yield': Obj(type='reg', feat=_feat_yield, features=[
        'soil factor (squares, mean)', 'soil factor (worst square)', 'soil factor (bed average soil)', 'sowing-month fit', 'expected temperature fit',
        'expected heat at flowering', 'rain ÷ crop water need', 'crop Ky', 'crop duration (100 d)', 'squares the soil model calls unsuitable',
        'soil model suitability', 'soil check on', 'smart irrigation on', 'spot spraying on', 'paddy (ponded)', 'repeated picking', 'transplanted',
        'expected yield (formula)', 'squares in the bed', 'crop tolerance of waterlogging', 'crop heat limit °C']),
}

# the seven models as the dashboard describes them (the numbers come from the offline report)
SPECS = [
    Obj(id='soil', title='Soil suitability', task='Is this square right for this crop: suitable, needs amendment, or unsuitable?',
        metric='correct class (from the probe reading, against the rules on the true soil)'),
    Obj(id='pest', title='Pest risk (7 days)', task='Will this plant reach the 8 % action threshold within a week if nothing is sprayed?',
        metric='right call on reaching 8 % within 7 days (accuracy and balanced accuracy)'),
    Obj(id='yield', title='Yield forecast', task='Season yield as a share of what the crop could give, forecast when the bed is planned',
        metric='season yield within ±10 points of the actual % of attainable'),
    Obj(id='establish', title='Planting day', task='Chance a square establishes if planted today',
        metric="within ±5 points of the simulator's true chance; and the same plant/wait call"),
    Obj(id='stress', title='Time to water stress', task="Hours until the root zone reaches the crop's stress limit if not watered",
        metric='within ±6 h or ±20 % (whichever is larger), plants that hit stress within 3 days'),
    Obj(id='spray', title='Spray efficacy', task='Share of pests a spot spray kills at a given hour', metric='within ±5 points of the share actually killed'),
    Obj(id='harvest', title='Harvest readiness', task='Is this plant ready to harvest or pick?', metric='right ready / not-ready call when the arm arrives'),
]
SPEC = {s.id: s for s in SPECS}


def calib(p, t):
    """Pest: the forest's raw score is re-mapped so that 0.5 is the decision point chosen on validation farms."""
    if t is None or t == 0.5:
        return p
    return 0.5 * p / t if p < t else 0.5 + 0.5 * (p - t) / (1 - t)


class ModelSuite:
    """The seven forests plus per-farm live bookkeeping (the dashboard's running tallies)."""

    def __init__(self):
        self.m = {}
        self.metrics = {}
        self.live = {}
        self.cal = {}
        self.cap = {}
        self.extra = {}
        self.ready = False
        self.total_ms = 0
        self.version = ''
        self.built = ''

    def load(self, pack):
        import time
        t0 = time.time()
        for mid, (meta, arr) in pack['models'].items():
            of = meta.get('of') or mid
            V = V2[of]
            rf = RandomForest.from_arrays({'type': meta['type'], 'nClasses': meta.get('nClasses'), 'F': meta.get('F'), 'combine': meta.get('combine'),
                                           'featureNames': V.features, 'classNames': V.classes or []},
                                          arr['feat'], arr['thr'], arr['left'], arr['right'], arr['val'], arr['roots'])
            if meta.get('depth') is not None:
                rf.depth = int(meta['depth'])
            self.m[mid] = rf
            self.cal[mid] = meta.get('threshold')
            self.cap[mid] = meta.get('cap')
            nodes = rf.node_count()
            if meta.get('of'):
                self.extra[mid] = {'of': of, 'nodes': nodes, 'trees': meta['nTrees']}
                continue
            met = Obj(id=mid, type=meta['type'], classes=V.classes or None, trees=meta['nTrees'], nodes=nodes, features=len(V.features))
            met.update(SPEC[of])
            met.update(meta.get('report') or {})
            self.metrics[mid] = met
            self.live[mid] = Obj(n=0, err=0.0, hit=0, sq=0.0)
        for mid, x in self.extra.items():
            if x['of'] in self.metrics:
                self.metrics[x['of']]['nodes'] += x['nodes']
                self.metrics[x['of']]['trees'] += x['trees']
        self.version = pack.get('version', '')
        self.built = pack.get('built', '')
        self.total_ms = (time.time() - t0) * 1000
        self.ready = True
        return self

    _shipped = None

    @classmethod
    def shipped(cls):
        if cls._shipped is None:
            cls._shipped = ModelSuite().load(load_pack())
        return cls._shipped

    def view(self):
        """The same forests with this farm's own live-accuracy counters."""
        v = object.__new__(ModelSuite)
        v.__dict__.update(self.__dict__)
        v.live = {mid: Obj(n=0, err=0.0, hit=0, sq=0.0) for mid in self.m}
        return v

    # ── predictions from what the farm knows (contexts built by the agent) ──
    def soil_proba(self, s, c):
        return self.m['soil'].predict_proba(feat_soil(s, c))

    def soil_proba_batch(self, soils, c):
        X = np.array([feat_soil(s, c) for s in soils])
        return self.m['soil'].predict_proba_batch(X)

    def pest_risk(self, f):
        V = V2['pest']
        p = calib(V.post(f, 0.0 if V.skip(f) else self.m['pest'].predict_proba(_feat_pest_full(f))[1]), self.cal.get('pest'))
        # with the optional trap network on, a trap alert makes every checked plant count as at risk (0.5 is the
        # decision point, below the spray trigger)
        return max(p, 0.5) if f.get('trapAlert') else p

    def pest_risk_batch(self, ctxs):
        V = V2['pest']
        out = [0.0] * len(ctxs)
        ask = [i for i, f in enumerate(ctxs) if not V.skip(f)]
        if ask:
            X = np.array([_feat_pest_full(ctxs[i]) for i in ask])
            P = self.m['pest'].predict_proba_batch(X)[:, 1]
            for i, p in zip(ask, P.tolist()):
                out[i] = p
        res = []
        for f, p in zip(ctxs, out):
            q = calib(V.post(f, p), self.cal.get('pest'))
            res.append(max(q, 0.5) if f.get('trapAlert') else q)
        return res

    def yield_frac(self, f):
        return clamp(self.m['yield'].predict(_feat_yield(f)), 0, 1.25)

    def yield_frac_batch(self, ctxs):
        X = np.array([_feat_yield(f) for f in ctxs])
        return np.clip(self.m['yield'].predict_batch(X), 0, 1.25)

    def _est_model(self, f):
        return self.m['establishT'] if f.get('transplant') and 'establishT' in self.m else self.m['establish']

    def establish(self, f):
        return clamp(self._est_model(f).predict(_feat_establish_full(f)), 0.02, 0.99)

    def establish_batch(self, ctxs):
        if not ctxs:
            return []
        m = self._est_model(ctxs[0])
        X = np.array([_feat_establish_full(f) for f in ctxs])
        return np.clip(m.predict_batch(X), 0.02, 0.99).tolist()

    def hours_to_stress(self, f):
        return max(0.0, self.m['stress'].predict(_feat_stress_one(f)))

    def hours_to_stress_batch(self, ctxs):
        """All plants of one check (sharing its forecasts)."""
        if not ctxs:
            return []
        X = stress_features_batch(ctxs)
        return np.maximum(0.0, self.m['stress'].predict_batch(X)).tolist()

    def spray_eff(self, f):
        return clamp(self.m['spray'].predict(_feat_spray_full(f)), 0, 1)

    def spray_eff_batch(self, ctxs):
        X = np.array([_feat_spray_full(f) for f in ctxs])
        return np.clip(self.m['spray'].predict_batch(X), 0, 1).tolist()

    def harvest_ready(self, f):
        return self.m['harvest'].predict_proba(_feat_harvest_full(f))[1]

    # ── live bookkeeping kept for the dashboard ──
    def reset_live(self):
        for mid in self.live:
            self.live[mid] = Obj(n=0, err=0.0, hit=0, sq=0.0)

    def record_hit(self, mid, hit):
        L = self.live.get(mid)
        if L is None:
            return
        L.n += 1
        L.hit += 1 if hit else 0

    def record_plan(self, mid, predicted, actual):
        L = self.live.get(mid)
        if L is None:
            return
        L.nPlan = (L.nPlan or 0) + 1
        L.errPlan = (L.errPlan or 0) + abs(predicted - actual)

    def record(self, mid, predicted, actual, kind):
        L = self.live.get(mid)
        if L is None:
            return
        L.n += 1
        if kind == 'reg':
            L.err += abs(predicted - actual)
            L.sq += (predicted - actual) ** 2
        else:
            L.hit += 1 if (1 if predicted >= 0.5 else 0) == actual else 0
            L.sq += (predicted - actual) ** 2
            L.predSum = (L.predSum or 0) + predicted
            L.actSum = (L.actSum or 0) + actual
