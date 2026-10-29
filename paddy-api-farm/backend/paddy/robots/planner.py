# Copied from the Agri Robotics Playground's Python port (agriplay/planner.py) for the Paddy API Farm v2.
# Changes: the import of Obj comes from paddy.core.util. The algorithms are unchanged.
""""Fastest planting" planning for the overhead arms.

Task allocation across arms (3 strategies, best makespan wins, then a relocate local
search), per-arm routing (nearest neighbour → 2-opt → Or-opt), and optimal refill/unload
trip splitting (Prins' split DP) for arms with limited seed magazines, spray tanks or
harvest bins. Baselines: one arm row-by-row, and two arms each taking half the rows.

Same algorithms as the web version; distances come from a matrix worked out once per route.
"""
from __future__ import annotations

import math

import numpy as np

from ..core.util import Obj

MOVE_OVERHEAD = 1.2  # s per move (acceleration + settling)
INF = math.inf


def d(a, b):
    return math.sqrt((a['x'] - b['x']) ** 2 + (a['z'] - b['z']) ** 2)


def tt(a, b, v):
    dist = d(a, b)
    return 0.0 if dist < 1e-6 else dist / v + MOVE_OVERHEAD


def _matrix(points):
    xs = np.array([p['x'] for p in points], float)
    zs = np.array([p['z'] for p in points], float)
    dx = xs[:, None] - xs[None, :]
    dz = zs[:, None] - zs[None, :]
    return np.sqrt(dx * dx + dz * dz).tolist()


# ── single-arm routing (indices: tasks 0..n-1, start = n) ──
def nearest_neighbour(D, n):
    used = [False] * n
    order = []
    cur = n
    for _ in range(n):
        row = D[cur]
        best, bd = -1, INF
        for i in range(n):
            if not used[i]:
                v = row[i]
                if v < bd:
                    bd, best = v, i
        used[best] = True
        order.append(best)
        cur = best
    return order


def two_opt(D, n, order, max_pass=6):
    """2-opt for an open path that starts at the start point and ends anywhere."""
    m = len(order)
    if m < 3:
        return order
    S = n
    for _ in range(max_pass):
        improved = False
        for i in range(m - 1):
            a = order[i - 1] if i > 0 else S
            b = order[i]
            Da, Db = D[a], D[b]
            dab = Da[b]
            for j in range(i + 1, m):
                c = order[j]
                if j + 1 < m:
                    e = order[j + 1]
                    De = D[e]
                    before = dab + De[c]
                    after = Da[c] + De[b]
                else:
                    before = dab
                    after = Da[c]
                if after < before - 1e-9:
                    order[i:j + 1] = order[i:j + 1][::-1]
                    improved = True
                    break
        if not improved:
            break
    return order


def or_opt(D, n, order, max_pass=3):
    """Or-opt: move chains of 1–3 tasks elsewhere in the path."""
    if len(order) < 4:
        return order
    S = n
    for _ in range(max_pass):
        improved = False
        for ln in (1, 2, 3):
            i = 0
            while i + ln <= len(order):
                L = len(order)
                pos = lambda k: order[k] if k >= 0 else S
                prev, first, last = pos(i - 1), order[i], order[i + ln - 1]
                nxt = order[i + ln] if i + ln < L else None
                remove_gain = D[prev][first] + ((D[last][nxt] - D[prev][nxt]) if nxt is not None else 0)
                best_j, best_delta, best_rev = -1, -1e-9, False
                Df, Dl = D[first], D[last]
                for j in range(-1, L):
                    if i - 1 <= j <= i + ln - 1:
                        continue
                    a = pos(j)
                    if j + 1 < L:
                        b = order[j + 1]
                        base = D[a][b]
                        ins = D[a][first] + Dl[b] - base
                        ins_r = D[a][last] + Df[b] - base
                    else:
                        ins = D[a][first]
                        ins_r = D[a][last]
                    if ins - remove_gain < best_delta:
                        best_delta, best_j, best_rev = ins - remove_gain, j, False
                    if ln > 1 and ins_r - remove_gain < best_delta:
                        best_delta, best_j, best_rev = ins_r - remove_gain, j, True
                if best_j != -1:
                    seg = order[i:i + ln]
                    del order[i:i + ln]
                    if best_rev:
                        seg.reverse()
                    at = best_j - ln + 1 if best_j > i else best_j + 1
                    order[at:at] = seg
                    improved = True
                i += 1
        if not improved:
            break
    return order


def split(arm, tasks, order, D=None):
    """Optimal placement of refill/unload trips for a fixed visiting order (Prins' split)."""
    n = len(order)
    v = arm['speed']
    S = {'x': arm['x'], 'z': arm['z']}
    T = lambda k: tasks[order[k - 1]]  # 1-based
    cap = arm.get('capacity')
    if not cap or not math.isfinite(cap):
        time = dist = 0.0
        cur = S
        for k in range(1, n + 1):
            time += tt(cur, T(k), v) + T(k)['service']
            dist += d(cur, T(k))
            cur = T(k)
        return {'time': time, 'dist': dist, 'steps': [Obj(kind='stop', stop=tasks[i]) for i in order], 'refills': 0}
    Q, c0, Dp, R = cap, arm['load'], arm['station'], arm['refillTime']
    V = [INF] * (n + 1)
    pred = [-1] * (n + 1)
    first_refill = [0] * (n + 1)
    V[0] = 0.0
    for i in range(1, n + 1):
        if not math.isfinite(V[i - 1]):
            continue
        for refill in ((False, True) if i == 1 else (True,)):
            capv = Q if refill else c0
            load = cost = 0.0
            for j in range(i, n + 1):
                load += T(j).get('demand') or 0
                if load > capv + 1e-9:
                    break
                if j == i:
                    frm = S if i == 1 else T(i - 1)
                    cost = (tt(frm, Dp, v) + R + tt(Dp, T(i), v)) if refill else tt(frm, T(i), v)
                    cost += T(i)['service']
                else:
                    cost += tt(T(j - 1), T(j), v) + T(j)['service']
                if V[i - 1] + cost < V[j]:
                    V[j] = V[i - 1] + cost
                    pred[j] = i
                    first_refill[j] = 1 if refill else 0
    if not math.isfinite(V[n]):
        return None
    segs = []
    j = n
    while j > 0:
        i = pred[j]
        segs.append((i, j, first_refill[j]))
        j = i - 1
    segs.reverse()
    steps = []
    refills = 0
    dist = 0.0
    cur = S
    for i, jj, rf in segs:
        if rf:
            steps.append(Obj(kind='station'))
            refills += 1
            dist += d(cur, Dp)
            cur = Dp
        for k in range(i, jj + 1):
            steps.append(Obj(kind='stop', stop=T(k)))
            dist += d(cur, T(k))
            cur = T(k)
    return {'time': V[n], 'dist': dist, 'steps': steps, 'refills': refills}


def route_arm(arm, tasks):
    if not tasks:
        return {'time': 0.0, 'dist': 0.0, 'steps': [], 'refills': 0, 'order': []}
    n = len(tasks)
    D = _matrix(list(tasks) + [{'x': arm['x'], 'z': arm['z']}])
    order = nearest_neighbour(D, n)
    order = two_opt(D, n, order, 3 if n > 250 else 8)
    order = or_opt(D, n, order, 1 if n > 250 else 3)
    order = two_opt(D, n, order, 2)
    # a stop heavier than the whole bin/tank can't be split; never crash on it, just ignore capacity
    s = split(arm, tasks, order) or split(dict(arm, load=0), tasks, order) or split(dict(arm, capacity=INF), tasks, order)
    s['order'] = order
    return s


# ── multi-arm allocation ──
def principal_split(tasks, arms):
    k, n = len(arms), len(tasks)
    mx = sum(t['x'] for t in tasks) / n
    mz = sum(t['z'] for t in tasks) / n
    sxx = szz = sxz = 0.0
    for t in tasks:
        a, b = t['x'] - mx, t['z'] - mz
        sxx += a * a
        szz += b * b
        sxz += a * b
    ang = 0.5 * math.atan2(2 * sxz, sxx - szz)
    ux, uz = math.cos(ang), math.sin(ang)
    idx = sorted(range(n), key=lambda i: tasks[i]['x'] * ux + tasks[i]['z'] * uz)
    work = [tasks[i]['service'] + 3 for i in idx]
    total = sum(work) + sum(a['offset'] for a in arms)
    groups = [[] for _ in range(k)]
    arm_order = sorted(range(k), key=lambda i: arms[i]['x'] * ux + arms[i]['z'] * uz)
    g = 0
    acc = arms[arm_order[0]]['offset']
    for q in range(len(idx)):
        target = total / k * (g + 1)
        if acc + work[q] / 2 > target and g < k - 1:
            g += 1
            acc += arms[arm_order[g]]['offset']
        groups[arm_order[g]].append(tasks[idx[q]])
        acc += work[q]
    return groups


def balanced_kmeans(tasks, arms):
    k = len(arms)
    cents = [tasks[0]]
    while len(cents) < k:
        best, bd = None, -1
        for t in tasks:
            m = min(d(c, t) for c in cents)
            if m > bd:
                bd, best = m, t
        cents.append(best)
    cents = [{'x': c['x'], 'z': c['z']} for c in cents]
    groups = None
    n = len(tasks)
    for _ in range(12):
        cap = math.ceil(n / k) + 1
        pairs = []
        for i, t in enumerate(tasks):
            for j, c in enumerate(cents):
                pairs.append((d(t, c), i, j))
        pairs.sort(key=lambda p: p[0])
        assigned = [-1] * n
        counts = [0] * k
        for _, i, j in pairs:
            if assigned[i] < 0 and counts[j] < cap:
                assigned[i] = j
                counts[j] += 1
        groups = [[] for _ in range(k)]
        for i, t in enumerate(tasks):
            groups[assigned[i]].append(t)
        cents = [({'x': sum(t['x'] for t in g) / len(g), 'z': sum(t['z'] for t in g) / len(g)} if g else cents[j]) for j, g in enumerate(groups)]
    out = [None] * k
    used = set()
    order = sorted(range(k), key=lambda a: min(d(arms[a], c) for c in cents))
    for ai in order:
        best, bd = -1, INF
        for j, c in enumerate(cents):
            if j not in used and d(arms[ai], c) < bd:
                bd, best = d(arms[ai], c), j
        used.add(best)
        out[ai] = groups[best]
    return out


def greedy_insertion(tasks, arms):
    k = len(arms)
    groups = [[] for _ in range(k)]
    ends = [{'x': a['x'], 'z': a['z']} for a in arms]
    loads = [a['offset'] for a in arms]
    rem = list(range(len(tasks)))
    while rem:
        # the arm that would finish first takes its nearest remaining task
        ai = 0
        for j in range(1, k):
            if loads[j] < loads[ai]:
                ai = j
        best, bd = 0, INF
        for q, ti in enumerate(rem):
            v = d(ends[ai], tasks[ti])
            if v < bd:
                bd, best = v, q
        ti = rem.pop(best)
        groups[ai].append(tasks[ti])
        loads[ai] += tt(ends[ai], tasks[ti], arms[ai]['speed']) + tasks[ti]['service']
        ends[ai] = tasks[ti]
    return groups


def evaluate_groups(groups, arms, cache=None):
    routes = []
    for i, g in enumerate(groups):
        key = None
        if cache is not None:
            key = (i, tuple(id(t) for t in g))
            hit = cache.get(key)
            if hit is not None:
                routes.append(hit)
                continue
        r = route_arm(arms[i], g)
        if cache is not None:
            cache[key] = r
        routes.append(r)
    makespan = max(arms[i]['offset'] + r['time'] for i, r in enumerate(routes))
    return {'routes': routes, 'makespan': makespan}


def improve(groups, arms, ev, budget=24, cache=None):
    """Relocate tasks off the critical arm while it shortens the makespan."""
    for _ in range(budget):
        fin = [arms[i]['offset'] + r['time'] for i, r in enumerate(ev['routes'])]
        crit = fin.index(max(fin))
        best = None
        cg = groups[crit]
        if len(cg) < 2:
            break
        cands = []
        for ti in range(len(cg)):
            for j in range(len(arms)):
                if j == crit:
                    continue
                near = min(d(t, cg[ti]) for t in groups[j]) if groups[j] else d(arms[j], cg[ti])
                cands.append((near, ti, j))
        cands.sort(key=lambda c: c[0])
        for _, ti, j in cands[:6]:
            ng = [list(g) for g in groups]
            ng[j].append(ng[crit].pop(ti))
            e2 = evaluate_groups(ng, arms, cache)
            if e2['makespan'] < ev['makespan'] - 0.5 and (best is None or e2['makespan'] < best['ev']['makespan']):
                best = {'groups': ng, 'ev': e2}
        if best is None:
            break
        groups, ev = best['groups'], best['ev']
    return {'groups': groups, 'ev': ev}


def row_order_route(arm, tasks):
    """Baseline: row-by-row (boustrophedon) visiting order, refill when empty."""
    rows = {}
    for t in tasks:
        key = math.floor(t['z'] * 2 + 0.5)
        rows.setdefault(key, []).append(t)
    ordered = []
    for r, k in enumerate(sorted(rows)):
        row = sorted(rows[k], key=lambda t: t['x'])
        if r % 2:
            row.reverse()
        ordered += row
    cap = arm.get('capacity')
    time = 0.0
    cur = {'x': arm['x'], 'z': arm['z']}
    load = arm['load'] if cap is not None and math.isfinite(cap) else INF
    for t in ordered:
        if (t.get('demand') or 0) > load + 1e-9:
            time += tt(cur, arm['station'], arm['speed']) + arm['refillTime']
            cur = arm['station']
            load = cap
        time += tt(cur, t, arm['speed']) + t['service']
        cur = t
        load -= t.get('demand') or 0
    return time


def baselines(tasks, arms):
    single = row_order_route(arms[0], tasks)
    naive = single
    if len(arms) > 1:
        zs = sorted(t['z'] for t in tasks)
        mid = zs[len(zs) // 2]
        parts = [[] for _ in arms]
        for t in tasks:
            parts[0 if t['z'] < mid else min(1, len(arms) - 1)].append(t)
        naive = max(a['offset'] + row_order_route(a, parts[i] if i < len(parts) else []) for i, a in enumerate(arms))
    return {'single': single, 'naive': naive}


# ── reach clustering: one trolley stop serves every site its arm can reach ──
def cluster_stops(tasks, arm, max_per):
    reach = arm.get('reach') or 0
    capv = arm.get('capacity')
    cap = capv if capv and math.isfinite(capv) else INF
    srt = sorted(tasks, key=lambda t: (t['z'], t['x']))
    used = set()
    stops = []
    for t in srt:
        if id(t) in used:
            continue
        group = [t]
        used.add(id(t))
        load = t.get('demand') or 0
        if reach > 0.3 and max_per > 1:
            tg = t.get('group') if t.get('group') is not None else 0
            cands = [c for c in srt if id(c) not in used and (c.get('group') if c.get('group') is not None else 0) == tg and d(c, t) <= 2 * reach]
            cands.sort(key=lambda c: d(c, t))
            for c in cands:
                if len(group) >= max_per:
                    break
                if load + (c.get('demand') or 0) > cap + 1e-9:
                    continue  # a stop must fit in one bin/tank/magazine
                g = group + [c]
                cx = sum(q['x'] for q in g) / len(g)
                cz = sum(q['z'] for q in g) / len(g)
                if all(math.sqrt((q['x'] - cx) ** 2 + (q['z'] - cz) ** 2) <= reach + 1e-6 for q in g):
                    group.append(c)
                    used.add(id(c))
                    load += c.get('demand') or 0
        stops.append(make_stop(group, arm))
    return stops


def make_stop(group, arm):
    cx = sum(q['x'] for q in group) / len(group)
    cz = sum(q['z'] for q in group) / len(group)
    return Obj(x=cx, z=cz, tasks=group, demand=sum((q.get('demand') or 0) for q in group),
               service=sum(q['service'] for q in group) + (len(group) - 1) * (arm.get('reachMove') or 1.5) + (arm.get('lowerTime') or 2.5) + (arm.get('raiseTime') or 2.5))


def plan(tasks, arms, opts=None):
    """tasks: [{id, x, z, service, demand, group}]; arms: [{id, x, z, speed, reach, lowerTime, raiseTime, reachMove,
    capacity, load, station, refillTime, offset}]"""
    opts = opts or {}
    if not tasks:
        return Obj(strategy='none', makespan=0, perArm=[Obj(armId=a['id'], steps=[], time=0, dist=0, tasks=0, stops=0, refills=0) for a in arms],
                   baseline={'single': 0, 'naive': 0}, speedupSingle=1, speedupNaive=1, stops=0)
    max_per = opts.get('maxPerStop') or 4
    proto = arms[0]
    for a in arms[1:]:
        if not ((proto.get('reach') or 0) <= (a.get('reach') or 0)):
            proto = a
    units = [make_stop([t], proto) for t in tasks] if opts.get('cluster') is False else cluster_stops(tasks, proto, max_per)
    cache = {}
    if len(arms) == 1:
        strategies = [('route', lambda: [list(units)])]
    else:
        strategies = [('principal-axis split', lambda: principal_split(units, arms)), ('balanced k-means', lambda: balanced_kmeans(units, arms)),
                      ('greedy earliest-finish', lambda: greedy_insertion(units, arms))]
    best = None
    for name, fn in strategies:
        groups = fn()
        ev = evaluate_groups(groups, arms, cache)
        if best is None or ev['makespan'] < best['ev']['makespan']:
            best = {'name': name, 'groups': groups, 'ev': ev}
    label = best['name']
    if len(arms) > 1 and len(units) <= (opts.get('improveMax') or 160):
        before = best['ev']['makespan']
        imp = improve(best['groups'], arms, best['ev'], cache=cache)
        if imp['ev']['makespan'] < before - 0.5:
            best['groups'], best['ev'] = imp['groups'], imp['ev']
            label += ' + relocate'
    bl = {'single': math.nan, 'naive': math.nan}
    if opts.get('baselines') is not False:
        bl = baselines([make_stop([t], proto) for t in tasks], arms)
    mk = best['ev']['makespan']
    finite = any(a.get('capacity') is not None and math.isfinite(a['capacity']) for a in arms)
    return Obj(
        strategy=f"reach clusters ({len(units)} stops for {len(tasks)} sites) · {label} · NN → 2-opt → Or-opt" + (' → refill split' if finite else ''),
        makespan=mk, stops=len(units),
        perArm=[Obj(armId=arms[i]['id'], steps=r['steps'], time=r['time'], dist=r['dist'], tasks=sum(len(u['tasks']) for u in best['groups'][i]),
                    stops=len(best['groups'][i]), refills=r['refills']) for i, r in enumerate(best['ev']['routes'])],
        baseline=bl, speedupSingle=bl['single'] / mk if mk else math.inf, speedupNaive=bl['naive'] / mk if mk else math.inf,
    )
