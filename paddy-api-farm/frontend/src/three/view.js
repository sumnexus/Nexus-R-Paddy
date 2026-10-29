// What the 3D farm shows at a given moment, built only from the recorded run (or the live server's reports):
// the square-by-square frames (06:00 and 18:00), the arms' keyframes, each decision's arm plan (the minute each square
// was done) and the hourly weather. The scene draws exactly this; nothing here simulates the farm or the arms.
import { clamp, lerp } from './util.js';

export const W13 = 13;
export const MOVING_CODES = new Set(['travel', 'return', 'tostation', 'yield']);

// internal square index (the twin's 13 × 6 grid with the bund column) ↔ field and its own index (row-major, 6 × 6)
export function sqOf(i) {
  const col = i % W13, r = Math.floor(i / W13);
  if (col === 6) return null;
  return col < 6 ? { f: 'A', k: r * 6 + col, r, c: col } : { f: 'B', k: r * 6 + col - 7, r, c: col - 7 };
}
export function internalIndex(f, r, c) { return r * W13 + (f === 'A' ? c : c + 7); }
export const sqName = (f, r, c) => `${f}${r + 1},${c + 1}`;
export function parseSq(name) {
  const m = /^([AB])(\d+),(\d+)$/.exec(name || '');
  return m ? { f: m[1], r: +m[2] - 1, c: +m[3] - 1 } : null;
}

// ── arm keyframes ──
export function compileTrack(kfs) {
  const n = kfs.length;
  const tr = { n, t: new Float64Array(n), x: new Float32Array(n), z: new Float32Array(n), m: new Float32Array(n), h: new Float32Array(n),
    code: new Int16Array(n), sq: new Int16Array(n) };
  kfs.forEach((k, i) => { tr.t[i] = k[0]; tr.x[i] = k[1]; tr.z[i] = k[2]; tr.m[i] = k[3]; tr.h[i] = k[4]; tr.code[i] = k[5]; tr.sq[i] = k[6]; });
  return tr;
}
export function appendTrack(tr, kfs, keepAfter = -Infinity) {
  const old = [];
  for (let i = 0; i < (tr ? tr.n : 0); i++) if (tr.t[i] >= keepAfter) old.push([tr.t[i], tr.x[i], tr.z[i], tr.m[i], tr.h[i], tr.code[i], tr.sq[i]]);
  const last = old.length ? old[old.length - 1][0] : -Infinity;
  return compileTrack(old.concat(kfs.filter((k) => k[0] > last)));
}
function lastAtOrBefore(arr, n, t) {
  let lo = 0, hi = n - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (arr[mid] <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}
const angLerp = (a, b, k) => { let d = b - a; d = Math.atan2(Math.sin(d), Math.cos(d)); return a + d * k; };

// Where an arm is at time t: between two keyframes it moves in a straight line (keyframes are at most 0.5 s apart
// while it travels) and the mast moves linearly; it keeps the state of the earlier keyframe.
export function armAt(tr, t, dock, codes) {
  if (!tr || !tr.n) return { x: dock.x, z: dock.z, mast: 0, heading: Math.PI / 2, code: 'parked', sq: -1, frac: 0 };
  const i = lastAtOrBefore(tr.t, tr.n, t);
  if (i < 0) return { x: dock.x, z: dock.z, mast: 0, heading: Math.PI / 2, code: 'parked', sq: -1, frac: 0 };
  const code = codes[tr.code[i]] || 'parked';
  if (i >= tr.n - 1) return { x: tr.x[i], z: tr.z[i], mast: tr.m[i], heading: tr.h[i], code, sq: tr.sq[i], frac: 1, t0: tr.t[i], t1: tr.t[i] };
  const t0 = tr.t[i], t1 = tr.t[i + 1], k = t1 > t0 ? clamp((t - t0) / (t1 - t0), 0, 1) : 1;
  return { x: lerp(tr.x[i], tr.x[i + 1], k), z: lerp(tr.z[i], tr.z[i + 1], k), mast: lerp(tr.m[i], tr.m[i + 1], k), heading: angLerp(tr.h[i], tr.h[i + 1], k),
    code, sq: tr.sq[i], frac: k, t0, t1 };
}
export function armBusyAt(tr, t, codes) {
  if (!tr || !tr.n) return false;
  const i = lastAtOrBefore(tr.t, tr.n, t);
  if (i < 0 || i >= tr.n - 1) return false;
  return codes[tr.code[i]] !== 'parked' || tr.t[i + 1] - t < 1;
}

// ── square changes the arms made between frames ──
// transplant: a seedling appears at the minute it was planted; harvest: stubble; irrigate/drain: the gate opens.
export function armEvents(decisions) {
  const ev = [];
  for (const d of decisions || []) {
    const p = d.arm_plan;
    if (!p || !p.tasks) continue;
    for (const [sq, , t1, st] of p.tasks) {
      if (st !== 'done' || t1 == null) continue;
      if (d.action === 'transplant' || d.action === 'harvest') {
        const s = typeof sq === 'number' ? sqOf(sq) : null;
        if (s) ev.push({ t: t1, kind: d.action, f: s.f, k: s.k, decision: d.id });
      } else if (d.action === 'irrigate' || d.action === 'drain') ev.push({ t: t1, kind: d.action, f: d.field, decision: d.id });
    }
  }
  ev.sort((a, b) => a.t - b.t);
  return ev;
}

// ── each arm's jobs, stops and tasks, from the decisions' arm plans ──
export const ROLE_ID = { planting: 1, watering: 2, pest: 3, harvest: 4 };
export const sqXZ = (i) => ({ x: (i % W13) + 0.5 - W13 / 2, z: Math.floor(i / W13) + 0.5 - 3 });
function planTasks(p, layout) {
  const gates = layout?.gates || {};
  return (p.tasks || []).filter((x) => x[1] != null).map(([k, t0, t1, st, ty]) => {
    const gate = typeof k === 'string';
    const pos = gate ? (gates[k] ? { x: gates[k].x, z: gates[k].z } : null) : sqXZ(k);
    return { key: k, gate, t0, t1: t1 ?? t0, st, ty: ty ?? (gate ? layout?.gate_handle_y ?? 0.6 : layout?.soil_tool_y ?? 0.05), x: pos?.x, z: pos?.z };
  }).filter((x) => x.x != null).sort((a, b) => a.t0 - b.t0);
}
function planStops(p) {
  return (p.route || []).filter((s) => s[0] === 'stop').map((s) => ({ x: s[1], z: s[2], items: s[3], shoulder: s[4] ?? null }));
}
export function armJobs(decisions, layout) {
  const byArm = { 1: [], 2: [], 3: [], 4: [] };
  for (const d of decisions || []) {
    const p = d.arm_plan;
    const id = p && ROLE_ID[p.arm];
    if (!id || p.start_t == null) continue;
    byArm[id].push({ id: p.job, decision: d.id, action: d.action, field: d.field, t0: p.start_t, t1: p.end_t ?? p.start_t, stops: planStops(p),
      tasks: planTasks(p, layout), plan: p });
  }
  for (const k of Object.keys(byArm)) byArm[k].sort((a, b) => a.t0 - b.t0);
  return byArm;
}
// a pending decision's dry-run plan (approve mode): the same shape, plus the previewed keyframes
export function previewJob(d, layout) {
  const p = d && d.arm_preview;
  if (!p || !ROLE_ID[p.arm]) return null;
  return { arm: ROLE_ID[p.arm], decision: d.id, action: d.action, field: d.field, t0: p.start_t, t1: p.end_t, stops: planStops(p), tasks: planTasks(p, layout),
    track: p.track ? compileTrack(p.track) : null, plan: p };
}

function jobAt(jobs, t) {
  let lo = 0, hi = jobs.length - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (jobs[mid].t0 <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans >= 0 && t <= jobs[ans].t1 + 1 ? jobs[ans] : null;
}

export const WORK_CODES = new Set(['probe', 'dig', 'seed', 'settle', 'valve', 'amend', 'spread', 'water', 'inspect', 'spray', 'cut', 'puddle', 'reach']);

// Everything the scene needs to pose one arm at time t: where the trolley is, how far the mast is down (the
// height of its shoulder joint at this stop, from the plan), and the point the tool is reaching for (the task's
// square or gate at the task's tool height). Inverse kinematics from these is done by the scene.
export function armPose(tl, a, t, idx, track) {
  const L = tl.layout, dock = L.docks[idx] || { x: 0, z: 0 };
  const p = armAt(track || tl.tracks[a.id], t, dock, tl.codes);
  const S = a.scale || 1;
  const rail = L.frame.rail, top = rail - 0.25 - L.mast_min;
  const lowY = a.role === 'harvester' ? L.low_y.harvester : L.low_y.default;
  const job = jobAt((tl.jobs && tl.jobs[a.id]) || [], t);
  let stop = null;
  if (job && p.mast > 0) {
    let best = 0.2;
    for (const s of job.stops) { const d = Math.hypot(s.x - p.x, s.z - p.z); if (d < best) { best = d; stop = s; } }
  }
  const lowShoulder = stop && stop.shoulder != null ? stop.shoulder : lowY;
  const m = p.mast <= 0 ? 0 : p.mast >= 1 ? 1 : p.mast * p.mast * (3 - 2 * p.mast);
  const shoulderY = top + (lowShoulder - top) * m;
  const fwd = [Math.cos(p.heading), Math.sin(p.heading)];
  const rest = [p.x + fwd[0] * 0.35 * S, shoulderY - 0.45 * S, p.z + fwd[1] * 0.35 * S];
  let target = rest, task = null, k = 0;
  if (job) {
    const ts = job.tasks;
    // the task in progress, the one before it and the next one
    let cur = -1;
    for (let j = 0; j < ts.length; j++) { if (ts[j].t0 <= t + 1e-6) cur = j; else break; }
    const inTask = cur >= 0 && t <= ts[cur].t1 + 1e-6;
    const tgt = (q) => [q.x, q.ty, q.z];
    if (p.code === 'lower') {
      const nx = ts[cur + 1] || (inTask ? ts[cur] : null);
      if (nx) { k = smooth(0.3, 1, p.mast); target = mix3(rest, tgt(nx), k); task = nx; }
    } else if (p.code === 'raise') {
      const pv = cur >= 0 ? ts[cur] : null;
      if (pv) { k = smooth(0.3, 1, p.mast); target = mix3(rest, tgt(pv), k); task = pv; }
    } else if (WORK_CODES.has(p.code) && inTask) {
      task = ts[cur];
      const prev = ts[cur - 1];
      const reachS = a.reachMove || 1.5;
      if (prev && Math.abs(prev.t1 - task.t0) < 0.6 && t - task.t0 < reachS && Math.hypot(prev.x - task.x, prev.z - task.z) > 1e-3) {
        k = smooth(0, 1, (t - task.t0) / reachS);
        target = mix3(tgt(prev), tgt(task), k);
      } else target = tgt(task);
    }
  }
  return { ...p, shoulderY, top, lowY, rest, target, task, job, stop, S, working: WORK_CODES.has(p.code) && p.code !== 'reach' && !!task };
}
const smooth = (a, b, x) => { const u = clamp((x - a) / (b - a), 0, 1); return u * u * (3 - 2 * u); };
const mix3 = (a, b, k) => [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k];

// ── a recorded run → a timeline the scene can be asked about at any moment ──
export function makeTimeline(data) {
  const frames = data.frames || [];
  const ft = Float64Array.from(frames.map((f) => f.t));
  const codes = data.arms?.codes || data.layout?.codes || [];
  const tracks = {};
  for (const [k, v] of Object.entries(data.arms?.tracks || {})) tracks[+k] = compileTrack(v);
  const events = armEvents(data.decisions);
  const evT = Float64Array.from(events.map((e) => e.t));
  // the replay runs until the last frame or the arms' last keyframe (the harvest ends the season mid-job)
  let tEnd = frames.length ? frames[frames.length - 1].t : 0;
  for (const tr of Object.values(tracks)) if (tr.n) tEnd = Math.max(tEnd, tr.t[tr.n - 1] + 1);
  return { kind: 'replay', data, frames, ft, codes, tracks, events, evT, layout: data.layout, weather: data.weather_hourly, water: data.water_hourly,
    power: data.power_hourly, jobs: armJobs(data.decisions, data.layout), month: data.season_month || 6,
    cameraHours: data.camera_hours || [6, 8, 10, 12, 14, 16, 18], sweep: data.scan_sweep_s || 120, tEnd };
}

export function frameIndexAt(tl, t) { return Math.max(0, lastAtOrBefore(tl.ft, tl.ft.length, t)); }

// the power plant in the hour that contains t (the hourly record), else as of the frame
function powerAt(p, t, frame) {
  if (p && p.pv && p.pv.length && p.from_hour != null) {
    const i = Math.floor(t / 3600) - p.from_hour;
    if (i >= 0 && i < p.pv.length) return { pv_kw: p.pv[i], soc: p.soc[i], load_kw: p.load[i], gen_on: !!p.gen[i], biogas_m3: p.biogas[i] };
  }
  return frame?.power || null;
}

function weatherAt(w, t) {
  if (!w || !w.rain) return { rain: 0, cloud: 0.3, wind: 2, temp: 27 };
  const i = clamp(Math.floor(t / 3600) - w.from_hour, 0, w.rain.length - 1);
  return { rain: w.rain[i] || 0, cloud: w.cloud[i] ?? 0.3, wind: w.wind[i] ?? 2, temp: w.temp[i] ?? 27 };
}

function scanAt(tl, t) {
  const day = Math.floor(t / 86400), sec = t - day * 86400;
  for (const h of tl.cameraHours) {
    const s = h * 3600;
    if (sec >= s && sec < s + tl.sweep) return { active: true, k: (sec - s) / tl.sweep, soil: h === 6 };
  }
  return { active: false, k: 0 };
}

// the true water level of field `name` at time t (mm above the mean soil surface), from the hourly record when there is
// one, else between the frames
function levelAt(tl, name, t, fr, nx, k) {
  const w = tl.water;
  if (w && w[name] && w[name].length) {
    const hf = t / 3600 - w.from_hour, i = Math.floor(hf), a = w[name];
    if (i >= 0 && i < a.length - 1) return lerp(a[i], a[i + 1], hf - i);
    if (i >= a.length - 1) return a[a.length - 1];
  }
  const x = fr.fields[name], y = nx ? nx.fields[name] : x;
  const lx = x.lvl ?? x.tube, ly = y?.lvl ?? y?.tube ?? lx;
  return lerp(lx, ly, k);
}

// The squares and fields at time t: the last frame, plus what the arms did since then; the farm's water reading moves
// between the frames' readings and the true level follows the hourly record.
export function fieldsAt(tl, t) {
  const fi = frameIndexAt(tl, t), fr = tl.frames[fi], nx = tl.frames[Math.min(tl.frames.length - 1, fi + 1)];
  const out = {};
  if (!fr) return { fields: out, frame: null, fi };
  const k = nx && nx.t > fr.t ? clamp((t - fr.t) / (nx.t - fr.t), 0, 1) : 0;
  for (const name of ['A', 'B']) {
    const x = fr.fields[name], y = nx ? nx.fields[name] : x;
    if (!x) continue;
    const tube = lerp(x.tube, y.tube, k);
    const lvl = levelAt(tl, name, t, fr, nx, k);
    const sq = [];
    for (let j = 0; j < 36; j++) {
      const elevObs = x.tube - x.sq.depth[j];
      const elev = x.sq.elev ? x.sq.elev[j] : elevObs;
      sq.push({ state: x.sq.state[j], prog: x.sq.prog[j], health: x.sq.health[j], depth: tube - elevObs, wd: lvl - elev, elev, pest: x.sq.pest[j], pid: x.sq.pid[j],
        soil: x.sq.soil[j], ph: x.sq.ph ? x.sq.ph[j] : null, risk: x.sq.risk[j], est: x.sq.est[j], gf: x.sq.gf ? x.sq.gf[j] === '1' : false });
    }
    out[name] = { name, phase: x.phase, tube, lvl, pumping: x.pumping, draining: x.draining, progress: x.progress, yield: x.yield, sq };
  }
  // the arms' work since the frame
  let e = lastAtOrBefore(tl.evT, tl.evT.length, fr.t) + 1;
  for (; e < tl.events.length && tl.events[e].t <= t; e++) {
    const ev = tl.events[e], F = out[ev.f];
    if (!F) continue;
    if (ev.kind === 'transplant') Object.assign(F.sq[ev.k], { state: 'L', prog: 0, health: 0.9, pest: 0, pid: -1, gf: F.sq[ev.k].state !== '.' || F.sq[ev.k].gf });
    else if (ev.kind === 'harvest') { if (F.sq[ev.k].state === 'L') Object.assign(F.sq[ev.k], { state: 'H', prog: null, pest: 0, pid: -1 }); }
    else if (ev.kind === 'irrigate') { F.pumping = true; F.draining = false; }
    else if (ev.kind === 'drain') { F.draining = true; F.pumping = false; }
  }
  return { fields: out, frame: fr, fi };
}

export function viewAt(tl, t, extra = {}) {
  const { fields, frame, fi } = fieldsAt(tl, t);
  const arms = (tl.layout?.arms || []).map((a, i) => ({ ...a, ...armPose(tl, a, t, i) }));
  return { t, month: tl.month, day: Math.floor(t / 86400), hour: (t % 86400) / 3600, fields, arms, weather: weatherAt(tl.weather, t), power: powerAt(tl.power, t, frame),
    scan: scanAt(tl, t), frame, fi, ...extra };
}

// Is any arm working at time t? (the replay slows down then, so the arms can be watched)
export function armsBusy(tl, t) {
  for (const a of tl.layout?.arms || []) if (armBusyAt(tl.tracks[a.id], t, tl.codes)) return true;
  return false;
}
// the next moment after t when an arm starts moving (to skip quiet hours quickly)
export function nextArmStart(tl, t) {
  let best = Infinity;
  for (const tr of Object.values(tl.tracks)) {
    const i = lastAtOrBefore(tr.t, tr.n, t) + 1;
    if (i < tr.n && tr.t[i] < best) best = tr.t[i];
  }
  return best;
}

// The canonical state the 3D farm shows (for the check against the twin): each square's plant (empty, a live plant
// and its stage, dead, harvested) and, at the frames, each field's water reading to 5 mm.
export function sceneState(view, withWater) {
  const parts = [];
  for (const name of ['A', 'B']) {
    const F = view.fields[name];
    if (!F) continue;
    let s = name + ':';
    for (const q of F.sq) {
      if (q.state === 'L') s += 'L' + (q.prog == null ? 0 : stageIdx(q.prog));
      else s += q.state;
    }
    if (withWater) s += `|${Math.round(F.lvl / 5) * 5}|${F.pumping ? 1 : 0}${F.draining ? 1 : 0}`;
    parts.push(s);
  }
  return parts.join(';');
}
const ENDS = [0.14, 0.45, 0.6, 0.7, 0.9];
function stageIdx(p) { let k = 0; while (k < ENDS.length && p >= ENDS[k]) k++; return k; }
export function fnv1a(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(16).padStart(8, '0');
}
