"""The 3D farm shows the twin's state: at every frame (06:00 and 18:00) the whole farm, and right after every square the
arms transplant or harvest and every gate they set, the squares and gates they changed.

Python records a season and, at each checkpoint, writes the twin's own state in the same canonical form the 3D view
uses (three/view.js sceneState: each square empty, a live plant with its stage, dead or harvested; the true water level
to 5 mm; the gates). Node then builds the 3D view's model from the exported replay bundle at the same moments and
compares. Needs node (skipped without it)."""
import json
import math
import os
import shutil
import subprocess
import tempfile
import unittest

import _helpers  # noqa: F401

from paddy.engine import Engine
from paddy.replay import export_engine

HERE = os.path.dirname(os.path.abspath(__file__))
ENDS = [0.14, 0.45, 0.6, 0.7, 0.9]


def stage_idx(p):
    k = 0
    while k < len(ENDS) and p >= ENDS[k]:
        k += 1
    return k


def sq_token(tw, i):
    if tw.done[i] and tw.harvestKg[i] > 0:
        return 'H'
    if tw.has[i] and tw.alive[i] and not tw.done[i]:
        return 'L%d' % stage_idx(round(float(tw.frac[i]), 3))
    if tw.has[i]:
        return 'D'
    return '.'


def canonical(tw, with_water=True):
    parts = []
    for f in range(2):
        fd = tw.fields[f]
        s = fd.name + ':'
        for r in range(tw.R):
            for c in range(tw.K):
                s += sq_token(tw, tw.sq_index(f, r, c))
        if with_water:
            lvl = round(float(fd.h), 1)
            s += '|%d|%d%d' % (math.floor(lvl / 5 + 0.5) * 5, 1 if fd.irrTarget is not None else 0, 1 if fd.drainTarget is not None else 0)
        parts.append(s)
    return ';'.join(parts)


@unittest.skipIf(shutil.which('node') is None, 'node is not installed')
class SceneMatchesTwin(unittest.TestCase):
    def test_checkpoints(self):
        e = Engine(seed=20261024, weatherYear=0, season='kharif', manager='builtin', keep_states=False, record_frames=True, record_tracks=True)
        cps = []
        orig_frame = e.frame

        def frame():
            fr = orig_frame()
            cps.append({'kind': 'frame', 't': e.tw.t, 'state': canonical(e.tw)})
            return fr
        e.frame = frame
        orig_done = e.arm_task_done

        def task_done(job, task, t):
            out = orig_done(job, task, t)
            tw = e.tw
            if job.kind in ('transplant', 'harvest'):
                f = job.field
                i = task.cell
                col = i % 13
                k = (i // 13) * 6 + (col if col < 6 else col - 7)
                cps.append({'kind': 'square', 't': t, 'f': tw.fields[f].name, 'k': k, 'token': sq_token(tw, i)})
            elif job.kind in ('irrigate', 'drain'):
                fd = tw.fields[job.field]
                cps.append({'kind': 'gate', 't': t, 'f': fd.name, 'pumping': fd.irrTarget is not None, 'draining': fd.drainTarget is not None})
            return out
        e.arm_task_done = task_done
        e.run()
        tmp = tempfile.mkdtemp(prefix='scene-state-')
        bundle = os.path.join(tmp, 'replay.json')
        export_engine(e, bundle)
        with open(os.path.join(tmp, 'checkpoints.json'), 'w') as fh:
            json.dump(cps, fh)
        r = subprocess.run(['node', os.path.join(HERE, 'e2e', 'scene_state.mjs'), bundle, os.path.join(tmp, 'checkpoints.json')],
                           capture_output=True, text=True, timeout=300)
        print(r.stdout[-2000:])
        self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
        res = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertGreater(res['frames'], 200)
        self.assertGreater(res['squares'], 100)
        self.assertGreater(res['gates'], 10)
        self.assertEqual(res['mismatches'], 0, res)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
