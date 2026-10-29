"""The digital twin: two bunded paddy fields (A and B), each a grid of 1 m² squares.

What it simulates, hour by hour:
  * weather and forecasts (the original project's Bengaluru generator, copied in ``paddy.core.climate``);
  * one water level per field (a bunded basin): rain, irrigation, evapotranspiration, percolation through the
    plough pan and overflow over the bund; below the surface the perched water table falls with the soil's
    specific yield, which is what an alternate-wetting-and-drying (AWD) water tube measures;
  * per square: soil (texture, pH, salinity, organic carbon, N-P-K), its small height difference from the field
    average (so the water depth differs a little from square to square), the rice plant's development by thermal
    time, water stress, nutrients, temperature and heat at flowering, pests (the original pest dynamics) and
    spray residue;
  * sensors: a camera pass every two hours by day (pest sightings with the original detection model), a daily soil
    scan, a precise probe on request, a water tube per field, a weather station and a rain gauge.

Actions come in two forms. The ``act_*`` methods apply a whole decision at once, at the top of the hour (v1's
behaviour, kept for the ``arms=False`` comparison runs). The per-square methods (``probe_square``, ``transplant_square``,
``spray_square``, ``harvest_square``, …) take the time the arm actually did the work, and the engine calls them as the
arms finish each square (``paddy.robots``). Pumping and draining start at the minute the watering arm sets the gate:
the hour's water balance only counts the part of the hour the gate was open. Everything is deterministic for a given
seed, weather year and sequence of actions.
"""
from __future__ import annotations

import math

import numpy as np

from .. import config as CFG
from ..core import agro as A
from ..core import climate as C
from ..core import data as D
from ..core.util import RNG, Obj, clamp, date_info, fbm, hash01, hash_n, make_noise_2d

DAY, HOUR = 86400, 3600
LN2 = math.log(2)
PADDY = next(c for c in D.CROPS if c.id == 'paddy')
PEST_KEYS = list(D.PESTS.keys())
PEST_IDX = {k: i for i, k in enumerate(PEST_KEYS)}
PC_KEYS = list(D.PESTICIDES.keys())
PC_IDX = {k: i for i, k in enumerate(PC_KEYS)}
_P = [D.PESTS[k] for k in PEST_KEYS]
P_BASE = np.array([p.base for p in _P])
P_R = np.array([p.r for p in _P])
P_TOPT = np.array([p.tOpt for p in _P])
P_TW = np.array([p.tWidth for p in _P])
P_RHK = np.array([0 if p.rh == 'humid' else 1 if p.rh == 'dry' else 2 for p in _P])
P_W0 = np.array([p.window[0] for p in _P])
P_W1 = np.array([p.window[1] for p in _P])
P_DMG = np.array([p.dmg for p in _P])
P_SUCK = np.array([p.cat in ('sucking', 'mite') for p in _P])
EFF = np.zeros((len(PC_KEYS) + 1, len(PEST_KEYS)))
for _i, _pc in enumerate(PC_KEYS):
    for _j, _pk in enumerate(PEST_KEYS):
        EFF[_i, _j] = D.pesticide_efficacy(_pc, _pk)
PC_HALF = np.array([D.PESTICIDES[k].halfLife for k in PC_KEYS] + [3.0])
PADDY_PEST_IDX = np.array([PEST_IDX[p] for p in PADDY.pests], np.int64)
STAGE_FLAGS = A.stage_flags(PADDY)            # (flowering?, weight) per stage
STAGE_ENDS = np.array([s['end'] for s in PADDY.stages])
TOT_GDD = A.total_gdd(PADDY)
POTENTIAL_KG_M2 = PADDY.yieldTHa * 0.1        # t/ha → kg per m²
ACC_KEYS = ('soil', 'wW', 'ksW', 'tempF', 'heatH', 'nutF', 'days', 'damage')
# AWD-sensitive window: from about a week before flowering to a week after (progress 0.55–0.75)
SENSITIVE = (0.55, 0.75)
FERTILISERS = {  # product → nutrient content (fractions of N, P₂O₅, K₂O)
    'urea': (0.46, 0.0, 0.0), 'dap': (0.18, 0.46, 0.0), 'mop': (0.0, 0.0, 0.60), 'ssp': (0.0, 0.16, 0.0), 'complex_10_26_26': (0.10, 0.26, 0.26),
}
AMENDMENTS = ('lime', 'gypsum', 'sulphur', 'compost', 'leach')


def PHI_DAYS(product):
    from ..decisions.rules import phi_days      # the rice label's waiting period
    return phi_days(product)


def AI_PER_M2(product):
    from ..decisions.rules import ai_g_per_m2, bio_kg_per_m2   # active ingredient at the label dose
    return ai_g_per_m2(product), bio_kg_per_m2(product)


def establish_adjust(depth_mm, seedling_age):
    """Practice factors on a transplanted seedling's chance that the original establishment formula doesn't
    know: drowning in deep water, drying out on a drained field, and old seedlings. All 1 under good practice
    (from 30 mm below the surface to 50 mm of standing water, seedlings up to 30 days old)."""
    p = 1.0
    if depth_mm > 50:
        p *= clamp(1 - (depth_mm - 50) / 200, 0.3, 1)
    elif depth_mm < -30:
        p *= clamp(1 - (-30 - depth_mm) / 200, 0.6, 1)
    if seedling_age > 30:
        p *= clamp(1 - 0.01 * (seedling_age - 30), 0.8, 1)
    return p


def stage_index(frac):
    k = int(np.sum(frac >= STAGE_ENDS))
    return min(k, len(STAGE_ENDS) - 1)


def stage_name(frac):
    return PADDY.stages[stage_index(frac)]['name']


class Twin:
    def __init__(self, cfg=None):
        cfg = dict(cfg or {})
        self.cfg = cfg
        self.seed = int(cfg.get('seed', 20261024))
        self.year = int(cfg.get('weatherYear', 0) or 0)
        self.season = cfg.get('season', 'kharif')
        if self.season not in CFG.SEASONS:
            raise ValueError(f'unknown season {self.season!r}')
        self.start_year = int(cfg.get('startYear', CFG.START_YEAR))
        self.start_month = CFG.SEASONS[self.season]
        self.R, self.K = int(cfg.get('rows', CFG.ROWS)), int(cfg.get('cols', CFG.COLS))
        self.W, self.H = 2 * self.K + 1, self.R          # internal grid: field A, a bund column, field B
        self.n = n = self.W * self.H
        col = np.arange(n) % self.W
        self.field_of = np.where(col < self.K, 0, np.where(col > self.K, 1, -1)).astype(np.int64)
        self.valid = self.field_of >= 0
        self.cells = [np.nonzero(self.field_of == f)[0] for f in range(2)]
        self.rng = RNG(hash_n(self.seed, 'twin', self.year))
        self.np_rng = np.random.default_rng(hash_n(self.seed, 'twin-np', self.year))
        wseed = hash_n(self.seed, 'wx', self.year) if self.year else hash_n(self.seed, 'wx')
        self.weather = C.Weather(wseed, self.start_year, self.start_month)
        if cfg.get('forecastMode') == 'realistic':
            self.weather.fc_mode = 'realistic'
        self.t = 6 * HOUR                                   # the season starts at 06:00 on the 1st
        F = lambda: np.zeros(n)
        for k in ('sand', 'clay', 'oc', 'ph', 'ec', 'nN', 'nP', 'nK', 'fc', 'wp', 'sat', 'kd', 'elev', 'tsoil',
                  'gdd', 'frac', 'health', 'ks', 'nutF', 'tempF', 'pest', 'pestAge', 'residue', 'seedAge',
                  'oPh', 'oN', 'oP', 'oK', 'oEc', 'oPest', 'oHealth', 'harvestKg', 'estP', 'nApplied'):
            setattr(self, k, F())
        self.plantedT = np.full(n, -1.0)
        self.failT = np.full(n, -1.0)
        self.lastSprayT = np.full(n, -1e12)
        self.noHarvestBefore = np.zeros(n)
        self.oFirstT = np.full(n, -1.0)
        self.oDetectT = np.full(n, -1e12)
        self.oTime = np.full(n, -1e12)
        self.has = np.zeros(n, bool)
        self.alive = np.zeros(n, bool)
        self.failed = np.zeros(n, bool)
        self.done = np.zeros(n, bool)                       # harvested
        self.pestId = np.full(n, -1, np.int64)
        self.oPestId = np.full(n, -1, np.int64)
        self.oIdVote = np.zeros(n, np.int64)
        self.residuePc = np.full(n, -1, np.int64)
        self.sprayCount = np.zeros(n, np.int64)          # sprays on each square this season
        self.lastMoa = [None] * n                        # IRAC group of the last spray on each square …
        self.lastMoaT = np.full(n, -1e12)                # … and when it was applied
        self.amendG = {k: F() for k in AMENDMENTS}       # amendment applied to each square this season (g/m², mm for leach)
        self.acc = {k: F() for k in ACC_KEYS}
        self.probe = {}                                     # square → last precise probe reading
        self.sprayEff = {}                                  # square → share killed by its last spray
        self._pad = {}
        ones = np.ones((self.H, self.W))
        self._n8 = self._nbr_sum(ones).ravel()
        self._gen_soil()
        # one water level per field (mm above the soil surface; negative = water table below it)
        h0 = -250.0 if self.season == 'kharif' else -150.0
        sand_f = [float(np.mean(self.sand[self.cells[f]])) for f in range(2)]
        self.fields = [Obj(name=CFG.FIELDS[f], h=h0, puddled=False, transplantT=None, harvestT=None, abandoned=False,
                           irrTarget=None, irrUntil=0.0, irrStart=0.0, drainTarget=None, drainStart=0.0, sowMonth=None,
                           percPuddled=1.5 + 0.08 * sand_f[f], percOpen=2.5 * (1.5 + 0.08 * sand_f[f]),
                           waterM3=0.0, drainedM3=0.0, overflowMm=0.0, percMm=0.0, etMm=0.0, rainMm=0.0, irrMm=0.0,
                           nKgHa=0.0, pKgHa=0.0, kKgHa=0.0, splits=[], sprays=[], sprayL=0.0, aiG=0.0, bioKg=0.0, energyKwh=0.0,
                           floodedH=0.0, cultH=0.0, dryStreakH=0.0, drainPeriods=0, inDryPeriod=False, harvestKg=0.0,
                           yieldTHa=None, amendKg={}, scouted=0, lastIrrT=None)
                       for f in range(2)]
        self.outbreaks = []
        self.announced = []        # outbreaks the farm was told about (what the decision maker may see)
        self.events_log = []
        self.fc_log = {}           # day → the daily forecasts issued that day (for the forests' earlier-issue inputs)
        self._log_forecast()
        self.scan_soil(full=True)
        self.version = 0
        self._fp_cache = {}
        self._tube_noise = {}

    # ── time ──
    @property
    def day(self):
        return int(self.t // DAY)

    @property
    def hour(self):
        return (self.t % DAY) / HOUR

    def date_info(self, t=None):
        tt = self.t if t is None else t
        return date_info(self.start_year, self.start_month, int(tt // DAY))

    def iso(self, t=None):
        tt = self.t if t is None else t
        di = self.date_info(tt)
        h = (tt % DAY) / HOUR
        return f'{di.y:04d}-{di.m:02d}-{di.d:02d}T{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}'

    def seedling_age(self, t=None):
        """Age of the nursery seedlings (days): the nursery was sown before the season so that seedlings are
        CFG.NURSERY_AGE_AT_START days old on its first morning."""
        tt = self.t if t is None else t
        return CFG.NURSERY_AGE_AT_START + (tt - 6 * HOUR) / DAY

    # ── squares ──
    def sq_index(self, field, r, c):
        """field 0/1, row and column 0-based → internal index."""
        col = c if field == 0 else self.K + 1 + c
        return r * self.W + col

    def sq_name(self, i):
        f = int(self.field_of[i])
        r, col = divmod(int(i), self.W)
        c = col if f == 0 else col - self.K - 1
        return f'{CFG.FIELDS[f]}{r + 1},{c + 1}'

    def sq_rc(self, i):
        f = int(self.field_of[i])
        r, col = divmod(int(i), self.W)
        return f, r, (col if f == 0 else col - self.K - 1)

    def _nbr_sum(self, grid, radius=1):
        H, W = grid.shape
        buf = self._pad.get(radius)
        if buf is None:
            buf = self._pad[radius] = np.zeros((H + 2 * radius, W + 2 * radius))
        buf[radius:radius + H, radius:radius + W] = grid
        s = np.zeros((H, W))
        for dj in range(-radius, radius + 1):
            for di in range(-radius, radius + 1):
                if di or dj:
                    s += buf[radius + dj:radius + dj + H, radius + di:radius + di + W]
        return s

    # ── soil (the original generator on this grid) ──
    def _gen_soil(self):
        seed = self.seed
        nz = lambda k: make_noise_2d(hash_n(seed, 'soil', k))
        nS, nC, nPh, nOc, nN, nP, nK, nEc, nE = (nz(k) for k in range(1, 10))
        r = RNG(hash_n(seed, 'field')).fork('patch')
        W, H = self.W, self.H
        acid = Obj(x=r.range(0.15, 0.45) * W, y=r.range(0.2, 0.8) * H, rad=r.range(2.0, 3.5))
        salt = Obj(x=r.range(0.6, 0.9) * W, y=r.range(0.2, 0.8) * H, rad=r.range(1.5, 2.5))
        clay_p = Obj(x=r.range(0.4, 0.7) * W, y=r.range(0.1, 0.9) * H, rad=r.range(2.0, 3.5))

        def fall(p, i, j):
            d = math.sqrt((i + 0.5 - p.x) ** 2 + (j + 0.5 - p.y) ** 2)
            return math.exp(-(d * d) / (p.rad * p.rad))

        monsoon = 6 <= self.start_month <= 10
        for j in range(H):
            for i in range(W):
                c = j * W + i
                x, y = i / 9, j / 9
                cp = fall(clay_p, i, j)
                # paddy land is usually the finer, lower-lying soil of a farm: the sand range is shifted down
                sand = clamp(18 + 48 * fbm(nS, x, y, 3) - 18 * cp, 12, 78)
                clay = clamp(40 - 0.36 * (sand - 35) + 14 * (fbm(nC, x * 1.3, y * 1.3, 3) - 0.5) + 10 * cp, 8, 52)
                if sand + clay > 95:
                    clay = 95 - sand
                ac, sa = fall(acid, i, j), fall(salt, i, j)
                ph = clamp(6.35 + 2.2 * (fbm(nPh, x * 0.8, y * 0.8, 3) - 0.5) - 1.15 * ac + 0.85 * sa, 4.4, 8.6)
                oc = clamp(0.25 + 0.85 * fbm(nOc, x, y, 3) - 0.1 * sa, 0.15, 1.3)
                ec = clamp(0.12 + 0.55 * fbm(nEc, x, y, 2) + 2.6 * sa, 0.08, 4.5)
                hyd = A.hydraulics(sand, clay, oc)
                self.sand[c], self.clay[c], self.ph[c], self.oc[c], self.ec[c] = sand, clay, ph, oc, ec
                self.fc[c], self.wp[c], self.sat[c], self.kd[c] = hyd.fc, hyd.wp, hyd.sat, hyd.kDrain
                self.nN[c] = clamp(130 + 230 * fbm(nN, x, y, 3) + 40 * (oc - 0.5), 90, 420)
                self.nP[c] = clamp(8 + 40 * fbm(nP, x, y, 3), 4, 60)
                self.nK[c] = clamp(100 + 260 * fbm(nK, x, y, 3), 70, 420)
                self.elev[c] = 40 * (fbm(nE, i / 3, j / 3, 2) - 0.5)
                self.tsoil[c] = 25.0
        for f in range(2):   # heights are relative to each field's mean surface
            cs = self.cells[f]
            self.elev[cs] -= float(np.mean(self.elev[cs]))
        self.elev[~self.valid] = 0.0
        _ = monsoon

    # ── sensors ──
    def scan_soil(self, full=False):
        """The daily soil scan over both fields: noisy pH, N, P, K and salinity."""
        g = self.np_rng
        z = g.standard_normal((5, self.n))
        self.oPh = self.ph + 0.07 * z[0]
        self.oN = np.maximum(0, self.nN + 7 * z[1])
        self.oP = np.maximum(0, self.nP + 1.4 * z[2])
        self.oK = np.maximum(0, self.nK + 7 * z[3])
        self.oEc = np.maximum(0.02, self.ec + 0.04 * z[4])
        self.oTime[:] = self.t

    def soil_reading(self, i, precise=None):
        """What the farm knows of a square's soil: its last probe reading if one was taken, else the daily scan."""
        p = self.probe.get(int(i)) if precise is not False else None
        if p is not None:
            return p
        return Obj(ph=float(self.oPh[i]), n=float(self.oN[i]), p=float(self.oP[i]), k=float(self.oK[i]), oc=float(self.oc[i]),
                   sand=float(self.sand[i]), clay=float(self.clay[i]), ec=float(self.oEc[i]))

    def true_soil(self, i):
        return Obj(ph=float(self.ph[i]), n=float(self.nN[i]), p=float(self.nP[i]), k=float(self.nK[i]), oc=float(self.oc[i]),
                   sand=float(self.sand[i]), clay=float(self.clay[i]), ec=float(self.ec[i]))

    def water_tube(self, f):
        """A field water tube: the water level ±3 mm, reading down to 30 cm below the surface."""
        slot = int(self.t // 1800)
        key = (f, slot)
        noise = self._tube_noise.get(key)
        if noise is None:
            z = hash_n(self.seed, 'tube', f, slot) / 4294967296
            noise = 3 * math.sqrt(-2 * math.log(max(1e-12, z))) * math.cos(2 * math.pi * hash01(self.seed, 'tube2', f, slot))
            if len(self._tube_noise) > 64:
                self._tube_noise.clear()
            self._tube_noise[key] = noise
        return max(CFG.TUBE_MIN_MM, min(CFG.BUND_MM, self.fields[f].h + noise))

    def _camera(self):
        """A camera pass over both fields: pest sightings (the original detection model) and plant health."""
        g = self.np_rng
        t = self.t
        cells = np.nonzero(self.valid)[0]
        self.oHealth[cells] = np.clip(self.health[cells] + 0.03 * g.standard_normal(len(cells)), 0, 1) * self.has[cells]
        s = self.pest[cells]
        pid = self.pestId[cells]
        opid = self.oPestId[cells]
        present = (s > 0.003) & (pid >= 0)
        if present.any():
            pc = cells[present]
            sv = s[present]
            seen = g.random(len(pc)) < 1 - np.exp(-sv / 0.045)
            if seen.any():
                sc = pc[seen]
                reading = np.clip(sv[seen] * (1 + 0.15 * g.standard_normal(len(sc))), 0.002, 1)
                ids = self.pestId[sc].copy()
                wrong = g.random(len(sc)) < 0.08
                for k in np.nonzero(wrong)[0]:
                    others = [PEST_IDX[p] for p in PADDY.pests if PEST_IDX[p] != ids[k]]
                    if others:
                        ids[k] = others[int(g.integers(0, len(others)))]
                first = self.oPestId[sc] < 0
                if first.any():
                    fc_ = sc[first]
                    self.oPestId[fc_] = ids[first]
                    self.oIdVote[fc_] = 1
                    self.oPest[fc_] = reading[first]
                    self.oFirstT[fc_] = t
                if (~first).any():
                    oc_ = sc[~first]
                    idn = ids[~first]
                    same = idn == self.oPestId[oc_]
                    self.oIdVote[oc_[same]] = np.minimum(4, self.oIdVote[oc_[same]] + 1)
                    diff = oc_[~same]
                    if len(diff):
                        self.oIdVote[diff] -= 1
                        flip = self.oIdVote[diff] <= 0
                        self.oPestId[diff[flip]] = idn[~same][flip]
                        self.oIdVote[diff[flip]] = 1
                    a = np.clip(0.3 + (t - self.oDetectT[oc_]) / (4 * HOUR), 0.3, 1)
                    lo = np.log(self.oPest[oc_])
                    self.oPest[oc_] = np.exp(lo + a * (np.log(reading[~first]) - lo))
                self.oDetectT[sc] = t
        stale = (~present) & (opid >= 0)
        if stale.any():
            st = cells[stale]
            self.oPest[st] = 0
            self.oPestId[st] = -1
            self.oFirstT[st] = -1
            self.oIdVote[st] = 0

    def inspect(self, i, t=None):
        """A close-up look at one square (scouting): exact."""
        i = int(i)
        tt = self.t if t is None else t
        if self.pest[i] > 0.003:
            if self.oPestId[i] < 0:
                self.oFirstT[i] = tt
            self.oPest[i] = self.pest[i]
            self.oPestId[i] = self.pestId[i]
            self.oIdVote[i] = 4
            self.oDetectT[i] = tt
        else:
            self.oPest[i] = 0
            self.oPestId[i] = -1
            self.oFirstT[i] = -1
            self.oIdVote[i] = 0

    def obs_age(self, i, t=None):
        tt = self.t if t is None else t
        return max(0.0, (tt - self.oFirstT[i]) / DAY) if self.oFirstT[i] >= 0 and self.oPestId[i] >= 0 else 0.0

    def obs_nbr_sev_grid(self, pk, radius=1):
        grid = np.where(self.oPestId == pk, self.oPest, 0.0).reshape(self.H, self.W)
        cnt = self._nbr_sum(np.ones((self.H, self.W)), radius).ravel()
        return self._nbr_sum(grid, radius).ravel() / cnt

    # ── water ──
    def local_depth(self, f=None):
        """Water depth over each square (mm): the field's level minus the square's height."""
        h = np.zeros(self.n)
        for k in range(2):
            h[self.cells[k]] = self.fields[k].h
        return h - self.elev

    def theta_from_depth(self, d):
        """Soil water content implied by the local water depth: saturated while water stands, falling to field
        capacity as the water table drops 30 cm, then towards wilting point."""
        sat, fc, wp = self.sat, self.fc, self.wp
        a = np.clip(-d / 300.0, 0, 1)
        b = np.clip((-d - 300.0) / 400.0, 0, 1)
        return np.where(d >= 0, sat, np.where(d > -300, sat - (sat - fc) * a, fc - (fc - wp) * b))

    def stress_threshold(self, frac):
        """The water depth below which rice starts to suffer: 15 cm below the surface ("safe AWD"), but none at all
        around flowering, and no stress once the grain is maturing."""
        thr = np.full(len(frac), float(CFG.AWD_THRESHOLD_MM))
        thr[(frac >= SENSITIVE[0]) & (frac < SENSITIVE[1])] = 0.0
        return thr

    def _gate_hours(self, dtH):
        """Pump-hours each field gets this hour, and the share of the hour each outlet is open. A gate set by the
        watering arm partway through the hour only counts from that minute; while both inlets are open the pump's flow
        is split evenly (v1's rule, now per minute)."""
        t0, t1 = self.t, self.t + dtH * HOUR
        span = {}
        for k in range(2):
            fd = self.fields[k]
            if fd.irrTarget is not None:
                a = max(t0, fd.irrStart)
                b = min(t1, fd.irrUntil) if fd.irrUntil > t0 else t1
                span[k] = (a, max(a, b))
        hours = {}
        if len(span) == 1:
            (k, (a, b)), = span.items()
            hours[k] = (b - a) / HOUR
        elif len(span) == 2:
            (a0, b0), (a1, b1) = span[0], span[1]
            ov = max(0.0, min(b0, b1) - max(a0, a1))
            hours[0] = (b0 - a0 - ov / 2) / HOUR
            hours[1] = (b1 - a1 - ov / 2) / HOUR
        drain = {}
        for k in range(2):
            fd = self.fields[k]
            if fd.drainTarget is not None:
                drain[k] = max(0.0, t1 - max(t0, fd.drainStart)) / HOUR
        return hours, drain

    def _water_hour(self, rain, et0, dtH):
        pump = CFG.PUMP_M3_PER_H * 1000.0          # litres per pump-hour = mm over 1 m²
        pump_h, drain_h = self._gate_hours(dtH)
        for k in range(2):
            fd = self.fields[k]
            cs = self.cells[k]
            area = len(cs) * CFG.SQUARE_M2
            h = fd.h
            # irrigation: pump towards the target level
            irr = 0.0
            if fd.irrTarget is not None:
                need = (fd.irrTarget - h) if h >= 0 else (-h * CFG.SPECIFIC_YIELD + fd.irrTarget)
                if need <= 0.5 or self.t >= fd.irrUntil:
                    fd.irrTarget = None
                else:
                    irr = min(need, pump * pump_h.get(k, 0.0) / area)
            # drainage of ponded water through the outlet
            drained = 0.0
            if fd.drainTarget is not None:
                if h <= fd.drainTarget + 0.5 or h <= 0:
                    fd.drainTarget = None
                else:
                    drained = min(h - max(0.0, fd.drainTarget), CFG.DRAIN_MM_PER_H * drain_h.get(k, dtH))
            # evapotranspiration
            live = cs[self.alive[cs] & ~self.done[cs]]
            n_live = len(live)
            if n_live:
                fl = np.minimum(1.0, self.frac[live])
                kc = float(np.mean([A.kc(PADDY, float(x)) for x in fl])) if n_live < 4 else float(np.mean(self._kc_vec(fl)))
                ks = float(np.mean(self.ks[live]))
            else:
                kc, ks = 0.0, 1.0
            wet = clamp(1 + h / 300.0, 0, 1)
            kc_bare = 1.05 if h > 0 else 0.75 * wet ** 1.5
            et = ((n_live * kc * ks) + (len(cs) - n_live) * kc_bare) / len(cs) * et0
            perc_day = fd.percPuddled if fd.puddled else fd.percOpen
            if h > 0:
                # percolation through the plough pan rises with the head of ponded water; lateral seepage through
                # the bunds grows with the depth of water against them
                perc = perc_day * (1 + h / CFG.PERC_HEAD_MM) / 24 * dtH
                seep = CFG.BUND_SEEP_PER_DAY * h / 24 * dtH
            else:
                # once the ponded water is gone the perched water table drains slowly through the pan; it stops
                # draining by the time the table is 10 cm down
                perc = perc_day / 24 * dtH * clamp(1 + h / CFG.PERC_STOP_MM, 0.0, 1)
                seep = 0.0
            perc += seep
            net = rain + irr - et - perc - drained
            if h >= 0:
                hn = h + net
                if hn < 0:
                    hn = hn / CFG.SPECIFIC_YIELD
            else:
                refill = -h * CFG.SPECIFIC_YIELD
                hn = net - refill if net > refill else h + net / CFG.SPECIFIC_YIELD
            over = 0.0
            if hn > CFG.BUND_MM:
                over = hn - CFG.BUND_MM
                hn = CFG.BUND_MM
            hn = max(hn, -800.0)
            fd.h = hn
            fd.overflowMm += over
            fd.percMm += perc
            fd.etMm += et
            fd.rainMm += rain
            fd.irrMm += irr
            m3 = irr * area / 1000.0
            fd.waterM3 += m3
            fd.energyKwh += m3 * CFG.PUMP_KWH_PER_M3
            fd.drainedM3 += drained * area / 1000.0
            if irr > 0:
                fd.lastIrrT = self.t
            # leaching with the water that percolates through the plough pan: little nitrogen (flooded soil holds it
            # mostly as ammonium), more on sandy soil; salts wash out steadily
            if perc > 0:
                self.nN[cs] *= np.exp(-CFG.N_LEACH_PER_MM * perc * (0.4 + self.sand[cs] / 100))
                self.ec[cs] = np.maximum(0.06, self.ec[cs] * np.exp(-0.0012 * perc))
            # methane bookkeeping (IPCC Tier 1: days of cultivation and the number of drainage periods)
            if fd.transplantT is not None and fd.harvestT is None and not fd.abandoned:
                fd.cultH += dtH
                if hn > 0:
                    fd.floodedH += dtH
                    fd.dryStreakH = 0.0
                    fd.inDryPeriod = False
                else:
                    fd.dryStreakH += dtH
                    if fd.dryStreakH >= 72 and not fd.inDryPeriod:
                        fd.inDryPeriod = True
                        # IPCC counts drainage periods of at least 3 days during the season; drying out for harvest
                        # (the end-season drain-down) doesn't count
                        if self.field_progress(k) < 0.9:
                            fd.drainPeriods += 1

    _KC_CACHE = {}

    def _kc_vec(self, fl):
        """agro.kc for many squares at once: the same piecewise curve with the same arithmetic, element by element, so
        the numbers are identical to calling it square by square (checked in the tests)."""
        fl = np.asarray(fl, float)
        k = PADDY.kc
        ini_end = PADDY.stages[0]['end']
        mid_start = max(ini_end + 0.1, 0.38)
        mid_end = 0.88 if PADDY.harvest['type'] == 'multi' else 0.8
        rise = k['ini'] + (k['mid'] - k['ini']) * ((fl - ini_end) / (mid_start - ini_end))
        t_end = np.clip((fl - mid_end) / (1 - mid_end), 0, 1)
        fall = k['mid'] + (k['end'] - k['mid']) * t_end
        return np.where(fl < ini_end, k['ini'], np.where(fl < mid_start, rise, np.where(fl < mid_end, k['mid'], fall)))

    # ── one hour of physics ──
    def step_hour(self):
        dtH = 1.0
        dtD = dtH / 24
        d, h0 = self.day, self.hour
        W = self.weather
        hm = h0 + 0.5
        T = W.temp(d, hm)
        rh = W.rh(d, hm)
        rain = W.rain_between(d, h0, h0 + dtH)
        et0 = W.et0_between(d, h0, h0 + dtH)
        env = Obj(t=self.t, T=T, rh=rh, rain=rain, et0=et0, wind=W.wind(d, hm))
        self._water_hour(rain, et0, dtH)
        depth = self.local_depth()
        live = self.has & self.alive & ~self.done
        # water stress per square
        if live.any():
            L = np.nonzero(live)[0]
            thr = self.stress_threshold(np.minimum(1.0, self.frac[L]))
            dl = depth[L]
            ks = np.where(dl >= thr, 1.0, np.clip(1 - (thr - dl) / 250.0, 0, 1))
            ks = np.where(self.frac[L] >= 0.9, 1.0, ks)
            self.ks[L] = ks
        # soil temperature & mineralisation (the original formulation, water-covered soil runs cooler)
        cover = np.zeros(self.n)
        if live.any():
            L = np.nonzero(live)[0]
            cover[L] = np.clip(0.06 + 0.94 * np.clip(self.frac[L] / 0.5, 0, 1) ** 0.85, 0, 1)
        sun = max(0.0, math.sin(C.sun_elevation(self.date_info().doy, hm)))
        wet = np.clip(1 + depth / 300.0, 0, 1.2)
        target = T + 2.2 * (1 - cover) * sun - 1.6 * wet
        self.tsoil += (target - self.tsoil) * (1 - math.exp(-dtH / 5))
        tf = np.where(self.tsoil <= 5, 0.0, np.clip(np.where(self.tsoil < 25, 0.3 + 0.7 * (self.tsoil - 5) / 20, 1.0), 0, 1))
        self.nN[self.valid] += (0.25 * (self.oc / 0.5) * tf * dtD)[self.valid]
        # spray residue decay and wash-off
        rm = self.residue > 0
        if rm.any():
            rc = np.nonzero(rm)[0]
            half = PC_HALF[self.residuePc[rc]]
            self.residue[rc] *= np.exp(-LN2 * dtD / half)
            if rain > 1:
                wash = rc[self.t - self.lastSprayT[rc] < 6 * HOUR]
                self.residue[wash] *= math.exp(-0.05 * rain)
            gone = rc[self.residue[rc] < 0.02]
            self.residue[gone] = 0
            self.residuePc[gone] = -1
        if live.any():
            self._grow(live, T, dtH, dtD)
        live2 = self.has & self.alive & ~self.done
        dead = ~live2 & ((self.pest > 0) | (self.pestId >= 0))
        if dead.any():
            self.pest[dead] = 0
            self.pestId[dead] = -1
            self.pestAge[dead] = 0
        if live2.any():
            self._pests(live2, env, dtH, dtD)
        self.t += dtH * HOUR
        # sensing at the top of the hour
        hr = int(round(self.hour)) % 24
        if self.t % HOUR < 1e-6:
            if hr in CFG.CAMERA_HOURS:
                self._camera()
            if hr == CFG.SOIL_SCAN_HOUR:
                self.scan_soil()
            if hr == 0:
                self._log_forecast()
        for o in self.outbreaks:
            if o.get('until') and self.t >= o['until']:
                o['active'] = False
        self.version += 1

    def _log_forecast(self):
        d = self.day
        self.fc_log[d] = [self.weather.forecast_day(d, k) for k in range(4)]

    def _grow(self, live, T, dtH, dtD):
        L = np.nonzero(live)[0]
        prev = self.frac[L].copy()
        gr = max(0.0, min(T, PADDY.tOpt[1] + 3) - PADDY.tBase)
        self.gdd[L] += gr * dtD
        frac = self.gdd[L] / TOT_GDD
        self.frac[L] = frac
        f1 = np.minimum(1.0, frac)
        dF = np.maximum(0.0, f1 - np.minimum(1.0, prev))
        npk = PADDY.npk
        self.nN[L] = np.maximum(5, self.nN[L] - npk[0] * 0.9 * dF)
        self.nP[L] = np.maximum(1, self.nP[L] - npk[1] * 0.12 * dF)
        self.nK[L] = np.maximum(5, self.nK[L] - npk[2] * 0.25 * dF)
        mn = np.minimum(np.minimum(self.nN[L] / (0.6 * npk[0] + 150), self.nP[L] / (0.12 * npk[1] + 14)), self.nK[L] / (0.5 * npk[2] + 110))
        nut = np.clip(np.clip(mn, 0, 1) ** 0.55, 0.25, 1)
        self.nutF[L] = nut
        tb, lo, hi, tmax = PADDY.tBase, PADDY.tOpt[0], PADDY.tOpt[1], PADDY.tMax
        top = tmax + 5
        if T <= tb:
            tfv = 0.0
        elif T < lo:
            tfv = clamp(0.3 + 0.7 * (T - tb) / (lo - tb), 0, 1)
        elif T <= hi:
            tfv = 1.0
        elif T >= top:
            tfv = 0.1
        else:
            tfv = clamp(1 - 0.9 * (T - hi) / (top - hi), 0.1, 1)
        self.tempF[L] = tfv
        st = np.minimum(np.sum(f1[:, None] >= STAGE_ENDS[None, :], axis=1), len(STAGE_ENDS) - 1)
        w = np.array([STAGE_FLAGS[k][1] for k in st])
        flower = np.array([STAGE_FLAGS[k][0] for k in st], bool)
        ks = self.ks[L]
        acc = self.acc
        acc['wW'][L] += w * dtD
        acc['ksW'][L] += w * ks * dtD
        acc['tempF'][L] += tfv * dtD
        acc['nutF'][L] += nut * dtD
        acc['days'][L] += dtD
        if T > tmax and flower.any():
            acc['heatH'][L[flower]] += (T - tmax) * dtH
        # establishment failure shows up a few days after transplanting
        fail = self.failed[L] & (self.t >= self.failT[L])
        if fail.any():
            self.alive[L[fail]] = False
            self.health[L[fail]] = 0.05
        late = frac > 1.2   # left standing too long: shattering and lodging
        if late.any():
            acc['damage'][L[late]] = np.minimum(0.95, acc['damage'][L[late]] + 0.012 * dtD)
        tgt = np.clip(np.minimum(ks ** 0.7, nut) * (1 - 0.8 * acc['damage'][L]), 0.05, 1)
        self.health[L] += (tgt - self.health[L]) * (1 - math.exp(-dtD * 0.6))

    def _pests(self, live2, env, dtH, dtD):
        T, rh = env.T, env.rh
        tf = np.exp(-(((T - P_TOPT) / P_TW) ** 2))
        rf = np.where(P_RHK == 0, 0.25 + 0.75 * min(1.0, max(0.0, (rh - 55) / 35)),
                      np.where(P_RHK == 1, 0.25 + 0.75 * min(1.0, max(0.0, (85 - rh) / 40)), 0.7))
        hz = P_BASE * tf * rf
        gr = P_R * (0.25 + 0.75 * tf) * (0.5 + 0.5 * rf)
        snap = self.pest.copy()
        ids_snap = self.pestId.copy()
        frac_all = self.frac
        slots_all = PADDY_PEST_IDX
        empty = live2 & (self.pestId < 0)
        if empty.any():
            E = np.nonzero(empty)[0]
            sl = np.broadcast_to(slots_all, (len(E), len(slots_all)))
            fe = frac_all[E][:, None]
            inw = (fe >= P_W0[sl]) & (fe <= P_W1[sl])
            nb = np.zeros(sl.shape)
            any_grid = (ids_snap >= 0).astype(np.float64).reshape(self.H, self.W)
            any_nbr = self._nbr_sum(any_grid).ravel()[E] > 0
            if any_nbr.any():
                for k, pk in enumerate(slots_all):
                    grid = np.where(ids_snap == pk, snap, 0.0).reshape(self.H, self.W)
                    if not grid.any():
                        continue
                    ns = (self._nbr_sum(grid).ravel() / self._n8)[E]
                    nb[:, k] = np.where(any_nbr, ns, 0.0)
            pcs = self.residuePc[E]
            eff_rows = EFF[np.where(pcs >= 0, pcs, len(PC_KEYS))]
            prot = self.residue[E][:, None] * eff_rows[:, slots_all]
            mult = np.ones(sl.shape)
            for o in self.outbreaks:
                if o.get('active') and env.t < o['until']:
                    pk = PEST_IDX.get(o['pest'], -1)
                    m = (self.field_of[E][:, None] == o['field']) & (sl == pk)
                    mult = np.where(m, o['mult'], mult)
            Hz = hz[sl] * np.where(inw, 1.0, 0.12) * PADDY.susceptibility * (1 + 10 * nb) * np.exp(-3 * prot) * mult * dtD
            u = self.np_rng.random(Hz.shape)
            hit = u < 1 - np.exp(-Hz)
            got = hit.any(axis=1)
            if got.any():
                first = np.argmax(hit, axis=1)
                rows = np.nonzero(got)[0]
                cells = E[rows]
                self.pestId[cells] = slots_all[first[rows]]
                self.pest[cells] = 0.02
                self.pestAge[cells] = 0
        grow = live2 & (ids_snap >= 0)
        if grow.any():
            G = np.nonzero(grow)[0]
            pk = self.pestId[G]
            pcs = self.residuePc[G]
            prot = self.residue[G] * EFF[np.where(pcs >= 0, pcs, len(PC_KEYS)), pk]
            fg = frac_all[G]
            inw = (fg >= P_W0[pk]) & (fg <= P_W1[pk])
            g = gr[pk] * np.where(inw, 1.0, 0.5) - 0.02 - 1.2 * prot
            e = np.exp(g * dtD)
            s0 = self.pest[G]
            s1 = s0 * e / (1 + s0 * (e - 1))
            if env.rain > 15 * dtH:
                s1 = np.where(P_SUCK[pk], s1 * math.exp(-0.15 * dtH), s1)
            self.pest[G] = s1
            self.pestAge[G] += dtD
            dmg = self.acc['damage']
            dmg[G] = np.minimum(0.95, dmg[G] + s1 * P_DMG[pk] * np.where(inw, 1.0, 0.5) * dtD)
            out = G[s1 < 0.003]
            self.pest[out] = 0
            self.pestId[out] = -1
            self.pestAge[out] = 0

    # ── events ──
    def trigger_event(self, kind, field=None, pest=None):
        d = self.day
        if kind == 'pest':
            cands = [k for k in range(2) if any(self.alive[i] and not self.done[i] and self.frac[i] < 0.95 for i in self.cells[k])]
            if field is not None:
                cands = [k for k in cands if k == field]
            if not cands:
                return Obj(ok=False, msg='No growing paddy to infest yet.')
            k = cands[0] if len(cands) == 1 else self.rng.pick(cands)
            pid = pest if pest in PADDY.pests else self.rng.pick(PADDY.pests)
            pk = PEST_IDX[pid]
            live = [int(i) for i in self.cells[k] if self.alive[i] and not self.done[i]]
            seed_c = self.rng.pick(live)
            sr, sc = divmod(seed_c, self.W)
            struck = []
            for i in live:
                r, c = divmod(i, self.W)
                if abs(r - sr) <= 1 and abs(c - sc) <= 1:
                    self.pestId[i] = pk
                    self.pest[i] = 0.18 + 0.2 * self.rng.random()
                    self.pestAge[i] = 2
                    struck.append(i)
            o = {'field': k, 'pest': pid, 'until': self.t + 10 * DAY, 'mult': 5, 'start': self.t, 'n': len(struck), 'active': True}
            self.outbreaks.append(o)
            self.announced.append({'field': CFG.FIELDS[k], 'pest': pid, 'plants': len(struck), 'at': self.iso()})
            msg = f'Pest outbreak: {D.PESTS[pid].name} on {len(struck)} plants in field {CFG.FIELDS[k]}'
        elif kind == 'dry':
            self.weather.add_mod({'kind': 'dry', 'from': d, 'to': d + 21})
            msg = 'Dry spell: no rain for 21 days, +2.5 °C, drier air'
        elif kind == 'storm':
            day, h = d, math.floor(self.hour) + 1
            if h + 5 > 23.9:
                day, h = d + 1, 14
            self.weather.add_mod({'kind': 'storm', 'from': day, 'to': day + 1, 'rain': 95, 'hour': h})
            msg = f"Heavy storm: about 95 mm {'today' if day == d else 'tomorrow'} from {h:02d}:00"
        elif kind == 'cloudy':
            self.weather.add_mod({'kind': 'cloudy', 'from': d, 'to': d + 7})
            msg = 'Overcast week'
        else:
            return Obj(ok=False, msg=f'unknown event {kind!r}')
        self.events_log.append({'at': self.iso(), 't': self.t, 'kind': kind, 'msg': msg})
        self.version += 1
        return Obj(ok=True, msg=msg)

    # ── actions (what the actuators do) ──
    def _squares(self, f, squares):
        cs = self.cells[f]
        if squares is None:
            return [int(i) for i in cs]
        out = []
        for r, c in squares:
            out.append(self.sq_index(f, r, c))
        return out

    def act_soil_check(self, f, squares=None):
        r = self.rng
        out = []
        for i in self._squares(f, squares):
            p = Obj(ph=float(self.ph[i]) + r.normal(0, 0.03), n=float(self.nN[i]) + r.normal(0, 3), p=float(self.nP[i]) + r.normal(0, 0.6),
                    k=float(self.nK[i]) + r.normal(0, 3), oc=float(self.oc[i]), sand=float(self.sand[i]), clay=float(self.clay[i]),
                    ec=max(0.02, float(self.ec[i]) + r.normal(0, 0.02)), t=self.t)
            self.probe[i] = p
            out.append(i)
        # v2 fix: new probe readings change what the farm knows, so the advisor's cached soil advice must be recomputed
        # (v1 left the version alone, and the soil check was scored on advice worked out before the probe)
        self.version += 1
        return Obj(ok=True, squares=len(out), msg=f'probed {len(out)} squares')

    def act_amend(self, f, squares, product, dose):
        sq = self._squares(f, squares)
        kg = 0.0
        for i in sq:
            self.amendG[product][i] += dose
            if product == 'lime':
                d_ph = min(1.2, dose / (150 + 6 * self.clay[i]) * 0.85)
                self.ph[i] = clamp(self.ph[i] + d_ph, 4.2, 8.8)
                kg += dose / 1000
            elif product == 'sulphur':
                d_ph = -min(0.8, dose / (40 + 1.5 * self.clay[i]) * 0.7)
                self.ph[i] = clamp(self.ph[i] + d_ph, 4.2, 8.8)
                kg += dose / 1000
            elif product == 'gypsum':
                # gypsum displaces sodium: salinity falls once water carries it away; pH of sodic soil drops a little
                self.ec[i] = max(0.08, self.ec[i] * (1 - min(0.35, dose / 1500)))
                if self.ph[i] > 7.5:
                    self.ph[i] = max(7.0, self.ph[i] - min(0.4, dose / 1500))
                kg += dose / 1000
            elif product == 'compost':
                k = dose / 1500
                self.oc[i] = min(1.4, self.oc[i] + 0.08 * k)
                self.nN[i] += 15 * k
                self.nK[i] += 10 * k
                self.fc[i] = min(0.45, self.fc[i] + 0.006 * k)
                kg += dose / 1000
            elif product == 'leach':
                self.ec[i] = max(0.08, self.ec[i] * (0.72 if dose >= 40 else 1 - 0.28 * dose / 40))
            self.probe.pop(i, None)   # the old probe reading no longer describes the square
        fd = self.fields[f]
        fd.amendKg[product] = fd.amendKg.get(product, 0.0) + kg
        if product == 'leach':
            water_mm = dose * len(sq) / len(self.cells[f])
            fd.irrMm += water_mm
            fd.waterM3 += dose * len(sq) * CFG.SQUARE_M2 / 1000
            fd.energyKwh += dose * len(sq) * CFG.SQUARE_M2 / 1000 * CFG.PUMP_KWH_PER_M3
            fd.h = self.level_after_adding(fd.h, water_mm)
            if fd.h > CFG.BUND_MM:
                fd.overflowMm += fd.h - CFG.BUND_MM
                fd.h = float(CFG.BUND_MM)
        self.version += 1
        return Obj(ok=True, squares=len(sq), msg=f'{product} on {len(sq)} squares')

    @staticmethod
    def level_after_adding(h, water_mm):
        """Field water level after adding water_mm over the whole field (the soil below the surface fills first)."""
        if h >= 0:
            return h + water_mm
        refill = -h * CFG.SPECIFIC_YIELD
        return water_mm - refill if water_mm > refill else h + water_mm / CFG.SPECIFIC_YIELD

    def act_puddle(self, f):
        fd = self.fields[f]
        fd.puddled = True
        # puddling also evens the surface a little
        cs = self.cells[f]
        self.elev[cs] *= 0.8
        self.version += 1
        return Obj(ok=True, msg=f'field {fd.name} puddled')

    def true_establish_chance(self, f, squares=None, seedling_age=None, t=None):
        """The real chance that a seedling transplanted now establishes, per square: the original formula on the
        weather that will actually come over the next three days and the true soil water, times penalties for bad
        practice that the original formula doesn't know (both 1 under good practice)."""
        cells = self.cells[f] if squares is None else squares
        if seedling_age is None:
            seedling_age = self.seedling_age(t)
        d = self.day if t is None else int(t // DAY)
        W = self.weather
        days = [W.day(d + q) for q in range(3)]
        max_rain3 = max(x.rain for x in days)
        tmax3 = max(x.tmax for x in days)
        depth = self.local_depth()
        theta = self.theta_from_depth(depth)
        out = []
        for i in cells:
            taw = max(0.02, self.fc[i] - self.wp[i])
            theta_rel = (theta[i] - self.wp[i]) / taw
            p = A.establish_p(max_rain3, tmax3, theta_rel, True, PADDY.tMax, PADDY.wlTol)
            p *= establish_adjust(float(depth[i]), seedling_age)
            out.append(float(p))
        return out

    def act_transplant(self, f, squares=None, seedling_age=None):
        fd = self.fields[f]
        if seedling_age is None:
            seedling_age = self.seedling_age()
        sq = [i for i in self._squares(f, squares) if not self.has[i] or self.failed[i]]
        if not sq:
            return Obj(ok=False, msg='nothing to transplant')
        di = self.date_info()
        n_ok = 0
        chances = self.true_establish_chance(f, sq, seedling_age)
        for i, p in zip(sq, chances):
            ok = hash01(self.seed, 'est', i, int(self.t)) < p
            self.estP[i] = p
            self.has[i] = True
            self.alive[i] = True
            self.failed[i] = not ok
            self.failT[i] = self.t + (3 + 2 * hash01(self.seed, 'failT', i, int(self.t))) * DAY if not ok else -1
            self.done[i] = False
            self.plantedT[i] = self.t
            self.gdd[i] = 0
            self.frac[i] = 0
            self.health[i] = 0.9
            self.ks[i] = 1
            self.nutF[i] = 1
            self.tempF[i] = 1
            self.seedAge[i] = seedling_age
            self.harvestKg[i] = 0
            for k in ACC_KEYS:
                self.acc[k][i] = 0.0
            self.acc['soil'][i] = A.soil_total(self.true_soil(i), PADDY)
            if seedling_age > 30:
                self.acc['soil'][i] *= clamp(1 - 0.01 * (seedling_age - 30), 0.8, 1)   # older seedlings tiller less
            self.pest[i] = 0
            self.pestId[i] = -1
            n_ok += 1 if ok else 0
        if fd.transplantT is None:
            fd.transplantT = self.t
            fd.sowMonth = di.m
        self.version += 1
        return Obj(ok=True, squares=len(sq), msg=f'transplanted {len(sq)} squares in field {fd.name}')

    def act_irrigate(self, f, target_mm, t=None):
        """Open the field's inlet and set its float to target_mm: the shared pump fills the field from time t (the
        minute the watering arm set the gate) until the float closes, or for at most 24 hours."""
        fd = self.fields[f]
        tt = self.t if t is None else t
        fd.irrTarget = float(target_mm)
        fd.irrStart = tt
        fd.irrUntil = tt + 24 * HOUR
        fd.drainTarget = None
        self.version += 1
        return Obj(ok=True, msg=f'pumping field {fd.name} towards {target_mm:.0f} mm')

    def act_drain(self, f, to_mm, t=None):
        """Open the field's outlet down to to_mm from time t (the inlet's flow stops)."""
        fd = self.fields[f]
        tt = self.t if t is None else t
        fd.drainTarget = max(0.0, float(to_mm))
        fd.drainStart = tt
        fd.irrTarget = None
        self.version += 1
        return Obj(ok=True, msg=f'draining field {fd.name} to {to_mm:.0f} mm')

    def act_fertilise(self, f, squares, product, kg_per_ha):
        n, p, k = FERTILISERS[product]
        sq = self._squares(f, squares)
        g = kg_per_ha / 10.0          # kg/ha → g/m²
        depth = self.local_depth()
        lost = 0.0
        for i in sq:
            # urea broadcast into standing water loses more nitrogen to the air (ammonia volatilisation)
            loss = (0.08 + 0.22 * clamp(depth[i] / 50.0, 0, 1)) if product in ('urea', 'dap') else 0.0
            dn = g * n * 10 * A.FERT_RECOVERY.n * (1 - loss)
            self.nN[i] += dn
            self.nP[i] += g * p * 10 * A.FERT_RECOVERY.p
            self.nK[i] += g * k * 10 * A.FERT_RECOVERY.k
            self.ec[i] += 0.004 * g * (n + p + k)
            self.nApplied[i] += kg_per_ha * n
            lost += g * n * 10 * loss
        fd = self.fields[f]
        share = len(sq) / len(self.cells[f])
        fd.nKgHa += kg_per_ha * n * share
        fd.pKgHa += kg_per_ha * p * share
        fd.kKgHa += kg_per_ha * k * share
        self.version += 1
        return Obj(ok=True, squares=len(sq), lostNKgHa=lost / max(1, len(sq)), msg=f'{kg_per_ha:.0f} kg/ha {product} on {len(sq)} squares')

    def act_scout(self, f, squares):
        sq = self._squares(f, squares)
        for i in sq:
            self.inspect(i)
        self.fields[f].scouted += len(sq)
        self.version += 1
        return Obj(ok=True, squares=len(sq), found=sum(1 for i in sq if self.oPestId[i] >= 0), msg=f'scouted {len(sq)} squares')

    def spray_conditions(self, t=None):
        tt = self.t if t is None else t
        d, h = int(tt // DAY), (tt % DAY) / HOUR
        W = self.weather
        rain6 = W.rain_between(d, h, min(24, h + 6)) + (W.rain_between(d + 1, 0, h + 6 - 24) if h + 6 > 24 else 0)
        di = self.date_info(tt)
        return Obj(wind=W.wind(d, h), temp=W.temp(d, h), rh=W.rh(d, h), rain6=rain6, sunSin=max(0.0, math.sin(C.sun_elevation(di.doy, h))), hour=h)

    def act_spray(self, f, squares, product, target_pest=None):
        pc = D.PESTICIDES[product]
        pci = PC_IDX[product]
        sq = self._squares(f, squares)
        cond = self.spray_conditions()
        kills = []
        looks = []
        for i in sq:
            self.inspect(i)                                   # the sprayer's close-up look just before spraying
            seen = int(self.oPestId[i])
            looks.append((i, PEST_KEYS[seen] if seen >= 0 else None, self.obs_age(i)))
            pk = int(self.pestId[i])
            pid = PEST_KEYS[pk] if pk >= 0 else target_pest
            sf = Obj(baseEff=D.pesticide_efficacy(product, pid) if pid else 0.5, wind=cond.wind, temp=cond.temp, rain6=cond.rain6, uv=pc.uv,
                     sunSin=cond.sunSin, cat=D.PESTS[pid].cat if pid else 'larva', ageDays=float(self.pestAge[i]) if pk >= 0 else 0.0)
            eff = A.spray_efficacy(sf)
            if pk >= 0:
                e = eff * (1 if D.pesticide_efficacy(product, pid) > 0.05 else 0.05)
                self.pest[i] *= (1 - e)
                if self.pest[i] < 0.003:
                    self.pest[i] = 0
                    self.pestId[i] = -1
                    self.pestAge[i] = 0
                kills.append((i, eff))
            self.sprayEff[int(i)] = eff
            self.residue[i] = max(self.residue[i], 1)
            self.residuePc[i] = pci
            self.lastSprayT[i] = self.t
            self.sprayCount[i] += 1
            self.lastMoa[i] = pc.moa
            self.lastMoaT[i] = self.t
            self.noHarvestBefore[i] = max(self.noHarvestBefore[i], self.t + PHI_DAYS(product) * DAY)
            self.inspect(i)
            r, col = divmod(i, self.W)
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):   # a little drift onto neighbours
                rr, cc = r + dr, col + dc
                if 0 <= rr < self.H and 0 <= cc < self.W:
                    k = rr * self.W + cc
                    if self.field_of[k] == f and self.residue[k] < 0.06:
                        self.residue[k] = 0.06
                        self.residuePc[k] = pci
                        self.lastSprayT[k] = max(self.lastSprayT[k], self.t - 1)
        fd = self.fields[f]
        fd.sprays.append({'t': self.t, 'product': product, 'squares': len(sq)})
        fd.sprayL += pc.mlPerSite / 1000 * len(sq)
        ai, bio = AI_PER_M2(product)
        fd.aiG += ai * len(sq)
        fd.bioKg += bio * len(sq)
        self.version += 1
        return Obj(ok=True, squares=len(sq), kills=kills, conditions=cond, looks=looks,
                   effs=[(int(i), float(self.sprayEff[int(i)])) for i in sq],
                   msg=f'{product} on {len(sq)} squares (mean kill {np.mean([k for _, k in kills]) * 100:.0f}% on {len(kills)} infested)' if kills else f'{product} on {len(sq)} squares (none infested)')

    def harvest_ready_truth(self, i):
        d = self.day
        W = self.weather
        frac = float(self.frac[i])
        rain2 = W.rain_between(d, 0, self.hour) + W.day(max(0, d - 1)).rain
        rh = W.rh(d, self.hour)
        return frac >= 1 and ((rain2 < 6 and rh < 90) or (frac - 1) * PADDY.durationDays > 8)

    def yield_fraction(self, i, f=None):
        k = int(self.field_of[i]) if f is None else f
        month = self.fields[k].sowMonth or self.start_month
        acc = {key: float(self.acc[key][i]) for key in ACC_KEYS}
        return A.yield_fraction(acc, PADDY, PADDY.monthFit[month - 1])

    def act_harvest(self, f):
        fd = self.fields[f]
        d = self.day
        W = self.weather
        rain2 = W.day(d).rain + W.day(max(0, d - 1)).rain
        kg = 0.0
        sq = [int(i) for i in self.cells[f] if self.has[i] and not self.done[i]]
        for i in sq:
            if not self.alive[i]:
                self.done[i] = True
                continue
            frac = float(self.frac[i])
            yf = self.yield_fraction(i, f)
            mature = 1 if frac >= 1 else clamp((frac - 0.85) / 0.15, 0, 1)
            wet = 0.9 if rain2 >= 6 and (frac - 1) * PADDY.durationDays <= 8 else 1
            depth_pen = 0.95 if fd.h > 20 else 1.0          # harvesting a flooded field loses some grain
            k = POTENTIAL_KG_M2 * CFG.SQUARE_M2 * yf * mature * wet * depth_pen
            self.harvestKg[i] = k
            kg += k
            self.done[i] = True
            self.alive[i] = False
            self.pest[i] = 0
            self.pestId[i] = -1
            self.oPest[i] = 0
            self.oPestId[i] = -1
        fd.harvestT = self.t
        fd.harvestKg = kg
        area_ha = len(self.cells[f]) * CFG.SQUARE_M2 / 10000
        fd.yieldTHa = kg / 1000 / area_ha
        self.version += 1
        return Obj(ok=True, kg=kg, msg=f'harvested field {fd.name}: {kg:.1f} kg ({fd.yieldTHa:.2f} t/ha)')

    # ── time to AWD: the field's water level run forward with no irrigation (the truth of the v2 check) ──
    def level_state(self, f):
        """What project_level needs of field f now (kept by the audit until the 72 h have passed)."""
        fd = self.fields[f]
        cs = self.cells[f]
        return {'t': self.t, 'h': fd.h, 'alive': self.alive[cs] & ~self.done[cs], 'frac': self.frac[cs].copy(), 'gdd': self.gdd[cs].copy(),
                'ks': self.ks[cs].copy(), 'failed': self.failed[cs].copy(), 'failT': self.failT[cs].copy(), 'elev': self.elev[cs].copy(),
                'perc_day': fd.percPuddled if fd.puddled else fd.percOpen}

    def project_level(self, f, thr, hours=73, t0=None, h0=None, trace=None, state=None):
        """Hours until field f's water level falls to thr if nothing is irrigated or drained, from its true state
        (now, or a ``level_state`` kept earlier): the same hourly water balance as ``step_hour`` (crop water use with its
        own stress factor, percolation, bund seepage, rain, the weather that actually comes), the crop growing by thermal
        time and failed seedlings dying on schedule. Returns the crossing time interpolated within the hour, or
        ``hours`` if the level never gets there."""
        st = state or self.level_state(f)
        W = self.weather
        t = st['t'] if t0 is None else t0
        h = st['h'] if h0 is None else h0
        if h <= thr:
            return 0.0
        perc_day = st['perc_day']
        if trace is None and self._cannot_reach(h, thr, t, perc_day, int(hours)):
            return float(hours)          # what the loop below would return (it is only skipped when that is certain)
        alive = st['alive'].copy()
        n = len(alive)
        frac = st['frac'].copy()
        gdd = st['gdd'].copy()
        ks = st['ks'].copy()
        failed = st['failed']
        failT = st['failT']
        elev = st['elev']
        perc_day = st['perc_day']
        for k in range(int(hours)):
            d, hr = int(t // DAY), (t % DAY) / HOUR
            T = W.temp(d, hr + 0.5)
            rain = W.rain_between(d, hr, hr + 1.0)
            et0 = W.et0_between(d, hr, hr + 1.0)
            live = np.nonzero(alive)[0]
            n_live = len(live)
            if n_live:
                fl = np.minimum(1.0, frac[live])
                kc = float(np.mean([A.kc(PADDY, float(x)) for x in fl])) if n_live < 4 else float(np.mean(self._kc_vec(fl)))
                kss = float(np.mean(ks[live]))
            else:
                kc, kss = 0.0, 1.0
            wet = clamp(1 + h / 300.0, 0, 1)
            kc_bare = 1.05 if h > 0 else 0.75 * wet ** 1.5
            et = ((n_live * kc * kss) + (n - n_live) * kc_bare) / n * et0
            if h > 0:
                perc = perc_day * (1 + h / CFG.PERC_HEAD_MM) / 24
                seep = CFG.BUND_SEEP_PER_DAY * h / 24
            else:
                perc = perc_day / 24 * clamp(1 + h / CFG.PERC_STOP_MM, 0.0, 1)
                seep = 0.0
            perc += seep
            net = rain - et - perc
            if h >= 0:
                hn = h + net
                if hn < 0:
                    hn = hn / CFG.SPECIFIC_YIELD
            else:
                refill = -h * CFG.SPECIFIC_YIELD
                hn = net - refill if net > refill else h + net / CFG.SPECIFIC_YIELD
            if hn > CFG.BUND_MM:
                hn = float(CFG.BUND_MM)
            hn = max(hn, -800.0)
            if trace is not None:
                trace.append(hn)
            if hn <= thr:
                return k + (h - thr) / (h - hn) if h > hn else float(k)
            h = hn
            # stress factor and growth for the next hour, as step_hour does them
            if n_live:
                depth = h - elev[live]
                st = np.full(n_live, float(CFG.AWD_THRESHOLD_MM))
                fl = np.minimum(1.0, frac[live])
                st[(fl >= SENSITIVE[0]) & (fl < SENSITIVE[1])] = 0.0
                kk = np.where(depth >= st, 1.0, np.clip(1 - (st - depth) / 250.0, 0, 1))
                kk = np.where(frac[live] >= 0.9, 1.0, kk)
                ks[live] = kk
                gr = max(0.0, min(T, PADDY.tOpt[1] + 3) - PADDY.tBase)
                gdd[live] += gr * (1.0 / 24)
                frac[live] = gdd[live] / TOT_GDD
                dead = failed[live] & (t >= failT[live])
                if dead.any():
                    alive[live[dead]] = False
            t += HOUR
        return float(hours)

    def _cannot_reach(self, h, thr, t, perc_day, hours):
        """True when the level provably stays above thr for ``hours`` (a speed-up of project_level that changes no
        result): a floor on the level hour by hour, with no rain and the most the field can lose — crop water use at
        paddy's highest crop coefficient (1.2; bare soil uses at most 1.05), percolation at the head of a full bund
        (percPuddled × 2) plus full bund seepage while there is water above the surface, and that loss divided by the
        specific yield below it. Each hour of the true balance stays above this floor (the floor's step is the smallest
        any step can be, and it rises with the level), so if the floor never reaches thr the level does not either."""
        W = self.weather
        loss_fix = perc_day / 12 + CFG.BUND_SEEP_PER_DAY * CFG.BUND_MM / 24
        c_max = max(PADDY.kc.values()) if hasattr(PADDY.kc, 'values') else 1.2
        c_max = max(c_max, 1.05)
        SY = CFG.SPECIFIC_YIELD
        g, tt = h, t
        for _ in range(hours):
            d, hr = int(tt // DAY), (tt % DAY) / HOUR
            L = c_max * W.et0_between(d, hr, hr + 1.0) + loss_fix
            if g > 0:
                g = g - L
                if g < 0:
                    g = g / SY
            else:
                g = g - L / SY
            if g <= thr + 1e-6:
                return False
            tt += HOUR
        return True

    def project_level_many(self, st, thr, Th, Rh, Eh, hours=73):
        """``project_level`` for many weathers at once, from a state dict like ``level_state``'s: Th, Rh, Eh are
        (draws, hours) arrays of each hour's temperature (at its middle), rain and reference evapotranspiration.
        Returns the crossing hour of each draw (``hours`` if none). The same arithmetic as project_level, element by
        element (checked against it with the weather that came, tools/models/ceiling_awd.py)."""
        Dn = Th.shape[0]
        if st['h'] <= thr:
            return np.zeros(Dn)
        h = np.full(Dn, float(st['h']))
        out = np.full(Dn, float(hours))
        alive = st['alive'].copy()
        n = len(alive)
        frac = np.tile(st['frac'], (Dn, 1))
        gdd = np.tile(st['gdd'], (Dn, 1))
        ks = np.tile(st['ks'], (Dn, 1))
        failed, failT, elev, perc_day = st['failed'], st['failT'], st['elev'], st['perc_day']
        done = np.zeros(Dn, bool)
        t = st['t']
        t_top = PADDY.tOpt[1] + 3
        SY = CFG.SPECIFIC_YIELD
        for k in range(int(hours)):
            live = np.nonzero(alive)[0]
            n_live = len(live)
            if n_live:
                fl = np.minimum(1.0, frac[:, live])
                if n_live < 4:
                    kc = np.array([np.mean([A.kc(PADDY, float(x)) for x in row]) for row in fl])
                else:
                    kc = self._kc_vec(fl.ravel()).reshape(Dn, n_live).mean(axis=1)
                kss = ks[:, live].mean(axis=1)
            else:
                kc, kss = np.zeros(Dn), np.ones(Dn)
            wet = np.clip(1 + h / 300.0, 0, 1)
            kc_bare = np.where(h > 0, 1.05, 0.75 * wet ** 1.5)
            et = ((n_live * kc * kss) + (n - n_live) * kc_bare) / n * Eh[:, k]
            perc = np.where(h > 0, perc_day * (1 + h / CFG.PERC_HEAD_MM) / 24 + CFG.BUND_SEEP_PER_DAY * h / 24,
                            perc_day / 24 * np.clip(1 + h / CFG.PERC_STOP_MM, 0.0, 1))
            net = Rh[:, k] - et - perc
            up = h + net
            up = np.where(up < 0, up / SY, up)
            refill = -h * SY
            down = np.where(net > refill, net - refill, h + net / SY)
            hn = np.where(h >= 0, up, down)
            hn = np.maximum(np.minimum(hn, float(CFG.BUND_MM)), -800.0)
            cross = (~done) & (hn <= thr)
            if cross.any():
                gap = h - hn
                frac_h = np.where(gap > 0, (h - thr) / np.where(gap > 0, gap, 1.0), 0.0)
                out[cross] = k + frac_h[cross]
                done |= cross
                if done.all():
                    break
            h = np.where(done, h, hn)
            if n_live:
                depth = h[:, None] - elev[live][None, :]
                stt = np.full((Dn, n_live), float(CFG.AWD_THRESHOLD_MM))
                fl = np.minimum(1.0, frac[:, live])
                stt[(fl >= SENSITIVE[0]) & (fl < SENSITIVE[1])] = 0.0
                kk = np.where(depth >= stt, 1.0, np.clip(1 - (stt - depth) / 250.0, 0, 1))
                kk = np.where(frac[:, live] >= 0.9, 1.0, kk)
                ks[:, live] = kk
                gr = np.maximum(0.0, np.minimum(Th[:, k], t_top) - PADDY.tBase)
                gdd[:, live] += gr[:, None] * (1.0 / 24)
                frac[:, live] = gdd[:, live] / TOT_GDD
                dead = failed[live] & (t >= failT[live])
                if dead.any():
                    alive[live[dead]] = False
            t += HOUR
        return out

    # ── per-square work by the arms (each at the time the arm did it) ──
    def probe_square(self, i, t):
        """The planting arm's soil probe on one square (same noise as act_soil_check)."""
        r = self.rng
        i = int(i)
        p = Obj(ph=float(self.ph[i]) + r.normal(0, 0.03), n=float(self.nN[i]) + r.normal(0, 3), p=float(self.nP[i]) + r.normal(0, 0.6),
                k=float(self.nK[i]) + r.normal(0, 3), oc=float(self.oc[i]), sand=float(self.sand[i]), clay=float(self.clay[i]),
                ec=max(0.02, float(self.ec[i]) + r.normal(0, 0.02)), t=t)
        self.probe[i] = p
        self.version += 1
        return p

    def amend_square(self, f, i, product, dose):
        """One square of an amendment (act_amend's formulas); returns the kilograms of product used."""
        i = int(i)
        kg = 0.0
        self.amendG[product][i] += dose
        if product == 'lime':
            d_ph = min(1.2, dose / (150 + 6 * self.clay[i]) * 0.85)
            self.ph[i] = clamp(self.ph[i] + d_ph, 4.2, 8.8)
            kg += dose / 1000
        elif product == 'sulphur':
            d_ph = -min(0.8, dose / (40 + 1.5 * self.clay[i]) * 0.7)
            self.ph[i] = clamp(self.ph[i] + d_ph, 4.2, 8.8)
            kg += dose / 1000
        elif product == 'gypsum':
            self.ec[i] = max(0.08, self.ec[i] * (1 - min(0.35, dose / 1500)))
            if self.ph[i] > 7.5:
                self.ph[i] = max(7.0, self.ph[i] - min(0.4, dose / 1500))
            kg += dose / 1000
        elif product == 'compost':
            k = dose / 1500
            self.oc[i] = min(1.4, self.oc[i] + 0.08 * k)
            self.nN[i] += 15 * k
            self.nK[i] += 10 * k
            self.fc[i] = min(0.45, self.fc[i] + 0.006 * k)
            kg += dose / 1000
        elif product == 'leach':
            self.ec[i] = max(0.08, self.ec[i] * (0.72 if dose >= 40 else 1 - 0.28 * dose / 40))
        self.probe.pop(i, None)
        fd = self.fields[f]
        fd.amendKg[product] = fd.amendKg.get(product, 0.0) + kg
        if product == 'leach':
            # the hose's water spreads over the whole basin
            water_mm = dose / len(self.cells[f])
            fd.irrMm += water_mm
            fd.waterM3 += dose * CFG.SQUARE_M2 / 1000
            fd.energyKwh += dose * CFG.SQUARE_M2 / 1000 * CFG.PUMP_KWH_PER_M3
            fd.h = self.level_after_adding(fd.h, water_mm)
            if fd.h > CFG.BUND_MM:
                fd.overflowMm += fd.h - CFG.BUND_MM
                fd.h = float(CFG.BUND_MM)
        self.version += 1
        return kg

    def puddle_square(self, f, i):
        """The rotary puddler over one square evens its surface a little (act_puddle's 0.8)."""
        self.elev[int(i)] *= 0.8
        self.version += 1

    def puddle_finish(self, f):
        """Every square done: the plough pan is sealed and the field counts as puddled."""
        self.fields[f].puddled = True
        self.version += 1

    def transplant_square(self, f, i, t, seedling_age=None):
        """One seedling hill transplanted at time t (act_transplant's rules, the establishment draw keyed to t)."""
        fd = self.fields[f]
        i = int(i)
        if seedling_age is None:
            seedling_age = self.seedling_age(t)
        p = self.true_establish_chance(f, [i], seedling_age, t=t)[0]
        ok = hash01(self.seed, 'est', i, int(t)) < p
        self.estP[i] = p
        self.has[i] = True
        self.alive[i] = True
        self.failed[i] = not ok
        self.failT[i] = t + (3 + 2 * hash01(self.seed, 'failT', i, int(t))) * DAY if not ok else -1
        self.done[i] = False
        self.plantedT[i] = t
        self.gdd[i] = 0
        self.frac[i] = 0
        self.health[i] = 0.9
        self.ks[i] = 1
        self.nutF[i] = 1
        self.tempF[i] = 1
        self.seedAge[i] = seedling_age
        self.harvestKg[i] = 0
        for k in ACC_KEYS:
            self.acc[k][i] = 0.0
        self.acc['soil'][i] = A.soil_total(self.true_soil(i), PADDY)
        if seedling_age > 30:
            self.acc['soil'][i] *= clamp(1 - 0.01 * (seedling_age - 30), 0.8, 1)
        self.pest[i] = 0
        self.pestId[i] = -1
        if fd.transplantT is None:
            fd.transplantT = t
            fd.sowMonth = self.date_info(t).m
        self.version += 1
        return ok

    def fertilise_square(self, f, i, product, kg_per_ha, depth_mm):
        """One square's share of a fertiliser decision (act_fertilise's formulas). Returns the N lost to the air (g)."""
        n, p, k = FERTILISERS[product]
        i = int(i)
        g = kg_per_ha / 10.0
        loss = (0.08 + 0.22 * clamp(depth_mm / 50.0, 0, 1)) if product in ('urea', 'dap') else 0.0
        dn = g * n * 10 * A.FERT_RECOVERY.n * (1 - loss)
        self.nN[i] += dn
        self.nP[i] += g * p * 10 * A.FERT_RECOVERY.p
        self.nK[i] += g * k * 10 * A.FERT_RECOVERY.k
        self.ec[i] += 0.004 * g * (n + p + k)
        self.nApplied[i] += kg_per_ha * n
        fd = self.fields[f]
        share = 1.0 / len(self.cells[f])
        fd.nKgHa += kg_per_ha * n * share
        fd.pKgHa += kg_per_ha * p * share
        fd.kKgHa += kg_per_ha * k * share
        self.version += 1
        return g * n * 10 * loss

    def scout_square(self, f, i, t):
        self.inspect(i, t)
        self.fields[f].scouted += 1
        self.version += 1
        return int(self.oPestId[int(i)]) >= 0

    def spray_square(self, f, i, product, target_pest, t):
        """The pest arm's close-up look and spray on one square at time t: the kill uses the wind, temperature, sun and
        the rain of the six hours after t (act_spray's formulas). Returns (looked-at pest, its tracked age, the
        conditions, the share killed, whether the square was infested)."""
        pc = D.PESTICIDES[product]
        pci = PC_IDX[product]
        i = int(i)
        cond = self.spray_conditions(t)
        self.inspect(i, t)
        seen = int(self.oPestId[i])
        look = (i, PEST_KEYS[seen] if seen >= 0 else None, self.obs_age(i, t))
        pk = int(self.pestId[i])
        pid = PEST_KEYS[pk] if pk >= 0 else target_pest
        sf = Obj(baseEff=D.pesticide_efficacy(product, pid) if pid else 0.5, wind=cond.wind, temp=cond.temp, rain6=cond.rain6, uv=pc.uv,
                 sunSin=cond.sunSin, cat=D.PESTS[pid].cat if pid else 'larva', ageDays=float(self.pestAge[i]) if pk >= 0 else 0.0)
        eff = A.spray_efficacy(sf)
        infested = pk >= 0
        # what decided the kill (for the planned-spray ceiling): the true pest, its true age and its base efficacy
        truth = (pk, sf.ageDays, sf.baseEff)
        if infested:
            e = eff * (1 if D.pesticide_efficacy(product, pid) > 0.05 else 0.05)
            self.pest[i] *= (1 - e)
            if self.pest[i] < 0.003:
                self.pest[i] = 0
                self.pestId[i] = -1
                self.pestAge[i] = 0
        self.sprayEff[i] = eff
        self.residue[i] = max(self.residue[i], 1)
        self.residuePc[i] = pci
        self.lastSprayT[i] = t
        self.sprayCount[i] += 1
        self.lastMoa[i] = pc.moa
        self.lastMoaT[i] = t
        self.noHarvestBefore[i] = max(self.noHarvestBefore[i], t + PHI_DAYS(product) * DAY)
        self.inspect(i, t)
        r, col = divmod(i, self.W)
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            rr, cc = r + dr, col + dc
            if 0 <= rr < self.H and 0 <= cc < self.W:
                k = rr * self.W + cc
                if self.field_of[k] == f and self.residue[k] < 0.06:
                    self.residue[k] = 0.06
                    self.residuePc[k] = pci
                    self.lastSprayT[k] = max(self.lastSprayT[k], t - 1)
        fd = self.fields[f]
        fd.sprayL += pc.mlPerSite / 1000
        ai, bio = AI_PER_M2(product)
        fd.aiG += ai
        fd.bioKg += bio
        self.version += 1
        return look, cond, eff, infested, truth

    def spray_record(self, f, product, n_squares, t):
        """One spray decision in the field's records (one entry however many squares it covered)."""
        self.fields[f].sprays.append({'t': t, 'product': product, 'squares': n_squares})

    def harvest_square(self, f, i, t):
        """The harvest arm cuts one square at time t (act_harvest's rules: unripe grain, a wet crop and a flooded field
        lose some yield). Returns the kilograms."""
        fd = self.fields[f]
        i = int(i)
        d = int(t // DAY)
        W = self.weather
        if not self.alive[i]:
            self.done[i] = True
            self.version += 1
            return 0.0
        rain2 = W.day(d).rain + W.day(max(0, d - 1)).rain
        frac = float(self.frac[i])
        yf = self.yield_fraction(i, f)
        mature = 1 if frac >= 1 else clamp((frac - 0.85) / 0.15, 0, 1)
        wet = 0.9 if rain2 >= 6 and (frac - 1) * PADDY.durationDays <= 8 else 1
        depth_pen = 0.95 if fd.h > 20 else 1.0
        k = POTENTIAL_KG_M2 * CFG.SQUARE_M2 * yf * mature * wet * depth_pen
        self.harvestKg[i] = k
        self.done[i] = True
        self.alive[i] = False
        self.pest[i] = 0
        self.pestId[i] = -1
        self.oPest[i] = 0
        self.oPestId[i] = -1
        self.version += 1
        return k

    def harvest_finish(self, f, t):
        """Every square cut: the field's harvest is recorded."""
        fd = self.fields[f]
        for i in self.cells[f]:
            if self.has[i] and not self.done[i] and not self.alive[i]:
                self.done[i] = True
        kg = float(sum(self.harvestKg[i] for i in self.cells[f]))
        fd.harvestT = t
        fd.harvestKg = kg
        area_ha = len(self.cells[f]) * CFG.SQUARE_M2 / 10000
        fd.yieldTHa = kg / 1000 / area_ha
        self.version += 1
        return Obj(ok=True, kg=kg, msg=f'harvested field {fd.name}: {kg:.1f} kg ({fd.yieldTHa:.2f} t/ha)')

    # ── summaries ──
    def field_progress(self, f):
        # cached per twin version: every change to the plants (the hourly growth, every action) bumps the version
        c = self._fp_cache
        if c.get('ver') != self.version:
            c.clear()
            c['ver'] = self.version
        v = c.get(f)
        if v is not None:
            return v
        cs = self.cells[f]
        live = cs[self.alive[cs] & ~self.done[cs]]
        v = 0.0 if not len(live) else float(np.median(self.frac[live]))
        c[f] = v
        return v

    def methane(self, f):
        """IPCC Tier 1 (2019 Refinement): baseline emission factor × water-regime scaling factor × organic-amendment
        scaling factor × days of cultivation. The pre-season water regime factor is 1 (not flooded before the
        season)."""
        fd = self.fields[f]
        days = fd.cultH / 24
        regime = 'continuous' if fd.drainPeriods == 0 else 'single' if fd.drainPeriods == 1 else 'multiple'
        # compost: dry tonnes per hectare over the field (fresh weight × dry-matter share), conversion factor 0.17
        compost_t_ha = fd.amendKg.get('compost', 0.0) * CFG.COMPOST_DRY_MATTER / 1000 / (len(self.cells[f]) * CFG.SQUARE_M2 / 10000)
        sf_o = (1 + compost_t_ha * CFG.CH4_CFOA_COMPOST) ** 0.59
        kg_ha = CFG.CH4_EF_KG_HA_DAY * CFG.CH4_SF_WATER[regime] * sf_o * days
        return Obj(kgHa=kg_ha, regime=regime, days=days, drainPeriods=fd.drainPeriods, sfOrganic=sf_o)

    def season_over(self):
        return all(fd.harvestT is not None or fd.abandoned for fd in self.fields) or self.day >= CFG.SEASON_MAX_DAYS
