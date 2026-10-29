"""Extra inputs of the v2 paddy forests that the farm computes from what it already knows (paddy v2).

* Soil: the suitability rules (agro.suitability_label, paddy's limits) applied to the reading itself, and the share of
  200 re-draws of the true soil behind the reading (the reading less the probe's own noise: pH ± 0.03, N ± 3, P ± 0.6,
  K ± 3 mg/kg, salinity ± 0.02 dS/m, normal) that the rules call unsuitable, needing amendment or suitable. The same
  200 re-draws are used for every square (fixed seed), so the inputs are a pure function of the reading.
* Harvest: the readiness rule (twin.harvest_ready_truth) on what the farm knows: the crop's progress, yesterday's and
  today's rain on its gauge and the station's humidity.

Both are functions of a row's own features, so they are added the same way when the farm advises and when the forests
are trained (tools/models/train.py), and old rows need no re-run.
"""
from __future__ import annotations

import numpy as np

from . import agro as A
from . import data as D

PADDY = next(c for c in D.CROPS if c.id == 'paddy')
PROBE_SD = {'ph': 0.03, 'n': 3.0, 'p': 0.6, 'k': 3.0, 'ec': 0.02}
SOIL_DRAWS = 200
_Z = np.random.default_rng(20261028).standard_normal((5, SOIL_DRAWS))


def soil_label_vec(ph, n, p, k, oc, sand, clay, ec, c=PADDY):
    """agro.suitability_label for arrays (0 unsuitable, 1 needs amendment, 2 suitable), the same tests in the same
    order."""
    o0, o1 = c['ph']
    t0, t1 = c['phTol']
    lo, hi = c['sand']
    sd = np.where(sand < lo, lo - sand, np.where(sand > hi, sand - hi, 0.0))
    npk = c['npk']
    sn = n / (0.6 * npk[0] + 150)
    sp = p / (0.12 * npk[1] + 14)
    sk = k / (0.5 * npk[2] + 110)
    bad = (ph < t0 - 0.3) | (ph > t1 + 0.2) | (ec > c['ecTol'] * 2.2) | (sd > 20) | ((clay > 40) & (c['wlTol'] < 0.3))
    amend = (ph < o0) | (ph > o1 + 0.25) | (ec > c['ecTol']) | (sd > 8) | (oc < 0.4) | (sn < 0.75) | (sp < 0.75) | (sk < 0.75)
    return np.where(bad, 0, np.where(amend, 1, 2))


def soil_extra(X):
    """X: rows of (pH, N, P, K, OC, sand, clay, EC) readings → (rules on the reading, P(unsuitable), P(amend),
    P(suitable)) per row."""
    X = np.asarray(X, float)
    ph, n, p, k, oc, sand, clay, ec = (X[:, j] for j in range(8))
    plug = soil_label_vec(ph, n, p, k, oc, sand, clay, ec)
    R = len(X)
    out = np.zeros((R, 4))
    out[:, 0] = plug
    z = _Z
    for a in range(0, R, 4096):
        b = min(R, a + 4096)
        lab = soil_label_vec(ph[a:b, None] - PROBE_SD['ph'] * z[0], n[a:b, None] - PROBE_SD['n'] * z[1], p[a:b, None] - PROBE_SD['p'] * z[2],
                             k[a:b, None] - PROBE_SD['k'] * z[3], oc[a:b, None], sand[a:b, None], clay[a:b, None],
                             ec[a:b, None] - PROBE_SD['ec'] * z[4])
        for cl in range(3):
            out[a:b, 1 + cl] = (lab == cl).mean(axis=1)
    return out


def harvest_rule(X):
    """X: rows of the harvest forest's features (progress, days past maturity, multi, pick ratio, rain 2 days, humidity,
    health, fruit load, progress − 1) → 1 where the readiness rule says ready, else 0."""
    X = np.asarray(X, float)
    frac, dpm, rain2, rh = X[:, 0], X[:, 1], X[:, 4], X[:, 5]
    return ((frac >= 1) & (((rain2 < 6) & (rh < 90)) | (dpm > 8))).astype(float)[:, None]


def check_soil_rules(n=2000, seed=3):
    """soil_label_vec agrees with agro.suitability_label (used by the tests)."""
    rng = np.random.default_rng(seed)
    rows = np.c_[rng.uniform(4, 9, n), rng.uniform(0, 400, n), rng.uniform(0, 60, n), rng.uniform(0, 400, n), rng.uniform(0.1, 2, n),
                 rng.uniform(5, 90, n), rng.uniform(5, 60, n), rng.uniform(0.02, 8, n)]
    keys = ('ph', 'n', 'p', 'k', 'oc', 'sand', 'clay', 'ec')
    ref = np.array([A.suitability_label(dict(zip(keys, r)), PADDY) for r in rows])
    return bool(np.all(ref == soil_label_vec(*(rows[:, j] for j in range(8)))))
