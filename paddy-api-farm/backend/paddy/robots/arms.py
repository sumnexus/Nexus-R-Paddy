# Copied from the Agri Robotics Playground's Python port (agriplay/arms.py) for the Paddy API Farm v2.
# Changes (each marked PADDY below): the four arms' roles, titles, magazines and stations for the paddy farm; three
# new work phases (valve, spread, puddle) with their power, category and effect; their status texts; the import of
# clamp. The Arm state machine and the traffic manager (reservation, velocity sampling, make-way, detours, unstick,
# safety monitor) are unchanged.
"""The four overhead robotic arms and the traffic manager.

Each arm hangs from a trolley that rides the cube frame's top rails, so it can travel in any
direction over the whole farm without touching the crops. Execution: route steps (stops /
station visits) → phases (lower, probe, dig…).

Traffic rules (no collisions, no deadlocks):
 1. Goal reservation — an arm only departs for a stop whose keep-out circle is free of other
    arms' goals and stationary arms; otherwise it takes the next free stop in its route
    (deferral) or holds.
 2. Prioritised velocity sampling with a 2.5 s look-ahead. Higher-priority arms treat
    lower-priority movers as obstacles padded by their stopping distance; if nothing is safe an
    arm brakes, which is always safe.
 3. Make-way — a lower-priority arm standing in a higher-priority arm's corridor moves to the
    nearest free spot outside it and waits there.
 4. Detour waypoints around working/stopped arms; stalls >15 s are broken by deferring the stop
    or side-stepping.
"""
from __future__ import annotations

import math

import numpy as np

from ..core.util import clamp

_sqrt, _cos, _sin, _atan2 = math.sqrt, math.cos, math.sin, math.atan2

# PADDY: the same four machines with paddy jobs. Arm 1 keeps Planter A's hardware (seedling magazine of 36 hills, one
# field); Arm 2 is Planter B's hardware with a 25 kg granule hopper and a water hose instead of the seed magazine; Arm 3
# is the pest guard (5 L tank); Arm 4 the bigger harvester (40 kg bin), which also carries the puddling tool.
DEFS = [
    dict(id=1, name='Arm 1', title='Planting arm', role='planter', color='#2f7ddf', radius=0.85, speed=1.2, accel=0.9, reach=0.8, lowerTime=2.5,
         raiseTime=2.5, reachMove=1.5, scale=1.0, capKind='seed', capacity=36, refillTime=18, station='seed', priority=1.1, movePower=220),
    dict(id=2, name='Arm 2', title='Watering arm', role='waterer', color='#11a39b', radius=0.85, speed=1.2, accel=0.9, reach=0.8, lowerTime=2.5,
         raiseTime=2.5, reachMove=1.5, scale=1.0, capKind='hopper', capacity=25, refillTime=18, station='store', priority=1.0, movePower=220),
    dict(id=3, name='Arm 3', title='Pest arm', role='protector', color='#e0662c', radius=0.85, speed=1.3, accel=1.0, reach=0.8, lowerTime=2.0,
         raiseTime=2.0, reachMove=1.2, scale=1.0, capKind='tank', capacity=5, refillTime=20, station='chem', priority=2.0, movePower=210),
    dict(id=4, name='Arm 4', title='Harvest arm', role='harvester', color='#8f5bd6', radius=1.3, speed=0.8, accel=0.6, reach=1.15, lowerTime=3.5,
         raiseTime=3.5, reachMove=2.0, scale=1.55, capKind='bin', capacity=40, refillTime=15, station='chute', priority=3.0, movePower=380),
]
# electrical power by phase (W); the harvester draws 1.5×
POWER = dict(idle=25, wait=30, move=0, lower=160, raise_=160, reach=120, probe=60, amend=140, dig=450, seed=120, settle=80, water=80, inspect=45,
             spray=150, pick=220, cut=520, uproot=480, refill=60, unload=140,
             valve=120, spread=100, puddle=450)  # PADDY: valve (turn a gate and set its float), spread (granules), puddle (rotary tool)
POWER['raise'] = POWER.pop('raise_')
CATEGORY = dict(probe='plant', amend='plant', dig='plant', seed='plant', settle='plant', water='water', inspect='spray', spray='spray',
                valve='water', spread='fertilise', puddle='prep',  # PADDY
                pick='harvest', cut='harvest', uproot='clear', refill='logistics', unload='logistics', lower='mast', reach='mast',
                idle='standby', wait='standby', move='move')
CATEGORY['raise'] = 'mast'
EFFECT = dict(dig='dust', uproot='dust', settle='water', water='water', spray='mist', seed='seed', amend='granules', pick='harvest', cut='harvest',
              probe='scan', inspect='scan', spread='granules', puddle='splash', valve='water')  # PADDY: last three
MARGIN, HORIZON, DECEL = 0.25, 2.5, 2.5
MOVING = ('travel', 'return', 'tostation', 'yield')


def sep(a, b):
    return a.radius + b.radius + MARGIN


class P:
    """A point or vector on the farm plane."""
    __slots__ = ('x', 'z')

    def __init__(self, x=0.0, z=0.0):
        self.x = x
        self.z = z

    def __getitem__(self, k):
        return getattr(self, k)

    def get(self, k, d=None):
        return getattr(self, k, d)

    def to_dict(self):
        return {'x': self.x, 'z': self.z}


def _pt(o):
    return o if isinstance(o, P) else P(o['x'], o['z'])


class Phase:
    __slots__ = ('name', 'dur', 'mm')

    def __init__(self, name, dur, mm=None):
        self.name = name
        self.dur = dur
        self.mm = mm


class Arm:
    def __init__(self, d, dock, station_pos):
        for k, v in d.items():
            setattr(self, k, v)
        self.defn = d
        self.dock = P(dock['x'], dock['z'])
        self.stationPos = station_pos
        self.pos = P(dock['x'], dock['z'])
        self.vel = P(0.0, 0.0)
        self.heading = math.pi / 2
        self.prev = dict(x=dock['x'], z=dock['z'], mast=0.0)
        self.vis = dict(x=dock['x'], z=dock['z'], mast=0.0)
        self.mast = 0.0  # 0 raised … 1 lowered
        self.state = 'idle'  # idle | travel | tostation | return | yield | work | station
        self.steps = []
        self.step = None
        self.taskQueue = []
        self.task = None
        self.phases = []
        self.phase = None
        self.phaseT = 0.0
        self.goal = None
        self.waypoints = []
        self.evade = None
        self.evadeT = 0.0
        self.load = d['capacity']  # remaining capacity units (seed cartridges / litres); bin uses binKg
        self.binKg = 0.0
        self.waitT = 0.0
        self.holdT = 0.0
        self.holding = False
        self.blocked = None
        self.toolTarget = dict(x=dock['x'], y=0.0, z=dock['z'])
        self.effect = None
        self.workFrac = 0.0
        self.stats = dict(tasks=0, stops=0, dist=0.0, busyT=0.0, waitT=0.0, energyJ=0.0, byCat={}, refills=0, deferrals=0, yields=0)
        self.planEndT = 0.0
        self._raised = False
        self._didTask = False

    @property
    def moving(self):
        return self.state in MOVING

    @property
    def working(self):
        return self.state == 'work' or self.state == 'station'

    @property
    def cap_frac(self):
        return self.binKg / self.capacity if self.capKind == 'bin' else self.load / self.capacity

    def has_work(self):
        return bool(self.step or self.steps)

    def remaining_stops(self):
        return [s for s in self.steps if s.kind == 'stop']

    def set_route(self, steps, now, est_time):
        self.steps = list(steps)
        self.planEndT = now + (est_time or 0)

    def current_goal(self):
        if self.evade is not None:
            return self.evade
        if self.waypoints:
            return self.waypoints[0]
        return self.goal

    def claim(self):
        """The spot this arm has claimed: where it works, or where it is heading to work."""
        if self.state == 'work' or self.state == 'station':
            return self.pos
        if (self.state == 'travel' or self.state == 'tostation') and self.goal is not None:
            return self.goal
        return None

    def update(self, dt, world):
        if self.state != 'idle':
            self.stats['busyT'] += dt
        # mast animation follows the lower/raise phases; otherwise it retracts
        ph = self.phase
        if ph is not None and (ph.name == 'lower' or ph.name == 'raise'):
            k = clamp(self.phaseT / ph.dur, 0, 1)
            self.mast = k if ph.name == 'lower' else 1 - k
        elif self.state != 'work':
            self.mast = max(0.0, self.mast - dt / (self.raiseTime or 2.5))
        st = self.state
        if st == 'idle':
            r = self._next_step(world)
            if r == 'hold':
                self.holdT += dt
                self.stats['waitT'] += dt
                self._energy(world, 'wait', dt)
                # don't block the field while waiting: after a few seconds, pull back toward the dock
                if self.holdT > 6 and _sqrt((self.pos.x - self.dock.x) ** 2 + (self.pos.z - self.dock.z) ** 2) > 0.05:
                    self.state = 'return'
                    self.goal = P(self.dock.x, self.dock.z)
                    self.waypoints = []
                return
            self.holdT = 0.0
            if r:
                return
            d_home = _sqrt((self.pos.x - self.dock.x) ** 2 + (self.pos.z - self.dock.z) ** 2)
            if d_home > 0.05 and self.mast <= 0.001:
                self.state = 'return'
                self.goal = P(self.dock.x, self.dock.z)
                self.waypoints = []
            else:
                self._energy(world, 'idle', dt)
        elif st == 'yield':
            if self.evade is None:
                self.state = 'idle'
        elif st in ('travel', 'tostation', 'return'):
            if self._arrived():
                self.vel.x = self.vel.z = 0.0
                if st == 'return':
                    self.state = 'idle'
                    self.goal = None
                    return
                if st == 'tostation':
                    self.state = 'station'
                    self.phases = [Phase('unload' if self.capKind == 'bin' else 'refill', self.refillTime)]
                    self._begin_phase()
                    return
                self.state = 'work'
                self._start_stop(world)
            elif st == 'return' and self.steps:
                r = self._next_step(world)  # start new work as soon as a spot frees up
                if r is True:
                    self.holdT = 0.0
        elif st == 'work' or st == 'station':
            self._run_phases(dt, world)

    def _arrived(self):
        if self.evade is not None:
            return False
        g = self.waypoints[0] if self.waypoints else self.goal
        if g is None:
            return True
        d = _sqrt((self.pos.x - g.x) ** 2 + (self.pos.z - g.z) ** 2)
        v = _sqrt(self.vel.x ** 2 + self.vel.z ** 2)
        if self.waypoints and d < 0.35:
            self.waypoints.pop(0)
            return False
        return not self.waypoints and d < 0.03 and v < 0.08

    def _next_step(self, world):
        """Start the next step if its spot is free. Returns True (started), 'hold' or False (no steps)."""
        while True:
            if not self.steps:
                return False
            T = world.traffic
            pick = -1
            for q in range(min(len(self.steps), 8)):
                s = self.steps[q]
                if s.kind == 'station':
                    if q == 0 and T.goal_free(self, self.stationPos['x'], self.stationPos['z']):
                        pick = 0
                    break
                if T.goal_free(self, s.stop['x'], s.stop['z']):
                    pick = q
                    break
            if pick < 0:
                self.holding = True
                return 'hold'
            self.holding = False
            if pick > 0:
                s = self.steps.pop(pick)
                self.steps.insert(0, s)
                self.stats['deferrals'] += 1
            self.step = self.steps.pop(0)
            if self.step.kind == 'station':
                self.state = 'tostation'
                self.goal = P(self.stationPos['x'], self.stationPos['z'])
            else:
                # close tasks that became moot while queued, so the farm can schedule that square again later
                keep = []
                for t in self.step.stop['tasks']:
                    if world.task_still_valid(t):
                        keep.append(t)
                    else:
                        world.on_task_done(self, t, True)
                self.step.stop['tasks'] = keep
                if not keep:
                    world.on_stop_skipped(self, self.step.stop)
                    self.step = None
                    continue
                self.state = 'travel'
                self.goal = P(self.step.stop['x'], self.step.stop['z'])
            self.waypoints = []
            self.waitT = 0.0
            world.on_step_start(self, self.step)
            return True

    def _start_stop(self, world):
        self.taskQueue = list(self.step.stop['tasks'])
        self.phases = [Phase('lower', self.lowerTime)]
        self.task = None
        self._raised = False
        self._begin_phase()

    def _begin_phase(self):
        self.phase = self.phases.pop(0) if self.phases else None
        self.phaseT = 0.0
        self.workFrac = 0.0
        self.effect = EFFECT.get(self.phase.name) if self.phase is not None else None

    def _run_phases(self, dt, world):
        if self.phase is None:
            if self.state == 'station':
                world.on_station_done(self)
                self.stats['refills'] += 1
                self.state = 'idle'
                self.step = None
                return
            if self.taskQueue:
                prev = self.task
                self.task = self.taskQueue.pop(0)
                if not world.task_still_valid(self.task):
                    world.on_task_done(self, self.task, True)
                    self.task = None
                    return
                self.phases = ([Phase('reach', self.reachMove)] if self._didTask else []) + world.phases_for(self, self.task)
                self._didTask = True
                world.on_task_start(self, self.task)
                self.toolTarget = dict(x=self.task['x'], y=world.tool_height(self.task), z=self.task['z'])
                self._begin_phase()
                return
            if self.task is not None:
                world.on_task_done(self, self.task, False)
                self.stats['tasks'] += 1
                self.task = None
            if not self._raised:
                self._raised = True
                self.phases = [Phase('raise', self.raiseTime)]
                self._begin_phase()
                return
            self._raised = False
            self._didTask = False
            self.stats['stops'] += 1
            st = self.step
            self.step = None
            self.state = 'idle'
            self.effect = None
            world.on_stop_done(self, st.stop)
            return
        ph = self.phase
        self.phaseT += dt
        self.workFrac = clamp(self.phaseT / ph.dur, 0, 1)
        self._energy(world, ph.name, dt)
        if (ph.name == 'water' or ph.name == 'settle') and self.task is not None:
            world.on_watering(self, self.task, dt, ph)
        if self.phaseT >= ph.dur:
            done = ph
            if self.task is not None and done.name not in ('lower', 'raise', 'reach'):
                extra = world.on_phase_done(self, self.task, done)
                if extra is False:
                    self.phases = [p for p in self.phases if p.name == 'raise']  # task aborted
                elif extra:
                    self.phases = list(extra) + self.phases
                if not self.phases:
                    world.on_task_done(self, self.task, extra is False)
                    self.stats['tasks'] += 1
                    self.task = None
            self._begin_phase()

    def _energy(self, world, phase_name, dt):
        w = POWER.get(phase_name, 50) * (1.5 if self.role == 'harvester' else 1)
        self.stats['energyJ'] += w * dt
        cat = 'harvest' if phase_name == 'inspect' and self.role == 'harvester' else CATEGORY.get(phase_name, 'other')
        bc = self.stats['byCat']
        bc[cat] = bc.get(cat, 0) + w * dt
        world.add_load(cat, w, dt)

    def _move_energy(self, world, speed, dt):
        w = self.movePower * (0.25 + 0.75 * speed / self.speed) + 30
        self.stats['energyJ'] += w * dt
        bc = self.stats['byCat']
        bc['move'] = bc.get('move', 0) + w * dt
        world.add_load('move', w, dt)

    def status_text(self):
        if self.state == 'work' and self.phase is not None:
            return dict(lower='Lowering', raise_='Raising', reach='Reaching', probe='Testing soil', amend='Amending soil', dig='Digging',
                        seed='Planting', settle='Watering in', water='Watering', inspect='Inspecting', spray='Spraying', pick='Picking',
                        cut='Harvesting', uproot='Clearing', valve='Setting the water gate', spread='Spreading fertiliser',  # PADDY: last three
                        puddle='Puddling').get('raise_' if self.phase.name == 'raise' else self.phase.name, 'Working')
        if self.state == 'yield' or self.evade is not None:
            return 'Making way'
        if self.holding:
            return 'Waiting for a free spot'
        if self.moving and self.waitT > 1.5:
            return 'Waiting (traffic)'
        return dict(idle='Parked', travel='Moving', tostation='To unloading chute' if self.capKind == 'bin' else 'To refill station',
                    station='Unloading' if self.capKind == 'bin' else 'Refilling').get(self.state if self.state != 'return' else 'x',
                                                                                       'Returning to dock' if self.state == 'return' else self.state)


# ── geometry helpers ──
def min_dist_over_horizon(px, pz, wx, wz, tau):
    ww = wx * wx + wz * wz
    t = clamp(-(px * wx + pz * wz) / ww if ww > 1e-9 else 0.0, 0, tau)
    return _sqrt((px + wx * t) ** 2 + (pz + wz * t) ** 2)


def point_seg_dist(px, pz, ax, az, bx, bz):
    vx, vz = bx - ax, bz - az
    L2 = vx * vx + vz * vz
    t = clamp(((px - ax) * vx + (pz - az) * vz) / L2, 0, 1) if L2 > 1e-12 else 0.0
    return _sqrt((px - (ax + vx * t)) ** 2 + (pz - (az + vz * t)) ** 2)


def seg_seg_dist(a, b, c, d):
    """Conservative: min of endpoint-to-segment distances (exact unless segments cross)."""
    def cross(p, q, r):
        return (q.x - p.x) * (r.z - p.z) - (q.z - p.z) * (r.x - p.x)
    d1, d2, d3, d4 = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(point_seg_dist(a.x, a.z, c.x, c.z, d.x, d.z), point_seg_dist(b.x, b.z, c.x, c.z, d.x, d.z),
               point_seg_dist(c.x, c.z, a.x, a.z, b.x, b.z), point_seg_dist(d.x, d.z, a.x, a.z, b.x, b.z))


def approach(cx, cz, tx, tz, dt, accel):
    """Velocity (cx, cz) moved toward the target velocity (tx, tz) by the arm's acceleration (or braking)."""
    dx, dz = tx - cx, tz - cz
    m = _sqrt(dx * dx + dz * dz)
    speeding = _sqrt(tx * tx + tz * tz) > _sqrt(cx * cx + cz * cz)
    a = (accel if speeding else DECEL) * dt
    if m <= a:
        return tx, tz
    return cx + dx / m * a, cz + dz / m * a


_ANGLES = (0, 0.35, -0.35, 0.7, -0.7, 1.05, -1.05, 1.45, -1.45, 1.9, -1.9, 2.5, -2.5, math.pi)
# candidate velocities: standing still, then every heading offset at full, 55 % and 25 % of the desired speed
_CAND_DA = np.array([0.0] + [da for s in (1, 0.55, 0.25) for da in _ANGLES])
_CAND_S = np.array([0.0] + [s for s in (1, 0.55, 0.25) for da in _ANGLES])


class _Seg:
    __slots__ = ('H', 'a', 'b', 'need')

    def __init__(self, H, a, b, need):
        self.H, self.a, self.b, self.need = H, a, b, need


class Traffic:
    def __init__(self, bounds):
        self.bounds = bounds
        self.arms = []
        self.minSep = math.inf
        self.minSepPair = None
        self.violations = 0
        self.nearMisses = 0
        self.detours = 0
        self.yields = 0
        self.holds = 0
        b = bounds
        self._b = (b['x0'], b['x1'], b['z0'], b['z1'])

    def in_bounds(self, x, z):
        x0, x1, z0, z1 = self._b
        return x0 <= x <= x1 and z0 <= z <= z1

    def goal_free(self, arm, x, z):
        """Is (x, z) free for ``arm`` to claim as its next work spot?"""
        for o in self.arms:
            if o is arm:
                continue
            need = sep(arm, o) + 0.05
            c = o.claim()
            if c is not None and _sqrt((c.x - x) ** 2 + (c.z - z) ** 2) < need:
                return False
            if not o.moving and _sqrt((o.pos.x - x) ** 2 + (o.pos.z - z) ** 2) < need:
                return False
            if o.evade is not None and _sqrt((o.evade.x - x) ** 2 + (o.evade.z - z) ** 2) < need:
                return False
        return True

    def step(self, arms, dt, world):
        self.arms = arms
        now = world.t
        self._make_way(arms, now)
        moving = [a for a in arms if a.moving and a.current_goal() is not None]
        for a in moving:
            self._detour(a, arms)
        order = sorted(moving, key=lambda a: -a.priority)
        chosen = {}
        for a in order:
            g = a.current_goal()
            dx, dz = g.x - a.pos.x, g.z - a.pos.z
            dist = _sqrt(dx * dx + dz * dz)
            ux, uz = (dx / dist, dz / dist) if dist > 1e-6 else (0.0, 0.0)
            final_leg = not a.waypoints and a.evade is None
            v_des = min(a.speed, _sqrt(2 * DECEL * 0.6 * dist) if final_leg else a.speed, dist / dt)
            base = _atan2(uz, ux)
            # fast path: nobody within reach of our look-ahead → go straight at the desired velocity
            reach_r = a.speed * HORIZON * 2 + 3.5
            near = False
            for o in arms:
                if o is not a and _sqrt((o.pos.x - a.pos.x) ** 2 + (o.pos.z - a.pos.z) ** 2) < reach_r:
                    near = True
                    break
            if not near:
                vx, vz = approach(a.vel.x, a.vel.z, ux * v_des, uz * v_des, dt, a.accel)
                if self.in_bounds(a.pos.x + vx * dt, a.pos.z + vz * dt):
                    chosen[a] = (vx, vz)
                    continue
            v = self._sample(a, arms, chosen, ux, uz, v_des, base, dt)
            if v is None:
                # nothing safe: brake (others already budget for our stopping distance)
                v = approach(a.vel.x, a.vel.z, 0.0, 0.0, dt, a.accel)
                for o in arms:  # never expected: if already too close, back straight away
                    if o is a:
                        continue
                    px, pz = a.pos.x - o.pos.x, a.pos.z - o.pos.z
                    L = _sqrt(px * px + pz * pz)
                    if sep(a, o) - 0.02 > L > 1e-6:
                        v = (px / L * 0.3, pz / L * 0.3)
                        break
            chosen[a] = v
        x0, x1, z0, z1 = self._b
        for a in moving:
            vx, vz = chosen[a]
            a.vel.x, a.vel.z = vx, vz
            sp = _sqrt(vx * vx + vz * vz)
            a.pos.x = clamp(a.pos.x + vx * dt, x0, x1)
            a.pos.z = clamp(a.pos.z + vz * dt, z0, z1)
            a.stats['dist'] += sp * dt
            if sp > 0.05:
                a.heading = _atan2(vz, vx)
            g = a.current_goal()
            dg = _sqrt((g.x - a.pos.x) ** 2 + (g.z - a.pos.z) ** 2) if g is not None else 0.0
            if a.evade is not None and dg < 0.05:
                a._move_energy(world, 0, dt)  # waiting at the side
                continue
            if sp > 0.12 or dg < 0.05:
                a.waitT = max(0.0, a.waitT - dt * 2)
                a._move_energy(world, sp, dt)
                a.blocked = None
            else:
                a.waitT += dt
                a.stats['waitT'] += dt
                a._move_energy(world, 0, dt)
                a.blocked = 'traffic'
            if a.waitT > 15 and a.evade is None:
                self._unstick(a, arms, world)
        for a in arms:
            if not a.moving:
                a.vel.x = a.vel.z = 0.0
        # safety monitor
        n = len(arms)
        for i in range(n):
            a = arms[i]
            for j in range(i + 1, n):
                b = arms[j]
                dd = _sqrt((a.pos.x - b.pos.x) ** 2 + (a.pos.z - b.pos.z) ** 2)
                need = a.radius + b.radius
                if dd - need < self.minSep:
                    self.minSep = dd - need
                    self.minSepPair = [a.id, b.id]
                if dd < need - 1e-3:
                    self.violations += 1
                elif dd < need + MARGIN * 0.5:
                    self.nearMisses += 1

    def _sample(self, a, arms, chosen, ux, uz, v_des, base, dt):
        """Prioritised velocity sampling: every candidate velocity at once; the best safe one (or None)."""
        if v_des > 1e-4:
            ang = base + _CAND_DA[1:]
            sc = _CAND_S[1:] * v_des
            cx = np.concatenate(([0.0], np.cos(ang) * sc))
            cz = np.concatenate(([0.0], np.sin(ang) * sc))
            da = _CAND_DA
        else:
            cx = cz = da = np.zeros(1)
        avx, avz = a.vel.x, a.vel.z
        dx, dz = cx - avx, cz - avz
        m = np.sqrt(dx * dx + dz * dz)
        speeding = np.sqrt(cx * cx + cz * cz) > _sqrt(avx * avx + avz * avz)
        acc = np.where(speeding, a.accel, DECEL) * dt
        with np.errstate(divide='ignore', invalid='ignore'):
            vx = np.where(m <= acc, cx, avx + dx / m * acc)
            vz = np.where(m <= acc, cz, avz + dz / m * acc)
        x0, x1, z0, z1 = self._b
        nx, nz = a.pos.x + vx * dt, a.pos.z + vz * dt
        safe = (nx >= x0) & (nx <= x1) & (nz >= z0) & (nz <= z1)
        # PADDY speed-up, same results: no candidate is faster than max(|current velocity|, v_des), so an arm farther
        # away than need + (that speed + its own speed) × the horizon can't make any candidate unsafe; skip it
        vmax = max(_sqrt(avx * avx + avz * avz), v_des)
        for o in arms:
            if o is a:
                continue
            ovx = ovz = 0.0
            pad = 0.0
            ch = chosen.get(o)
            if ch is not None:
                ovx, ovz = ch
            elif o.moving:
                sp = _sqrt(o.vel.x ** 2 + o.vel.z ** 2)
                pad = sp * sp / (2 * DECEL) + 0.05
            px, pz = a.pos.x - o.pos.x, a.pos.z - o.pos.z
            need = sep(a, o) + pad
            d0 = _sqrt(px * px + pz * pz)
            if d0 - (vmax + _sqrt(ovx * ovx + ovz * ovz)) * HORIZON > need + 1e-6:
                continue
            wx, wz = vx - ovx, vz - ovz
            if d0 < need:
                safe &= ~(px * wx + pz * wz < -1e-9)
            else:
                ww = wx * wx + wz * wz
                with np.errstate(divide='ignore', invalid='ignore'):
                    t = np.clip(np.where(ww > 1e-9, -(px * wx + pz * wz) / ww, 0.0), 0, HORIZON)
                ex, ez = px + wx * t, pz + wz * t
                safe &= ~(np.sqrt(ex * ex + ez * ez) < need)
            if not safe.any():
                return None
        if not safe.any():
            return None
        ex, ez = vx - ux * v_des, vz - uz * v_des
        score = vx * ux + vz * uz - 0.35 * np.sqrt(ex * ex + ez * ez) - 0.05 * np.abs(da)
        score = np.where(safe, score, -np.inf)
        b = int(np.argmax(score))
        return (float(vx[b]), float(vz[b]))

    def _safe(self, a, vx, vz, arms, chosen):
        for o in arms:
            if o is a:
                continue
            ovx = ovz = 0.0
            pad = 0.0
            ch = chosen.get(o)
            if ch is not None:
                ovx, ovz = ch
            elif o.moving:
                sp = _sqrt(o.vel.x ** 2 + o.vel.z ** 2)
                pad = sp * sp / (2 * DECEL) + 0.05
            px, pz = a.pos.x - o.pos.x, a.pos.z - o.pos.z
            wx, wz = vx - ovx, vz - ovz
            need = sep(a, o) + pad
            if _sqrt(px * px + pz * pz) < need:
                if px * wx + pz * wz < -1e-9:
                    return False
                continue
            if min_dist_over_horizon(px, pz, wx, wz, HORIZON) < need:
                return False
        return True

    def _corridors(self, L, arms):
        """Corridors of higher-priority movers: their position → next target → final goal."""
        segs = []
        for H in arms:
            if H is L or not H.moving or H.priority <= L.priority:
                continue
            pts = [P(H.pos.x, H.pos.z)]
            if H.evade is not None:
                pts.append(H.evade)
            pts += H.waypoints
            if H.goal is not None and H.state != 'yield':
                pts.append(H.goal)
            need = sep(L, H) + 0.3
            for k in range(len(pts) - 1):
                segs.append(_Seg(H, pts[k], pts[k + 1], need))
            if len(pts) == 1:
                segs.append(_Seg(H, pts[0], pts[0], need))
        return segs

    def _spot_valid(self, L, x, z, arms, segs):
        if not self.in_bounds(x, z):
            return False
        for o in arms:
            if o is L:
                continue
            need = sep(L, o) + 0.1
            if _sqrt((o.pos.x - x) ** 2 + (o.pos.z - z) ** 2) < need:
                return False
            c = o.claim()
            if c is not None and _sqrt((c.x - x) ** 2 + (c.z - z) ** 2) < need:
                return False
            if o.evade is not None and _sqrt((o.evade.x - x) ** 2 + (o.evade.z - z) ** 2) < need:
                return False
        for s in segs:
            if point_seg_dist(x, z, s.a.x, s.a.z, s.b.x, s.b.z) < s.need + 0.25:
                return False
        return True

    def _find_spot(self, L, arms, segs):
        best, bd = None, math.inf
        for r in (0.8, 1.6, 2.4, 3.3, 4.4, 5.6):
            for k in range(16):
                ang = k / 16 * math.pi * 2
                x, z = L.pos.x + _cos(ang) * r, L.pos.z + _sin(ang) * r
                if not self._spot_valid(L, x, z, arms, segs):
                    continue
                g = L.goal if L.goal is not None else L.dock  # prefer spots toward our own goal
                cost = r + 0.15 * _sqrt((x - g.x) ** 2 + (z - g.z) ** 2)
                if cost < bd:
                    bd, best = cost, P(x, z)
            if best is not None:
                break
        return best

    def _make_way(self, arms, now):
        for L in arms:
            if L.state == 'work' or L.state == 'station':
                L.evade = None
                continue
            segs = self._corridors(L, arms)
            in_way = any(point_seg_dist(L.pos.x, L.pos.z, s.a.x, s.a.z, s.b.x, s.b.z) < s.need for s in segs)
            if L.evade is not None:
                # resume once nobody's corridor covers us or our own path ahead
                my_path = (P(L.pos.x, L.pos.z), L.goal) if L.goal is not None else None
                path_clear = my_path is None or not any(seg_seg_dist(my_path[0], my_path[1], s.a, s.b) < s.need - 0.25 for s in segs)
                if (not in_way and path_clear) or now - L.evadeT > 90:
                    L.evade = None
                    if L.state == 'yield':
                        L.state = 'idle'
                    continue
                if self._spot_valid(L, L.evade.x, L.evade.z, arms, segs):
                    continue
            elif not in_way:
                continue
            spot = self._find_spot(L, arms, segs)
            if spot is not None:
                if L.evade is None:
                    self.yields += 1
                    L.stats['yields'] += 1
                    L.evadeT = now
                L.evade = spot
                if L.state == 'idle':
                    L.state = 'yield'

    def _detour(self, a, arms):
        g = a.goal
        if g is None or a.waypoints or a.evade is not None:
            return
        dx, dz = g.x - a.pos.x, g.z - a.pos.z
        L = _sqrt(dx * dx + dz * dz)
        if L < 0.5:
            return
        ux, uz = dx / L, dz / L
        best_obs, best_along = None, math.inf
        for o in arms:
            if o is a:
                continue
            stopped = not o.moving or (_sqrt(o.vel.x ** 2 + o.vel.z ** 2) < 0.1 and o.evade is None and o.priority > a.priority)
            if not stopped:
                continue
            need = sep(a, o) + 0.35
            ox, oz = o.pos.x - a.pos.x, o.pos.z - a.pos.z
            along = ox * ux + oz * uz
            if along <= 0 or along >= L + need:
                continue
            if abs(-ox * uz + oz * ux) >= need:
                continue
            if _sqrt((g.x - o.pos.x) ** 2 + (g.z - o.pos.z) ** 2) < sep(a, o):
                return  # goal held: wait, don't detour
            if along < best_along:
                best_along, best_obs = along, o
        if best_obs is None:
            return
        o = best_obs
        need = sep(a, o) + 0.45
        pick, pd = None, math.inf
        for side in (1, -1):
            wx, wz = o.pos.x - uz * side * need, o.pos.z + ux * side * need
            if not self._spot_valid(a, wx, wz, arms, []):
                continue
            cost = _sqrt((wx - a.pos.x) ** 2 + (wz - a.pos.z) ** 2) + _sqrt((g.x - wx) ** 2 + (g.z - wz) ** 2)
            if cost < pd:
                pd, pick = cost, P(wx, wz)
        if pick is not None:
            a.waypoints.append(pick)
            self.detours += 1

    def _unstick(self, a, arms, world):
        a.waitT = 0.0
        # 1) goal held by a stationary arm → take another stop, or give the step back and hold
        if a.state == 'travel' and a.step is not None and a.goal is not None:
            for o in arms:
                if o is not a and not o.moving and _sqrt((o.pos.x - a.goal.x) ** 2 + (o.pos.z - a.goal.z) ** 2) < sep(a, o) + 0.05:
                    a.steps.insert(0, a.step)
                    a.step = None
                    a.goal = None
                    a.waypoints = []
                    a.state = 'idle'
                    a.stats['deferrals'] += 1
                    self.holds += 1
                    return
        # 2) otherwise side-step to a free spot to break symmetry
        spot = self._find_spot(a, arms, self._corridors(a, arms))
        if spot is not None:
            a.waypoints = [spot]
            a.stats['yields'] += 1
            self.yields += 1
