"""Advice from the trained random forests, built only from what the farm knows.

The forests are the original project's (soil suitability, planting-day establishment for transplanted crops,
pest risk over 7 days, yield forecast, spray efficacy and harvest readiness). Their inputs are put together the
same way the original farm manager put them together: probe or scan readings, the forecasts as issued (today's
and the two previous days' issues, the hourly forecast), the rain gauge and station, the cameras' sightings.
The decision maker sees the outputs as labelled advice; nothing here decides anything.
"""
from __future__ import annotations

import math

import numpy as np

from .. import config as CFG
from ..core import agro as A
from ..core import climate as C
from ..core import data as D
from ..core import models as M
from ..core import paddy_features as PF
from ..core.util import Obj, clamp, hash01, hash_n
from ..decisions import rules as R
from . import wxpost as WP
from .twin import DAY, HOUR, PADDY, PEST_IDX, PEST_KEYS, PC_KEYS, SENSITIVE, TOT_GDD, establish_adjust

SENSITIVE_LO, SENSITIVE_HI = SENSITIVE
AWD_CAP = 96                                   # hours the farm's own water-balance estimate looks ahead
AWD_MC_HOURS = 76                              # v2: the horizon past which no draw needs running (the check looks 72 h ahead)
AWD_POST_DRAWS, AWD_POST_K, AWD_POST_K_SEEN, AWD_POST_M = 256, 3000, 30000, 48   # v2: the posterior Monte Carlo's size (256 draws: the value most draws agree on settles)
PLAN_POST_DRAWS = 128                          # v2: posterior draws of the rain after a planned spray

def anchored_predict(rf, X):
    """A paddy forest's prediction. A forest anchored on one of its inputs (``rf.anchor``: the farm's own posterior
    estimate) predicts a correction of that input — its ratio ('ratio', at least ``min``) or its difference ('diff') —
    so where the forest has nothing to add, the prediction is the posterior estimate itself."""
    p = rf.predict_batch(X)
    a = getattr(rf, 'anchor', None)
    if a:
        base = X[:, int(a['feature'])]
        if a.get('kind') == 'ratio':
            p = np.maximum(float(a.get('min', 1.0)), base) * p
        elif a.get('kind') == 'diff':
            p = base + p
    return np.maximum(0.0, p)


SOIL_CODES = {0: 'U', 1: 'A', 2: 'S'}          # unsuitable / needs amendment / suitable
EST_DECISION = 0.72                            # the original farm transplants when the chance is at least 72 %
PEST_ACT_AT_RAW = 0.1                          # the original farm treats a raw forest score of 0.1 as "at risk"
HARVEST_AT = 0.75                              # …and sends the harvester at a readiness score of 0.75


class Advisor:
    def __init__(self, twin, suite=None, policy=None):
        self.tw = twin
        self.m = suite or M.ModelSuite.shipped().view()
        # how the farm is managed, as the yield forest describes it (soil test, smart water, spot spraying)
        self.policy = {'soilCheck': True, 'smartWater': True, 'spotSpray': True}
        self.policy.update(policy or {})
        tw = twin
        # the farm's survey of square heights (laser level, ±5 mm)
        self.elev_obs = np.array([tw.elev[i] + 5 * (hash01(tw.seed, 'survey', i) - 0.5) * 2 for i in range(tw.n)])
        self._cache = {}
        self.keep_x = False           # v2: keep each establishment check's feature row (for the model round's data)
        # pest-risk scores are calibrated so that 0.5 is the forest's own decision point; the original farm sprays
        # at a raw score of 0.1, which is this calibrated value
        cal = self.m.cal.get('pest')
        self.pest_act_at = M.calib(PEST_ACT_AT_RAW, cal) if cal is not None else PEST_ACT_AT_RAW

    def _key(self, name, extra=()):
        return (name, self.tw.version, round(self.tw.t)) + tuple(extra)

    def _cached(self, key, fn):
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if len(self._cache) > 64:
            self._cache.clear()
        v = fn()
        self._cache[key] = v
        return v

    # ── soil ──
    def soil(self, f):
        def run():
            tw = self.tw
            cells = tw.cells[f]
            readings = [tw.soil_reading(i) for i in cells]
            sp = self.m.m.get('soilP') if hasattr(self.m, 'm') else None
            if sp is not None:
                # v2 paddy soil forest: the reading, the rules on it and the share of the probe-noise re-draws in each
                # class (paddy is the only crop here)
                Xs = np.array([[rd[k] for k in ('ph', 'n', 'p', 'k', 'oc', 'sand', 'clay', 'ec')] for rd in readings], float)
                P = sp.predict_proba_batch(np.c_[Xs, PF.soil_extra(Xs)])
            else:
                P = self.m.soil_proba_batch(readings, PADDY)
            out = []
            for i, rd, p in zip(cells, readings, P):
                lab = int(np.argmax(p))
                doses = A.amendment_doses(rd, PADDY) if lab >= 1 else []
                out.append(Obj(i=int(i), sq=tw.sq_name(i), code=SOIL_CODES[lab], proba=[float(x) for x in p], probed=int(i) in tw.probe,
                               doses=[Obj(product=d.kind, dose=float(d.get('grams') if d.get('grams') is not None else d.get('mm')),
                                          unit='g/m²' if d.get('grams') is not None else 'mm') for d in doses]))
            return out
        return self._cached(self._key('soil', (f,)), run)

    # ── planting day ──
    def establish_shared(self):
        def run():
            tw = self.tw
            W = tw.weather
            d, h = tw.day, tw.hour
            fc = [W.forecast_day(d, k) for k in range(3)]
            rain_h, temp_h = [], []
            for q in range(72):
                x = W.forecast_hour(tw.t, tw.t + q * HOUR)
                rain_h.append(x.rain)
                temp_h.append(x.temp)
            rain_rest = sum(rain_h[q] for q in range(math.ceil(24 - h)))
            er = [M.exp_rain_day(x) for x in fc]
            p1 = tw.fc_log.get(d - 1)
            p2 = tw.fc_log.get(d - 2)
            return Obj(maxRain3=max(er), sumRain3=er[0] + er[1] + er[2], tmax3=max(x.tmax for x in fc), fc=fc, doy=tw.date_info().doy, hour=h,
                       rainSoFar=W.rain_between(d, 0, h), rainRest=rain_rest, tSoFar=max(W.temp(d, 0), W.temp(d, min(h, 14.5))),
                       rainH=rain_h, tempH=temp_h, prev1=p1, prev2=p2)
        return self._cached(self._key('est_shared'), run)

    def establishment(self, f, squares=None):
        """Chance that a seedling transplanted now establishes, per square (the transplanted-crop forest)."""
        def run():
            tw = self.tw
            ec = self.establish_shared()
            cells = tw.cells[f] if squares is None else squares
            fd = tw.fields[f]
            h_obs = tw.water_tube(f)
            ctxs = []
            for i in cells:
                d_est = h_obs - self.elev_obs[i]
                theta = float(tw.theta_from_depth(np.full(tw.n, d_est))[i])
                taw = max(0.02, tw.fc[i] - tw.wp[i])
                ctx = Obj(ec)
                ctx.update(thetaRel=(theta - tw.wp[i]) / taw, eSd=0.01, sand=float(tw.sand[i]), transplant=True, tMaxCrop=PADDY.tMax, wlTol=PADDY.wlTol)
                ctxs.append(ctx)
            # the farm's practice factors (deep water, a dried-out field, old seedlings) from the observed depth
            age = tw.seedling_age()
            adj = [establish_adjust(h_obs - self.elev_obs[i], age) for i in cells]
            ep = self.m.m.get('establishP') if hasattr(self.m, 'm') else None
            if ep is not None:
                # v2 paddy establishment forest: the transplanted-crop features and the practice factor in, the chance out
                xs = [M._feat_establish_full(c) for c in ctxs]
                ch = np.clip(ep.predict_batch(np.array([x + [a] for x, a in zip(xs, adj)], float)), 0.02, 0.99).tolist()
                return [Obj(i=int(i), sq=tw.sq_name(i), chance=float(c), forest=float(c) / a if a > 0 else float(c), adj=a, x=x)
                        for i, c, a, x in zip(cells, ch, adj, xs)], fd.name
            ps = self.m.establish_batch(ctxs)
            xs = [M._feat_establish_full(c) for c in ctxs] if self.keep_x else [None] * len(ctxs)
            return [Obj(i=int(i), sq=tw.sq_name(i), chance=float(p) * a, forest=float(p), adj=a, x=x)
                    for i, p, a, x in zip(cells, ps, adj, xs)], fd.name
        return self._cached(self._key('est', (f, None if squares is None else tuple(squares))), run)[0]

    # ── yield ──
    def yield_forecast(self, f, offsets=(0, 4, 8, 12, 16, 20, 24, 28)):
        """Share of attainable yield the yield forest expects if the field is transplanted today (and the best of
        the next four weeks)."""
        def run():
            tw = self.tw
            cells = tw.cells[f]
            soil = self.soil(f)
            readings = [tw.soil_reading(i) for i in cells]
            sf = [A.soil_total(r, PADDY) for r in readings]
            n = len(cells)
            suit = sum(s.proba[2] + 0.75 * s.proba[1] for s in soil) / n
            unsuit = sum(1 for s in soil if s.code == 'U') / n
            bed = Obj({k: float(np.mean([r[k] for r in readings])) for k in ('ph', 'n', 'p', 'k', 'oc', 'sand', 'clay', 'ec')})
            sf_bed = A.soil_factors(bed, PADDY).total
            ctxs, info = [], []
            for off in offsets:
                di = tw.date_info(tw.t + off * DAY)
                exp = M.expected_season(PADDY, di.doy)
                ctxs.append(Obj(crop=PADDY, sfMean=float(np.mean(sf)), sfMin=float(np.min(sf)), sfBed=sf_bed, monthFit=PADDY.monthFit[di.m - 1], exp=exp,
                                unsuit=unsuit, suit=suit, soilCheck=self.policy['soilCheck'], smartWater=self.policy['smartWater'],
                                spotSpray=self.policy['spotSpray'], nCells=n))
                info.append(off)
            yfs = self.m.yield_frac_batch(ctxs).tolist()
            best = max(zip(info, yfs), key=lambda x: x[1] - 0.004 * x[0])
            return Obj(today=float(yfs[0]), bestDay=int(best[0]), best=float(best[1]), ctx=ctxs[0])
        return self._cached(self._key('yield', (f,)), run)

    # ── pests ──
    def field_pests(self, f):
        tw = self.tw
        by = {}
        live = 0
        any_ = 0
        for i in tw.cells[f]:
            if tw.alive[i] and not tw.done[i]:
                live += 1
            pk = int(tw.oPestId[i])
            if pk < 0 or not (tw.oPest[i] > 0):
                continue
            any_ += 1
            g = by.setdefault(pk, {'n': 0, 'max': 0.0, 'cells': []})
            g['n'] += 1
            g['max'] = max(g['max'], float(tw.oPest[i]))
            g['cells'].append(int(i))
        return {'by': by, 'any': any_, 'live': live}

    def _announced_share(self, f, pk, fp):
        tw = self.tw
        pid = PEST_KEYS[pk]
        o = next((x for x in tw.outbreaks if x['field'] == f and x['pest'] == pid and tw.t - x['start'] < DAY), None)
        if o is None:
            return 0.0
        seen = fp['by'].get(pk, {'n': 0})['n']
        unseen = max(1, fp['live'] - seen)
        return min(1.0, max(0, o['n'] - seen) / unseen)

    def pest_contexts(self, f):
        """Pest-risk inputs for every plant the original farm would ask about (a sighting on it or within two
        squares, or an outbreak announced in the field)."""
        tw = self.tw
        W = tw.weather
        d = tw.day
        fcs = [W.forecast_day(d, k) for k in range(3)]
        T = sum((x.tmax + x.tmin) / 2 for x in fcs) / 3
        rh = sum(x.rh for x in fcs) / 3
        rain7 = sum(x.rainMm * x.rainProb for x in fcs) * 7 / 3
        fc = [[x.tmax, x.tmin, x.rh, x.rainMm, x.rainProb] for x in fcs]
        fp = self.field_pests(f)
        cases = []
        if not fp['by'] and not any(o['field'] == f and o.get('active') for o in tw.outbreaks):
            return cases, fp
        pks = set(fp['by'].keys()) | {PEST_IDX[o['pest']] for o in tw.outbreaks if o['field'] == f and o.get('active')}
        for pk in sorted(pks):
            pid = PEST_KEYS[pk]
            if pid not in PADDY.pests:
                continue
            n1 = tw.obs_nbr_sev_grid(pk, 1)
            n2 = tw.obs_nbr_sev_grid(pk, 2)
            g = fp['by'].get(pk, {'n': 0, 'max': 0.0})
            ann = self._announced_share(f, pk, fp)
            for i in tw.cells[f]:
                if not tw.alive[i] or tw.done[i]:
                    continue
                opk = int(tw.oPestId[i])
                own = float(tw.oPest[i]) if opk == pk else 0.0
                if own >= A.ETL:
                    continue
                if opk < 0 and not (n2[i] > 0) and ann <= 0:
                    continue
                other = float(tw.oPest[i]) if opk >= 0 and opk != pk else 0.0
                pc = PC_KEYS[tw.residuePc[i]] if tw.residuePc[i] >= 0 else None
                ores = max(0.0, float(tw.residue[i]) + 0.02 * (hash01(tw.seed, 'res', i, int(tw.t // HOUR)) - 0.5) * 2)
                ctx = Obj(T=T, rh=rh, rain7=rain7, frac=float(tw.frac[i]), pest=D.PESTS[pid], pestId=pid, crop=PADDY,
                          nbrSev=float(n1[i]), nbr2=float(n2[i]), curSev=own, protection=ores * (D.pesticide_efficacy(pc, pid) if pc else 0),
                          residue=ores, pesticide=pc, sinceSpray=(tw.t - float(tw.lastSprayT[i])) / DAY,
                          outbreak=1 if any(o['field'] == f and o['pest'] == pid and tw.t < o['until'] for o in tw.outbreaks) else 0,
                          fc=fc, gdd=float(tw.gdd[i]), totGDD=TOT_GDD, doy=tw.date_info().doy,
                          bedSeen=g['n'], bedMax=g['max'], bedShare=g['n'] / fp['any'] if fp['any'] else 0, otherSev=other,
                          vote=int(tw.oIdVote[i]) if own > 0 else 0, announced=0 if own > 0 else ann, trapAlert=0)
                cases.append((int(i), pk, ctx))
        return cases, fp

    def pest_risk(self, f):
        def run():
            cases, fp = self.pest_contexts(f)
            preds = self.m.pest_risk_batch([c[2] for c in cases]) if cases else []
            out = [Obj(i=i, sq=self.tw.sq_name(i), pest=PEST_KEYS[pk], risk=float(p), ctx=ctx) for (i, pk, ctx), p in zip(cases, preds)]
            return out, fp
        return self._cached(self._key('pest', (f,)), run)

    # ── sprays ──
    def spray_options(self, f, pid, max_products=4, hours=18):
        """For a pest seen in the field: each product labelled for it on paddy that works on it, with the
        spray-efficacy forest's best hour in the next 18 hours and its prediction for right now. The twin runs in
        one-hour steps, so the candidate times are whole hours."""
        def run():
            tw = self.tw
            W = tw.weather
            pest = D.PESTS[pid]
            cells = tw.cells[f]
            age = max([0.0] + [tw.obs_age(i) for i in cells if tw.oPestId[i] == PEST_IDX[pid]])
            flowering = any(tw.alive[i] and A.is_flowering(PADDY, min(1, float(tw.frac[i]))) for i in cells)
            base = math.ceil(tw.t / HOUR - 1e-9) * HOUR
            opts = []
            # the candidate hours and their forecasts are the same for every product: work them out once
            hrs = []
            for k in range(hours + 1):
                t = base + k * HOUR
                h = (t % DAY) / HOUR
                if h < R.SPRAY_HOURS[0] or h > R.SPRAY_HOURS[1]:
                    continue
                fh = W.forecast_hour(tw.t, t)
                rain_h = [W.forecast_hour(tw.t, t + q * HOUR).rain for q in range(6)]
                di = tw.date_info(t)
                sun = max(0.0, math.sin(C.sun_elevation(di.doy, h)))
                hrs.append((k, t, h, fh, rain_h, sun))
            prods = []
            for pcid in R.products_for(pid):
                pc = D.PESTICIDES.get(pcid)
                if not pc:
                    continue
                eff = D.pesticide_efficacy(pcid, pid)
                if eff < 0.3:
                    continue
                ctxs, slots = [], []
                for k, t, h, fh, rain_h, sun in hrs:
                    ctxs.append(Obj(hour=h, wind=fh.wind, temp=fh.temp, rh=fh.rh, rain6=sum(rain_h), rainH=rain_h, uv=pc.uv, baseEff=eff,
                                    cat=pest.cat, ageDays=age + k / 24, sunSin=sun))
                    slots.append((k, t, h, fh.wind, sum(rain_h)))
                if ctxs:
                    prods.append((pcid, pc, eff, ctxs, slots))
            # one forest call for every product (the spray forest takes the median of its trees, row by row, so this is
            # exactly what one call per product gives)
            allc = [c for x in prods for c in x[3]]
            alle = self.m.spray_eff_batch(allc) if allc else []
            at = 0
            for pcid, pc, eff, ctxs, slots in prods:
                effs = alle[at:at + len(ctxs)]
                at += len(ctxs)
                # best hour: highest predicted kill, a small penalty per hour of waiting (as the original farm did)
                best = max(zip(slots, effs), key=lambda x: x[1] - 0.012 * x[0][0])
                now = effs[0] if slots[0][0] == 0 else None
                dusk_only = bool(flowering and pc.bee > R.BEE_TOXIC)
                safe = [(sl, e) for sl, e in zip(slots, effs)
                        if sl[4] < R.SPRAY_RAIN_MM_6H and sl[3] <= R.SPRAY_WIND_MS and (not dusk_only or R.bee_window(sl[2]))]
                best_safe = max(safe, key=lambda x: x[1] - 0.012 * x[0][0]) if safe else None
                opts.append(Obj(product=pcid, name=pc.name, kind=pc.kind, moa=pc.moa, phiDays=R.phi_days(pcid), beeToxicity=pc.bee, baseEfficacy=eff,
                                bestTime=tw.iso(best[0][1]), bestT=best[0][1], bestEfficacy=float(best[1]), nowEfficacy=None if now is None else float(now),
                                bestWind=float(best[0][3]), bestRain6=float(best[0][4]),
                                duskOnlyIfFlowering=dusk_only,
                                bestSafeTime=tw.iso(best_safe[0][1]) if best_safe else None, bestSafeT=best_safe[0][1] if best_safe else None,
                                bestSafeEfficacy=float(best_safe[1]) if best_safe else None,
                                slots=[(int(sl[1]), float(e), float(sl[3]), float(sl[4])) for sl, e in zip(slots, effs)]))
            opts.sort(key=lambda o: -(o.bestSafeEfficacy if o.bestSafeEfficacy is not None else o.bestEfficacy - 1))
            return opts[:max_products]
        return self._cached(self._key('spray', (f, pid, hours, max_products)), run)

    # ── time to AWD (v2: the time-to-stress forest's paddy job) ──
    AWD_NAMES = ['tube reading mm', 'AWD threshold mm', 'tube above the threshold mm', 'standing water (tube > 0)', 'the farm\'s water-balance estimate h',
                 'tube change last 6 h mm/h', 'tube change last 24 h mm/h', 'tube history known', 'rain gauge last 24 h mm', 'hours since irrigation',
                 'expected rain today mm', 'expected rain tomorrow mm', 'expected rain day 3 mm', 'forecast rain today mm', 'forecast rain tomorrow mm',
                 'forecast rain day 3 mm', 'rain chance today', 'rain chance tomorrow', 'rain chance day 3', 'forecast ET0 today mm',
                 'forecast ET0 tomorrow mm', 'forecast ET0 day 3 mm', 'hourly forecast rain next 24 h mm', 'hourly forecast rain 24-48 h mm',
                 'hourly forecast rain 48-72 h mm', 'crop progress', 'crop coefficient', 'live share', 'mean sand %', 'percolation estimate mm/day',
                 'puddled', 'hour of the check', 'season (sin)', 'season (cos)', 'estimate without rain h',
                 'estimate without 1.5 mm/h forecast hours h', 'forecast hours at 1.5 mm/h', 'other forecast rain hours',
                 # v2, from the farm's own records of the last two days: the tube's fall over the latest stretch of hours
                 # with no rain on the gauge, no pumping and no outlet open
                 'dry-stretch fall mm/h', 'dry-stretch hours', 'dry-stretch mean reading mm', 'dry-stretch known', 'dry-stretch estimate h',
                 'rain gauge last 6 h mm',
                 # v2: the twin's documented water balance from the farm's fitted level on 256 draws of the coming 73 hours
                 # from the posterior of the weather given the forecasts (sim/wxpost.py), and the level fit itself
                 'posterior: value most draws are within tolerance of h', 'posterior 10th percentile h', 'posterior median h',
                 'posterior 90th percentile h', 'posterior share past 72 h', 'posterior share within tolerance of its value',
                 'level estimate mm', 'hours in the level fit', 'level fit rms mm']

    @staticmethod
    def dry_stretch(f, slot, h_obs, tube_log, flags, max_h=48):
        """The tube's fall over the latest run of hours with no rain on the gauge (< 0.1 mm), no pumping and no outlet
        open: least-squares slope (mm/h), its length (hours) and its mean reading. (0, 0, 0) without at least 6 hours."""
        if not tube_log or not flags:
            return 0.0, 0, 0.0
        ts, hs = [0.0], [float(h_obs)]
        for k in range(1, max_h + 1):
            fl = flags.get((f, slot - k))
            r = tube_log.get((f, slot - k))
            if fl is None or r is None or fl[0] >= 0.1 or fl[1] or fl[2]:
                break
            ts.append(-float(k))
            hs.append(float(r))
        n = len(ts) - 1
        if n < 6:
            return 0.0, n, 0.0
        tm, hm = sum(ts) / len(ts), sum(hs) / len(hs)
        sxx = sum((x - tm) ** 2 for x in ts)
        sxy = sum((x - tm) * (y - hm) for x, y in zip(ts, hs))
        return sxy / sxx, n, hm

    def awd_context(self, f, thr, tube_log=None, flags=None, full=True):
        """What the farm knows when it asks how long field f's water takes to fall to thr: the tube and its recent
        readings, the forecasts as issued, the crop and the soil readings, and the farm's own water-balance estimate
        (its documented physics run on the forecast). Returns (features, estimate in hours). ``full=False`` (no
        time-to-AWD forest loaded and no rows kept): only the estimate, as (None, estimate) — the posterior and the
        level fit are then needed by nothing, and skipping them changes no number."""
        tw = self.tw
        W = tw.weather
        fd = tw.fields[f]
        cs = tw.cells[f]
        t = tw.t
        d, hr = tw.day, tw.hour
        h_obs = tw.water_tube(f)
        fc = [W.forecast_day(d, k) for k in range(3)]
        fh = [W.forecast_hour(t, t + q * HOUR) for q in range(72)]
        rain_h = [x.rain for x in fh]
        live = [int(i) for i in cs if tw.alive[i] and not tw.done[i]]
        prog = tw.field_progress(f)
        kc = A.kc(PADDY, min(1.0, prog)) if live else 0.0
        live_share = len(live) / len(cs)
        sand = float(np.mean([tw.soil_reading(i).sand for i in cs]))
        perc = (1.5 + 0.08 * sand) * (1.0 if fd.puddled else 2.5)
        log = tube_log or {}
        slot = int(round(t / HOUR))
        h6, h24 = log.get((f, slot - 6)), log.get((f, slot - 24))
        s6 = (h_obs - h6) / 6 if h6 is not None else 0.0
        s24 = (h_obs - h24) / 24 if h24 is not None else 0.0
        rain24 = W.rain_between(d, 0, hr) + (W.rain_between(d - 1, hr, 24) if d > 0 else 0.0)
        since_irr = min(240.0, (t - fd.lastIrrT) / HOUR) if fd.lastIrrT is not None else 240.0
        # the farm's estimate: its water balance on the forecast (hourly rain as forecast, the day's forecast ET0 spread
        # over the hours like the sun, crop coefficient at today's progress, percolation from the soil readings)
        # the surveyed square heights tell the farm which squares dry out first (their plants then use less water)
        el = [float(self.elev_obs[i]) for i in live]
        st_thr = 0.0 if SENSITIVE_LO <= prog < SENSITIVE_HI else float(CFG.AWD_THRESHOLD_MM)
        et0_h = self._et0_hours(t, fc)
        est = self._awd_estimate(h_obs, thr, et0_h, rain_h, kc, live_share, perc, el, st_thr)
        if not full:
            return None, est
        est_dry = self._awd_estimate(h_obs, thr, et0_h, (), kc, live_share, perc, el, st_thr)
        est_nofa = self._awd_estimate(h_obs, thr, et0_h, [0.0 if abs(r - 1.5) < 1e-9 else r for r in rain_h], kc, live_share, perc, el, st_thr)
        doy = tw.date_info().doy
        a = 2 * math.pi * doy / 365
        slope, n_dry, mean_dry = self.dry_stretch(f, slot, h_obs, log, flags)
        # v2: the level now from the tube's recent readings and the farm's water balance, then the time to AWD from
        # that level under the posterior of the coming weather; skipped when even a dry run with 25 % more
        # evapotranspiration than forecast cannot reach the threshold within the horizon (every draw would be past it)
        h_est, n_fit, rms_fit = self.level_estimate(f, slot, h_obs, log, flags)
        est_dry_hi = self._awd_estimate(h_est, thr, [e * 1.25 for e in et0_h], (), kc, live_share, perc, el, st_thr)
        mc = [73.0, 73.0, 73.0, 73.0, 1.0, 1.0] if est_dry_hi >= float(AWD_MC_HOURS) else self._awd_post(f, thr, h_est)
        known = n_dry >= 6
        est_obs = min(float(AWD_CAP), (h_obs - thr) / max(0.05, -slope)) if known else float(AWD_CAP)
        g6 = W.rain_between(d, max(0.0, hr - 6), hr) + (W.rain_between(d - 1, 24 + hr - 6, 24) if hr < 6 and d > 0 else 0.0)
        x = [h_obs, thr, h_obs - thr, 1.0 if h_obs > 0 else 0.0, est, s6, s24, 1.0 if h24 is not None else 0.0, rain24, since_irr,
             fc[0].rainMm * fc[0].rainProb, fc[1].rainMm * fc[1].rainProb, fc[2].rainMm * fc[2].rainProb, fc[0].rainMm, fc[1].rainMm, fc[2].rainMm,
             fc[0].rainProb, fc[1].rainProb, fc[2].rainProb, fc[0].et0, fc[1].et0, fc[2].et0, sum(rain_h[:24]), sum(rain_h[24:48]), sum(rain_h[48:72]),
             prog, kc, live_share, sand, perc, 1.0 if fd.puddled else 0.0, hr, math.sin(a), math.cos(a), est_dry, est_nofa,
             sum(1 for r in rain_h if abs(r - 1.5) < 1e-9), sum(1 for r in rain_h if r > 0 and abs(r - 1.5) >= 1e-9),
             slope if known else 0.0, float(n_dry), mean_dry if known else 0.0, 1.0 if known else 0.0, est_obs, g6] + mc + [h_est, float(n_fit), rms_fit]
        return x, est

    # ── v2: the farm's estimate of a field's water level now, and its time to AWD under the forecasts' uncertainty ──
    def perc_estimate(self, f):
        """Percolation through the plough pan (mm/day) from the soil readings' sand, as the twin's soil sets it."""
        tw = self.tw
        sand = float(np.mean([tw.soil_reading(i).sand for i in tw.cells[f]]))
        return (1.5 + 0.08 * sand) * (1.0 if tw.fields[f].puddled else 2.5)

    def level_estimate(self, f, slot, h_obs, tube_log, flags, max_h=48):
        """The farm's estimate of field f's water level now: its own water balance run over the last hours (rain from
        its gauge, ET0 from its station, pumped and drained water from its meters, percolation from the soil readings,
        crop water use from the crop's stage and the surveyed square heights) from the starting level that best fits
        the tube readings of those hours (weighted least squares, recent hours weighted more; readings at the tube's
        limits left out). Returns (estimate, hours used, rms of the fit's residuals)."""
        tw = self.tw
        if not tube_log or not flags:
            return float(h_obs), 0, 0.0
        L = 0
        for k in range(1, max_h + 1):
            fl = flags.get((f, slot - k))
            if (f, slot - k) not in tube_log or fl is None or len(fl) < 6:
                break
            L = k
        if L < 3:
            return float(h_obs), L, 0.0
        reads = np.array([tube_log[(f, slot - k)] for k in range(L, 0, -1)] + [float(h_obs)])
        ins = [flags[(f, slot - k)] for k in range(L, 0, -1)]
        cs = tw.cells[f]
        live = cs[tw.alive[cs] & ~tw.done[cs]]
        n, n_live = len(cs), len(live)
        if n_live >= 4:
            kc = float(np.mean(tw._kc_vec(np.minimum(1.0, tw.frac[live]))))
        elif n_live:
            kc = float(np.mean([A.kc(PADDY, float(x)) for x in np.minimum(1.0, tw.frac[live])]))
        else:
            kc = 0.0
        el = self.elev_obs[live]
        fl1 = np.minimum(1.0, tw.frac[live])
        stt = np.where((fl1 >= SENSITIVE_LO) & (fl1 < SENSITIVE_HI), 0.0, float(CFG.AWD_THRESHOLD_MM))
        mature = tw.frac[live] >= 0.9
        safe = float(np.max(el + stt)) if n_live else -1e9      # above this level no square is short of water
        perc_day = self.perc_estimate(f)
        SY, seep_k = CFG.SPECIFIC_YIELD, CFG.BUND_SEEP_PER_DAY / 24

        PH, PS, BUND = CFG.PERC_HEAD_MM, CFG.PERC_STOP_MM, float(CFG.BUND_MM)
        ins = [(float(g), float(irr), float(drn), float(e0)) for g, _, _, irr, drn, e0 in ins]
        a_live, a_bare = n_live * kc / n, (n - n_live) / n

        def ks_at(h):
            if not n_live or h >= safe:
                return 1.0
            dep = h - el
            kk = np.where(dep >= stt, 1.0, np.clip(1 - (stt - dep) / 250.0, 0, 1))
            return float(np.where(mature, 1.0, kk).mean())

        def run(h):
            """The farm's water balance from level h at the window's start, hour by hour (scalar arithmetic)."""
            traj = [h]
            for g, irr, drn, e0 in ins:
                ks = ks_at(h)
                if h > 0:
                    et = (a_live * ks + a_bare * 1.05) * e0
                    perc = perc_day * (1 + h / PH) / 24 + seep_k * h
                else:
                    wet = min(1.0, max(0.0, 1 + h / 300.0))
                    et = (a_live * ks + a_bare * 0.75 * wet ** 1.5) * e0
                    perc = perc_day / 24 * min(1.0, max(0.0, 1 + h / PS))
                net = g + irr - et - perc - drn
                if h >= 0:
                    hn = h + net
                    if hn < 0:
                        hn = hn / SY
                else:
                    refill = -h * SY
                    hn = net - refill if net > refill else h + net / SY
                h = min(max(hn, -800.0), BUND)
                traj.append(h)
            return np.array(traj)

        use = (reads > CFG.TUBE_MIN_MM + 0.01) & (reads < CFG.BUND_MM - 0.01)
        if use.sum() < 3:
            return float(h_obs), L, 0.0
        w = np.exp(-(L - np.arange(L + 1)) / 24.0) * use
        # weighted least squares for the starting level: Gauss-Newton from the first reading (the balance is nearly
        # affine in its starting level; three steps settle it to well under 0.01 mm)
        h0 = float(reads[0])
        tr = run(h0)
        for _ in range(3):
            J = run(h0 + 1.0) - tr
            den = float(np.sum(w * J * J))
            if den <= 1e-12:
                break
            step = float(np.sum(w * J * (reads - tr))) / den
            step = max(-30.0, min(30.0, step))
            h0 += step
            tr = run(h0)
            if abs(step) < 0.005:
                break
        res = (tr - reads)[use]
        return float(tr[-1]), L, float(np.sqrt(np.mean(res ** 2)))

    def farm_level_state(self, f, h_est):
        """The state the projection needs, as the farm knows it: its level estimate, the live squares (cameras), the
        crop's thermal time (station temperatures since each square was planted), the surveyed square heights, the
        percolation from the soil readings. Seedlings that will still fail are not known."""
        tw = self.tw
        cs = tw.cells[f]
        alive = tw.alive[cs] & ~tw.done[cs]
        frac, gdd = tw.frac[cs].copy(), tw.gdd[cs].copy()
        el = self.elev_obs[cs].copy()
        fl = np.minimum(1.0, frac)
        stt = np.where((fl >= SENSITIVE_LO) & (fl < SENSITIVE_HI), 0.0, float(CFG.AWD_THRESHOLD_MM))
        dep = h_est - el
        ks = np.where(dep >= stt, 1.0, np.clip(1 - (stt - dep) / 250.0, 0, 1))
        ks = np.where(frac >= 0.9, 1.0, ks)
        return {'t': tw.t, 'h': float(h_est), 'alive': alive, 'frac': frac, 'gdd': gdd, 'ks': ks, 'failed': np.zeros(len(cs), bool),
                'failT': np.full(len(cs), np.inf), 'elev': el, 'perc_day': self.perc_estimate(f)}

    def _awd_post(self, f, thr, h_est, D=AWD_POST_DRAWS):
        """The time to AWD on D draws of the next 73 hours' weather from the posterior of the generator's days given
        the forecasts (sim/wxpost.py), from the farm's own state estimate, with the twin's documented physics.
        Returns [the value within tolerance of most draws that would be scored (under 72 h; 73 if none), 10th, 50th and
        90th percentile, share of draws past 72 h, share of the scored draws within tolerance of that value]. Its own
        seeded random numbers: nothing else is touched."""
        tw = self.tw
        Th, Rh, Eh = self.weather_draws(D)
        v = self.project_fast(self.farm_level_state(f, h_est), thr, Th, Rh, Eh, hours=73)
        sc = np.sort(v[v < 72])
        from ..core.rf import cover_point
        cp = cover_point(sc, lambda u: max(6.0, 0.2 * u), None) if len(sc) else 73.0
        q10, q50, q90 = np.quantile(v, [0.1, 0.5, 0.9])
        cp = float(cp if cp is not None else (sc[len(sc) // 2] if len(sc) else 73.0))
        conf = float(np.mean(np.abs(sc - cp) <= np.maximum(6.0, 0.2 * sc) + 1e-9)) if len(sc) else 1.0
        return [cp, float(q10), float(q50), float(q90), float(np.mean(v >= 72)), conf]

    def weather_draws(self, D=AWD_POST_DRAWS, hours=73):
        """D draws of the next ``hours`` hours' weather from the posterior given what the farm knows now (shared by both
        fields within the hour: it is the same weather). Its own seeded random numbers."""
        tw = self.tw
        key = (tw.t, tw.weather.version, D, hours)
        c = getattr(self, '_wx_draws', None)
        if c is not None and c[0] == key:
            return c[1]
        obs = WP.Obs.from_weather(tw.weather, tw.t, farm=True)
        rng = np.random.default_rng(hash_n(tw.seed, 'awd-post', int(tw.t)))
        smp = WP.Sampler(obs, hours=hours, K=AWD_POST_K, K_seen=AWD_POST_K_SEEN, M=AWD_POST_M, rng=rng)
        out = smp.draw(D)
        self._wx_draws = (key, out)
        return out

    def project_fast(self, st, thr, Th, Rh, Eh, hours=73):
        """The farm's projection of a field's level for many weathers (the twin's documented hourly balance, as
        ``Twin.project_level_many``), made fast for the farm: every live square's crop grows by the same thermal time,
        so the field's mean crop coefficient is a function of that common growth (tabulated), and the mean stress factor
        is a function of the level (tabulated over the surveyed heights); the stress thresholds and the mature squares
        are those at the start of the 73 hours. Returns the crossing hour of each draw (``hours`` if none)."""
        tw = self.tw
        Dn = Th.shape[0]
        if st['h'] <= thr:
            return np.zeros(Dn)
        alive = st['alive']
        n = len(alive)
        live = np.nonzero(alive)[0]
        n_live = len(live)
        SY = CFG.SPECIFIC_YIELD
        t_top, tb = PADDY.tOpt[1] + 3, PADDY.tBase
        if n_live:
            f0 = st['frac'][live]
            d_max = hours * max(0.0, t_top - tb) / 24.0 / TOT_GDD
            dgrid = np.linspace(0.0, d_max + 1e-9, 257)
            fl = np.minimum(1.0, f0[None, :] + dgrid[:, None])
            kc_tab = tw._kc_vec(fl.ravel()).reshape(fl.shape).mean(axis=1)
            el = st['elev'][live]
            fl0 = np.minimum(1.0, f0)
            stt = np.where((fl0 >= SENSITIVE_LO) & (fl0 < SENSITIVE_HI), 0.0, float(CFG.AWD_THRESHOLD_MM))
            mature = f0 >= 0.9
            hgrid = np.arange(-560.0, float(CFG.BUND_MM) + 0.5, 0.5)
            dep = hgrid[:, None] - el[None, :]
            kk = np.where(dep >= stt[None, :], 1.0, np.clip(1 - (stt[None, :] - dep) / 250.0, 0, 1))
            ks_tab = np.where(mature[None, :], 1.0, kk).mean(axis=1)
            kss = np.full(Dn, float(np.mean(st['ks'][live])))
        a_live, a_bare = n_live / n, (n - n_live) / n
        h = np.full(Dn, float(st['h']))
        out = np.full(Dn, float(hours))
        done = np.zeros(Dn, bool)
        delta = np.zeros(Dn)
        perc_day = st['perc_day']
        for k in range(int(hours)):
            kc = np.interp(delta, dgrid, kc_tab) if n_live else 0.0
            wet = np.clip(1 + h / 300.0, 0, 1)
            kc_bare = np.where(h > 0, 1.05, 0.75 * wet ** 1.5)
            et = (a_live * kc * (kss if n_live else 1.0) + a_bare * kc_bare) * Eh[:, k]
            perc = np.where(h > 0, perc_day * (1 + h / CFG.PERC_HEAD_MM) / 24 + CFG.BUND_SEEP_PER_DAY * h / 24,
                            perc_day / 24 * np.clip(1 + h / CFG.PERC_STOP_MM, 0.0, 1))
            net = Rh[:, k] - et - perc
            up = h + net
            up = np.where(up < 0, up / SY, up)
            refill = -h * SY
            down = np.where(net > refill, net - refill, h + net / SY)
            hn = np.clip(np.where(h >= 0, up, down), -800.0, float(CFG.BUND_MM))
            cross = (~done) & (hn <= thr)
            if cross.any():
                gap = h - hn
                out[cross] = k + np.where(gap > 0, (h - thr) / np.where(gap > 0, gap, 1.0), 0.0)[cross]
                done |= cross
                if done.all():
                    break
            h = np.where(done, h, hn)
            if n_live:
                kss = np.interp(h, hgrid, ks_tab)
                delta = delta + np.maximum(0.0, np.minimum(Th[:, k], t_top) - tb) / 24.0 / TOT_GDD
        return out

    def _et0_hours(self, t, fc, cap=AWD_CAP):
        """The forecast's daily ET0 spread over the hours like the sun (the farm's reading of the next four days)."""
        out = []
        d0 = int(t // DAY)
        doy0 = self.tw.date_info(t).doy
        for k in range(cap):
            tt = t + k * HOUR
            dd = int(tt // DAY)
            out.append(fc[min(2, dd - d0)].et0 * C.et_weights((doy0 + dd - d0 - 1) % 365 + 1)[int((tt % DAY) // HOUR) % 24])
        return out

    def _awd_estimate(self, h, thr, et0_h, rain_h, kc, live_share, perc, elev=(), st_thr=-150.0):
        """The farm's water balance on the forecast: hours until the level reaches thr (capped)."""
        if h <= thr:
            return 0.0
        el = np.asarray(elev, float)
        top = float(el.max()) if len(el) else -1e9
        ks = 1.0
        nr = len(rain_h)
        seep_k = CFG.BUND_SEEP_PER_DAY / 24
        for k, et0 in enumerate(et0_h):
            if h > 0:
                et = (live_share * kc * ks + (1 - live_share) * 1.05) * et0
                loss = perc * (1 + h / CFG.PERC_HEAD_MM) / 24 + seep_k * h
            else:
                wet = clamp(1 + h / 300.0, 0, 1)
                et = (live_share * kc * ks + (1 - live_share) * 0.75 * wet ** 1.5) * et0
                loss = perc / 24 * clamp(1 + h / CFG.PERC_STOP_MM, 0.0, 1)
            net = (rain_h[k] if k < nr else 0.0) - et - loss
            if h >= 0:
                hn = h + net
                if hn < 0:
                    hn = hn / CFG.SPECIFIC_YIELD
            else:
                refill = -h * CFG.SPECIFIC_YIELD
                hn = net - refill if net > refill else h + net / CFG.SPECIFIC_YIELD
            hn = min(hn, float(CFG.BUND_MM))
            if hn <= thr:
                return k + (h - thr) / (h - hn) if h > hn else float(k)
            h = hn
            if h - top < st_thr:          # some squares are now drier than the stress threshold: their plants use less
                dep = h - el
                ks = float(np.mean(np.where(dep >= st_thr, 1.0, np.clip(1 - (st_thr - dep) / 250.0, 0, 1))))
            else:
                ks = 1.0
        return float(len(et0_h))

    def hours_to_awd(self, f, thr, tube_log=None, flags=None):
        """The prediction the farm uses (and the audit scores): the paddy time-to-AWD forest when one is loaded, else
        the farm's water-balance estimate. Cached for the hour (the state and the audit both ask)."""
        def run():
            m = self.m.m.get('awd') if hasattr(self.m, 'm') else None
            x, est = self.awd_context(f, thr, tube_log, flags, full=m is not None or bool(getattr(self, 'keep_x', False)))
            if m is None:
                return est, x
            return float(anchored_predict(m, np.array([x], float))[0]), x
        return self._cached(self._key('awd', (f, thr)), run)

    # ── the planned spray (v2 target): per square, when the spray is decided ──
    def spray_weather(self, t_dec, tsq, draws=PLAN_POST_DRAWS):
        """The weather at the minutes tsq (seconds) as the farm can know it at t_dec: the wind from the day's wind (today:
        the station's reading now; later days: what all the hourly wind forecasts for that day agree on, each being
        the true wind ± 20 %) times the hour's usual share of it; the temperature from the days' highs and lows fitted to
        the station's readings and to every hourly temperature forecast (weighted by their error); the sun from the
        minute; the rain of the six hours after each minute on draws from the posterior of the coming weather
        (sim/wxpost.py). Returns (wind, temp, sun) per minute and rain6 as (draws × minutes)."""
        tw = self.tw
        W = tw.weather
        d0 = int(t_dec // DAY)
        h_now = (t_dec % DAY) / HOUR
        tsq = np.asarray(tsq, float)
        t_last = float(tsq.max()) if len(tsq) else t_dec
        nq = int(math.ceil((t_last - t_dec) / HOUR)) + 8
        fh = [W.forecast_hour(t_dec, t_dec + q * HOUR) for q in range(min(nq + 30, 76))]
        share = lambda hh: 0.45 + 0.9 * math.exp(-(((hh - 14.5) / 4.5) ** 2))     # noqa: E731  (Weather.wind's daily shape)
        # the day's wind
        dwind = {d0: W.wind(d0, h_now) / share(h_now)}
        lohi = {}
        for q, x in enumerate(fh):
            tq = t_dec + q * HOUR
            d, hh = int(tq // DAY), (tq % DAY) / HOUR
            if d == d0:
                continue
            s = share(hh)
            lo, hi = lohi.get(d, (0.0, 1e9))
            lohi[d] = (max(lo, x.wind / (1.2 * s)), min(hi, x.wind / (0.8 * s)))
        bounds = {d0: (dwind[d0], dwind[d0])}
        for d, (lo, hi) in lohi.items():
            dwind[d] = 0.5 * (lo + hi)
            bounds[d] = (min(lo, hi), max(lo, hi))
        # the days' highs and lows: yesterday's and today's past ones from the station, the rest fitted
        days = list(range(d0 - 1, int((t_last + 7 * HOUR) // DAY) + 2))
        known = {}
        yd = W.day(max(0, d0 - 1))
        known[('x', d0 - 1)], known[('n', d0 - 1)] = yd.tmax, yd.tmin
        td = W.day(d0)
        if h_now >= 6:
            known[('n', d0)] = td.tmin
        if h_now >= 14.5:
            known[('x', d0)] = td.tmax
        unk = [(k, d) for d in days for k in ('x', 'n') if (k, d) not in known]
        col = {u: j for j, u in enumerate(unk)}
        A_, b_, w_ = [], [], []

        def coef(d, hh):
            """T(d, hh) = Σ c·(high or low of a day): the generator's daily curve."""
            if 6 <= hh <= 14.5:
                c = (1 - math.cos(math.pi * (hh - 6) / 8.5)) / 2
                return [(('n', d), 1 - c), (('x', d), c)]
            if hh > 14.5:
                c = (1 + math.cos(math.pi * (hh - 14.5) / 15.5)) / 2
                return [(('n', d + 1), 1 - c), (('x', d), c)]
            c = (1 + math.cos(math.pi * (hh + 9.5) / 15.5)) / 2
            return [(('n', d), 1 - c), (('x', d - 1), c)]
        for q, x in enumerate(fh):
            tq = t_dec + q * HOUR
            d, hh = int(tq // DAY), (tq % DAY) / HOUR
            row, rhs = np.zeros(len(unk)), x.temp
            ok = True
            for key, c in coef(d, hh):
                if key in known:
                    rhs -= c * known[key]
                elif key in col:
                    row[col[key]] += c
                else:
                    ok = False
            if ok and row.any():
                A_.append(row)
                b_.append(rhs)
                w_.append(1.0 / (0.3 + 0.04 * q))
        for k in range(min(5, len(days))):           # the daily forecast's highs and lows, weakly
            fdk = W.forecast_day(d0, k)
            for key, val, sd in ((('x', d0 + k), fdk.tmax, 0.5 + 0.35 * k), (('n', d0 + k), fdk.tmin, 0.8 * (0.5 + 0.35 * k))):
                if key in col:
                    row = np.zeros(len(unk))
                    row[col[key]] = 1.0
                    A_.append(row)
                    b_.append(val)
                    w_.append(1.0 / sd)
        vals = dict(known)
        if unk and A_:
            Aw = np.array(A_) * np.array(w_)[:, None]
            bw = np.array(b_) * np.array(w_)
            sol = np.linalg.lstsq(Aw, bw, rcond=None)[0]
            for u, j in col.items():
                vals[u] = float(sol[j])
        for u in unk:
            if u not in vals:
                vals[u] = W.forecast_day(d0, min(4, max(0, u[1] - d0))).tmax if u[0] == 'x' else W.forecast_day(d0, min(4, max(0, u[1] - d0))).tmin
        wind, temp, sun, wlo, whi = [], [], [], [], []
        for ti in tsq:
            d, hh = int(ti // DAY), (ti % DAY) / HOUR
            wd = dwind.get(d)
            if wd is None:
                wd = W.forecast_hour(t_dec, ti).wind / share(int(hh))
                bounds[d] = (wd / 1.2, wd / 0.8)
            wind.append(wd * share(hh))
            wlo.append(bounds[d][0] * share(hh))
            whi.append(bounds[d][1] * share(hh))
            temp.append(sum(c * vals.get(key, 0.0) for key, c in coef(d, hh)))
            sun.append(max(0.0, math.sin(C.sun_elevation(tw.date_info(ti).doy, hh))))
        # rain in the six hours after each minute, on posterior draws of the coming hours
        obs = WP.Obs.from_weather(W, t_dec, farm=True)
        rng = np.random.default_rng(hash_n(tw.seed, 'plan-post', int(t_dec)))
        smp = WP.Sampler(obs, hours=nq, K=AWD_POST_K, K_seen=AWD_POST_K_SEEN, M=AWD_POST_M, rng=rng)
        _, Rh, _ = smp.draw(draws)
        cum = np.concatenate([np.zeros((draws, 1)), np.cumsum(Rh, axis=1)], axis=1)
        last = cum.shape[1] - 1

        def cum_at(x):
            """Rain since t_dec up to x hours later, for every draw (linear within the hour)."""
            x = min(max(0.0, x), float(last))
            i = min(int(x), last - 1) if last > 0 else 0
            fr = x - i
            return cum[:, i] + fr * (cum[:, min(i + 1, last)] - cum[:, i])
        rain6 = np.zeros((draws, len(tsq)))
        for k, ti in enumerate(tsq):
            a = (ti - t_dec) / HOUR
            rain6[:, k] = np.maximum(0.0, cum_at(a + 6) - cum_at(a))
        self.last_wind_bounds = (np.array(wlo), np.array(whi))
        return np.array(wind), np.array(temp), np.array(sun), rain6

    @staticmethod
    def _kill_vec(base, wind, temp, rain6, uv, sun, cat, age):
        """agro.spray_efficacy for many draws at once (the same formula)."""
        wind_f = np.where(wind <= 2.5, 1.0, np.clip(1 - 0.12 * (wind - 2.5), 0.35, 1))
        rain_f = 1 - 0.65 * (1 - np.exp(-rain6 / 4))
        temp_f = np.where(temp > 32, np.clip(1 - 0.04 * (temp - 32), 0.6, 1), np.where(temp < 15, 0.85, 1.0))
        uv_f = 1 - 0.35 * uv * max(0.0, sun)
        old = 0.01 if cat in ('sucking', 'mite') else 0.025
        age_f = 1 - old * min(age, 14)
        return np.clip(base * wind_f * rain_f * temp_f * uv_f * age_f, 0, 1)

    def planned_spray(self, f, product, squares, exec_t, target_pest, v1_value=None, times=None):
        """The kill the farm expects on each square when the arm sprays it, from what it knows at the decision: the
        product, the pest the cameras see on the square (else the target pest) with its tracked age carried forward,
        and (v2) the minute the arm's own plan reaches the square (``times``: square → planned time; else exec_t) with
        the weather then as the station and all the forecasts issued now allow (``spray_weather``): the kill on posterior
        draws of the rain, and the value within ±5 points of most of them. Returns (predictions, feature rows). Without
        the paddy planned-spray forest the prediction is v1's: one number for every square."""
        tw = self.tw
        W = tw.weather
        pc = D.PESTICIDES[product]
        t_dec = tw.t
        h = (exec_t % DAY) / HOUR
        fh = W.forecast_hour(t_dec, exec_t)
        rain_h = [W.forecast_hour(t_dec, exec_t + q * HOUR).rain for q in range(6)]
        sun = max(0.0, math.sin(C.sun_elevation(tw.date_info(exec_t).doy, h)))
        lead = (exec_t - t_dec) / HOUR
        tsq = [float((times or {}).get(int(i), exec_t)) for i in squares]
        wind_m, temp_m, sun_m, rain6 = self.spray_weather(t_dec, tsq)
        from ..core.rf import cover_point
        ctxs, xs = [], []
        for k, i in enumerate(squares):
            seen = int(tw.oPestId[i])
            p = PEST_KEYS[seen] if seen >= 0 else target_pest
            age = tw.obs_age(i) + lead / 24 if seen >= 0 else 0.0
            ctx = Obj(hour=h, wind=fh.wind, temp=fh.temp, rh=fh.rh, rain6=sum(rain_h), rainH=rain_h, uv=pc.uv,
                      baseEff=D.pesticide_efficacy(product, p) if p else 0.5, cat=D.PESTS[p].cat if p else 'larva', ageDays=age, sunSin=sun)
            ctxs.append(ctx)
            # v2: at the arm's minute, with the weather as the forecasts allow
            lead_i = (tsq[k] - t_dec) / HOUR
            age_i = tw.obs_age(i) + lead_i / 24 if seen >= 0 else 0.0
            kills = self._kill_vec(ctx.baseEff, wind_m[k], temp_m[k], rain6[:, k], pc.uv, sun_m[k], ctx.cat, age_i)
            srt = np.sort(kills)
            cp = cover_point(srt, lambda v: 0.05, None)
            cp = float(srt[len(srt) // 2]) if cp is None else float(cp)
            conf = float(np.mean(np.abs(kills - cp) <= 0.05 + 1e-12))
            det = float(self._kill_vec(ctx.baseEff, wind_m[k], temp_m[k], np.zeros(1), pc.uv, sun_m[k], ctx.cat, age_i)[0])
            since_det = min(30.0, (t_dec - tw.oDetectT[i]) / DAY) if tw.oDetectT[i] > -1e11 else 30.0
            since_spray = min(30.0, (t_dec - tw.lastSprayT[i]) / DAY) if tw.lastSprayT[i] > -1e11 else 30.0
            xs.append(M._feat_spray_full(ctx) + [lead, 1.0 if seen >= 0 else 0.0, float(int(tw.oIdVote[i]) if seen >= 0 else 0)]
                      + [cp, det, float(np.mean(kills)), float(wind_m[k]), float(temp_m[k]), float(sun_m[k]), float(np.mean(rain6[:, k])),
                         float(np.mean(rain6[:, k] > 0.05)), lead_i, age_i, float(tw.oPest[i]) if seen >= 0 else 0.0, since_det, since_spray,
                         float(tw.sprayCount[i]), conf, float(self.last_wind_bounds[0][k]), float(self.last_wind_bounds[1][k])])
        # the decision's own forecast, kept with the rows for the planned-spray ceiling: when it was decided and for when,
        # the hourly rain forecast for the six hours after the planned hour, and that hour's forecast temperature and wind
        self.last_plan_meta = [t_dec, float(exec_t), lead] + [float(r) for r in rain_h] + [fh.temp, fh.wind, float(list(D.PESTICIDES).index(product))]
        m = self.m.m.get('plan') if hasattr(self.m, 'm') else None
        if m is not None:
            preds = np.clip(anchored_predict(m, np.array(xs, float)), 0, 1).tolist()
        elif v1_value is not None:
            preds = [float(v1_value)] * len(squares)
        else:
            preds = [None] * len(squares)
        return preds, xs

    # ── harvest ──
    def harvest(self, f):
        def run():
            tw = self.tw
            d = tw.day
            W = tw.weather
            rain2 = W.rain_between(d, 0, tw.hour) + W.day(max(0, d - 1)).rain
            rh_now = W.rh(d, tw.hour)
            cells = [int(i) for i in tw.cells[f] if tw.alive[i] and not tw.done[i]]
            cand = [i for i in cells if tw.frac[i] >= 0.96]
            if not cells:
                return Obj(readyShare=0.0, meanScore=0.0, n=0, ctxs=[])
            ctxs = []
            for i in cand:
                frac = float(tw.frac[i])
                ctxs.append(Obj(frac=frac, daysPastMat=(frac - 1) * PADDY.durationDays, multi=False, pickRatio=0, rain2=rain2, rh=rh_now,
                                health=float(tw.oHealth[i]), fruitLoad=1 if frac >= 0.8 else 0, start=1))
            ps = []
            if ctxs:
                X = np.array([M._feat_harvest_full(c) for c in ctxs])
                hp = self.m.m.get('harvestP') if hasattr(self.m, 'm') else None
                if hp is not None:
                    # v2 paddy harvest forest: the v1 features and the readiness rule on what the farm knows
                    ps = hp.predict_proba_batch(np.c_[X, PF.harvest_rule(X)])[:, 1].tolist()
                else:
                    ps = self.m.m['harvest'].predict_proba_batch(X)[:, 1].tolist()
            ready = sum(1 for p in ps if p >= HARVEST_AT)
            return Obj(readyShare=ready / len(cells), meanScore=float(np.mean(ps)) if ps else 0.0, n=len(cells),
                       ctxs=list(zip(cand, ctxs, ps)))
        return self._cached(self._key('harvest', (f,)), run)
