"""Draws of the coming hours' weather given what is known at a moment (paddy v2): the posterior of the weather
generator's days given the forecasts issued then. Used by the farm's time-to-AWD estimate (sim/advisor.py) and by the
time-to-AWD ceiling (tools/models/ceiling_awd.py).

How the weather is made (core/climate.py): each day from a wet/dry Markov chain, a gamma rain amount, one shower whose
start and length come from uniforms, and normal noise on the high and the low; event modifiers (dry spell, storm,
overcast week) change days deterministically. How the forecasts are made (standard mode):

* hourly, issued at a moment for each coming hour: the rain rate the hour starts with, missed with probability
  0.08 + 0.01 × lead (h) and then called dry; a dry hour "missed" is called 1.5 mm/h 15 % of the time; the temperature
  ± (0.3 + 0.04 × lead) °C, uniform;
* daily, issued each day for the next days: a chance of rain from a skill drawn in a range that depends on whether the
  day is wet and on a miss (more likely at longer lead), shrunk towards climatology with lead; highs ± (0.5 + 0.35 ×
  lead) °C and lows ± 0.8 × that, uniform.

Treating every hashed or random number as unknown and uniform (or normal, as the generator draws it), the days are
independent given the wet/dry states of the chain, and so are the forecasts. Each day's posterior is sampled by
weighting prior draws by the likelihood of everything forecast for it (an observed rain rate is matched within ±2 % and
then used exactly; the daily chance of rain; the highs and lows, and the hourly temperatures from 06:00 to 14:00, which
depend on that day's high and low only); the wet/dry pattern of the days is drawn from its exact posterior; and each
draw's high and low come from the same weighted sample. The daily rain amount is not used (for a dry day it is an exact
function of the chance of rain, a quirk of the simulated forecaster that no real forecast would give away).

What the farm knows (``Obs.from_weather(..., farm=True)``): the forecasts, the events announced so far, whether it
rained yesterday, and today's weather so far from its own station (a shower under way: its start and rate, not how long
it will last). The ceiling is a little more generous (the generator's own wet/dry state of yesterday; a shower under
way known to its end).
"""
from __future__ import annotations

import math
from itertools import product

import numpy as np

from ..core import climate as C
from ..core.util import date_info

DAY, HOUR = 86400.0, 3600.0
N = C.N
EPS = 0.02          # an observed rain rate is matched within ±2 %


class Obs:
    """What is known at t0 (seconds from the season start): the forecasts issued then and the past.

    rain_fc, temp_fc: the hourly forecast for t0 + q h (q = 0, 1, …); day_fc: (chance of rain, high, low) of the daily
    forecast issued on t0's day for leads 0–4; mods: the event modifiers in force at t0; prev_wet: the chain's state the
    day before; today: the whole of today's weather when it is known (dict with wet, rain, start, dur, tmax, tmin,
    cloud), else None; today_partial: a shower under way at t0 (dict with start and rate), else None; today_tmin /
    today_tmax: today's low / high when already past."""

    def __init__(self, t0, start_year, start_month, rain_fc, temp_fc, day_fc, mods, prev_wet, today=None, today_tmin=None,
                 today_tmax=None, today_partial=None):
        self.t0, self.start_year, self.start_month = float(t0), start_year, start_month
        self.rain_fc, self.temp_fc, self.day_fc = list(rain_fc), list(temp_fc), list(day_fc)
        self.mods, self.prev_wet = list(mods), bool(prev_wet)
        self.today, self.today_tmin, self.today_tmax = today, today_tmin, today_tmax
        self.today_partial = today_partial

    @staticmethod
    def from_weather(W, t0, hours=76, farm=False):
        """The observation at t0 from a Weather, as it stands at t0 (call it then: later events are not yet added)."""
        d0 = int(t0 // DAY)
        h0 = (t0 % DAY) / HOUR
        fh = [W.forecast_hour(t0, t0 + q * HOUR) for q in range(hours)]
        fd = [W.forecast_day(d0, k) for k in range(5)]
        today, partial = None, None
        dd = W.day(d0)
        if dd.wet and dd.rain > 0 and dd.rainStart < h0:        # today's shower has begun
            ended = dd.rainStart + dd.rainDur <= h0
            if not farm:
                today = {'wet': True, 'rain': dd.rain, 'start': dd.rainStart, 'dur': dd.rainDur, 'tmax': dd.tmax, 'tmin': dd.tmin,
                         'cloud': dd.cloud, 'wet_n': bool(dd.base[6])}
            elif ended:
                # the farm's gauge has seen the whole shower; the high is known once past (14:30), the day's cloud once
                # the day is over (18:00): until then they are drawn
                today = {'wet': True, 'rain': dd.rain, 'start': dd.rainStart, 'dur': dd.rainDur, 'tmax': dd.tmax if h0 >= 14.5 else None,
                         'tmin': dd.tmin if h0 >= 6 else None, 'cloud': dd.cloud if h0 >= 18 else None, 'wet_n': True}
            else:
                partial = {'start': dd.rainStart, 'rate': dd.rain / dd.rainDur}
        if d0 >= 1:
            prev = W.day(d0 - 1).wet if farm else W.days[d0 - 1].base[6]
        else:
            prev = False
        return Obs(t0, W.start_year, W.start_month, [x.rain for x in fh], [x.temp for x in fh],
                   [(x.rainProb, x.tmax, x.tmin) for x in fd], [dict(m) for m in W.mods], prev, today,
                   today_tmin=dd.tmin if h0 >= 6 else None, today_tmax=dd.tmax if h0 >= 14.5 else None, today_partial=partial)


def _chain(m0):
    p = min(0.6, max(0.002, N.rainDays[m0] / C.DAYS_IN_MONTH[m0]))
    p11 = min(0.9, p + 0.35)
    p01 = min(0.9, max(0.001, p * (1 - p11) / (1 - p)))
    return p01, p11


def _mods_for(mods, d):
    return [m for m in mods if m['from'] <= d < m['to']]


def derive_rain(d, di, mods_d, wet_n, R, u1, u2, u3):
    """The generator's day (rain, shower and cloud) for candidate natural draws, vectorised (Weather._finish)."""
    K = len(u1)
    m = di.m - 1
    wet = np.full(K, bool(wet_n))
    rain = np.array(R, float) if wet_n else np.zeros(K)
    for mod in mods_d:
        k = mod['kind']
        if k == 'dry':
            wet[:] = False
            rain[:] = 0.0
        if k == 'storm' and d == mod['from']:
            wet[:] = True
            rain[:] = float(mod['rain'])
        if k == 'cloudy':
            s = u3 < 0.5
            rain = np.where(s, np.maximum(rain * 0.3, 1.5 + 4 * u2), rain)
            wet = wet | s
    cloud = np.where(wet, 0.55 + 0.4 * u2, np.clip(N.cloudDry[m] + 0.25 * (u2 - 0.5), 0.02, 0.8))
    for mod in mods_d:
        k = mod['kind']
        if k == 'dry':
            cloud = 0.05 + 0.12 * u2
        if k == 'cloudy':
            cloud = 0.86 + 0.1 * u2
        if k == 'storm' and d == mod['from']:
            cloud = np.full(K, 0.97)
    conv = 4 <= di.m <= 11
    dur = np.where(wet, np.clip(1 + rain / 12 + 2 * u3, 1, 10), 0.0)
    start = np.where(wet, 13 + 6 * u1 if conv else 24 * u1, 0.0)
    for mod in mods_d:
        if mod['kind'] == 'storm' and d == mod['from']:
            start = np.full(K, float(mod['hour']))
            dur = np.full(K, 5.0)
    start = np.where(start + dur > 23.99, np.maximum(0.0, 23.99 - dur), start)
    return wet, rain, start, dur, cloud


def temp_shift(di, mods_d, wet, rain):
    """The normal high and low of each candidate day given its wetness and events (before the generator's noise)."""
    m = di.m - 1
    dmax = np.where(wet, -(1.5 + np.minimum(4, 0.03 * rain)), 0.0)
    dmin = np.where(wet, 0.5, 0.0)
    for mod in mods_d:
        if mod['kind'] == 'dry':
            dmax = dmax + 2.5
            dmin = dmin + 0.8
        if mod['kind'] == 'cloudy':
            dmax = dmax - 2
    return N.tmax[m] + dmax, N.tmin[m] + dmin


def _pop_lik(k, rp, wet, rain, dry_mod, p_clim):
    """Likelihood of the daily forecast's chance of rain (lead k) for each candidate day."""
    w = min(0.8, 0.13 * k)

    def band(a, b):
        lo, hi = a + (p_clim - a) * w, b + (p_clim - b) * w
        return (1.0 / ((b - a) * (1 - w))) if lo - 1e-9 <= rp <= hi + 1e-9 else 0.0
    if dry_mod:
        l_dry = band(0.01, 0.07)
    else:
        qd = 0.08 + 0.04 * k
        l_dry = qd * band(0.45, 0.85) + (1 - qd) * band(0.03, 0.27)
    qw = 0.12 + 0.05 * k + np.where(rain < 3, 0.25, 0.0)
    l_wet = qw * band(0.08, 0.33) + (1 - qw) * band(0.55, 0.95)
    return np.where(wet, l_wet, l_dry)


def _tdens(TX, TN, I, mu_x, mu_n):
    """Prior density (up to a constant) of each proposal's high and low for each candidate's normal values."""
    return np.exp(-0.5 * ((TX[None, :] - mu_x[:, None]) / 1.2) ** 2 - 0.5 * ((TN[None, :] - mu_n[:, None]) / 0.9) ** 2) * I[None, :]


def _gamma_pdf(x, k, theta):
    return np.where(x > 0, np.exp((k - 1) * np.log(np.maximum(x, 1e-300)) - x / theta - math.lgamma(k) - k * math.log(theta)), 0.0)


class Sampler:
    """Draws of the next ``hours`` hours (temperature at mid-hour, rain, reference evapotranspiration) given an Obs.

    K candidate days per day and wet/dry state (K_seen when the hourly forecast shows that day's rain rate: those are
    first filtered on the rate, which few prior draws match); M proposals for each day's high and low."""

    def __init__(self, obs, hours=73, K=6000, K_seen=60000, M=192, rng=None):
        self.o = obs
        self.hours = hours
        self.K, self.K_seen, self.M = K, K_seen, M
        self.rng = rng if rng is not None else np.random.default_rng(1)
        self.diag = {}
        self.d0 = int(obs.t0 // DAY)
        self.h0 = (obs.t0 % DAY) / HOUR
        self._hrs = {}
        for q in range(min(hours, len(obs.rain_fc))):
            t = obs.t0 + q * HOUR
            self._hrs.setdefault(int(t // DAY), []).append((q, int(round((t % DAY) / HOUR))))

    def _days(self):
        d_end = int((self.o.t0 + (self.hours - 1) * HOUR) // DAY)
        return self.d0, d_end

    def _hours_of(self, d):
        """(q, hour of day) of the forecast hours that fall on day d, within the window."""
        return self._hrs.get(d, [])

    def _seen_rate(self, d):
        rates = {round(self.o.rain_fc[q], 9) for q, _ in self._hours_of(d) if self.o.rain_fc[q] > 0 and abs(self.o.rain_fc[q] - 1.5) >= 1e-9}
        if not rates:
            return None
        return max(rates) if len(rates) == 1 else -1.0       # -1: two rates on one day (cannot happen with one shower)

    def _rain_lik(self, d, wet, rain, start, dur):
        o = self.o
        L = np.ones(len(wet))
        rate = np.where(wet & (dur > 0), rain / np.where(dur > 0, dur, 1.0), 0.0)
        for q, h in self._hours_of(d):
            fc = o.rain_fc[q]
            miss = 0.08 + 0.01 * q
            cr = np.where(wet & (start <= h) & (h < start + dur), rate, 0.0)
            if fc == 0:
                L *= np.where(cr > 0, miss, 1 - 0.15 * miss)
            elif abs(fc - 1.5) < 1e-9:
                L *= np.where(cr > 0, 0.0, 0.15 * miss)
            else:
                L *= np.where((cr > 0) & (np.abs(cr - fc) <= EPS * fc), 1 - miss, 0.0)
        return L

    def _day_candidates(self, d, s):
        """Candidate days for natural state s that are consistent with the forecasts, their likelihood, and their mean
        likelihood over all prior draws (the day's marginal likelihood for that state)."""
        o, rng = self.o, self.rng
        di = date_info(o.start_year, o.start_month, d)
        m0 = di.m - 1
        mods_d = _mods_for(o.mods, d)
        seen = self._seen_rate(d)
        K = self.K_seen if seen is not None else self.K
        mean_wet = N.rain[m0] / max(0.3, N.rainDays[m0])
        R = np.clip(rng.gamma(0.75, mean_wet / 0.75, K), 0.6, 140.0) if s else np.zeros(K)
        u1, u2, u3 = rng.random(K), rng.random(K), rng.random(K)
        wet, rain, start, dur, cloud = derive_rain(d, di, mods_d, s, R, u1, u2, u3)
        keep = np.ones(K, bool)
        if seen is not None:
            rate = np.where(wet & (dur > 0), rain / np.where(dur > 0, dur, 1.0), 0.0)
            keep &= wet & (np.abs(rate - seen) <= EPS * seen) if seen > 0 else np.zeros(K, bool)
        if d == self.d0:      # nothing has fallen yet today: no shower began before t0
            keep &= ~(wet & (start < self.h0))
        ix = np.nonzero(keep)[0]
        wet, rain, start, dur, cloud = wet[ix], rain[ix], start[ix], dur[ix], cloud[ix]
        L = self._rain_lik(d, wet, rain, start, dur)
        k = d - self.d0
        dry_mod = any(m['kind'] == 'dry' for m in mods_d)
        if 0 <= k < len(o.day_fc):
            p_clim = 0.03 if dry_mod else min(0.6, max(0.01, N.rainDays[m0] / C.DAYS_IN_MONTH[m0]))
            L = L * _pop_lik(k, o.day_fc[k][0], wet, rain, dry_mod, p_clim)
        ok = L > 0
        wet, rain, start, dur, cloud, L = wet[ok], rain[ok], start[ok], dur[ok], cloud[ok], L[ok]
        # temperatures: the high and low from the prior, restricted to what the daily and hourly forecasts allow; a
        # common uniform proposal over the daily forecast's box, weighted by each candidate's own prior density
        mu_x, mu_n = temp_shift(di, mods_d, wet, rain)
        TX, TN, I = self._temp_proposals(d, k)
        lt = np.empty(len(L))
        for a in range(0, len(L), 2000):
            b = min(len(L), a + 2000)
            lt[a:b] = _tdens(TX, TN, I, mu_x[a:b], mu_n[a:b]).mean(axis=1)
        L = L * lt
        return {'d': d, 'L': L, 'ml': float(L.sum() / K), 'wet': wet, 'rain': rain, 'start': start, 'dur': dur, 'cloud': cloud,
                'seen': seen if seen is not None and seen > 0 else None, 'TX': TX, 'TN': TN, 'I': I, 'mu_x': mu_x, 'mu_n': mu_n}

    def _temp_proposals(self, d, k):
        o, M, rng = self.o, self.M, self.rng
        if d == self.d0 and o.today_tmax is not None and o.today_tmin is not None:
            TX, TN = np.full(M, o.today_tmax), np.full(M, o.today_tmin)
        else:
            sd = 0.5 + 0.35 * max(0, k)
            fx, fn = o.day_fc[min(max(0, k), len(o.day_fc) - 1)][1:]
            TX = fx + (rng.random(M) - 0.5) * 2 * sd
            TN = fn + (rng.random(M) - 0.5) * 1.6 * sd
            if d == self.d0 and o.today_tmin is not None:
                TN = np.full(M, o.today_tmin)
        I = np.ones(M)
        for q, h in self._hours_of(d):
            if 6 <= h <= 14:
                T = TN + (TX - TN) * (1 - math.cos(math.pi * (h - 6) / 8.5)) / 2
                I *= np.abs(T - o.temp_fc[q]) <= (0.6 + 0.08 * q) / 2 + 1e-9
        if I.sum() <= 0:            # too few proposals inside the hourly windows: keep the daily box only
            self.diag['temp_box_only'] = self.diag.get('temp_box_only', 0) + 1
            I = np.ones(M)
        return TX, TN, I

    def _face_value(self, d, n):
        """A day as forecast (when no candidate fits what was forecast): rain at the forecast rates, the forecast's
        high and low."""
        o = self.o
        hrs = [(q, h) for q, h in self._hours_of(d) if o.rain_fc[q] > 0 and abs(o.rain_fc[q] - 1.5) >= 1e-9]
        k = max(0, d - self.d0)
        fx, fn = o.day_fc[min(k, len(o.day_fc) - 1)][1:]
        if d == self.d0 and o.today_tmin is not None:
            fn = o.today_tmin
        if d == self.d0 and o.today_tmax is not None:
            fx = o.today_tmax
        if hrs:
            st, en = hrs[0][1], hrs[-1][1] + 1
            rate = o.rain_fc[hrs[0][0]]
            return (np.full(n, float(st)), np.full(n, float(en - st)), np.full(n, rate), np.full(n, fx), np.full(n, fn), np.full(n, 0.9),
                    np.ones(n, bool))
        return (np.zeros(n), np.zeros(n), np.zeros(n), np.full(n, fx), np.full(n, fn), np.full(n, 0.3), np.zeros(n, bool))

    def _today_partial(self, n):
        """Today with a shower under way at t0: its start and rate are known; its length is drawn from the generator's
        rule (length = 1 + amount / 12 + 2 u, amount = rate × length) weighted by the gamma prior of the amount, longer
        than it has run so far, and weighted by what the hourly forecast says of the hours left today."""
        o, rng = self.o, self.rng
        p = o.today_partial
        di = date_info(o.start_year, o.start_month, self.d0)
        m0 = di.m - 1
        r, s0 = p['rate'], p['start']
        K = self.K
        u3 = rng.random(K)
        if r < 12:
            dur = (1 + 2 * u3) / (1 - r / 12)
            J = (1 + 2 * u3) / (1 - r / 12) ** 2
        else:
            dur, J = np.full(K, 10.0), np.full(K, 10.0)
        over = dur > 10
        dur = np.where(over, 10.0, dur)
        J = np.where(over, 10.0, J)
        R = r * dur
        mean_wet = N.rain[m0] / max(0.3, N.rainDays[m0])
        w = _gamma_pdf(R, 0.75, mean_wet / 0.75) * J
        w *= (s0 + dur > self.h0) & (s0 + dur <= 24.0)
        wet = np.ones(K, bool)
        w *= self._rain_lik(self.d0, wet, R, np.full(K, s0), dur)
        if w.sum() <= 0:
            w = ((s0 + dur > self.h0)).astype(float) + 1e-12
        ci = rng.choice(K, size=n, p=w / w.sum())
        u2 = rng.random(n)
        cloud = 0.55 + 0.4 * u2
        TX, TN = self._today_temps(n)
        return (np.full(n, s0), dur[ci], np.full(n, r), TX, TN, cloud, np.ones(n, bool))

    def _today_temps(self, n):
        """Today's high and low for n draws: as observed when past, else from today's daily forecast box restricted to
        the hourly forecasts (as for any other day, for a wet day's normal values)."""
        o, rng = self.o, self.rng
        t = o.today or {}
        tx_obs = t.get('tmax') if t.get('tmax') is not None else o.today_tmax
        tn_obs = t.get('tmin') if t.get('tmin') is not None else o.today_tmin
        if tx_obs is not None and tn_obs is not None:
            return np.full(n, tx_obs), np.full(n, tn_obs)
        TX, TN, I = self._temp_proposals(self.d0, 0)
        if tn_obs is not None:
            TN = np.full(len(TN), tn_obs)
        di = date_info(o.start_year, o.start_month, self.d0)
        mu_x, mu_n = temp_shift(di, _mods_for(o.mods, self.d0), np.array([True]), np.array([float(t.get('rain', 5.0))]))
        w = _tdens(TX, TN, I, mu_x, mu_n)[0]
        if w.sum() <= 0:
            w = I + 1e-12
        mi = rng.choice(len(w), size=n, p=w / w.sum())
        return TX[mi], TN[mi]

    def draw(self, n):
        """n draws → (Th, Rh, Eh) arrays of shape (n, hours)."""
        o, rng = self.o, self.rng
        d0, d_end = self._days()
        days = list(range(d0, d_end + 1))
        fixed_today = o.today is not None or o.today_partial is not None
        free = [d for d in days if not (d == d0 and fixed_today)]
        cands = {d: [self._day_candidates(d, 0), self._day_candidates(d, 1)] for d in free}
        # the wet/dry pattern of the free days: exact posterior over the 2^n patterns (the chain starts from yesterday's
        # state, or today's when today is known)
        if o.today is not None:
            start_state = bool(o.today.get('wet_n', o.today['wet']))
        elif o.today_partial is not None:
            start_state = True
        else:
            start_state = o.prev_wet
        pats, ws = [], []
        for pat in product((0, 1), repeat=len(free)):
            w, prev = 1.0, start_state
            for d, s in zip(free, pat):
                p01, p11 = _chain(date_info(o.start_year, o.start_month, d).m - 1)
                pw = p11 if prev else p01
                w *= (pw if s else 1 - pw) * cands[d][s]['ml']
                prev = s
            pats.append(pat)
            ws.append(w)
        ws = np.array(ws)
        dead_days = [d for d in free if cands[d][0]['ml'] <= 0 and cands[d][1]['ml'] <= 0]
        self.diag['fallback_days'] = len(dead_days)
        if ws.sum() <= 0:
            # some day fits no candidate (e.g. an event the farm does not know of): those days as forecast, the others
            # from their own posterior with the chain ignored around them
            ws = np.array([np.prod([cands[d][s]['ml'] if d not in dead_days else 1.0 for d, s in zip(free, pat)]) for pat in pats])
            if ws.sum() <= 0:
                ws = np.ones(len(pats))
        pick = rng.choice(len(pats), size=n, p=ws / ws.sum())
        H = self.hours
        per = {}
        for d in days:
            if d == d0 and o.today is not None:
                t = o.today
                rate = t['rain'] / t['dur'] if t['wet'] and t['dur'] > 0 else 0.0
                tx, tn = self._today_temps(n)
                cl = np.full(n, t['cloud']) if t.get('cloud') is not None else 0.55 + 0.4 * rng.random(n)
                per[d] = (np.full(n, t['start']), np.full(n, t['dur']), np.full(n, rate), tx, tn, cl, np.full(n, bool(t['wet'])))
                continue
            if d == d0 and o.today_partial is not None:
                per[d] = self._today_partial(n)
                continue
            if d in dead_days:
                per[d] = self._face_value(d, n)
                continue
            st, du, ra, tx, tn, cl = (np.zeros(n) for _ in range(6))
            we = np.zeros(n, bool)
            j = free.index(d)
            states = np.array([pats[p][j] for p in pick])
            for s in (0, 1):
                rows = np.nonzero(states == s)[0]
                if not len(rows):
                    continue
                c = cands[d][s]
                if len(c['L']) == 0 or c['L'].sum() <= 0:
                    fv = self._face_value(d, len(rows))
                    st[rows], du[rows], ra[rows], tx[rows], tn[rows], cl[rows], we[rows] = fv
                    continue
                L = c['L']
                ci = rng.choice(len(L), size=len(rows), p=L / L.sum())
                st[rows], du[rows], cl[rows] = c['start'][ci], c['dur'][ci], c['cloud'][ci]
                we[rows] = c['wet'][ci]
                rr = np.where(c['dur'][ci] > 0, c['rain'][ci] / np.where(c['dur'][ci] > 0, c['dur'][ci], 1.0), 0.0)
                if c['seen'] is not None:
                    rr = np.where(c['wet'][ci], c['seen'], 0.0)      # the forecast's rate, exactly
                ra[rows] = rr
                # the high and low: one of the proposals, by this candidate's weight
                Wt = _tdens(c['TX'], c['TN'], c['I'], c['mu_x'][ci], c['mu_n'][ci])
                tot = Wt.sum(axis=1)
                Wt[tot <= 0] = c['I']
                cum = np.cumsum(Wt, axis=1)
                u = rng.random(len(rows)) * cum[:, -1]
                mi = np.minimum((cum < u[:, None]).sum(axis=1), Wt.shape[1] - 1)
                tx[rows], tn[rows] = c['TX'][mi], c['TN'][mi]
            per[d] = (st, du, ra, tx, tn, cl, we)
        # the low of the day after the window (the last evening's temperatures): from its daily forecast
        k_next = d_end + 1 - d0
        sd = 0.5 + 0.35 * k_next
        fn_next = o.day_fc[min(k_next, len(o.day_fc) - 1)][2]
        tn_next = fn_next + (rng.random(n) - 0.5) * 1.6 * sd
        Th, Rh, Eh = np.zeros((n, H)), np.zeros((n, H)), np.zeros((n, H))
        et_day = {}
        for d in days:
            st, du, ra, tx, tn, cl, we = per[d]
            di = date_info(o.start_year, o.start_month, d)
            ra_ = C.extraterrestrial(di.doy)
            e = 0.0023 * 0.408 * ra_ * ((tx + tn) / 2 + 17.8) * np.sqrt(np.maximum(0.5, tx - tn))
            et_day[d] = (e * np.where(cl > 0.8, 0.82, 1.0), C.et_weights(di.doy))
        for q in range(H):
            t = o.t0 + q * HOUR
            d = int(t // DAY)
            h = int(round((t % DAY) / HOUR))
            st, du, ra, tx, tn, cl, we = per[d]
            a = np.maximum(h, st)
            b = np.minimum(h + 1.0, st + du)
            Rh[:, q] = np.where(we & (b > a), ra * (b - a), 0.0)
            e, w = et_day[d]
            Eh[:, q] = e * w[h % 24]
            hm = h + 0.5
            ptx = per[d - 1][3] if (d - 1) in per else tx
            ntn = per[d + 1][4] if (d + 1) in per else tn_next
            if 6 <= hm <= 14.5:
                Th[:, q] = tn + (tx - tn) * (1 - math.cos(math.pi * (hm - 6) / 8.5)) / 2
            elif hm > 14.5:
                Th[:, q] = ntn + (tx - ntn) * (1 + math.cos(math.pi * (hm - 14.5) / 15.5)) / 2
            else:
                Th[:, q] = tn + (ptx - tn) * (1 + math.cos(math.pi * (hm + 9.5) / 15.5)) / 2
        return Th, Rh, Eh


def true_likelihood(W, obs, d):
    """The likelihood the sampler gives day d's true natural draws (a check of the sampler: it must be positive), and
    whether the vectorised generator rebuilds the true day exactly."""
    dd = W.day(d)
    noise_t, noise_tn, _, u1, u2, u3, bwet, brain = dd.base
    di = date_info(obs.start_year, obs.start_month, d)
    s = Sampler(obs, K=1, K_seen=1, M=1)
    mods_d = _mods_for(obs.mods, d)
    wet, rain, start, dur, cloud = derive_rain(d, di, mods_d, bwet, np.array([brain]), np.array([u1]), np.array([u2]), np.array([u3]))
    out = {'wet': bool(wet[0]), 'rain': float(rain[0]), 'start': float(start[0]), 'dur': float(dur[0]), 'cloud': float(cloud[0])}
    L = float(s._rain_lik(d, wet, rain, start, dur)[0])
    d0 = int(obs.t0 // DAY)
    k = d - d0
    if d == d0 and wet[0] and start[0] < (obs.t0 % DAY) / HOUR:
        L = 0.0
    if 0 <= k < len(obs.day_fc):
        dry_mod = any(m['kind'] == 'dry' for m in mods_d)
        m0 = di.m - 1
        p_clim = 0.03 if dry_mod else min(0.6, max(0.01, N.rainDays[m0] / C.DAYS_IN_MONTH[m0]))
        L *= float(_pop_lik(k, obs.day_fc[k][0], wet, rain, dry_mod, p_clim)[0])
        sd = 0.5 + 0.35 * k
        ok_t = abs(dd.tmax - obs.day_fc[k][1]) <= sd + 1e-9 and abs(dd.tmin - obs.day_fc[k][2]) <= 0.8 * sd + 1e-9
        for q, h in s._hours_of(d):
            if 6 <= h <= 14:
                T = dd.tmin + (dd.tmax - dd.tmin) * (1 - math.cos(math.pi * (h - 6) / 8.5)) / 2
                ok_t = ok_t and abs(T - obs.temp_fc[q]) <= (0.6 + 0.08 * q) / 2 + 1e-9
        out['temp_ok'] = ok_t
    out['L'] = L
    out['same_as_generator'] = (out['wet'] == dd.wet and abs(out['rain'] - dd.rain) < 1e-9 and abs(out['start'] - dd.rainStart) < 1e-9
                                and abs(out['dur'] - dd.rainDur) < 1e-9 and abs(out['cloud'] - dd.cloud) < 1e-9)
    return out
