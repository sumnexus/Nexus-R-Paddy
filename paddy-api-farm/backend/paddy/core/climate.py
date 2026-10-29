# Copied from the Agri Robotics Playground's Python port (agriplay/climate.py) for the Paddy API Farm; unchanged below.
"""Bengaluru weather: monthly normals → stochastic daily weather → hourly values;
Hargreaves ET₀; sun position and solar irradiance; forecasts (standard and realistic).
"""
from __future__ import annotations

import functools
import math
import struct

from .util import RNG, clamp, date_info, hash_n, lerp, Obj

LAT, LON = 12.97, 77.59  # Bengaluru
# Approximate monthly normals (Jan…Dec).
NORMALS = Obj(
    tmax=[27.9, 30.7, 33.2, 34.0, 32.9, 29.0, 27.6, 27.6, 28.2, 27.8, 26.6, 25.9],
    tmin=[15.8, 17.4, 19.8, 21.4, 21.1, 20.0, 19.6, 19.5, 19.3, 19.1, 17.3, 15.9],
    rain=[2, 7, 16, 47, 120, 105, 115, 147, 212, 168, 54, 16],  # mm / month
    rainDays=[0.3, 0.6, 1.2, 3.2, 7.0, 6.5, 7.5, 9.8, 10.0, 8.8, 4.0, 1.5],
    rh=[60, 52, 45, 54, 64, 74, 77, 78, 77, 76, 72, 66],  # % daily mean
    wind=[2.3, 2.3, 2.4, 2.5, 3.2, 4.5, 4.6, 4.1, 3.1, 2.3, 2.4, 2.4],  # m/s
    cloudDry=[0.12, 0.10, 0.12, 0.22, 0.35, 0.60, 0.66, 0.62, 0.52, 0.44, 0.30, 0.20],
)
N = NORMALS
DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
D2R = math.pi / 180
_sin, _cos, _exp, _sqrt = math.sin, math.cos, math.exp, math.sqrt


def declination(doy):
    return 0.409 * _sin(2 * math.pi / 365 * doy - 1.39)


def extraterrestrial(doy):
    """FAO-56 eq. 21 — extraterrestrial radiation, MJ m⁻² day⁻¹."""
    phi = LAT * D2R
    dr = 1 + 0.033 * _cos(2 * math.pi / 365 * doy)
    d = declination(doy)
    ws = math.acos(clamp(-math.tan(phi) * math.tan(d), -1, 1))
    return 24 * 60 / math.pi * 0.0820 * dr * (ws * _sin(phi) * _sin(d) + _cos(phi) * _cos(d) * _sin(ws))


def hargreaves(tmax, tmin, doy):
    """Hargreaves–Samani reference evapotranspiration (mm/day)."""
    ra = extraterrestrial(doy)
    tmean = (tmax + tmin) / 2
    return 0.0023 * 0.408 * ra * (tmean + 17.8) * _sqrt(max(0.5, tmax - tmin))


@functools.lru_cache(maxsize=1 << 16)   # paddy v2: memoised (a pure function of its two numbers; same results)
def sun_position(doy, hour):
    """Solar elevation and azimuth (radians) for local clock time (IST, UTC+5:30)."""
    d = declination(doy)
    phi = LAT * D2R
    b = 2 * math.pi * (doy - 81) / 364
    eot = 9.87 * _sin(2 * b) - 7.53 * _cos(b) - 1.5 * _sin(b)  # minutes
    solar_time = hour + (4 * (LON - 82.5) + eot) / 60
    h = (solar_time - 12) * 15 * D2R
    sin_el = _sin(phi) * _sin(d) + _cos(phi) * _cos(d) * _cos(h)
    el = math.asin(clamp(sin_el, -1, 1))
    cos_az = (_sin(d) - sin_el * _sin(phi)) / (_cos(el) * _cos(phi) + 1e-9)
    az = math.acos(clamp(cos_az, -1, 1))  # from north, clockwise
    if h > 0:
        az = 2 * math.pi - az
    return el, az


def sun_elevation(doy, hour):
    return sun_position(doy, hour)[0]


def ghi(doy, hour, cloud):
    """Clear-sky global horizontal irradiance (W/m², Haurwitz) with cloud attenuation."""
    el = sun_elevation(doy, hour)
    if el <= 0:
        return 0.0
    s = _sin(el)
    clear = 0.92 * 1098 * s * _exp(-0.057 / s)  # 0.92: typical haze/turbidity
    return clear * (1 - 0.75 * clamp(cloud, 0, 1) ** 2.2)


_et_weights = {}
_F32 = struct.Struct('<f')


def _f32(x):
    return _F32.unpack(_F32.pack(x))[0]



def et_weights(doy):
    """Fraction of daily ET₀ that happens in hour [h, h+1) — follows the sun."""
    w = _et_weights.get(doy)
    if w is not None:
        return w
    # stored as 32-bit floats, as in the web version (its weights live in a Float32Array)
    w = [_f32(max(0.0, _sin(sun_elevation(doy, h + 0.5))) + 0.015) for h in range(24)]
    s = 0.0
    for v in w:
        s += v
    w = tuple(_f32(v / s) for v in w)
    _et_weights[doy] = w
    return w


# Parameters of the realistic forecaster (also used to compute its ceilings).
class FCR:
    nuRain = 3
    nuT = 4
    faMean = 4

    @staticmethod
    def sigRain(L):
        return 0.30 + 0.12 * L

    @staticmethod
    def sigT(L):
        return 0.5 + 0.3 * L

    @staticmethod
    def sigTn(L):
        return 0.4 + 0.25 * L

    @staticmethod
    def miss(L, R):
        return 0.10 + 0.05 * L + (0.25 if R < 3 else 0)

    @staticmethod
    def falseAlarm(L):
        return 0.06 + 0.03 * L

    @staticmethod
    def pop(A):
        return 0.6 + 0.35 * (1 - _exp(-A / 10)) if A > 0 else 0.05

    @staticmethod
    def share(conv, h):
        """Climatological share of a day's rain falling in hour h (per hour): afternoon/evening
        showers (13–23 h, peaking at 17:30) in the convective months, otherwise even."""
        if not conv:
            return 1 / 24
        v = max(0.0, 1 - abs(h - 17.5) / 5.5)
        return v / 5.5


class Day:
    """One generated day of weather."""
    __slots__ = ('i', 'doy', 'm', 'wet', 'rain', 'tmax', 'tmin', 'rh', 'wind', 'cloud', 'et0', 'rainStart',
                 'rainDur', 'tags', 'base')

    def to_dict(self):
        return {k: getattr(self, k) for k in self.__slots__ if k != 'base'}


class Forecast(Obj):
    """A daily forecast: day, rainProb, rainMm, tmax, tmin, et0, wind, rh."""


class Weather:
    def __init__(self, seed, start_year, start_month):
        self.seed = seed
        self.start_year = start_year
        self.start_month = start_month
        self.rng = RNG(hash_n(seed, 'weather'))
        self.days = []
        self._wet = False
        self.mods = []
        # forecasts: 'standard' (the original forecaster) or 'realistic' (long-tailed errors, no hour-level
        # timing; an optional farm setting, off by default). The weather itself is the same in both.
        self.fc_mode = 'standard'
        self.version = 0  # bumped whenever an event changes the weather
        self._fc_cache = {}
        self._fh_cache = {}

    # ── days ──
    def day(self, i) -> Day:
        if i < 0:
            i = 0
        days = self.days
        while len(days) <= i:
            self._gen()
        return days[i]

    def _mods_for(self, i):
        return [m for m in self.mods if m['from'] <= i < m['to']]

    def _gen(self):
        i = len(self.days)
        di = date_info(self.start_year, self.start_month, i)
        m = di.m - 1
        r = self.rng
        p = clamp(N.rainDays[m] / DAYS_IN_MONTH[m], 0.002, 0.6)
        p11 = min(0.9, p + 0.35)
        p01 = clamp(p * (1 - p11) / (1 - p), 0.001, 0.9)
        wet = r.random() < p11 if self._wet else r.random() < p01
        mean_wet = N.rain[m] / max(0.3, N.rainDays[m])
        rain = min(140.0, max(0.6, r.gamma(0.75, mean_wet / 0.75))) if wet else 0.0
        noise_t = r.normal(0, 1.2)
        noise_tn = r.normal(0, 0.9)
        noise_rh = r.normal(0, 4)
        u1, u2, u3 = r.random(), r.random(), r.random()
        d = Day()
        d.i, d.doy, d.m, d.wet, d.rain = i, di.doy, di.m, wet, rain
        d.base = (noise_t, noise_tn, noise_rh, u1, u2, u3, wet, rain)
        self._finish(d)
        # the natural wet/dry chain carries on underneath any event (dry spell, storm…), so the
        # weather never depends on how far ahead someone had looked when an event was added
        self._wet = wet
        self.days.append(d)

    def _finish(self, d: Day):
        """Derive the dependent variables (applies any active modifiers). Always starts again from the
        day's natural weather, so re-deriving is idempotent."""
        m = d.m - 1
        noise_t, noise_tn, noise_rh, u1, u2, u3, bwet, brain = d.base
        d.wet, d.rain = bwet, brain
        mods = self._mods_for(d.i)
        for mod in mods:
            k = mod['kind']
            if k == 'dry':
                d.wet, d.rain = False, 0.0
            if k == 'storm' and d.i == mod['from']:
                d.wet, d.rain = True, float(mod['rain'])
            if k == 'cloudy' and u3 < 0.5:
                d.wet, d.rain = True, max(d.rain * 0.3, 1.5 + 4 * u2)
        d.tmax = N.tmax[m] + noise_t - (1.5 + min(4, 0.03 * d.rain) if d.wet else 0)
        d.tmin = N.tmin[m] + noise_tn + (0.5 if d.wet else 0)
        d.rh = N.rh[m] + (9 if d.wet else -3) + noise_rh
        d.wind = N.wind[m] * (0.75 + 0.5 * u1)
        d.cloud = 0.55 + 0.4 * u2 if d.wet else clamp(N.cloudDry[m] + 0.25 * (u2 - 0.5), 0.02, 0.8)
        d.tags = []
        for mod in mods:
            k = mod['kind']
            if k == 'dry':
                d.tmax += 2.5
                d.tmin += 0.8
                d.rh -= 15
                d.cloud = 0.05 + 0.12 * u2
                d.wind *= 1.2
                d.tags.append('dry spell')
            if k == 'cloudy':
                d.cloud = 0.86 + 0.1 * u2
                d.tmax -= 2
                d.rh += 6
                d.tags.append('overcast')
            if k == 'storm' and d.i == mod['from']:
                d.cloud = 0.97
                d.tags.append('storm')
        if d.tmax < d.tmin + 3:
            d.tmax = d.tmin + 3
        d.rh = clamp(d.rh, 22, 98)
        d.et0 = hargreaves(d.tmax, d.tmin, d.doy) * (0.82 if d.cloud > 0.8 else 1)
        # rain timing: convective afternoon/evening showers in Apr–Nov, otherwise any time
        conv = 4 <= d.m <= 11
        d.rainDur = clamp(1 + d.rain / 12 + 2 * u3, 1, 10) if d.wet else 0.0
        d.rainStart = (13 + 6 * u1 if conv else 24 * u1) if d.wet else 0.0
        for mod in mods:
            if mod['kind'] == 'storm' and d.i == mod['from']:
                d.rainStart = float(mod['hour'])
                d.rainDur = 5.0
        if d.rainStart + d.rainDur > 23.99:
            d.rainStart = max(0.0, 23.99 - d.rainDur)

    def add_mod(self, mod: dict):
        """Apply an event modifier to days [from, to) — re-derives already generated days."""
        self.mods.append(mod)
        self.version += 1
        self._fc_cache.clear()
        self._fh_cache.clear()
        for i in range(mod['from'], min(mod['to'], len(self.days))):
            self._finish(self.days[i])

    def active_tags(self, i):
        return self.day(i).tags

    # ── hourly accessors (day index, hour 0–24) ──
    def temp(self, i, h):
        d = self.day(i)
        # min at 06:00, max at 14:30 (piecewise cosine); night cools toward next day's tmin
        if 6 <= h <= 14.5:
            return d.tmin + (d.tmax - d.tmin) * (1 - _cos(math.pi * (h - 6) / 8.5)) / 2
        if h > 14.5:
            n = self.day(i + 1)
            return n.tmin + (d.tmax - n.tmin) * (1 + _cos(math.pi * (h - 14.5) / 15.5)) / 2
        p = self.day(max(0, i - 1))
        return d.tmin + (p.tmax - d.tmin) * (1 + _cos(math.pi * (h + 9.5) / 15.5)) / 2

    def rh(self, i, h):
        d = self.day(i)
        t = self.temp(i, h)
        tm = (d.tmax + d.tmin) / 2
        return clamp(d.rh - (t - tm) * 2.6 + (12 if self.rain_rate(i, h) > 0 else 0), 18, 100)

    def rain_rate(self, i, h):
        """mm/h"""
        d = self.day(i)
        if not d.wet or d.rain <= 0:
            return 0.0
        return d.rain / d.rainDur if d.rainStart <= h < d.rainStart + d.rainDur else 0.0

    def rain_between(self, i, h0, h1):
        """Rain falling in [h0, h1) of day i (mm)."""
        d = self.day(i)
        if not d.wet:
            return 0.0
        a = max(h0, d.rainStart)
        b = min(h1, d.rainStart + d.rainDur)
        return d.rain * (b - a) / d.rainDur if b > a else 0.0

    def wind(self, i, h):
        d = self.day(i)
        return d.wind * (0.45 + 0.9 * _exp(-(((h - 14.5) / 4.5) ** 2)))

    def cloud(self, i, h):
        d = self.day(i)
        return max(d.cloud, 0.93) if self.rain_rate(i, h) > 0 else d.cloud

    def ghi(self, i, h):
        d = self.day(i)
        return ghi(d.doy, h, self.cloud(i, h))

    def et0_between(self, i, h0, h1):
        d = self.day(i)
        w = et_weights(d.doy)
        s, h = 0.0, h0
        while h < h1 - 1e-9:
            hi = math.floor(h)
            nxt = min(h1, hi + 1)
            s += w[hi % 24] * (nxt - h)
            h = nxt
        return d.et0 * s

    # ── forecasts (stable within a day, imperfect, worse with lead time) ──
    def forecast_day(self, from_day, lead) -> Forecast:
        key = (from_day, lead)
        hit = self._fc_cache.get(key)
        if hit is not None:
            return hit
        out = self._forecast_day_r(from_day, lead) if self.fc_mode == 'realistic' else self._forecast_day_s(from_day, lead)
        if len(self._fc_cache) > 4000:
            self._fc_cache.clear()
        self._fc_cache[key] = out
        return out

    def _forecast_day_s(self, from_day, lead):
        target = from_day + lead
        d = self.day(target)
        m = d.m - 1
        seed = self.seed

        def u(k):
            return hash_n(seed, 'fc', target, from_day, k) / 4294967296

        # inside a dry spell (a monsoon break) forecasters lean on the persistent pattern
        dry = any(md['kind'] == 'dry' for md in self._mods_for(target))
        p_clim = 0.03 if dry else clamp(N.rainDays[m] / DAYS_IN_MONTH[m], 0.01, 0.6)
        # forecasts miss some rain and raise some false alarms, more often further ahead
        miss = u(7) < ((0.12 + 0.05 * lead + (0.25 if d.rain < 3 else 0)) if d.wet else 0.08 + 0.04 * lead)
        if d.wet:
            skill = 0.08 + 0.25 * u(1) if miss else 0.55 + 0.4 * u(1)
        elif dry:
            skill = 0.01 + 0.06 * u(1)
        else:
            skill = 0.45 + 0.4 * u(1) if miss else 0.03 + 0.24 * u(1)
        w = min(0.8, 0.13 * lead)
        rain_prob = clamp(lerp(skill, p_clim, w), 0, 0.99)
        mean_wet = N.rain[m] / max(0.3, N.rainDays[m])
        if d.wet:
            rain_mm = lerp(d.rain * (0.2 if miss else 0.55 + 0.9 * u(2)), mean_wet * rain_prob, w)
        else:
            rain_mm = mean_wet * rain_prob * (0.8 if miss else 0.3)
        sd = 0.5 + 0.35 * lead
        tmax = d.tmax + (u(3) - 0.5) * 2 * sd
        tmin = d.tmin + (u(4) - 0.5) * 1.6 * sd
        return Forecast(day=target, rainProb=rain_prob, rainMm=rain_mm, tmax=tmax, tmin=tmin, et0=hargreaves(tmax, tmin, d.doy),
                        wind=d.wind * (0.85 + 0.3 * u(5)), rh=d.rh + (u(6) - 0.5) * 8)

    def forecast_hour(self, from_t, t):
        """Hour-level forecast for the coming hours (spray timing, rain-aware checks)."""
        key = (from_t, t)
        hit = self._fh_cache.get(key)
        if hit is not None:
            return hit
        out = self._forecast_hour_r(from_t, t) if self.fc_mode == 'realistic' else self._forecast_hour_s(from_t, t)
        if len(self._fh_cache) > 20000:
            self._fh_cache.clear()
        self._fh_cache[key] = out
        return out

    def _forecast_hour_s(self, from_t, t):
        i = math.floor(t / 86400)
        h = (t % 86400) / 3600
        lead = max(0.0, (t - from_t) / 3600)
        seed, th, fd = self.seed, math.floor(t / 3600), math.floor(from_t / 86400)

        def u(k):
            return hash_n(seed, 'fh', th, fd, k) / 4294967296

        rain = self.rain_rate(i, h)
        miss = u(1) < 0.08 + 0.01 * lead
        if miss:
            rain = 0.0 if rain > 0 else (1.5 if u(2) < 0.15 else 0.0)
        return Obj(temp=self.temp(i, h) + (u(3) - 0.5) * (0.6 + 0.08 * lead), rh=self.rh(i, h) + (u(4) - 0.5) * 8,
                   wind=self.wind(i, h) * (0.8 + 0.4 * u(5)), rain=rain)

    # ── realistic forecasts (optional) ──
    # Daily: the rain amount is the true amount times a long-tailed (Student-t, 3 d.f.) log error that grows
    # with lead time, with misses and false alarms; the chance of rain follows from the amount; temperatures
    # carry Student-t (4 d.f.) errors. Each issue day has its own errors. Hourly: the day's forecast spread
    # over the climatological timing, so hour-level timing adds nothing the daily forecast doesn't say.
    def _forecast_day_r(self, from_day, lead):
        target = from_day + lead
        d = self.day(target)
        m = d.m - 1
        L = max(0, lead)
        seed = self.seed

        def U(k):
            return (hash_n(seed, 'fcR', target, from_day, k) + 0.5) / 4294967296

        def nrm(k):
            return _sqrt(-2 * math.log(U(k))) * _cos(2 * math.pi * U(k + 1))

        def tdist(nu, k):
            q = 0.0
            for j in range(nu):
                x = nrm(k + 2 + 2 * j)
                q += x * x
            return nrm(k) / _sqrt(q / nu)

        dry = any(md['kind'] == 'dry' for md in self._mods_for(target))
        A = 0.0
        if d.wet and d.rain > 0:
            A = 0.0 if U(40) < FCR.miss(L, d.rain) else d.rain * _exp(FCR.sigRain(L) * tdist(FCR.nuRain, 50))
        elif not dry and U(41) < FCR.falseAlarm(L):
            A = -FCR.faMean * math.log(U(42))
        p_clim = 0.03 if dry else clamp(N.rainDays[m] / DAYS_IN_MONTH[m], 0.01, 0.6)
        w = min(0.8, 0.13 * L)
        rain_prob = clamp(lerp(FCR.pop(A), p_clim, w), 0, 0.99)
        tmax = d.tmax + FCR.sigT(L) * tdist(FCR.nuT, 60)
        tmin = d.tmin + FCR.sigTn(L) * tdist(FCR.nuT, 70)
        # wind and humidity come from the climate and the forecast's own rain call
        return Forecast(day=target, rainProb=rain_prob, rainMm=A, tmax=tmax, tmin=tmin, et0=hargreaves(tmax, tmin, d.doy),
                        wind=N.wind[m] * (0.75 + 0.5 * U(80)), rh=clamp(N.rh[m] + (9 if A > 0 else -3) + (U(81) - 0.5) * 8, 22, 98))

    def _forecast_hour_r(self, from_t, t):
        i = math.floor(t / 86400)
        h = (t % 86400) / 3600
        frm = math.floor(from_t / 86400)
        lead = max(0, i - frm)
        f = self.forecast_day(frm, lead)
        conv = 4 <= self.day(i).m <= 11
        rain = f.rainMm * FCR.share(conv, h) if f.rainMm > 0 else 0.0
        if 6 <= h <= 14.5:
            temp = f.tmin + (f.tmax - f.tmin) * (1 - _cos(math.pi * (h - 6) / 8.5)) / 2
        elif h > 14.5:
            temp = f.tmin + (f.tmax - f.tmin) * (1 + _cos(math.pi * (h - 14.5) / 15.5)) / 2
        else:
            temp = f.tmin + (f.tmax - f.tmin) * (1 + _cos(math.pi * (h + 9.5) / 15.5)) / 2
        seed, th = self.seed, math.floor(t / 3600)

        def u(k):
            return hash_n(seed, 'fhR', th, frm, k) / 4294967296

        return Obj(temp=temp, rh=clamp(f.rh - (temp - (f.tmax + f.tmin) / 2) * 2.6 + (12 if rain > 0 else 0) + (u(4) - 0.5) * 8, 18, 100),
                   wind=f.wind * (0.45 + 0.9 * _exp(-(((h - 14.5) / 4.5) ** 2))) * (0.8 + 0.4 * u(5)), rain=rain)


def season_of(month):
    if 6 <= month <= 9:
        return 'Kharif (SW monsoon)'
    if 10 <= month <= 12:
        return 'Rabi (NE monsoon)'
    if month <= 2:
        return 'Rabi / winter'
    return 'Summer'


def clim_month(m):
    """Climatology helpers used by the planner (expected conditions in a month)."""
    tmax, tmin = N.tmax[m - 1], N.tmin[m - 1]
    return Obj(tmax=tmax, tmin=tmin, tmean=(tmax + tmin) / 2, rain=N.rain[m - 1],
               et0=hargreaves(tmax, tmin, (m - 1) * 30.4 + 15), rainDays=N.rainDays[m - 1])


_clim_day = {}


def clim_day(doy):
    """Daily climatology: monthly normals interpolated between mid-month points."""
    hit = _clim_day.get(doy)
    if hit is not None:
        return hit
    mp = (doy - 15.2) / 30.44
    f0 = math.floor(mp)
    fr = mp - f0
    m0 = ((f0 % 12) + 12) % 12
    m1 = (m0 + 1) % 12

    def L(a):
        return a[m0] + (a[m1] - a[m0]) * fr

    tmax, tmin = L(N.tmax), L(N.tmin)
    out = Obj(tmax=tmax, tmin=tmin, tmean=(tmax + tmin) / 2, rain=L(N.rain) / 30.44)
    _clim_day[doy] = out
    return out
