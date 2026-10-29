"""The arm system: the four arms on the cube frame carry out the farm's decisions as jobs.

Every accepted decision that needs a machine becomes one *job* for one arm (the arm is fixed by the action:
``ACTION_ARM``). A job is a list of *tasks* (one per square, or one water gate), each with its work phases. Jobs for
the same arm run strictly in the order they were submitted; an arm takes its next job as soon as it has finished the
last one, from wherever it is. When an arm starts a job, the playground's planner (a copy, ``planner.py``) groups the
tasks into stops (all squares the arm reaches from one trolley position), orders the stops (nearest neighbour,
2-opt, Or-opt) and places refill or unload trips (Prins' split). The playground's arm state machine and traffic manager
(a copy, ``arms.py``) then drive the trolleys: goal reservation, prioritised velocity sampling with a 2.5 s look-ahead,
make-way, detours, and a safety monitor that counts every approach closer than two keep-out radii.

Time: the system steps 0.25 s while anything moves and from phase change to phase change (at most 5 s) while the arms
only work in place, exactly like the playground's world loop. The engine advances it in lock-step with the hourly twin:
right after the decision point at the top of an hour it runs the arms through that hour; every task that finishes is
handed to the engine at once, with its finishing time, and the engine applies it to the twin before the hour's
physics. So a 12-square spray is applied square by square as the arm reaches each square, and each square's kill uses
the weather at that minute.

Everything here is a pure function of the submitted jobs and their times, so runs stay deterministic and replay
exactly. ``preview`` runs a copy forward to show a pending decision's planned path without touching the farm.
"""
from __future__ import annotations

import copy
import math

from ..core.util import Obj
from . import arms as ARMS
from . import layout as L
from . import planner as P

ARM_DT = 0.25            # s, arm and traffic sub-step while anything moves (the playground's value)
MAX_WORK_STEP = 5.0      # s, longest step while the arms only work in place
FLOW_LPS = 0.8           # the watering arm's hose, L/s (the playground's watering nozzle)
KEYFRAME_EVERY = 0.5     # s, keyframes kept for the 3D view while an arm moves
ROLE_NAME = {1: 'planting', 2: 'watering', 3: 'pest', 4: 'harvest'}
ACTION_ARM = {'transplant': 1, 'soil_check': 1, 'irrigate': 2, 'drain': 2, 'amend': 2, 'fertilise': 2, 'scout': 3, 'spray': 3,
              'harvest': 4, 'puddle': 4}
# keyframe codes: what the arm is doing (the 3D view animates by these)
CODES = ['parked', 'travel', 'return', 'tostation', 'yield', 'station', 'lower', 'raise', 'reach', 'probe', 'dig', 'seed', 'settle',
         'valve', 'amend', 'spread', 'water', 'inspect', 'spray', 'cut', 'puddle', 'wait', 'refill', 'unload']
CODE = {c: i for i, c in enumerate(CODES)}


def phases_for(kind, params):
    """Work phases (name, seconds) for one task of a job. Durations are the playground's where the job exists there
    (probe 5 s, dig 6 s, seed 7 s for a transplant, inspect 3 s, inspect 2 s + spray 5 s, cut 14 s, amend 6 s, the hose at
    0.8 L/s); the paddy-only ones are explained in the README."""
    if kind == 'soil_check':
        return [('probe', 5.0)]
    if kind == 'transplant':
        # in a puddled field the seedling hill goes into soft mud: no watering-in, the last phase only firms the mud
        return [('dig', 6.0), ('seed', 7.0), ('settle', 3.0)]
    if kind in ('irrigate', 'drain'):
        return [('valve', 6.0)]
    if kind == 'amend':
        if params.get('product') == 'leach':
            return [('water', max(3.0, float(params.get('dose', 0)) / FLOW_LPS))]
        return [('amend', 6.0)]
    if kind == 'fertilise':
        return [('spread', 3.0)]
    if kind == 'scout':
        return [('inspect', 3.0)]
    if kind == 'spray':
        return [('inspect', 2.0), ('spray', 5.0)]
    if kind == 'harvest':
        return [('cut', 14.0)]
    if kind == 'puddle':
        return [('puddle', 8.0)]
    raise ValueError(f'no arm job for {kind!r}')


def lowered_shoulder(d, ty):
    """Height of the shoulder joint once the mast has lowered for a tool target at height ty: the mast comes down until
    the shoulder is as far above the target as it is above the soil at full extension, never lower than that."""
    g = L.arm_geometry(d)
    drop = g['lowY'] - L.SOIL_TOOL_Y
    top = g['railY'] - 0.25 - g['mastMin']
    return min(top, max(g['lowY'], ty + drop))


def reach_check(d, stop_x, stop_z, shoulder_y, tx, ty, tz):
    """Inverse kinematics of one task from one stop: horizontal reach, shoulder-to-tool distance, inner elbow angle and
    mast extension. Returns dict(ok, h, dist, elbow, mast, why)."""
    g = L.arm_geometry(d)
    h = math.hypot(tx - stop_x, tz - stop_z)
    v = shoulder_y - ty
    dist = math.hypot(h, v)
    L1, L2 = g['L1'], g['L2']
    why = []
    if h > d['reach'] + 1e-6:
        why.append(f'{h:.3f} m to the side is beyond the {d["reach"]} m reach')
    if v <= 0:
        why.append('target above the shoulder')
    c = (L1 * L1 + L2 * L2 - dist * dist) / (2 * L1 * L2)
    elbow = math.degrees(math.acos(max(-1.0, min(1.0, c))))
    if dist >= L1 + L2 or dist <= abs(L1 - L2):
        why.append(f'{dist:.3f} m from the shoulder is out of the links\' range')
    if not (L.ELBOW_MIN_DEG <= elbow <= L.ELBOW_MAX_DEG):
        why.append(f'elbow at {elbow:.1f}° is outside {L.ELBOW_MIN_DEG:.0f}–{L.ELBOW_MAX_DEG:.0f}°')
    mast = g['railY'] - 0.25 - shoulder_y
    if not (g['mastMin'] - 1e-9 <= mast <= g['mastMax'] + 1e-9):
        why.append(f'mast {mast:.2f} m outside {g["mastMin"]:.2f}–{g["mastMax"]:.2f} m')
    return dict(ok=not why, h=h, dist=dist, elbow=elbow, mast=mast, why=why)


class NullWorld:
    """What a preview copy reports to: nothing changes on the farm."""

    def arm_job_may_start(self, job, t):
        return True, ''

    def arm_job_cancelled(self, job, t, why):
        pass

    def arm_task_valid(self, job, task, t):
        return True

    def arm_task_done(self, job, task, t):
        return None

    def arm_job_started(self, job, t):
        pass

    def arm_job_done(self, job, t):
        pass


class ArmSystem:
    def __init__(self, world=None, energy=None, record=True, t0=0.0):
        self.world = world or NullWorld()
        self.energy = energy
        self.record = record
        self.t = float(t0)
        self.arms = [ARMS.Arm(d, L.DOCKS[i], L.STATIONS[d['station']]) for i, d in enumerate(ARMS.DEFS)]
        self.traffic = ARMS.Traffic(L.BOUNDS)
        self.traffic.arms = self.arms
        self.queues = {a.id: [] for a in self.arms}     # job ids waiting, per arm, in submission order
        self.current = {a.id: None for a in self.arms}  # job id the arm is working on
        self.jobs = {}
        self.n_jobs = 0
        self._t_end_step = self.t
        self.tracks = {a.id: [] for a in self.arms}     # keyframes [t, x, z, mast, heading, code, square]
        self._last_kf = {a.id: None for a in self.arms}
        self.steps = 0
        self.busy_s = 0.0                               # seconds with any arm at work (the engine reads and resets it)

    # ── jobs ──
    def make_job(self, kind, field, targets, params=None, decision=None, label=None, demand=None, tool_y=None, preview=False):
        """targets: square indices (internal grid) or ('inlet' | 'outlet', field) for a water gate. demand / tool_y: per
        target (magazine units; tool height in m)."""
        params = dict(params or {})
        if preview:
            jid = 'preview'
        else:
            self.n_jobs += 1
            jid = f'j{self.n_jobs:05d}'
        arm_id = ACTION_ARM[kind]
        ph = phases_for(kind, params)
        service = sum(p[1] for p in ph)
        tasks = []
        for k, tg in enumerate(targets):
            if isinstance(tg, tuple):
                gate = L.GATES[tg]
                x, z, sq, gname = gate['x'], gate['z'], -1, f'{tg[0]}_{"AB"[tg[1]]}'
                ty = L.GATE_HANDLE_Y
            else:
                sq = int(tg)
                x, z = L.square_xz(sq)
                gname = None
                ty = tool_y[k] if tool_y is not None else L.SOIL_TOOL_Y
            tasks.append(Obj(id=f'{jid}.{k}', job=jid, type=kind, cell=sq, gate=gname, x=x, z=z, service=service,
                             demand=float(demand[k]) if demand is not None else 0.0, group=field if field is not None else 0,
                             ty=float(ty), phases=ph, status='queued', t0=None, t1=None, prio=0))
        job = Obj(id=jid, kind=kind, arm=arm_id, field=field, params=params, decision=decision, label=label or kind,
                  tasks=tasks, status='new', submitted=None, start=None, tasks_done_t=None, end=None, plan=None,
                  stats=None, done=0, skipped=0, results=[])
        return job

    def submit(self, job, t=None):
        job.submitted = self.t if t is None else t
        job.status = 'queued'
        self.jobs[job.id] = job
        self.queues[job.arm].append(job.id)
        return job

    def busy_arm(self, arm_id):
        a = self.arms[arm_id - 1]
        return self.current[arm_id] is not None or bool(self.queues[arm_id]) or a.state != 'idle' or bool(a.steps)

    @property
    def busy(self):
        for a in self.arms:
            if a.state != 'idle' or a.steps or self.current[a.id] is not None or self.queues[a.id]:
                return True
            if a.mast > 0.001 or math.hypot(a.pos.x - a.dock.x, a.pos.z - a.dock.z) > 0.05:
                return True
        return False

    def _start_next(self, a):
        q = self.queues[a.id]
        if not q or self.current[a.id] is not None:
            return
        if a.step is not None or a.steps or a.state not in ('idle', 'return'):
            return
        job = self.jobs[q.pop(0)]
        ok, why = self.world.arm_job_may_start(job, self.t)
        if not ok:
            job.status = 'cancelled'
            for t in job.tasks:
                t.status = 'cancelled'
            self.world.arm_job_cancelled(job, self.t, why)
            return self._start_next(a)
        self.current[a.id] = job.id
        job.status = 'active'
        job.start = self.t
        job.stats = Obj(dist0=a.stats['dist'], energy0=a.stats['energyJ'], refills0=a.stats['refills'], minClear=math.inf,
                        viol0=self.traffic.violations, near0=self.traffic.nearMisses, waits0=a.stats['waitT'], deferrals0=a.stats['deferrals'],
                        yields0=a.stats['yields'])
        tasks = [t for t in job.tasks if t.status == 'queued']
        cap_finite = a.capKind in ('seed', 'tank', 'bin', 'hopper')
        cap_units = a.capacity - a.binKg if a.capKind == 'bin' else a.load
        desc = dict(id=a.id, x=a.pos.x, z=a.pos.z, speed=a.speed, reach=a.reach, lowerTime=a.lowerTime, raiseTime=a.raiseTime,
                    reachMove=a.reachMove, capacity=a.capacity if cap_finite else math.inf, load=cap_units, station=a.stationPos,
                    refillTime=a.refillTime, offset=0)
        pl = P.plan(tasks, [desc], dict(maxPerStop=6 if a.role == 'harvester' else 4, baselines=False))
        steps = pl.perArm[0].steps
        # the stop's mast height: low enough for its lowest target
        for s in steps:
            if s.kind == 'stop':
                s.stop['shoulder'] = lowered_shoulder(a.defn, min(t.ty for t in s.stop['tasks']))
        job.plan = Obj(strategy=pl.strategy, makespan=float(pl.makespan), stops=int(pl.stops), sites=len(tasks),
                       route=[('station', round(a.stationPos['x'], 2), round(a.stationPos['z'], 2)) if s.kind == 'station' else
                              ('stop', round(s.stop['x'], 3), round(s.stop['z'], 3), [t.cell if t.cell >= 0 else t.gate for t in s.stop['tasks']],
                               round(s.stop['shoulder'], 3))
                              for s in steps])
        for t in tasks:
            t.status = 'assigned'
        a.set_route(steps, self.t, pl.makespan)
        self.world.arm_job_started(job, self.t)

    def _job_of(self, task):
        return self.jobs.get(task.job)

    # ── callbacks from the arms (the playground's world interface) ──
    def task_still_valid(self, task):
        if task.status == 'cancelled':
            return False
        job = self._job_of(task)
        return self.world.arm_task_valid(job, task, self._t_end_step)

    def on_stop_skipped(self, arm, stop):
        pass

    def on_step_start(self, arm, step):
        pass

    def on_task_start(self, arm, task):
        task.status = 'active'
        task.t0 = self.t

    def phases_for(self, arm, task):
        return [ARMS.Phase(n, d) for n, d in task.phases]

    def tool_height(self, task):
        return task.ty

    def on_watering(self, arm, task, dt, phase):
        pass                      # the leach water is applied when the task finishes

    def on_phase_done(self, arm, task, phase):
        return None

    def on_task_done(self, arm, task, skipped):
        if task.status in ('done', 'skipped'):
            return
        job = self._job_of(task)
        t = self._t_end_step
        task.t1 = t
        if skipped:
            task.status = 'skipped'
            job.skipped += 1
        else:
            task.status = 'done'
            job.done += 1
            if arm.capKind == 'bin':
                res = self.world.arm_task_done(job, task, t)
                kg = res.get('kg') if isinstance(res, dict) and res.get('kg') is not None else task.demand
                arm.binKg += kg
            else:
                arm.load = max(0.0, arm.load - task.demand)
                self.world.arm_task_done(job, task, t)
        if job.done + job.skipped >= len(job.tasks) and job.tasks_done_t is None:
            job.tasks_done_t = t

    def on_stop_done(self, arm, stop):
        if arm.capKind == 'bin' and arm.binKg >= arm.capacity * 0.95 and arm.steps and arm.steps[0].kind != 'station':
            arm.steps.insert(0, Obj(kind='station'))
        if arm.capKind == 'bin' and arm.binKg > 0.5 and not arm.steps and arm.step is None:
            arm.steps.append(Obj(kind='station'))      # the harvester empties its bin before parking

    def on_station_done(self, arm):
        if arm.capKind == 'bin':
            arm.binKg = 0.0
        else:
            arm.load = arm.capacity

    def add_load(self, cat, w, dt):
        if self.energy is not None:
            self.energy.add_load(cat, w, dt)

    # ── stepping ──
    def _next_dt(self, t_end):
        if not self.busy:
            return t_end - self.t
        d_arm = ARM_DT
        if not any(a.moving for a in self.arms):
            tmin = MAX_WORK_STEP
            for a in self.arms:
                if a.state in ('work', 'station') and a.phase is not None:
                    tmin = min(tmin, a.phase.dur - a.phaseT + 1e-4)
                elif a.state == 'idle':
                    if (a.steps or a.mast > 0.001 or self.queues[a.id] or self.current[a.id] is not None
                            or math.hypot(a.pos.x - a.dock.x, a.pos.z - a.dock.z) > 0.05):
                        tmin = ARM_DT
                else:
                    tmin = ARM_DT
            d_arm = max(ARM_DT, tmin)
        return min(d_arm, t_end - self.t)

    def advance_to(self, t_end):
        """Run the arms until t_end (simulated seconds)."""
        while self.t < t_end - 1e-9:
            dt = self._next_dt(t_end)
            if dt <= 1e-9:
                break
            if not self.busy:
                self.t = t_end
                break
            self._step(dt)

    def _step(self, dt):
        self.steps += 1
        self.busy_s += dt
        self._t_end_step = self.t + dt
        for a in self.arms:
            self._start_next(a)
        for a in self.arms:
            a.prev['x'], a.prev['z'], a.prev['mast'] = a.pos.x, a.pos.z, a.mast
        for a in self.arms:
            a.update(dt, self)
        self.traffic.step(self.arms, min(dt, ARM_DT), self)
        self.t = self._t_end_step
        # clearance of every working arm to the others, and finished jobs
        for a in self.arms:
            jid = self.current[a.id]
            if jid is None:
                continue
            job = self.jobs[jid]
            for o in self.arms:
                if o is a:
                    continue
                c = math.hypot(a.pos.x - o.pos.x, a.pos.z - o.pos.z) - a.radius - o.radius
                if c < job.stats.minClear:
                    job.stats.minClear = c
            if job.tasks_done_t is not None and a.step is None and not a.steps and a.state in ('idle', 'return'):
                self._finish_job(a, job)
        if self.record:
            self._keyframes()

    def _finish_job(self, a, job):
        s = job.stats
        job.end = self.t
        job.status = 'done'
        self.current[a.id] = None
        job.summary = Obj(arm=a.id, arm_name=ROLE_NAME[a.id], start=job.start, tasks_done=job.tasks_done_t, end=job.end,
                          busy_s=round(job.end - job.start, 1), travel_m=round(a.stats['dist'] - s.dist0, 2),
                          energy_wh=round((a.stats['energyJ'] - s.energy0) / 3600, 2), refills=a.stats['refills'] - s.refills0,
                          min_clearance_m=round(s.minClear, 3) if math.isfinite(s.minClear) else None,
                          collisions=self.traffic.violations - s.viol0, near_misses=self.traffic.nearMisses - s.near0,
                          traffic_wait_s=round(a.stats['waitT'] - s.waits0, 1), deferrals=a.stats['deferrals'] - s.deferrals0,
                          made_way=a.stats['yields'] - s.yields0, done=job.done, skipped=job.skipped,
                          tasks=[[t.cell if t.cell >= 0 else t.gate, round(t.t0, 2) if t.t0 is not None else None,
                                  round(t.t1, 2) if t.t1 is not None else None, t.status, round(t.ty, 3)] for t in job.tasks])
        self.world.arm_job_done(job, self.t)
        # an arm with nothing else queued goes home (the arm state machine does that by itself)

    def _code(self, a):
        if a.state in ('work', 'station') and a.phase is not None:
            return CODE.get(a.phase.name, CODE['station'])
        if a.state == 'idle':
            return CODE['wait'] if a.holding else CODE['parked']
        if a.evade is not None:
            return CODE['yield']
        return CODE.get(a.state, CODE['travel'])

    def _keyframes(self):
        """Keep a keyframe whenever an arm changes what it does, and every 0.5 s while it moves (linear interpolation
        between them is within 3 cm of the true path at the arms' accelerations)."""
        t = self.t
        for a in self.arms:
            code = self._code(a)
            sq = a.task['cell'] if (a.task is not None and a.state == 'work') else -1
            last = self._last_kf[a.id]
            kf = [round(t, 2), round(a.pos.x, 3), round(a.pos.z, 3), round(a.mast, 3), round(a.heading, 3), code, sq]
            if last is not None and last[5] == code and last[6] == sq:
                if a.moving and t - last[0] < KEYFRAME_EVERY - 1e-6:
                    continue
                if (not a.moving and abs(last[1] - kf[1]) < 1e-3 and abs(last[2] - kf[2]) < 1e-3 and abs(last[3] - kf[3]) < 2e-3
                        and abs(last[4] - kf[4]) < 1e-3):
                    continue
            self.tracks[a.id].append(kf)
            self._last_kf[a.id] = kf

    def flush_tracks(self):
        """Hand over (and forget) the keyframes recorded so far, per arm."""
        out = {a.id: self.tracks[a.id] for a in self.arms}
        self.tracks = {a.id: [] for a in self.arms}
        return out

    # ── what the state and the website show ──
    def _estimate(self, a, job, x, z, load):
        """The planner's estimate of a queued job's duration if it started with the arm at (x, z)."""
        cap_finite = a.capKind in ('seed', 'tank', 'bin', 'hopper')
        desc = dict(id=a.id, x=x, z=z, speed=a.speed, reach=a.reach, lowerTime=a.lowerTime, raiseTime=a.raiseTime, reachMove=a.reachMove,
                    capacity=a.capacity if cap_finite else math.inf, load=load, station=a.stationPos, refillTime=a.refillTime, offset=0)
        pl = P.plan([t for t in job.tasks if t.status == 'queued'], [desc], dict(maxPerStop=6 if a.role == 'harvester' else 4, baselines=False))
        last = next((s_ for s_ in reversed(pl.perArm[0].steps) if s_.kind == 'stop'), None)
        end = (last.stop['x'], last.stop['z']) if last is not None else (x, z)
        return float(pl.makespan), end

    def arm_status(self):
        """Per arm: None when parked and free, else its jobs (the current one first) and when it should be done."""
        out = {}
        for a in self.arms:
            jid = self.current[a.id]
            q = list(self.queues[a.id])
            if jid is None and not q:
                out[ROLE_NAME[a.id]] = None
                continue
            est, pos = self.t, (a.pos.x, a.pos.z)
            load = a.capacity - a.binKg if a.capKind == 'bin' else a.load
            if jid is not None:
                j = self.jobs[jid]
                est = max(self.t, j.start + (j.plan.makespan if j.plan else 0))
                last = next((s_ for s_ in reversed(a.steps) if s_.kind == 'stop'), a.step if a.step is not None and a.step.kind == 'stop' else None)
                if last is not None:
                    pos = (last.stop['x'], last.stop['z'])
            for k in q:
                dur, pos = self._estimate(a, self.jobs[k], pos[0], pos[1], load)
                est += dur
            labels = ([self.jobs[jid].label] if jid else []) + [self.jobs[k].label for k in q]
            out[ROLE_NAME[a.id]] = Obj(job=labels[0], jobs=labels[:4], busy_until=est, queued=len(q))
        return out

    def snapshot(self):
        """Where each arm is now (for the website between keyframes)."""
        return [dict(id=a.id, name=ROLE_NAME[a.id], title=a.title, x=round(a.pos.x, 3), z=round(a.pos.z, 3), mast=round(a.mast, 3),
                     heading=round(a.heading, 3), code=self._code(a), status=a.status_text(),
                     job=self.current[a.id], queued=len(self.queues[a.id]), load=round(a.binKg if a.capKind == 'bin' else a.load, 2),
                     capacity=a.capacity, capKind=a.capKind) for a in self.arms]

    def totals(self):
        return dict(minSep=None if not math.isfinite(self.traffic.minSep) else round(self.traffic.minSep, 3), minSepPair=self.traffic.minSepPair,
                    violations=self.traffic.violations, nearMisses=self.traffic.nearMisses, detours=self.traffic.detours,
                    yields=self.traffic.yields, holds=self.traffic.holds, steps=self.steps,
                    arms={ROLE_NAME[a.id]: dict(tasks=a.stats['tasks'], stops=a.stats['stops'], dist_m=round(a.stats['dist'], 1),
                                                busy_s=round(a.stats['busyT'], 1), wait_s=round(a.stats['waitT'], 1),
                                                energy_wh=round(a.stats['energyJ'] / 3600, 1), refills=a.stats['refills'],
                                                deferrals=a.stats['deferrals'], made_way=a.stats['yields'])
                          for a in self.arms})

    # ── a dry run for a pending decision ──
    def preview(self, job, horizon_s=6 * 3600):
        """Run a copy of the arm system forward with ``job`` added (nothing on the farm changes) and return that job's
        planned timeline: the route, the arm's keyframes, each task's planned time, busy time, travel, energy and the
        closest approach to the other arms."""
        world, energy, jobs, tracks = self.world, self.energy, self.jobs, self.tracks
        live = {k: v for k, v in jobs.items() if v.status in ('queued', 'active')}
        self.world, self.energy, self.jobs, self.tracks = None, None, live, {a.id: [] for a in self.arms}
        try:
            cp = copy.deepcopy(self)
        finally:
            self.world, self.energy, self.jobs, self.tracks = world, energy, jobs, tracks
        cp.world, cp.energy, cp.record = NullWorld(), None, True
        cp.tracks = {a.id: [] for a in cp.arms}
        cp._last_kf = {a.id: None for a in cp.arms}
        j = copy.deepcopy(job)
        cp.submit(j, cp.t)
        t_stop = cp.t + horizon_s
        while cp.t < t_stop and j.status != 'done':
            cp.advance_to(min(t_stop, cp.t + 600))
        tr = cp.flush_tracks()
        return Obj(job=j.id, arm=j.arm, status=j.status, plan=j.plan, summary=getattr(j, 'summary', None),
                   track=tr[j.arm], tracks=tr)
