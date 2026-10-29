// The 3D view's model (frontend/src/three/view.js) at the twin's checkpoints: see tests/test_scene_state.py.
//   node tests/e2e/scene_state.mjs replay.json checkpoints.json
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const V = await import(pathToFileURL(path.join(here, '..', '..', 'frontend', 'src', 'three', 'view.js')).href);
const data = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const cps = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const tl = V.makeTimeline(data);
let frames = 0, squares = 0, gates = 0, bad = [], earlyish = [];
for (const c of cps) {
  if (c.kind === 'frame') {
    // the frame is taken at its time: the view at that moment shows the frame itself
    const v = V.viewAt(tl, c.t);
    const got = V.sceneState(v, true);
    frames++;
    if (got !== c.state) bad.push({ kind: 'frame', t: c.t, want: c.state, got });
  } else if (c.kind === 'square') {
    // right after the arm finished the square (the plans keep times to the hundredth of a second) — and not before
    const tokAt = (t) => { const q = V.viewAt(tl, t).fields[c.f].sq[c.k]; return q.state === 'L' ? 'L' + (q.prog == null ? 0 : stage(q.prog)) : q.state; };
    const tok = tokAt(c.t + 0.01), before = tokAt(c.t - 0.05);
    squares++;
    if (tok !== c.token) bad.push({ kind: 'square', t: c.t, f: c.f, k: c.k, want: c.token, got: tok });
    if (before === c.token && c.token !== 'D') earlyish.push({ t: c.t, f: c.f, k: c.k, token: c.token });
  } else if (c.kind === 'gate') {
    const v = V.viewAt(tl, c.t + 0.01);
    const F = v.fields[c.f];
    gates++;
    if (!!F.pumping !== c.pumping || !!F.draining !== c.draining) bad.push({ kind: 'gate', t: c.t, f: c.f, want: [c.pumping, c.draining], got: [F.pumping, F.draining] });
  }
}
function stage(p) { const E = [0.14, 0.45, 0.6, 0.7, 0.9]; let k = 0; while (k < E.length && p >= E[k]) k++; return k; }
for (const b of bad.slice(0, 8)) console.log('MISMATCH', JSON.stringify(b));
console.log(JSON.stringify({ frames, squares, gates, mismatches: bad.length, changed_before_the_arm: earlyish.length, hash_example: V.fnv1a(V.sceneState(V.viewAt(tl, cps[0].t), true)) }));
for (const b of earlyish.slice(0, 4)) console.log('ALREADY CHANGED BEFORE THE ARM', JSON.stringify(b));
process.exit(bad.length || earlyish.length ? 1 : 0);
