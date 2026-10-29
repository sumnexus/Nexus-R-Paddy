# Copied from the Agri Robotics Playground's Python port (agriplay/rf.py) for the Paddy API Farm; unchanged below.
"""Random forest, written from scratch with NumPy.

* CART trees on quantile-binned features (fast histogram splits)
* bootstrap bagging + random feature subsets (mtry)
* classification (Gini, class-probability leaves) and regression (variance)
* regression leaves: mean, median or "cover" (the value most targets in the leaf are
  within tolerance of); trees combined by mean, median or cover
* out-of-bag (OOB) score, impurity-based feature importance
* evaluation: accuracy, confusion matrix, AUC, Brier, R², MAE, RMSE

A trained forest is stored as flat arrays (all trees concatenated): ``feat`` (−1 marks
a leaf), ``thr``, ``left``, ``right`` and ``val`` (leaf values: one per node for
regression, one row of class probabilities for classification). Prediction walks every
tree for every sample at once (NumPy), or one sample through plain Python lists.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np


class RandomForest:
    def __init__(self, type='cls', nTrees=40, maxDepth=12, minLeaf=3, mtry=0, nBins=32, seed=1,
                 leafStat='mean', tol: Optional[Callable] = None, coverW: Optional[Callable] = None,
                 combine='mean', nClasses=0):
        self.type = type
        self.nTrees = nTrees
        self.maxDepth = maxDepth
        self.minLeaf = minLeaf
        self.mtry = mtry  # 0 → sqrt(F) for cls, F/3 for reg
        self.nBins = min(255, nBins or 32)
        self.seed = seed or 1
        self.leafStat = leafStat
        self.tol = tol
        self.coverW = coverW
        self.combine = combine
        self.nClasses = nClasses
        self.featureNames = []
        self.classNames = []
        self.importance = None
        self.oob = None
        self.trainSize = 0
        self.F = 0
        self._clear_arrays()

    # ── storage ──
    def _clear_arrays(self):
        self.feat = np.zeros(0, np.int32)
        self.thr = np.zeros(0, np.float64)
        self.left = np.zeros(0, np.int32)
        self.right = np.zeros(0, np.int32)
        self.val = np.zeros((0, 1), np.float64)
        self.roots = np.zeros(0, np.int64)
        self.depth = 0
        self._lists = None

    def set_trees(self, trees):
        """trees: list of dicts with node-local arrays feat, thr, left, right (−1 = none) and val (n × C)."""
        C = self.nClasses if self.type == 'cls' else 1
        feats, thrs, lefts, rights, vals, roots = [], [], [], [], [], []
        off = 0
        for t in trees:
            n = len(t['feat'])
            f = np.asarray(t['feat'], np.int32)
            l = np.asarray(t['left'], np.int64)
            r = np.asarray(t['right'], np.int64)
            feats.append(f)
            thrs.append(np.asarray(t['thr'], np.float64))
            lefts.append(np.where(l >= 0, l + off, -1).astype(np.int32))
            rights.append(np.where(r >= 0, r + off, -1).astype(np.int32))
            v = np.asarray(t['val'], np.float64).reshape(n, C)
            vals.append(v)
            roots.append(off)
            off += n
        self.feat = np.concatenate(feats) if feats else np.zeros(0, np.int32)
        self.thr = np.concatenate(thrs) if thrs else np.zeros(0)
        self.left = np.concatenate(lefts) if lefts else np.zeros(0, np.int32)
        self.right = np.concatenate(rights) if rights else np.zeros(0, np.int32)
        self.val = np.concatenate(vals) if vals else np.zeros((0, C))
        self.roots = np.asarray(roots, np.int64)
        self.nTrees = len(roots)
        self.depth = self._max_depth()
        self._lists = None

    def _max_depth(self):
        if not len(self.feat):
            return 0
        depth = np.zeros(len(self.feat), np.int32)
        best = 0
        # nodes are stored parent-before-child within each tree
        internal = np.nonzero(self.feat >= 0)[0]
        for nd in internal:
            d = depth[nd] + 1
            depth[self.left[nd]] = d
            depth[self.right[nd]] = d
            if d > best:
                best = d
        return int(best)

    def node_count(self):
        return int(len(self.feat))

    def max_depth_reached(self):
        return self.depth

    # ── training ──
    def _prepare(self, X, y):
        X = np.asarray(X, dtype=np.float64)
        n, F = X.shape
        self.F, self.n = F, n
        if self.type == 'cls' and not self.nClasses:
            self.nClasses = int(np.max(y)) + 1
        edges = []
        binned = np.empty((n, F), np.uint8)
        for f in range(F):
            col = X[:, f]
            s = np.sort(col)
            cuts = []
            for b in range(1, self.nBins):
                v = s[min(n - 1, (b * n) // self.nBins)]
                if not cuts or v > cuts[-1]:
                    cuts.append(v)
            while cuts and cuts[-1] >= s[n - 1]:  # the largest value must not be a cut
                cuts.pop()
            e = np.asarray(cuts, np.float64)
            edges.append(e)
            binned[:, f] = np.searchsorted(e, col, side='left')  # number of edges < v
        self.edges = edges
        self.binned = binned
        self.yArr = np.asarray(y, np.int64 if self.type == 'cls' else np.float64)

    def fit_iter(self, X, y, featureNames=(), classNames=()):
        """Generator: trains one tree per step (yields progress 0..1)."""
        self.featureNames = list(featureNames)
        self.classNames = list(classNames)
        self._prepare(X, y)
        n, F = self.n, self.F
        C = self.nClasses if self.type == 'cls' else 1
        mtry = self.mtry or (max(1, round(math.sqrt(F))) if self.type == 'cls' else max(1, round(F / 3)))
        rng = np.random.default_rng(self.seed)
        imp = np.zeros(F)
        oob_sum = np.zeros((n, C))
        oob_cnt = np.zeros(n, np.int64)
        trees = []
        self.trainSize = n
        for t in range(self.nTrees):
            idx = rng.integers(0, n, n)
            in_bag = np.zeros(n, bool)
            in_bag[idx] = True
            tree = self._build_tree(idx, rng, mtry, imp)
            trees.append(tree)
            out = np.nonzero(~in_bag)[0]
            if len(out):
                leaves = self._leaf_of_binned(tree, out)
                oob_sum[out] += tree['val'][leaves]
                oob_cnt[out] += 1
            yield (t + 1) / self.nTrees
        s = imp.sum()
        self.importance = (imp / s if s > 0 else imp).tolist()
        m = oob_cnt > 0
        if self.type == 'cls':
            pred = np.argmax(oob_sum[m], axis=1)
            self.oob = float(np.mean(pred == self.yArr[m])) if m.any() else math.nan
        else:
            p = oob_sum[m, 0] / oob_cnt[m]
            yy = self.yArr[m]
            sst = float(np.sum((yy - yy.mean()) ** 2))
            self.oob = 1 - float(np.sum((yy - p) ** 2)) / sst if sst > 0 else math.nan
        self.set_trees([{'feat': t['feat'], 'thr': t['thr'], 'left': t['left'], 'right': t['right'], 'val': t['val']} for t in trees])
        self.binned = None
        self.yArr = None

    def fit(self, X, y, featureNames=(), classNames=()):
        for _ in self.fit_iter(X, y, featureNames, classNames):
            pass
        return self

    def _leaf_value(self, ys, mean):
        n = len(ys)
        if self.leafStat == 'mean' or n < 3:
            return mean
        ys = np.sort(ys)
        if self.leafStat == 'median':
            return float(ys[n >> 1]) if n % 2 else float((ys[n // 2 - 1] + ys[n // 2]) / 2)
        cp = cover_point(ys, self.tol, self.coverW)
        return mean if cp is None else cp

    def _build_tree(self, idx, rng, mtry, imp):
        F = self.F
        cls = self.type == 'cls'
        C = self.nClasses if cls else 1
        binned, y, min_leaf = self.binned, self.yArr, self.minLeaf
        feat, thr, left, right, val, bin_thr = [], [], [], [], [], []
        stack = [(idx, 0, -1, 0)]
        feat_idx = np.arange(F)
        while stack:
            work, depth, parent, is_right = stack.pop()
            node = len(feat)
            feat.append(-1)
            thr.append(0.0)
            left.append(-1)
            right.append(-1)
            bin_thr.append(0)
            if parent >= 0:
                if is_right:
                    right[parent] = node
                else:
                    left[parent] = node
            n_node = len(work)
            yw = y[work]
            if cls:
                tot = np.bincount(yw, minlength=C).astype(np.float64)
                p = tot / n_node
                parent_imp = 1 - float(np.sum(p * p))
                pure = np.count_nonzero(tot) <= 1
                mean = 0.0
            else:
                mean = float(yw.mean())
                parent_imp = float(np.mean(yw * yw)) - mean * mean
                pure = parent_imp < 1e-12

            def make_leaf():
                if cls:
                    val.append(tot / n_node)
                else:
                    val.append(np.array([self._leaf_value(yw, mean)]))

            if pure or depth >= self.maxDepth or n_node < 2 * min_leaf:
                make_leaf()
                continue
            # pick mtry random features (partial Fisher–Yates)
            for i in range(mtry):
                j = i + int(rng.integers(0, F - i))
                feat_idx[i], feat_idx[j] = feat_idx[j], feat_idx[i]
            best_gain, best_f, best_b = 1e-12, -1, -1
            for q in range(mtry):
                f = int(feat_idx[q])
                nb = len(self.edges[f]) + 1
                if nb < 2:
                    continue
                bcol = binned[work, f]
                if cls:
                    counts = np.bincount(bcol.astype(np.int64) * C + yw, minlength=nb * C).reshape(nb, C)[:nb - 1]
                    lc = np.cumsum(counts, axis=0)
                    nL = lc.sum(axis=1)
                    nR = n_node - nL
                    ok = (nL >= min_leaf) & (nR >= min_leaf)
                    if not ok.any():
                        continue
                    with np.errstate(divide='ignore', invalid='ignore'):
                        pl = lc / nL[:, None]
                        pr = (tot[None, :] - lc) / nR[:, None]
                        gL = 1 - np.sum(pl * pl, axis=1)
                        gR = 1 - np.sum(pr * pr, axis=1)
                        gain = parent_imp - (nL * gL + nR * gR) / n_node
                else:
                    cnt = np.bincount(bcol, minlength=nb).astype(np.float64)
                    sm = np.bincount(bcol, weights=yw, minlength=nb)
                    sq = np.bincount(bcol, weights=yw * yw, minlength=nb)
                    sT, s2T = sm.sum(), sq.sum()
                    sse_p = s2T - sT * sT / n_node
                    sL = np.cumsum(sm)[:nb - 1]
                    s2L = np.cumsum(sq)[:nb - 1]
                    nL = np.cumsum(cnt)[:nb - 1]
                    nR = n_node - nL
                    ok = (nL >= min_leaf) & (nR >= min_leaf)
                    if not ok.any():
                        continue
                    with np.errstate(divide='ignore', invalid='ignore'):
                        sR, s2R = sT - sL, s2T - s2L
                        sse = (s2L - sL * sL / nL) + (s2R - sR * sR / nR)
                        gain = (sse_p - sse) / n_node
                gain = np.where(ok, gain, -np.inf)
                b = int(np.argmax(gain))
                if gain[b] > best_gain:
                    best_gain, best_f, best_b = float(gain[b]), f, b
            if best_f < 0:
                make_leaf()
                continue
            go_left = binned[work, best_f] <= best_b
            wl, wr = work[go_left], work[~go_left]
            if len(wl) < 1 or len(wr) < 1:
                make_leaf()
                continue
            imp[best_f] += best_gain * n_node
            feat[node] = best_f
            thr[node] = float(self.edges[best_f][best_b])
            bin_thr[node] = best_b
            stack.append((wr, depth + 1, node, 1))
            stack.append((wl, depth + 1, node, 0))
        # leaves get their value rows in node order
        n_nodes = len(feat)
        V = np.zeros((n_nodes, C))
        k = 0
        for nd in range(n_nodes):
            if feat[nd] < 0:
                V[nd] = val[k]
                k += 1
        return {'feat': np.asarray(feat, np.int32), 'thr': np.asarray(thr), 'left': np.asarray(left, np.int32),
                'right': np.asarray(right, np.int32), 'val': V, 'binThr': np.asarray(bin_thr, np.int32)}

    def _leaf_of_binned(self, tree, rows):
        nd = np.zeros(len(rows), np.int64)
        f = tree['feat']
        while True:
            fn = f[nd]
            active = fn >= 0
            if not active.any():
                return nd
            ff = np.where(active, fn, 0)
            go = self.binned[rows, ff] <= tree['binThr'][nd]
            nd = np.where(active, np.where(go, tree['left'][nd], tree['right'][nd]), nd)

    # ── prediction ──
    def leaves(self, X):
        """Leaf node index reached in every tree for every row: (nTrees, N)."""
        X = np.asarray(X, np.float64)
        if X.ndim == 1:
            X = X[None, :]
        N = X.shape[0]
        idx = np.repeat(self.roots[:, None], N, axis=1)
        rows = np.arange(N)[None, :]
        feat, thr, left, right = self.feat, self.thr, self.left, self.right
        for _ in range(self.depth + 1):
            f = feat[idx]
            active = f >= 0
            if not active.any():
                break
            xv = X[rows, np.where(active, f, 0)]
            nxt = np.where(xv <= thr[idx], left[idx], right[idx])
            idx = np.where(active, nxt, idx)
        return idx

    def tree_values(self, X):
        """Per-tree leaf values: (nTrees, N, C)."""
        return self.val[self.leaves(X)]

    def predict_proba_batch(self, X):
        X = np.asarray(X, np.float64)
        if X.ndim == 2 and X.shape[0] <= 6:  # a few rows: walking the trees in Python is faster
            return np.array([self.predict_proba(x) for x in X.tolist()]).reshape(X.shape[0], -1)
        return self.tree_values(X).mean(axis=0)  # (N, C)

    def predict_batch(self, X):
        X = np.asarray(X, np.float64)
        if X.ndim == 2 and X.shape[0] <= 6:
            return np.array([self.predict(x) for x in X.tolist()], float if self.type != 'cls' else int)
        tv = self.tree_values(X)
        if self.type == 'cls':
            return np.argmax(tv.mean(axis=0), axis=1)
        v = tv[:, :, 0]
        if self.combine == 'median':
            return np.median(v, axis=0)
        if self.combine == 'cover':
            out = np.empty(v.shape[1])
            for i in range(v.shape[1]):
                col = np.sort(v[:, i])
                cp = cover_point(col, self.tol, None)
                out[i] = col[len(col) >> 1] if cp is None else cp
            return out
        return v.mean(axis=0)

    def _as_lists(self):
        if self._lists is None:
            self._lists = (self.feat.tolist(), self.thr.tolist(), self.left.tolist(), self.right.tolist(),
                           self.val.tolist(), self.roots.tolist())
        return self._lists

    def _leaf_vals_one(self, x):
        feat, thr, left, right, val, roots = self._as_lists()
        out = []
        for nd in roots:
            f = feat[nd]
            while f >= 0:
                nd = left[nd] if x[f] <= thr[nd] else right[nd]
                f = feat[nd]
            out.append(val[nd])
        return out

    def predict_proba(self, x):
        vals = self._leaf_vals_one(x)
        C = len(vals[0])
        T = len(vals)
        return [sum(v[c] for v in vals) / T for c in range(C)]

    def predict(self, x):
        if self.type == 'cls':
            p = self.predict_proba(x)
            b = 0
            for c in range(1, len(p)):
                if p[c] > p[b]:
                    b = c
            return b
        v = [t[0] for t in self._leaf_vals_one(x)]
        if self.combine == 'median':
            v.sort()
            m = len(v)
            return v[m >> 1] if m % 2 else (v[m // 2 - 1] + v[m // 2]) / 2
        if self.combine == 'cover':
            v.sort()
            cp = cover_point(v, self.tol, None)
            return v[len(v) >> 1] if cp is None else cp
        return sum(v) / len(v)

    def predict_spread(self, x):
        v = [t[0] for t in self._leaf_vals_one(x)]
        m = sum(v) / len(v)
        return {'mean': m, 'sd': math.sqrt(sum((a - m) ** 2 for a in v) / len(v))}

    # ── saving ──
    def to_dict(self):
        return {'type': self.type, 'nClasses': self.nClasses, 'F': self.F, 'nTrees': self.nTrees, 'combine': self.combine,
                'featureNames': self.featureNames, 'classNames': self.classNames, 'oob': self.oob, 'importance': self.importance,
                'feat': self.feat, 'thr': self.thr, 'left': self.left, 'right': self.right, 'val': self.val, 'roots': self.roots}

    @classmethod
    def from_arrays(cls, meta, feat, thr, left, right, val, roots):
        rf = cls(type=meta['type'], nTrees=int(len(roots)), combine=meta.get('combine') or 'mean', nClasses=int(meta.get('nClasses') or 0))
        rf.F = int(meta.get('F') or 0)
        rf.featureNames = list(meta.get('featureNames') or [])
        rf.classNames = list(meta.get('classNames') or [])
        rf.feat = np.asarray(feat, np.int32)
        rf.thr = np.asarray(thr, np.float64)
        rf.left = np.asarray(left, np.int32)
        rf.right = np.asarray(right, np.int32)
        C = rf.nClasses if rf.type == 'cls' else 1
        rf.val = np.asarray(val, np.float64).reshape(len(rf.feat), C)
        rf.roots = np.asarray(roots, np.int64)
        rf.depth = rf._max_depth()
        return rf


def cover_point(ys, tol, wf):
    """The point covered by the most intervals [y − tol(y), y + tol(y)] (ys sorted): a sweep over endpoints."""
    ev = []
    for v in ys:
        v = float(v)
        if wf is not None and not (wf(v) > 0):
            continue
        t = tol(v)
        ev.append((v - t, 1))
        ev.append((v + t, -1))
    if not ev:
        return None
    ev.sort(key=lambda e: (e[0], -e[1]))
    cur, best, at = 0, -1, ev[0][0]
    for x, d in ev:
        cur += d
        if d > 0 and cur > best:
            best, at = cur, x
    # centre of the best region: from where the count peaks to where it drops
    j, c2, lo, hi = 0, 0, at, at
    while j < len(ev):
        c2 += ev[j][1]
        if ev[j][0] == at and c2 == best:
            break
        j += 1
    for k in range(j + 1, len(ev)):
        if ev[k][1] < 0:
            hi = ev[k][0]
            break
    return (lo + hi) / 2


# ── evaluation ──
def evaluate(model: RandomForest, X, y):
    X = np.asarray(X, np.float64)
    y = np.asarray(y)
    if model.type == 'cls':
        C = model.nClasses
        P = model.predict_proba_batch(X)
        pred = np.argmax(P, axis=1)
        conf = np.zeros((C, C), np.int64)
        np.add.at(conf, (y.astype(np.int64), pred), 1)
        res = {'accuracy': float(np.mean(pred == y)), 'confusion': conf.tolist(), 'n': int(len(y))}
        res['recall'] = [float(conf[c, c] / conf[c].sum()) if conf[c].sum() else math.nan for c in range(C)]
        res['precision'] = [float(conf[c, c] / conf[:, c].sum()) if conf[:, c].sum() else math.nan for c in range(C)]
        if C == 2:
            res['brier'] = float(np.mean((P[:, 1] - y) ** 2))
            res['auc'] = auc(list(zip(P[:, 1].tolist(), y.tolist())))
            res['baseRate'] = float(np.mean(y))
        return res
    p = model.predict_batch(X)
    mu = float(np.mean(y))
    ssr = float(np.sum((y - p) ** 2))
    sst = float(np.sum((y - mu) ** 2))
    return {'r2': 1 - ssr / sst if sst > 0 else math.nan, 'mae': float(np.mean(np.abs(y - p))), 'rmse': math.sqrt(ssr / len(y)), 'n': int(len(y))}


def auc(pairs):
    pairs = sorted(pairs, key=lambda a: a[0])
    rank_sum, n_pos, n_neg, i = 0.0, 0, 0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j + 1) / 2  # 1-based average rank for ties
        for k in range(i, j):
            if pairs[k][1] == 1:
                rank_sum += avg_rank
                n_pos += 1
            else:
                n_neg += 1
        i = j
    if not n_pos or not n_neg:
        return math.nan
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def train_test_split(X, y, test_frac, seed, groups=None):
    """Random split; with ``groups``, whole groups go to one side."""
    from .util import RNG
    rng = RNG(seed)
    n = len(X)
    if groups is not None:
        ids = rng.shuffle(list(dict.fromkeys(groups)))
        n_test = round(len(ids) * test_frac)
        test_ids = set(ids[:n_test])
        te = [i for i in range(n) if groups[i] in test_ids]
        tr = [i for i in range(n) if groups[i] not in test_ids]
    else:
        idx = rng.shuffle(list(range(n)))
        n_test = round(n * test_frac)
        te, tr = idx[:n_test], idx[n_test:]
    pick = lambda arr, ix: [arr[i] for i in ix]
    return {'Xtr': pick(X, tr), 'ytr': pick(y, tr), 'Xte': pick(X, te), 'yte': pick(y, te),
            'groupsTest': len({groups[i] for i in te}) if groups is not None else None}
