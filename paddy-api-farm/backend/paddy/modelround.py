"""The v2 model round: training the paddy forests on set G, scoring them on set T (and later L), reports.

Rows come from the audit (``Engine(rows=True)``; one .npz per season, written by ``paddy.datasets``). Every check is
scored exactly as the audit scores it (``paddy/sim/audit.py``, DEFINITIONS.md and the v2 addendum):

    soil       predicted class = the class the rules give the true soil
    establish  predicted chance within ±5 points of the true chance
    pest       call at 0.5 = whether the plant reaches 8 % within 7 days (rows carry a weight: hard negatives 1, easy
               negatives 10, positives 1), accuracy and balanced accuracy
    yield      forecast within ±10 points of the actual share of attainable
    spray      predicted kill within ±5 points of the actual kill
    plan       (v2 target) the kill predicted for each square when the spray was decided, within ±5 points
    harvest    ready / not ready call at 0.5 = the ready rule
    awd        hours to the AWD threshold within ±6 h or ±20 % of the truth, for truths under 72 h

Reports give each rate pooled, per decision maker and per season (kharif, rabi), with case counts, Wilson intervals and
a season-bootstrap interval (10,000 draws of whole seasons), and the share of seasons at ≥ 95 % among seasons with at
least 20 cases of the check.
"""
from __future__ import annotations

import json
import math
import os
import time

import numpy as np

from . import datasets as DS
from .core.rf import RandomForest

MODELS = ('soil', 'establish', 'pest', 'yield', 'spray', 'plan', 'harvest', 'awd')
# what each forest of the paddy pack is, for the website's Models view and the replay page
PACK_INFO = {
    'awd': ('Time to AWD (paddy)', "hours until a field's water falls to its AWD threshold if not irrigated: the twin's water balance "
            "from the farm's fitted level on 256 draws of the coming weather given the forecasts; the forest corrects that "
            'estimate (a ratio) from the draws\' spread, the level fit and the crop', None),
    'plan': ('Spray efficacy, planned (paddy)', 'the kill expected on each square when a spray is decided, at the minute the arm '
             'will reach it, with the weather as all the forecasts allow (128 draws of the rain); the forest corrects that '
             'estimate (a difference) from the pest the cameras see and its history', None),
    'harvestP': ('Harvest readiness (paddy)', "ready / not ready per plant, with the readiness rule on what the farm measures", 'harvest'),
    'soilP': ('Soil suitability (paddy)', 'suitable / needs amendment / unsuitable per square, with the rules on the reading and '
              'the probe-noise re-draws', 'soil'),
    'establishP': ('Planting day (paddy)', 'chance a seedling transplanted now establishes, with the practice factor', 'establish'),
    'yield': ('Yield forecast (paddy)', 'share of attainable yield, trained on paddy seasons', 'yield'),
}


def awd_features():
    """The time-to-AWD forest's inputs, in order (the advisor's own list)."""
    from .sim.advisor import Advisor
    return list(Advisor.AWD_NAMES)


def pack_models_info(meta):
    """The paddy pack's forests as the Models view lists them (title, task, size, inputs, training rows, scores)."""
    out = []
    for mid, m in ((meta or {}).get('models') or {}).items():
        title, task, replaces = PACK_INFO.get(mid, (mid, '', None))
        feats = m.get('features')
        out.append({'id': mid, 'title': title, 'task': task, 'trees': m.get('nTrees'), 'nodes': m.get('nodes'),
                    'features': len(feats) if feats else m.get('F'), 'trained_on': m.get('trained_on'), 'replaces': replaces,
                    'report': m.get('report'), 'anchor': (m.get('anchor') or {}).get('kind')})
    return out


# the inputs of the two new forests, in the order sim/advisor.py builds them
PLAN_FEATURES = ['hour (sin)', 'hour (cos)', 'forecast wind m/s', 'forecast temperature °C', 'forecast humidity %',
                 'forecast rain next 6 h mm', 'product UV sensitivity', 'base efficacy', 'larva', 'sucking', 'mite', 'miner',
                 'tracked age d', 'sun (sin of elevation)', 'kill rule at the forecast hour', 'heaviest forecast hour mm',
                 'forecast rainy hours', 'lead h', 'pest seen', 'identification votes',
                 'kill most posterior draws are within ±5 points of', 'kill rule with no rain', 'mean kill over the draws',
                 'wind at the arm\'s minute m/s', 'temperature at the arm\'s minute °C', 'sun at the arm\'s minute',
                 'mean rain in the 6 h after mm', 'chance of rain in the 6 h after', 'lead to the arm\'s minute h',
                 'tracked age at the arm\'s minute d', 'seen severity', 'days since last detection', 'days since last spray',
                 'sprays on the square this season', 'share of draws within ±5 points of that kill',
                 'wind at the minute, lowest the forecasts allow', 'wind at the minute, highest the forecasts allow']
TARGET = 0.95
PACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'core', 'models')
PACK_NPZ = os.path.join(PACK_DIR, 'paddy_forests.npz')
PACK_META = os.path.join(PACK_DIR, 'paddy_forests.json')
# tolerance functions for forests whose leaves and trees are combined by "cover" (the value most targets are within
# tolerance of), by name, so a saved forest gets its own back
TOLS = {'awd': lambda v: max(6.0, 0.20 * v), 'kill5': lambda v: 0.05, 'pts5': lambda v: 0.05, 'pts10': lambda v: 0.10,
        # an anchored time-to-AWD forest predicts the ratio truth ÷ posterior estimate: ± 0.2 of a ratio is the ± 20 %
        'ratio20': lambda v: 0.20}


# ── rows ──
def load(set_name, model, out=DS.DEFAULT_OUT, managers=None, width=None, idx=None):
    """One check's rows from a set: X, y, extra, meta (case i, decision maker, season 0 = kharif / 1 = rabi).
    ``width``: keep only seasons whose rows have that many features (a feature added mid-way leaves older seasons
    with fewer columns)."""
    Xs, ys, Es, meta = [], [], [], []
    root = os.path.join(out, set_name)
    if not os.path.isdir(root):
        return None
    for m in sorted(os.listdir(root)):
        if managers and m not in managers:
            continue
        d = os.path.join(root, m)
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.npz') or fn.endswith('.tmp.npz'):
                continue
            i = int(fn[:5])
            if idx is not None and i not in idx:
                continue
            if not os.path.exists(os.path.join(d, fn[:5] + '.json.gz')):
                continue
            z = np.load(os.path.join(d, fn))
            if f'X_{model}' not in z:
                continue
            X = z[f'X_{model}']
            if width is not None and X.shape[1] != width:
                continue
            Xs.append(X)
            ys.append(z[f'y_{model}'])
            Es.append(z[f'e_{model}'])
            meta += [(i, m, i % 2)] * len(X)
    if not Xs:
        return None
    w = max(X.shape[1] for X in Xs)
    if any(X.shape[1] != w for X in Xs):
        raise ValueError(f'{model}: rows of different widths in {set_name}; pass width=')
    ne = max(E.shape[1] for E in Es)
    Es = [np.pad(E, ((0, 0), (0, ne - E.shape[1])), constant_values=np.nan) for E in Es]
    return np.concatenate(Xs), np.concatenate(ys).astype(float), np.concatenate(Es), np.array(meta, dtype=object)


def widths(set_name, model, out=DS.DEFAULT_OUT):
    """How many seasons have each row width for a check (to see where a feature was added)."""
    cnt = {}
    root = os.path.join(out, set_name)
    for m in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        d = os.path.join(root, m)
        for fn in os.listdir(d):
            if fn.endswith('.npz') and not fn.endswith('.tmp.npz'):
                z = np.load(os.path.join(d, fn))
                if f'X_{model}' in z:
                    w = z[f'X_{model}'].shape[1]
                    cnt[w] = cnt.get(w, 0) + 1
    return cnt


# ── scoring, as the audit does ──
def hits(model, pred, y, E=None):
    """Per-row pass (bool) and per-row weight, and the mask of rows that are scored."""
    n = len(y)
    w = np.ones(n)
    scored = np.ones(n, bool)
    if model == 'soil':
        h = np.round(pred) == np.round(y)
    elif model == 'establish':
        h = np.abs(pred - y) <= 0.05 + 1e-12
    elif model == 'yield':
        h = np.abs(pred - y) <= 0.10 + 1e-12
    elif model in ('spray', 'plan'):
        h = np.abs(pred - y) <= 0.05 + 1e-12
    elif model == 'harvest':
        h = (pred >= 0.5) == (y >= 0.5)
    elif model == 'pest':
        h = (pred >= 0.5) == (y >= 0.5)
        if E is not None:
            w = np.where(np.isfinite(E[:, 1]), E[:, 1], 1.0)
    elif model == 'awd':
        scored = y < 72
        h = np.abs(pred - y) <= np.maximum(6.0, 0.20 * y) + 1e-9
    else:
        raise ValueError(model)
    return h.astype(bool), w, scored


def wilson(k, n, z=1.96):
    if n <= 0:
        return (float('nan'), float('nan'))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def season_bootstrap(hit, w, season_ids, draws=10000, seed=7):
    """95 % interval of the pooled rate from resampling whole seasons."""
    ids, inv = np.unique(season_ids, return_inverse=True)
    num = np.bincount(inv, weights=hit * w, minlength=len(ids))
    den = np.bincount(inv, weights=w, minlength=len(ids))
    if len(ids) < 2:
        r = num.sum() / max(1e-12, den.sum())
        return (r, r)
    rng = np.random.default_rng(seed)
    out = np.empty(draws)
    for k in range(0, draws, 1000):
        m = min(1000, draws - k)
        pick = rng.integers(0, len(ids), size=(m, len(ids)))
        out[k:k + m] = num[pick].sum(1) / np.maximum(1e-12, den[pick].sum(1))
    return (float(np.quantile(out, 0.025)), float(np.quantile(out, 0.975)))


def rate(hit, w, meta, sel=None, bootstrap=True):
    sel = np.ones(len(hit), bool) if sel is None else sel
    if not sel.any():
        return None
    h, ww, mm = hit[sel].astype(float), w[sel], meta[sel]
    n = int(sel.sum())
    r = float(np.sum(h * ww) / np.sum(ww))
    neff = n if np.allclose(ww, 1) else float(np.sum(ww) ** 2 / np.sum(ww ** 2))
    out = {'rate': r, 'n': n, 'wilson': wilson(r * neff, neff)}
    sid = np.array([f'{a}|{b}' for a, b in zip(mm[:, 0], mm[:, 1])])
    if bootstrap:
        out['boot'] = season_bootstrap(h, ww, sid)
    # share of seasons at ≥ 95 % among seasons with ≥ 20 cases
    ids, inv = np.unique(sid, return_inverse=True)
    cnt = np.bincount(inv, minlength=len(ids))
    num = np.bincount(inv, weights=h * ww, minlength=len(ids))
    den = np.bincount(inv, weights=ww, minlength=len(ids))
    ok = cnt >= 20
    out['seasons'] = int(ok.sum())
    out['share95'] = float(np.mean(num[ok] / den[ok] >= TARGET - 1e-12)) if ok.any() else None
    if bootstrap and ok.sum() >= 2:
        # the share's own 95 % interval, from 10,000 draws of whole seasons (among seasons with ≥ 20 cases)
        ind = (num[ok] / den[ok] >= TARGET - 1e-12).astype(float)
        rng = np.random.default_rng(11)
        bs = np.concatenate([ind[rng.integers(0, len(ind), size=(1000, len(ind)))].mean(axis=1) for _ in range(10)])
        out['share95_boot'] = (float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975)))
    return out


def breakdown(model, pred, y, E, meta, bootstrap=True):
    """Pooled, per decision maker, per season; pest also balanced accuracy and recall on each class."""
    h, w, sc = hits(model, pred, y, E)
    h, w, y2, mm, p2 = h[sc], w[sc], y[sc], meta[sc], pred[sc]
    res = {'pooled': rate(h, w, mm, bootstrap=bootstrap)}
    for m in DS.MANAGERS4:
        res[m] = rate(h, w, mm, mm[:, 1] == m, bootstrap=bootstrap)
    for s, lab in ((0, 'kharif'), (1, 'rabi')):
        res[lab] = rate(h, w, mm, mm[:, 2] == s, bootstrap=bootstrap)
    if model == 'pest':
        pos = y2 >= 0.5
        for lab, sel in (('pos', pos), ('neg', ~pos)):
            res['recall_' + lab] = rate(h, w, mm, sel, bootstrap=False)
        rp, rn = res['recall_pos'], res['recall_neg']
        res['balanced'] = None if not rp or not rn else 0.5 * (rp['rate'] + rn['rate'])
        for m in DS.MANAGERS4 + ('kharif', 'rabi'):
            selm = (mm[:, 1] == m) if m in DS.MANAGERS4 else (mm[:, 2] == (0 if m == 'kharif' else 1))
            a = rate(h, w, mm, selm & pos, bootstrap=False)
            b = rate(h, w, mm, selm & ~pos, bootstrap=False)
            res['balanced_' + m] = None if not a or not b else 0.5 * (a['rate'] + b['rate'])
            res['npos_' + m] = int((selm & pos).sum())
    return res


def fmt(r):
    if not r:
        return '–'
    s = f"{r['rate'] * 100:.1f}% (n={r['n']:,}"
    if r.get('boot'):
        s += f", boot {r['boot'][0] * 100:.1f}–{r['boot'][1] * 100:.1f}"
    s += ')'
    return s


# ── forests ──
def train(kind, X, y, nTrees=40, maxDepth=14, minLeaf=5, mtry=0, nBins=64, seed=11, leafStat='mean', combine='mean', tol=None, nClasses=0,
          sample=None, rng_seed=5, weights=None):
    """A forest on (a sample of) the rows. ``weights`` (e.g. pest's 1/10 for subsampled easy negatives) are honoured by
    replicating rows in the sample."""
    idx = np.arange(len(y))
    if weights is not None and not np.allclose(weights, 1):
        rng = np.random.default_rng(rng_seed)
        p = weights / weights.sum()
        n = sample or len(y)
        idx = rng.choice(len(y), size=n, replace=True, p=p)
    elif sample and len(y) > sample:
        rng = np.random.default_rng(rng_seed)
        idx = rng.choice(len(y), size=sample, replace=False)
    rf = RandomForest(type=kind, nTrees=nTrees, maxDepth=maxDepth, minLeaf=minLeaf, mtry=mtry, nBins=nBins, seed=seed, leafStat=leafStat,
                      combine=combine, tol=tol, nClasses=nClasses)
    rf.fit(X[idx], y[idx])
    return rf


def predict(model, rf, X):
    if model in ('harvest', 'pest'):
        return rf.predict_proba_batch(X)[:, 1]
    if model == 'soil':
        return np.argmax(rf.predict_proba_batch(X), axis=1).astype(float)
    return rf.predict_batch(X)


# ── the pack of paddy forests (loaded on top of the v1 forests) ──
def save_pack(forests, path_npz=PACK_NPZ, path_meta=PACK_META, info=None):
    """forests: {model id: (RandomForest, meta dict)}"""
    arrays, meta = {}, {'version': (info or {}).get('version', 'paddy-v2'), 'built': time.strftime('%Y-%m-%d'), 'models': {}}
    for mid, (rf, m) in forests.items():
        d = rf.to_dict()
        for k in ('feat', 'thr', 'left', 'right', 'val', 'roots'):
            a = np.asarray(d[k])
            if k in ('thr', 'val'):
                a = a.astype(np.float32)        # half the size; float32 is what the playground's packs keep too
            elif k in ('feat', 'left', 'right'):
                a = a.astype(np.int32)
            arrays[f'{mid}__{k}'] = a
        meta['models'][mid] = dict(m, type=rf.type, nTrees=int(len(d['roots'])), nClasses=int(getattr(rf, 'nClasses', 0) or 0), combine=rf.combine,
                                   F=int(getattr(rf, 'F', 0) or 0), nodes=int(rf.node_count()))
    meta.update(info or {})
    np.savez_compressed(path_npz, **arrays)
    with open(path_meta, 'w') as fh:
        json.dump(meta, fh, indent=1)
    return path_npz, path_meta


def load_pack(path_npz=PACK_NPZ, path_meta=PACK_META):
    """{model id: RandomForest} from the paddy pack, or {} when there is none."""
    if not (os.path.exists(path_npz) and os.path.exists(path_meta)):
        return {}, None
    with open(path_meta) as fh:
        meta = json.load(fh)
    z = np.load(path_npz)
    out = {}
    for mid, m in meta['models'].items():
        rf = RandomForest.from_arrays({'type': m['type'], 'nClasses': m.get('nClasses'), 'F': m.get('F'), 'combine': m.get('combine'),
                                       'featureNames': m.get('features') or [], 'classNames': m.get('classes') or []},
                                      z[f'{mid}__feat'], z[f'{mid}__thr'], z[f'{mid}__left'], z[f'{mid}__right'], z[f'{mid}__val'], z[f'{mid}__roots'])
        rf.tol = TOLS.get(m.get('tol'))
        # a forest anchored on one of its own inputs (the farm's posterior estimate) predicts a correction of it: the
        # ratio to it ('ratio', the time to AWD) or the difference from it ('diff', the planned kill)
        rf.anchor = m.get('anchor')
        out[mid] = rf
    return out, meta
