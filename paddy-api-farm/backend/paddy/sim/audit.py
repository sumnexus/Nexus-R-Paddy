"""Checks of the forests' advice against what really happened, with the original project's fixed definitions
(test/acc/DEFINITIONS.md, copied to docs/DEFINITIONS.md):

  soil        the class called from the probe reading matches the rules applied to the square's true soil;
  establish   at every planting check, each square's predicted establishment chance is within ±5 points of the
              true chance (true soil water, the weather that actually came over the next three days); and the
              plant/wait call (field mean ≥ 72 %) matches the call the true chances give;
  pest        whether a plant's own infestation reaches 8 % within 7 days if nothing is sprayed, run forward with the
              weather that actually came (accuracy and balanced accuracy);
  yield       the forecast made at transplanting is within ±10 points of the season's actual share of attainable;
  spray       the predicted kill is within ±5 points of what the spray actually did;
  harvest     the ready / not-ready call matches the ready rule at the assessment;
  awd         (v2) the hours until a field's water falls to its AWD threshold if nothing is irrigated, within ±6 h or
              ±20 % of the truth (the twin's own water balance with the weather that actually came), for truths < 72 h;
  sprayPlan   (v2 target) the kill predicted for each square when the spray was decided, within ±5 points.

With ``rows=True`` every check also keeps its feature row and truth (the training and test data of the v2 model round).
"""
from __future__ import annotations

import math

import numpy as np

from ..core import agro as A
from ..core import data as D
from ..core.util import Obj, hash01
from .twin import DAY, HOUR, LN2, PADDY, PC_KEYS, PEST_KEYS, TOT_GDD

DEF = Obj(yieldTol=0.10, estTol=0.05, estDecision=0.72, sprayTol=0.05, pestDays=7, pestThreshold=A.ETL, awdHours=72, awdCap=73,
          awdTolH=6.0, awdTolRel=0.20)
KEYS = ('soil', 'establish', 'establishDecision', 'pest', 'pestPos', 'pestNeg', 'yield', 'spray', 'sprayPlan', 'harvest', 'awd')
ROW_KEYS = ('soil', 'pest', 'yield', 'establish', 'awd', 'spray', 'plan', 'harvest')


def awd_hit(pred, truth):
    return abs(pred - truth) <= max(DEF.awdTolH, DEF.awdTolRel * truth) + 1e-9


def pest_outcome(W, t0, st, days):
    """The pest dynamics run forward hour by hour with no new sprays; returns the highest severity reached."""
    ps = D.PESTS[st['pest']]
    sev = st['sev']
    res = st['residue']
    half = D.PESTICIDES[st['pesticide']].halfLife if st.get('pesticide') else 3
    eff_on = D.pesticide_efficacy(st['pesticide'], st['pest']) if st.get('pesticide') else 0
    gdd = st['gdd']
    top = sev
    w0, w1 = ps.window
    for k in range(int(days * 24)):
        t = t0 + k * HOUR
        d, h = int(t // DAY), (t % DAY) / HOUR
        T, rh = W.temp(d, h + 0.5), W.rh(d, h + 0.5)
        rain = W.rain_between(d, h, h + 1)
        gdd += max(0.0, min(T, PADDY.tOpt[1] + 3) - PADDY.tBase) / 24
        frac = gdd / TOT_GDD
        if res > 0:
            res *= math.exp(-LN2 / 24 / half)
            if rain > 1 and t - st['lastSprayT'] < 6 * HOUR:
                res *= math.exp(-0.05 * rain)
            if res < 0.02:
                res = 0.0
        tf = math.exp(-(((T - ps.tOpt) / ps.tWidth) ** 2))
        if ps.rh == 'humid':
            rf = 0.25 + 0.75 * min(1.0, max(0.0, (rh - 55) / 35))
        elif ps.rh == 'dry':
            rf = 0.25 + 0.75 * min(1.0, max(0.0, (85 - rh) / 40))
        else:
            rf = 0.7
        g = ps.r * (0.25 + 0.75 * tf) * (0.5 + 0.5 * rf) * (1.0 if w0 <= frac <= w1 else 0.5) - 0.02 - 1.2 * res * eff_on
        e = math.exp(g / 24)
        sev = sev * e / (1 + sev * (e - 1))
        if rain > 15 and ps.cat in ('sucking', 'mite'):
            sev *= math.exp(-0.15)
        top = max(top, sev)
        if sev < 0.003:
            break
    return top


def wilson(hit, n):
    if n <= 0:
        return [0.0, 1.0]
    z = 1.96
    p = hit / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


class Audit:
    def __init__(self, twin, advisor, rows=False):
        self.tw = twin
        self.adv = advisor
        self.t = {k: [0, 0] for k in KEYS}
        self.pending = []             # pest checks waiting for their 7 days
        self.awd_pending = []         # time-to-AWD checks waiting for their 72 hours
        self.yield_pred = {}          # field → forecast made at transplanting
        self.yield_x = {}             # field → its feature row (with rows=True)
        self.samples = {k: [] for k in ('establish', 'spray', 'yield')}
        self.rows = {k: [] for k in ROW_KEYS} if rows else None

    def _row(self, key, x, y, extra=()):
        if self.rows is not None:
            self.rows[key].append((list(map(float, x)), float(y), list(map(float, extra))))

    def _bump(self, key, hit):
        x = self.t[key]
        x[0] += 1 if hit else 0
        x[1] += 1

    # soil: every probed square
    def on_soil_check(self, f, squares):
        tw = self.tw
        adv = {s.i: s for s in self.adv.soil(f)}
        for i in squares:
            s = adv.get(int(i))
            if s is None:
                continue
            truth = A.suitability_label(tw.true_soil(i), PADDY)
            pred = {'U': 0, 'A': 1, 'S': 2}[s.code]
            self._bump('soil', pred == truth)
            if self.rows is not None:
                rd = tw.soil_reading(i)
                self._row('soil', [rd[k] for k in ('ph', 'n', 'p', 'k', 'oc', 'sand', 'clay', 'ec')], truth, [pred, f] + list(s.proba))

    # planting checks: every decision point before transplanting a puddled field, and the transplant itself
    def on_establish_check(self, f, squares=None):
        tw = self.tw
        adv = self.adv.establishment(f, squares)
        cells = [a.i for a in adv]
        truth = tw.true_establish_chance(f, cells)
        for a, tr in zip(adv, truth):
            self._bump('establish', abs(a.chance - tr) <= DEF.estTol + 1e-12)
            if self.rows is not None and a.get('x') is not None:
                self._row('establish', a.x, tr, [a.chance, a.forest, a.adj, f])
        if adv:
            pm = sum(a.chance for a in adv) / len(adv)
            tm = sum(truth) / len(truth)
            self._bump('establishDecision', (pm >= DEF.estDecision) == (tm >= DEF.estDecision))
            self.samples['establish'].append({'t': tw.t, 'field': tw.fields[f].name, 'pred': pm, 'true': tm})

    def on_transplant(self, f, yield_forecast, x=None, extra=None):
        self.yield_pred[f] = yield_forecast
        if x is not None:
            self.yield_x[f] = (x, extra or [])

    # pest risk: once a day at 08:00, every live plant in a field where the cameras see a pest, for each paddy pest
    # seen there, if the plant's own seen severity of it is below 8 %; scored once the week has passed. Plants the
    # farm doesn't ask the forest about (no sighting on them or within two squares) count as a prediction of 0.
    def pest_check(self):
        tw = self.tw
        for f in range(2):
            cs = tw.cells[f]
            seen = sorted({int(tw.oPestId[i]) for i in cs if tw.oPestId[i] >= 0 and tw.oPest[i] > 0 and tw.has[i] and not tw.done[i]})
            seen = [pk for pk in seen if PEST_KEYS[pk] in PADDY.pests]
            if not seen:
                continue
            risks, _ = self.adv.pest_risk(f)
            rmap = {(r.i, r.pest): r.risk for r in risks}
            cmap = {(r.i, r.pest): r.ctx for r in risks} if self.rows is not None else {}
            cases = []
            for pk in seen:
                pid = PEST_KEYS[pk]
                for i in cs:
                    i = int(i)
                    if not (tw.has[i] and tw.alive[i] and not tw.done[i]):
                        continue
                    own = float(tw.oPest[i]) if tw.oPestId[i] == pk else 0.0
                    if own >= A.ETL:
                        continue
                    st = None
                    if tw.pestId[i] == pk:
                        pc = int(tw.residuePc[i])
                        st = dict(sev=float(tw.pest[i]), residue=float(tw.residue[i]), pesticide=PC_KEYS[pc] if pc >= 0 else None,
                                  lastSprayT=float(tw.lastSprayT[i]), gdd=float(tw.gdd[i]), pest=pid)
                    c = {'i': i, 'pred': rmap.get((i, pid), 0.0), 'st': st}
                    if self.rows is not None:
                        ctx = cmap.get((i, pid))
                        if ctx is not None:
                            c['ctx'] = ctx
                            c['meta'] = [pk, float(ctx.get('announced') or 0), f]
                    cases.append(c)
            if cases:
                self.pending.append({'t0': tw.t, 'cases': cases})

    def tick(self):
        tw = self.tw
        if self.awd_pending:
            self.tick_awd()
        keep = []
        for g in self.pending:
            if tw.t - g['t0'] < DEF.pestDays * DAY:
                keep.append(g)
                continue
            for k in g['cases']:
                top = pest_outcome(tw.weather, g['t0'], k['st'], DEF.pestDays) if k['st'] is not None else 0.0
                truth = 1 if top >= DEF.pestThreshold else 0
                pred = 1 if k['pred'] >= 0.5 else 0
                self._bump('pest', pred == truth)
                if k.get('ctx') is not None:
                    # every positive and every hard negative (score ≥ 0.1 or severity reaching 4 %), one easy negative in ten
                    # (weight 10): the rows that teach the forest something, at a tenth of the size
                    hard = truth or k['pred'] >= 0.1 or top >= 0.04
                    take = hard or hash01(tw.seed, 'pestrow', k['i'], int(g['t0'])) < 0.1
                    if take:
                        from ..core import models as M
                        self._row('pest', M._feat_pest_full(k['ctx']), truth, [k['pred'], 1.0 if hard else 10.0] + k['meta'] + [top])
                self._bump('pestPos' if truth else 'pestNeg', pred == truth)
                if truth and len(self.samples.setdefault('pestPos', [])) < 2000:
                    self.samples['pestPos'].append({'t0': g['t0'], 'pred': k['pred']})
        self.pending = keep

    def on_spray(self, pairs, planned=None, xs=None, plan_rows=None, extra=None):
        """Every sprayed square: (predicted kill from what the farm knows when it sprays, actual kill). ``planned`` is the
        prediction made when the spray was decided: one number for every square (v1) or a list, square by square (v2).
        xs / plan_rows: the feature rows (with rows=True)."""
        for k, (pred, eff) in enumerate(pairs):
            self._bump('spray', abs(pred - eff) <= DEF.sprayTol + 1e-12)
            pl = planned[k] if isinstance(planned, (list, tuple)) else planned
            if pl is not None:
                self._bump('sprayPlan', abs(pl - eff) <= DEF.sprayTol + 1e-12)
            if xs is not None and k < len(xs) and xs[k] is not None:
                self._row('spray', xs[k], eff, [pred] + (list(extra[k]) if extra is not None and k < len(extra) else []))
            if plan_rows is not None and k < len(plan_rows) and plan_rows[k] is not None:
                self._row('plan', plan_rows[k][0], eff, [pl if pl is not None else -1.0] + list(plan_rows[k][1]))
            if len(self.samples['spray']) < 5000:
                self.samples['spray'].append({'t': self.tw.t, 'pred': pred, 'true': eff})

    def on_harvest_assess(self, f):
        h = self.adv.harvest(f)
        for i, ctx, p in h.ctxs:
            truth = self.tw.harvest_ready_truth(i)
            self._bump('harvest', (p >= 0.5) == truth)
            if self.rows is not None:
                from ..core import models as M
                self._row('harvest', M._feat_harvest_full(ctx), 1.0 if truth else 0.0, [p, f])

    def on_harvest(self, f):
        tw = self.tw
        pred = self.yield_pred.get(f)
        cells = [int(i) for i in tw.cells[f] if tw.has[i]]
        if pred is None or not cells:
            return
        actual = float(np.mean([tw.harvestKg[i] / (tw.cfg.get('potentialKg', None) or (PADDY.yieldTHa * 0.1)) for i in cells]))
        self._bump('yield', abs(pred - actual) <= DEF.yieldTol + 1e-12)
        if f in self.yield_x:
            x, extra = self.yield_x[f]
            self._row('yield', x, actual, [pred, f] + list(extra))
        self.samples['yield'].append({'field': tw.fields[f].name, 'pred': pred, 'true': actual})

    # time to AWD (v2): at 06:00 and 18:00, each transplanted field before the drain-down, not being irrigated, whose
    # water level is above its threshold; scored once 72 hours have passed, with the weather that actually came
    def on_awd_check(self, f, thr, pred, x=None):
        tw = self.tw
        self.awd_pending.append({'t0': tw.t, 'f': f, 'thr': thr, 'pred': float(pred), 'x': x, 'state': tw.level_state(f)})

    def _score_awd(self, g):
        truth = self.tw.project_level(g['f'], g['thr'], hours=DEF.awdCap, state=g['state'])
        if truth < DEF.awdHours:
            self._bump('awd', awd_hit(g['pred'], truth))
        if g.get('x') is not None:
            self._row('awd', g['x'], truth, [g['pred'], g['f'], g['thr']])

    def tick_awd(self, final=False):
        tw = self.tw
        keep = []
        for g in self.awd_pending:
            if final or tw.t - g['t0'] >= DEF.awdHours * HOUR:
                self._score_awd(g)
            else:
                keep.append(g)
        self.awd_pending = keep

    def summary(self):
        out = {}
        for k, (hit, n) in self.t.items():
            out[k] = {'n': n, 'acc': hit / n if n else None, 'ci': wilson(hit, n) if n else None}
        pp, pn = self.t['pestPos'], self.t['pestNeg']
        if pp[1] and pn[1]:
            out['pest']['balanced'] = (pp[0] / pp[1] + pn[0] / pn[1]) / 2
        return out
