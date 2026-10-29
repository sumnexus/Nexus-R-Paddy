"""The arms (v2): reach and joint limits for every square, no collisions, job order, determinism, exact timing."""
from __future__ import annotations

import math
import unittest

import _helpers  # noqa: F401  (puts backend/ on the path)

from paddy.robots import layout as L
from paddy.robots import system as S
from paddy.robots.arms import DEFS

A_SQ = [r * 13 + c for r in range(6) for c in range(6)]
B_SQ = [r * 13 + 7 + c for r in range(6) for c in range(6)]
ALL = A_SQ + B_SQ
KINDS = {  # kind → (params, demand per square, tool heights to try)
    'soil_check': ({}, 0.0, [L.SOIL_TOOL_Y]),
    'transplant': ({}, 1.0, [L.SOIL_TOOL_Y]),
    'puddle': ({}, 0.0, [L.SOIL_TOOL_Y]),
    'fertilise': ({'product': 'urea', 'kg_per_ha': 54}, 0.0054, [0.3, 0.75, 1.15]),
    'amend': ({'product': 'lime', 'dose': 2000}, 2.0, [0.3, 1.15]),
    'scout': ({}, 0.0, [0.15, 0.6]),
    'spray': ({'product': 'neem'}, 0.06, [0.15, 0.6]),
    'harvest': ({}, 0.45, [0.15, 0.6]),
}


class Recorder(S.NullWorld):
    def __init__(self):
        self.done = []

    def arm_task_done(self, job, task, t):
        self.done.append((round(t, 3), job.id, task.cell if task.cell >= 0 else task.gate))
        return None


def run(jobs, t_end=8 * 3600.0, record=True):
    w = Recorder()
    sysm = S.ArmSystem(world=w, record=record, t0=6 * 3600.0)
    made = []
    for kind, field, targets, params, demand, ty in jobs:
        j = sysm.make_job(kind, field, targets, params=params, demand=[demand] * len(targets) if demand is not None else None,
                          tool_y=[ty] * len(targets) if ty is not None else None)
        sysm.submit(j)
        made.append(j)
    sysm.advance_to(t_end)
    return sysm, made, w


class ReachTests(unittest.TestCase):
    def test_every_square_every_job_within_reach_and_joint_limits(self):
        """All 72 squares, every kind of job and tool height: each stop the planner makes puts every one of its squares
        inside the arm's reach with the elbow and mast inside their limits."""
        checked = 0
        for kind, (params, dem, heights) in KINDS.items():
            for ty in heights:
                for field, sq in ((0, A_SQ), (1, B_SQ)):
                    sysm, (job,), _ = run([(kind, field, sq, params, dem, ty)], record=False)
                    self.assertEqual(job.status, 'done', (kind, ty, field))
                    self.assertEqual(job.done, 36)
                    d = DEFS[job.arm - 1]
                    tmap = {t.cell: t for t in job.tasks}
                    covered = set()
                    for st in job.plan.route:
                        if st[0] != 'stop':
                            continue
                        _, x, z, cells, rec_shoulder = st
                        shoulder = S.lowered_shoulder(d, min(tmap[c].ty for c in cells))
                        self.assertAlmostEqual(rec_shoulder, shoulder, places=3)
                        for c in cells:
                            t = tmap[c]
                            r = S.reach_check(d, x, z, shoulder, t.x, t.ty, t.z)
                            self.assertTrue(r['ok'], (kind, ty, c, r))
                            covered.add(c)
                            checked += 1
                    self.assertEqual(covered, set(sq))
        self.assertEqual(checked, 72 * sum(len(h) for _, _, h in KINDS.values()))

    def test_water_gates_within_reach(self):
        for (kind, f), g in L.GATES.items():
            act = 'irrigate' if kind == 'inlet' else 'drain'
            sysm, (job,), _ = run([(act, f, [(kind, f)], {'target_depth_mm': 50, 'to_depth_mm': 0}, None, None)], record=False)
            self.assertEqual(job.status, 'done')
            d = DEFS[job.arm - 1]
            _, x, z, cells, rec_shoulder = job.plan.route[0]
            self.assertAlmostEqual(rec_shoulder, S.lowered_shoulder(d, L.GATE_HANDLE_Y), places=3)
            r = S.reach_check(d, x, z, S.lowered_shoulder(d, L.GATE_HANDLE_Y), g['x'], L.GATE_HANDLE_Y, g['z'])
            self.assertTrue(r['ok'], r)

    def test_layout_fits_the_frame(self):
        b, fr = L.BOUNDS, L.FRAME
        for i, dk in enumerate(L.DOCKS):
            self.assertTrue(b['x0'] <= dk['x'] <= b['x1'] and b['z0'] <= dk['z'] <= b['z1'])
            r = DEFS[i]['radius']
            self.assertTrue(fr['z0'] < dk['z'] - r and dk['z'] + r < fr['z1'], i)
        for j in range(4):
            for k in range(j + 1, 4):
                gap = math.hypot(L.DOCKS[j]['x'] - L.DOCKS[k]['x'], L.DOCKS[j]['z'] - L.DOCKS[k]['z'])
                # wider than the traffic manager's corridor, so a departing arm never moves a parked neighbour
                self.assertGreater(gap, DEFS[j]['radius'] + DEFS[k]['radius'] + 0.25 + 0.3)
        for st in L.STATIONS.values():
            self.assertTrue(b['x0'] <= st['x'] <= b['x1'] and b['z0'] <= st['z'] <= b['z1'])


class TrafficTests(unittest.TestCase):
    def test_four_arms_at_once_never_collide(self):
        """Every arm busy in the same field at the same time (and crossing to the other field): zero violations of the
        keep-out circles, and every job finishes."""
        jobs = [('transplant', 0, A_SQ, {}, 1.0, None),
                ('fertilise', 0, A_SQ, {'product': 'urea', 'kg_per_ha': 54}, 0.0054, 0.3),
                ('spray', 0, A_SQ[::2], {'product': 'neem'}, 0.06, 0.4),
                ('harvest', 1, B_SQ, {}, 0.45, 0.6),
                ('scout', 1, B_SQ[::3], {}, 0.0, 0.4),
                ('irrigate', 1, [('inlet', 1)], {'target_depth_mm': 50}, None, None),
                ('drain', 0, [('outlet', 0)], {'to_depth_mm': 0}, None, None),
                ('soil_check', 1, B_SQ, {}, 0.0, None)]
        sysm, made, w = run(jobs, t_end=9 * 3600.0)
        tot = sysm.totals()
        self.assertEqual(tot['violations'], 0)
        self.assertGreaterEqual(tot['minSep'], 0.0)
        for j in made:
            self.assertEqual(j.status, 'done', j.kind)
            self.assertEqual(j.summary.collisions, 0)
            self.assertGreaterEqual(j.summary.min_clearance_m, 0.0)
        self.assertFalse(sysm.busy)

    def test_jobs_for_one_arm_run_in_submission_order(self):
        jobs = [('scout', 0, A_SQ[:4], {}, 0.0, 0.3), ('spray', 1, B_SQ[:6], {'product': 'neem'}, 0.06, 0.3),
                ('scout', 1, B_SQ[30:], {}, 0.0, 0.3)]
        sysm, made, w = run(jobs)
        self.assertTrue(made[0].end <= made[1].start + 1e-6 and made[1].end <= made[2].start + 1e-6)
        order = [jid for _, jid, _ in w.done]
        self.assertEqual(order, sorted(order))

    def test_deterministic(self):
        jobs = [('transplant', 0, A_SQ, {}, 1.0, None), ('harvest', 1, B_SQ, {}, 0.45, 0.6), ('spray', 0, A_SQ[5:20], {'product': 'neem'}, 0.06, 0.4)]
        a, _, wa = run(jobs)
        b, _, wb = run(jobs)
        self.assertEqual(wa.done, wb.done)
        self.assertEqual(a.flush_tracks(), b.flush_tracks())

    def test_preview_matches_execution(self):
        """The dry run for a pending decision gives exactly the times the real run then has (no other job between)."""
        w = Recorder()
        sysm = S.ArmSystem(world=w, record=True, t0=6 * 3600.0)
        first = sysm.make_job('fertilise', 0, A_SQ, params={'product': 'urea', 'kg_per_ha': 54}, demand=[0.0054] * 36, tool_y=[0.3] * 36)
        sysm.submit(first)
        sysm.advance_to(6 * 3600.0 + 40)           # the watering arm is on its way
        job = sysm.make_job('spray', 0, A_SQ[10:22], params={'product': 'neem'}, demand=[0.06] * 12, tool_y=[0.4] * 12)
        pv = sysm.preview(job)
        sysm.submit(job)
        sysm.advance_to(8 * 3600.0)
        self.assertEqual(job.status, 'done')
        self.assertEqual(pv.summary.tasks, job.summary.tasks)
        self.assertEqual(pv.summary.busy_s, job.summary.busy_s)
        self.assertEqual(pv.track, [kf for kf in sysm.flush_tracks()[job.arm] if kf[0] >= pv.track[0][0] - 1e-9][:len(pv.track)])


if __name__ == '__main__':
    unittest.main()
