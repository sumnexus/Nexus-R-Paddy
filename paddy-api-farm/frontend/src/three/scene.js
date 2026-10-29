// ─────────────────────────────────────────────────────────────────────────────
// scene.js — the Paddy API Farm's 3D farm. Adapted from the Agri Robotics Playground's FarmScene
// (agri-sim/src/ui/scene.js, copied, never edited there). Kept: the renderer and camera (gl.js), the ground, the
// sensor frame and its rails, the service bay, the solar array, battery and biogas plant, palms and shrubs, the four
// arms with their inverse kinematics and tools, particles, rain, sky, sun and labels.
// New for the paddies: two bunded basins with their inlets, outlets, supply pipe, pump and drain; water drawn at its
// true depth; soil that darkens when wet and cracks as it dries; the AWD water tubes with their scale and AWD line;
// rice by stage (seedling tufts, tillers, panicles, golden ripe grain), stubble, dead and gap-filled squares; the scan
// bar's passes; plan-view overlays; the selected decision's arm and squares; approve-mode path previews.
// The scene draws only what view.js says the farm was doing at that moment. It never simulates anything.
// ─────────────────────────────────────────────────────────────────────────────
import { GX } from './gl.js';
import { GEO } from './geometry.js';
import { clamp, lerp, smoothstep, hash01, makeNoise2D, fbm, RNG, sunPosition, seasonDoy, RICE, canopy, ETL, esc } from './util.js';
import { sqXZ, internalIndex } from './view.js';
import { cellFill } from '../layers.js';

const { M4, hexLin, mixLin } = GX;
const { box, cylinder, sphere, plane, ring, blade, mat, Builder, riceStubble } = GEO;

export const BUND_H = 0.2;          // m, bund top above the mean soil surface (the twin overflows at 150 mm of water)
export const GAUGE_ZERO = 0.32;     // m, height of the "soil surface" mark on each tube's scale board

const PARTICLE_TYPES = {
  water: { col: [0.45, 0.72, 1.0, 0.85], size: 4.5, life: 0.7, grav: -6, spread: 0.25, vy: -1.5, rate: 140 },
  flow: { col: [0.5, 0.74, 0.95, 0.8], size: 5.5, life: 0.55, grav: -5, spread: 0.12, vy: 0.4, rate: 90 },
  dust: { col: [0.55, 0.38, 0.24, 0.7], size: 9, life: 1.1, grav: 0.4, spread: 0.6, vy: 0.6, rate: 60 },
  mist: { col: [0.85, 0.95, 0.8, 0.35], size: 16, life: 1.6, grav: 0.15, spread: 0.5, vy: -0.4, rate: 80 },
  seed: { col: [0.45, 0.7, 0.3, 1], size: 4, life: 0.5, grav: -7, spread: 0.08, vy: -0.8, rate: 40 },
  granules: { col: [0.92, 0.9, 0.85, 1], size: 3.5, life: 0.6, grav: -7, spread: 0.2, vy: -0.5, rate: 70 },
  harvest: { col: [0.95, 0.78, 0.35, 1], size: 5, life: 0.8, grav: -4, spread: 0.12, vy: 2.2, rate: 26 },
  splash: { col: [0.42, 0.33, 0.24, 0.85], size: 5, life: 0.6, grav: -6, spread: 0.35, vy: 1.6, rate: 90 },
  scan: { col: [0.4, 1.0, 0.7, 0.9], size: 3, life: 0.35, grav: 0, spread: 0.05, vy: -1.6, rate: 30 },
  smoke: { col: [0.6, 0.6, 0.62, 0.35], size: 22, life: 3.5, grav: 0.6, spread: 0.2, vy: 0.8, rate: 12 },
};
const EFFECT = { dig: 'splash', settle: 'splash', seed: 'seed', water: 'water', spray: 'mist', amend: 'granules', spread: 'granules', cut: 'harvest',
  probe: 'scan', inspect: 'scan', puddle: 'splash' };

// CSS colour tokens → linear RGB (the overlays use the plan view's colours, so one legend serves both)
const _css = new Map();
function cssLin(name) {
  if (_css.has(name)) return _css.get(name);
  let v = '';
  try { v = getComputedStyle(document.documentElement).getPropertyValue(name).trim(); } catch (e) { v = ''; }
  const out = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.test(v) ? hexLin(v) : hexLin('#888888');
  _css.set(name, out);
  return out;
}
export function resetCssCache() { _css.clear(); }

export class PaddyScene {
  constructor(engine, opts = {}) {
    this.e = engine;
    this.low = !!opts.low;               // phones: fewer hills per square, a smaller soil texture, no shadows
    this.reduced = !!opts.reduced;       // prefers-reduced-motion: no sway, blinking or bobbing; few particles
    this.sets = []; this.labels = new Map();
    this.overlay = null; this.showLabels = true;
    this._plantKey = ''; this._soilKey = ''; this._elevKey = { A: '', B: '' };
    this.lines = engine.lineBuffer(12000);
    this.dashes = engine.lineBuffer(4000); this.dashes.depthTest = false;
    this.rain = engine.lineBuffer(2400);
    this.pts = engine.pointBuffer(5000);
    const N = 5000;
    this.particles = { n: 0, max: N, x: new Float32Array(N), y: new Float32Array(N), z: new Float32Array(N), vx: new Float32Array(N), vy: new Float32Array(N), vz: new Float32Array(N),
      life: new Float32Array(N), max0: new Float32Array(N), c: new Float32Array(N * 4), s: new Float32Array(N), g: new Float32Array(N) };
    this._emitAcc = new Map();
    this.drops = null;
    this.labelLayer = null;
    this._rng = new RNG(9001);
  }
  attachLabels(el) { this.labelLayer = el; }
  _set(geo, max, m) { const mesh = this.e.mesh(geo.build ? geo.build() : geo); const s = this.e.instanced(mesh, max, m); s.meshRef = mesh; this.sets.push(s); return s; }
  dispose() {
    for (const s of this.sets) this.e.remove(s);
    this.sets = [];
    if (this.labelLayer) this.labelLayer.innerHTML = '';
    this.labels.clear();
  }

  // ── build everything (static) ──
  build(layout, extras = {}) {
    this.dispose();
    this.L = layout;
    this.pvKwp = extras.pvKwp || 1.5;
    this.fr = layout.frame;
    this.boxes = this._set(box(1, 1, 1, '#ffffff'), 1800, { spec: 0.35 });
    this.cyls = this._set(cylinder(1, 1, 1, 14, '#ffffff', { capBot: true }), 500, { spec: 0.35 });
    this.balls = this._set(sphere(1, 12, 8, '#ffffff'), 200, { spec: 0.3 });
    this.glow = this._set(box(1, 1, 1, '#ffffff'), 500, { unlit: true, castShadow: false });
    this._staticBoxes = []; this._staticCyls = []; this._staticBalls = []; this._glow = [];
    this._buildGround();
    this._buildBasins();
    this._buildWaterWorks();
    this._buildFrame();
    this._buildService();
    this._buildPower();
    this._buildSurroundings();
    this._buildArms();
    this._buildPlants();
    this._buildMarkers();
    this._commitStatic();
    this._ledSet = new Set(this._leds);
    this._buildLabels();
    this._plantKey = ''; this._soilKey = '';
  }
  _addBox(x, y, z, sx, sy, sz, color, ry = 0, rx = 0, rz = 0) { this._staticBoxes.push([x, y, z, sx, sy, sz, hexLin(color), ry, rx, rz]); return this._staticBoxes.length - 1; }
  _addCyl(x, y, z, r, h, color, ry = 0, rx = 0, rz = 0) { this._staticCyls.push([x, y, z, r, h, r, hexLin(color), ry, rx, rz]); return this._staticCyls.length - 1; }
  _addBall(x, y, z, r, color, sy = 1) { this._staticBalls.push([x, y, z, r, r * sy, r, hexLin(color), 0, 0, 0]); }
  _commitStatic() {
    const fill = (set, list) => {
      list.forEach((b, i) => {
        M4.trs(set.M, i * 16, b[0], b[1], b[2], b[7], b[3], b[4], b[5], b[8], b[9]);
        set.C.set([b[6][0], b[6][1], b[6][2], 1], i * 4);
        set.P.set([0, 0, 0, 0], i * 4);
      });
      set.count = list.length; set.dirtyM = set.dirtyC = set.dirtyP = true;
    };
    fill(this.boxes, this._staticBoxes);
    fill(this.cyls, this._staticCyls);
    fill(this.balls, this._staticBalls);
    this._cylBase = this._staticCyls.length;
  }

  _buildGround() {
    const fr = this.fr, o = this.L.outer;
    const nz = makeNoise2D(77);
    const grass1 = hexLin('#4a7530'), grass2 = hexLin('#6f8838'), dirt = hexLin('#8a6a48'), gravel = hexLin('#a39a8a'), paddyNb = hexLin('#5f8f3a');
    const g = plane(320, 320, '#ffffff', this.low ? 64 : 110, this.low ? 64 : 110, (x, z) => {
      const n = fbm(nz, x / 18 + 50, z / 18 + 50, 3);
      let c = mixLin(grass1, grass2, clamp(n * 1.6 - 0.4, 0, 1));
      // neighbours' paddies to the north and south (flat, bright green bands)
      const nb = (z < fr.z0 - 9 || z > fr.z1 + 14) && Math.abs(x) < 46 ? 1 : 0;
      if (nb) c = mixLin(c, paddyNb, 0.55 + 0.25 * fbm(nz, x / 5, z / 5, 2));
      // gravel apron around the frame, a dirt track to the west
      const ax = Math.max(fr.x0 - 1.5 - x, 0, x - fr.x1 - 1.5), az = Math.max(fr.z0 - 1.5 - z, 0, z - fr.z1 - 1.5);
      const apron = Math.hypot(ax, az);
      if (apron < 1.6) c = mixLin(gravel, c, apron / 1.6);
      if (Math.abs(z) < 2.0 && x < fr.x0 - 6) c = mixLin(dirt, c, Math.abs(z) / 2.0 * 0.7);
      // the basins' surroundings: worn earth just outside the bunds
      const bx = Math.max(o.x0 - x, 0, x - o.x1), bz = Math.max(o.z0 - z, 0, z - o.z1);
      const near = Math.hypot(bx, bz);
      if (near < 0.7) c = mixLin(dirt, c, 0.4 + 0.6 * near / 0.7);
      return c;
    });
    const s = this._set(g, 1, { spec: 0.03 });
    M4.trs(s.M, 0, 0, -0.012, 0, 0, 1, 1, 1); s.count = 1;
  }

  // two basins: soil surface (with each square's true height), a water surface per field, bunds with grass on top
  _buildBasins() {
    const L = this.L;
    this.PX = this.low ? 16 : 32;                         // soil texture: texels per metre
    this.SUB = 4;                                          // soil mesh subdivisions per square
    this.basin = {};
    for (const name of ['A', 'B']) {
      const F = L.fields[name], cx = (F.x[0] + F.x[1]) / 2, w = F.x[1] - F.x[0], d = F.z[1] - F.z[0];
      const n = 6 * this.SUB;
      const tw = Math.round(w * this.PX), th = Math.round(d * this.PX);
      const data = new Uint8Array(tw * th * 4);
      const tex = this.e.texture(tw, th, data, false);
      const geo = plane(w, d, '#ffffff', n, n);
      const soil = this._set(geo, 1, { spec: 0.08, map: tex });
      M4.trs(soil.M, 0, cx, 0, 0, 0, 1, 1, 1); soil.count = 1;
      // the same surface, unlit, for the plan overlays (their colours then match the legend; see _updateSoil)
      const flat = this.e.instanced(soil.meshRef, 1, { map: tex, unlit: true, castShadow: false });
      this.sets.push(flat);
      M4.trs(flat.M, 0, cx, 0, 0, 0, 1, 1, 1); flat.count = 0;
      const pos = new Float32Array(geo.p);
      // crack pattern (Voronoi cell borders, cells ≈ 0.3 m) and texel noise, worked out once
      const crack = new Float32Array(tw * th), noise = new Float32Array(tw * th);
      const nz = makeNoise2D(name === 'A' ? 311 : 733), r = new RNG(name === 'A' ? 17 : 29);
      const cell = 0.3, gx = Math.ceil(w / cell), gz = Math.ceil(d / cell), seeds = [];
      for (let j = 0; j < gz; j++) for (let i = 0; i < gx; i++) seeds.push([(i + r.random()) * cell, (j + r.random()) * cell]);
      for (let y = 0; y < th; y++) for (let x = 0; x < tw; x++) {
        const px = (x + 0.5) / this.PX, pz = (y + 0.5) / this.PX;
        const ci = Math.floor(px / cell), cj = Math.floor(pz / cell);
        let d1 = 1e9, d2 = 1e9;
        for (let dj = -1; dj <= 1; dj++) for (let di = -1; di <= 1; di++) {
          const ii = ci + di, jj = cj + dj;
          if (ii < 0 || jj < 0 || ii >= gx || jj >= gz) continue;
          const s = seeds[jj * gx + ii], dd = Math.hypot(s[0] - px, s[1] - pz);
          if (dd < d1) { d2 = d1; d1 = dd; } else if (dd < d2) d2 = dd;
        }
        const edge = d2 - d1;
        crack[y * tw + x] = 1 - smoothstep(0.0, 1.6 / this.PX, edge);
        noise[y * tw + x] = fbm(nz, px * 2.2, pz * 2.2, 3) - 0.5 + 0.25 * (hash01(x, y, name) - 0.5);
      }
      const water = this._set(plane(1, 1, '#ffffff'), 1, { transparent: true, spec: 0.75, castShadow: false, order: 1 });
      this.basin[name] = { F, cx, w, d, n, tw, th, data, tex, soil, flat, pos, crack, noise, water };
    }
    // bunds: outer ring and the bund between the fields, earth with grass on top
    const o = L.outer, bw = L.bund_w, earth = '#7a5a3c', turf = '#6c8a3a';
    const bund = (x0, x1, z0, z1) => {
      this._addBox((x0 + x1) / 2, BUND_H / 2, (z0 + z1) / 2, x1 - x0, BUND_H, z1 - z0, earth);
      this._addBox((x0 + x1) / 2, BUND_H + 0.008, (z0 + z1) / 2, Math.max(0.05, x1 - x0 - 0.12), 0.016, Math.max(0.05, z1 - z0 - 0.12), turf);
    };
    bund(o.x0, o.x1, o.z0, o.z0 + bw);
    bund(o.x0, o.x1, o.z1 - bw, o.z1);
    bund(o.x0, o.x0 + bw, o.z0 + bw, o.z1 - bw);
    bund(o.x1 - bw, o.x1, o.z0 + bw, o.z1 - bw);
    bund(L.fields.A.x[1], L.fields.B.x[0], o.z0 + bw, o.z1 - bw);
  }

  // inlets, outlets, the supply pipe from the pump, the drain channel, and each field's water tube with its scale
  _buildWaterWorks() {
    const L = this.L, P = L.pump, pipeZ = L.pipe_z, drainZ = L.drain_z, o = L.outer;
    // pump house over the borewell
    this._addBox(P.x, 0.45, P.z, 1.0, 0.9, 0.9, '#c9b79c');
    this._addBox(P.x, 0.95, P.z, 1.15, 0.1, 1.05, '#8a4b32');
    this._addCyl(P.x - 0.62, 0, P.z + 0.1, 0.12, 0.35, '#6f7a80');
    this.pumpLamp = this._glow.length; this._glow.push([P.x + 0.51, 0.7, P.z, 0.02, 0.08, 0.08, '#8892a0']);
    // supply pipe along the north, branches to the two inlets
    const pipeCol = '#4d6f8f', pr = 0.055, py = 0.09;
    const xEnd = L.gates.inlet_B.x;
    // a cylinder grows along +Y from its base: turned −90° about z it lies along +x, +90° about x along +z
    const seg = (x0, x1, z) => { this._staticCyls.push([x0, py, z, pr, x1 - x0, pr, hexLin(pipeCol), 0, 0, -Math.PI / 2]); };
    seg(P.x, xEnd + 0.06, pipeZ);
    this._addCyl(P.x, 0, pipeZ, pr * 1.2, py + 0.02, pipeCol);
    for (const g of [L.gates.inlet_A, L.gates.inlet_B]) {
      this._staticCyls.push([g.x, py, pipeZ, pr * 0.85, (g.z - pipeZ), pr * 0.85, hexLin(pipeCol), 0, Math.PI / 2, 0]);
    }
    // gates: concrete boxes through the bund, a handwheel on a post at the height the arm turns it
    this.gates = {};
    const hy = L.gate_handle_y;
    for (const [key, g] of Object.entries(L.gates)) {
      const inlet = key.startsWith('inlet');
      this._addBox(g.x, BUND_H / 2 + 0.03, g.z, 0.5, BUND_H + 0.06, 0.62, '#b7b2a8');
      this._addBox(g.x, BUND_H / 2, g.z + (inlet ? 0.36 : -0.36), 0.42, 0.04, 0.12, '#9d988e');
      this._addCyl(g.x + 0.18, BUND_H + 0.06, g.z, 0.025, hy - BUND_H - 0.08, '#6f7a80');
      const wheel = this._set(cylinder(0.11, 0.11, 0.025, 18, '#ffffff', { capBot: true }), 1, { spec: 0.5 });
      this.gates[key] = { ...g, inlet, field: key.slice(-1), wheel, open: 0 };
      // spout into the field (inlet) or weir board (outlet)
      if (inlet) this._addBox(g.x, 0.08, g.z + 0.38, 0.16, 0.1, 0.22, '#9d988e');
      else this._addBox(g.x, BUND_H * 0.75, g.z, 0.34, BUND_H * 0.5, 0.04, '#7b6a52');
    }
    // drain channel along the south: concrete-edged ditch with water while a field drains
    const dx0 = o.x0 - 0.6, dx1 = o.x1 + 0.6;
    this._addBox((dx0 + dx1) / 2, 0.004, drainZ, dx1 - dx0, 0.008, 0.36, '#4b3d2e');
    this._addBox((dx0 + dx1) / 2, 0.03, drainZ - 0.2, dx1 - dx0, 0.06, 0.05, '#a8a39a');
    this._addBox((dx0 + dx1) / 2, 0.03, drainZ + 0.2, dx1 - dx0, 0.06, 0.05, '#a8a39a');
    for (const g of [L.gates.outlet_A, L.gates.outlet_B]) this._addBox(g.x, 0.02, (g.z + drainZ) / 2 + 0.1, 0.18, 0.03, drainZ - g.z - 0.1, '#a8a39a');
    this.drainWater = this._set(box(1, 1, 1, '#ffffff'), 1, { transparent: true, spec: 0.9, castShadow: false, order: 1 });
    // AWD water tubes (true size: 15 cm across, 10 cm above the soil) with a scale board on the bund beside each
    this.tubes = {};
    for (const name of ['A', 'B']) {
      const g = L.gates[`inlet_${name}`];
      const x = name === 'A' ? L.fields.A.x[0] + 2.0 : L.fields.B.x[0] + 1.0;   // between the gates, near the south bund
      const tz = L.fields[name].z[1] - 0.22, bz = L.fields[name].z[1] + 0.25;
      this._addCyl(x, -0.02, tz, 0.075, 0.12, '#e8ecef');
      this._addCyl(x, 0.098, tz, 0.062, 0.004, '#30383d');
      // the board: a post, a white face (−300 … +150 mm, true scale), a black "soil surface" mark, a red AWD line
      const top = GAUGE_ZERO + 0.15, bot = GAUGE_ZERO - 0.3;
      this._addBox(x, BUND_H + (top + 0.02 - BUND_H) / 2, bz + 0.03, 0.05, top + 0.02 - BUND_H, 0.03, '#6f7a80');
      this._addBox(x, (top + bot) / 2, bz, 0.1, top - bot, 0.012, '#f3f1ea');
      this._glow.push([x, GAUGE_ZERO, bz - 0.008, 0.12, 0.006, 0.004, '#202020']);
      const awd = L.awd_threshold_mm ?? -150;
      this._glow.push([x, GAUGE_ZERO + awd / 1000, bz - 0.008, 0.13, 0.008, 0.004, '#e5392b']);
      for (let mmk = -250; mmk <= 150; mmk += 50) if (mmk !== 0 && mmk !== awd) this._glow.push([x - 0.035, GAUGE_ZERO + mmk / 1000, bz - 0.008, 0.03, 0.003, 0.004, '#5a5a5a']);
      this.tubes[name] = { x, tz, bz, gateX: g.x };
    }
    this.gaugeCol = this._set(box(1, 1, 1, '#ffffff', { base: true }), 2, { unlit: true, castShadow: false });
  }

  _buildFrame() {
    const fr = this.fr, steel = '#aab3b8', steelDark = '#7f898f';
    const top = fr.top, x0 = fr.x0, x1 = fr.x1, z0 = fr.z0, z1 = fr.z1;
    const posts = [];
    const nX = Math.max(2, Math.round((x1 - x0) / 4)), nZ = Math.max(2, Math.round((z1 - z0) / 4));
    for (let i = 0; i <= nX; i++) { const x = lerp(x0, x1, i / nX); posts.push([x, z0], [x, z1]); }
    for (let j = 1; j < nZ; j++) { const z = lerp(z0, z1, j / nZ); posts.push([x0, z], [x1, z]); }
    for (const [x, z] of posts) { this._addBox(x, top / 2, z, 0.16, top, 0.16, steel); this._addBox(x, 0.05, z, 0.42, 0.1, 0.42, '#8d8d86'); }
    this._addBox((x0 + x1) / 2, top, z0, x1 - x0 + 0.16, 0.18, 0.18, steel); this._addBox((x0 + x1) / 2, top, z1, x1 - x0 + 0.16, 0.18, 0.18, steel);
    this._addBox(x0, top, (z0 + z1) / 2, 0.18, 0.18, z1 - z0, steel); this._addBox(x1, top, (z0 + z1) / 2, 0.18, 0.18, z1 - z0, steel);
    for (let z = Math.ceil(z0 + 1); z < z1 - 0.5; z += 2) this._addBox((x0 + x1) / 2, fr.rail + 0.12, z, x1 - x0, 0.08, 0.07, steelDark);
    for (let x = Math.ceil(x0 + 1); x < x1 - 0.5; x += 4) this._addBox(x, fr.rail + 0.2, (z0 + z1) / 2, 0.07, 0.08, z1 - z0, steelDark);
    for (let i = 0; i < nX; i++) {
      const xa = lerp(x0, x1, i / nX), xb = lerp(x0, x1, (i + 1) / nX), Ln = Math.hypot(xb - xa, top), ang = Math.atan2(top, xb - xa);
      for (const z of [z0, z1]) this._addBox((xa + xb) / 2, top / 2, z, Ln, 0.05, 0.05, steelDark, 0, 0, i % 2 ? ang : -ang);
    }
    this._leds = [];
    for (const [x, z] of posts) { this._leds.push(this._glow.length); this._glow.push([x, 1.2, z, 0.1, 0.1, 0.1, '#5ef2a0']); this._addBox(x, 1.2, z, 0.22, 0.16, 0.22, '#3d464b'); }
  }

  _buildService() {
    const L = this.L, fr = this.fr, o = L.outer;
    const bx0 = fr.x0 + 0.2, bx1 = o.x0 - 0.45;
    this._addBox((bx0 + bx1) / 2, 0.03, 0, bx1 - bx0, 0.06, fr.z1 - fr.z0 - 0.8, '#b8b2a6');
    L.docks.forEach((d, i) => {
      const c = L.arms[i].color;
      this._addBox(d.x, 0.08, d.z, 1.5, 0.05, 1.5, '#6f7472');
      this._glow.push([d.x, 0.11, d.z - 0.72, 1.5, 0.02, 0.06, c], [d.x, 0.11, d.z + 0.72, 1.5, 0.02, 0.06, c]);
    });
    const st = L.stations;
    // seedling magazine: a cabinet with nursery trays of green seedlings
    this._addBox(st.seed.x, 0.6, st.seed.z, 0.9, 1.2, 1.0, '#d8c79a');
    for (let k = 0; k < 4; k++) this._addBox(st.seed.x + 0.46, 0.25 + k * 0.25, st.seed.z, 0.03, 0.12, 0.8, '#7b6a45');
    for (let k = 0; k < 3; k++) this._addBox(st.seed.x, 1.25, st.seed.z - 0.3 + k * 0.3, 0.8, 0.08, 0.26, k % 2 ? '#6aa84f' : '#5c9e3e');
    // fertiliser and amendment store: a shed with sacks and a hopper
    this._addBox(st.store.x, 0.55, st.store.z, 1.0, 1.1, 0.9, '#c7c0b1');
    this._addBox(st.store.x, 1.14, st.store.z, 1.15, 0.08, 1.05, '#5f6b63');
    for (let k = 0; k < 3; k++) this._addBox(st.store.x + 0.62, 0.14 + k * 0.17, st.store.z - 0.15 + (k % 2) * 0.12, 0.3, 0.16, 0.45, '#ece6d6');
    // harvest chute over grain bags
    this._addCyl(st.chute.x, 0.6, st.chute.z, 0.5, 0.7, '#8f5bd6'); this._addCyl(st.chute.x, 0, st.chute.z, 0.16, 0.6, '#6c6c6c');
    for (let k = 0; k < 3; k++) this._addBox(st.chute.x - 0.95, 0.2 + k * 0.3, st.chute.z + 0.75, 0.55, 0.28, 0.38, '#d9c98f');
    // spray mixing station: tank and cabinet
    this._addBox(st.chem.x, 0.45, st.chem.z, 1.0, 0.9, 0.8, '#d7d3cb'); this._addCyl(st.chem.x, 0.9, st.chem.z, 0.3, 0.55, '#e0662c');
    this._addBox(st.chem.x + 0.52, 0.55, st.chem.z, 0.02, 0.3, 0.5, '#ffcf3f');
  }

  _buildPower() {
    const fr = this.fr;
    // solar array south of the frame, tilted 13° to face south (+z); 0.3 kWp panels
    const nPanels = Math.max(2, Math.round(this.pvKwp / 0.3)), rows = nPanels > 6 ? 2 : 1, perRow = Math.ceil(nPanels / rows);
    const z0 = fr.z1 + 3.6, xs = -6.5 - (perRow - 1) * 2.15 / 2;
    this.panels = [];
    for (let r = 0; r < rows; r++) for (let k = 0; k < perRow; k++) {
      if (r * perRow + k >= nPanels) break;
      const x = xs + k * 2.15, z = z0 + r * 2.4;
      this._addBox(x, 0.55, z - 0.35, 0.06, 1.1, 0.06, '#8a8f93'); this._addBox(x, 0.35, z + 0.35, 0.06, 0.7, 0.06, '#8a8f93');
      this._addBox(x, 0.95, z, 2.04, 0.05, 1.04, '#cfd5d6', 0, 0.227);
      this.panels.push(this._addBox(x, 0.975, z, 1.96, 0.02, 0.96, '#1d3557', 0, 0.227));
    }
    this.panelCenter = [xs + (perRow - 1) / 2 * 2.15, 1.3, z0 + (rows - 1) * 1.2];
    // battery container, biogas digester and its generator, west of the service bay
    const bx = fr.x0 - 3.2, bz = fr.z1 - 2.6;
    this._addBox(bx, 1.0, bz, 2.2, 2.0, 1.5, '#e9e6df'); this._addBox(bx + 1.11, 1.0, bz, 0.02, 1.6, 1.1, '#cfcac0');
    this.socBar = this._glow.length; this._glow.push([bx + 1.13, 0.5, bz, 0.02, 1.0, 0.18, '#5ef2a0']);
    this.batteryPos = [bx, 2.25, bz];
    const gx = fr.x0 - 3.4, gz = fr.z1 - 6.6;
    this._addBall(gx, 0, gz, 1.4, '#a9a9a2', 0.55); this._addCyl(gx, 0.76, gz, 0.55, 0.32, '#7d8a6a');
    this._addBox(gx + 2.1, 0.45, gz + 0.4, 1.0, 0.9, 0.7, '#5f6b63'); this._addCyl(gx + 2.4, 0.9, gz + 0.5, 0.05, 0.7, '#3a3a3a');
    this.exhaust = [gx + 2.4, 1.65, gz + 0.5]; this.biogasPos = [gx, 1.5, gz];
    // straw stack beside the digester (the harvested straw feeds it)
    this._addBall(gx + 0.6, 0, gz - 1.9, 0.75, '#c9a54a', 0.8);
  }

  _buildSurroundings() {
    const fr = this.fr, r = new RNG(4242);
    const palm = new Builder();
    let px = 0, py = 0;
    for (let s = 0; s < 6; s++) { const h = 1.2; palm.add(cylinder(0.13 - s * 0.012, 0.16 - s * 0.012, h, 7, s % 2 ? '#7d6448' : '#8a6f50', { capTop: false }), mat(px, py, 0, 0, 0, 0.05)); px += 0.06; py += h * 0.99; }
    for (let k = 0; k < 13; k++) { const up = k % 3 === 0; palm.add(blade(up ? 2.6 : 3.3, 0.5, up ? 1.3 : 1.8, '#4f7428', '#86a43e', 7, up ? 0.35 : 0.75), mat(px, py, 0, k / 13 * Math.PI * 2 + (k % 2) * 0.2, 0, 0)); }
    for (let k = 0; k < 5; k++) palm.add(sphere(0.12, 6, 4, '#6b7a2a'), mat(px + Math.cos(k) * 0.16, py - 0.15, Math.sin(k) * 0.16));
    const nPalm = this.low ? 18 : 36;
    const palms = this._set(palm, nPalm, { spec: 0.05, doubleSided: true });
    let n = 0;
    for (let k = 0; k < 90 && n < nPalm; k++) {
      const ang = r.range(0, Math.PI * 2), d = r.range(24, 66);
      const x = Math.cos(ang) * d - 3, z = Math.sin(ang) * d;
      if (x > fr.x0 - 12 && x < fr.x1 + 6 && z > fr.z0 - 6 && z < fr.z1 + 12) continue;
      if (d < 60 && Math.cos(ang - 2.39) > 0.86) continue;   // keep the default overview's line of sight clear
      M4.trs(palms.M, n * 16, x, 0, z, r.range(0, 6.28), 1, r.range(0.85, 1.25), 1);
      palms.P.set([this.reduced ? 0 : 0.006, r.range(0, 6), 0, 0], n * 4); n++;
    }
    palms.count = n;
    const bush = new Builder();
    for (let k = 0; k < 5; k++) bush.add(sphere(0.6, 7, 5, k % 2 ? '#4f6f2e' : '#5d7d35'), mat(Math.cos(k * 1.3) * 0.5, 0.45 + 0.1 * (k % 2), Math.sin(k * 1.3) * 0.5, 0, 0, 0, 1, 0.8, 1));
    const bushes = this._set(bush, 80, { spec: 0.02 });
    let m = 0;
    const w = fr.x1 - fr.x0 + 22, h = fr.z1 - fr.z0 + 22, X0 = fr.x0 - 14, Z0 = fr.z0 - 9;
    for (let k = 0; k < 80; k++) {
      let s = k / 80 * 2 * (w + h), x, z;
      if (s < w) { x = X0 + s; z = Z0; } else if ((s -= w) < h) { x = X0 + w; z = Z0 + s; } else if ((s -= h) < w) { x = X0 + w - s; z = Z0 + h; } else { s -= w; x = X0; z = Z0 + h - s; }
      if (Math.abs(z) < 3 && x < fr.x0) continue;
      if (z > fr.z1 + 2 && z < fr.z1 + 7 && x > -12 && x < 0) continue;   // the solar array
      M4.trs(bushes.M, m * 16, x + r.range(-0.6, 0.6), 0, z + r.range(-0.6, 0.6), r.range(0, 6.28), r.range(0.8, 1.3), r.range(0.7, 1.2), r.range(0.8, 1.3));
      m++;
    }
    bushes.count = m;
  }

  _buildArms() {
    const n = this.L.arms.length;
    this.armTrolley = this._set(box(1, 1, 1, '#ffffff'), n, { spec: 0.5 });
    this.armWheels = this._set(cylinder(1, 1, 1, 10, '#ffffff', { capBot: true }), n * 4, { spec: 0.4 });
    this.armMast = this._set(cylinder(1, 1, 1, 10, '#ffffff', { capBot: true }), n * 2, { spec: 0.6 });
    this.armJoint = this._set(sphere(1, 10, 7, '#ffffff'), n * 3, { spec: 0.5 });
    this.armLink = this._set(box(1, 1, 1, '#ffffff', { base: true }), n * 2, { spec: 0.45 });
    this.armExtra = this._set(box(1, 1, 1, '#ffffff', { base: true }), n * 8, { spec: 0.4 });
    const auger = new Builder();
    auger.add(cylinder(0.006, 0.028, 0.28, 8, '#d9d9d9', { capBot: true }), mat(0, 0, 0, 0, Math.PI));
    for (let k = 0; k < 4; k++) auger.add(cylinder(0.06 - k * 0.01, 0.06 - k * 0.01, 0.012, 10, '#bfbfbf', { capBot: true }), mat(0, -0.05 - k * 0.055, 0));
    this.toolAuger = this._set(auger, 2, { spec: 0.7 });
    const nozzle = new Builder();
    nozzle.add(cylinder(0.05, 0.025, 0.14, 8, '#e0662c', { capTop: true }), mat(0, 0, 0, 0, Math.PI));
    nozzle.add(box(0.12, 0.05, 0.05, '#2b2b2b'), mat(0.06, -0.04, 0));
    this.toolNozzle = this._set(nozzle, 2, { spec: 0.5 });
    // the watering arm's tool: a gate key above a granule spout
    const key = new Builder();
    key.add(cylinder(0.02, 0.02, 0.22, 8, '#9aa3a8', { capBot: true }), mat(0, 0, 0, 0, Math.PI));
    key.add(box(0.16, 0.025, 0.025, '#3a4045'), mat(0, -0.21, 0));
    key.add(cylinder(0.045, 0.03, 0.08, 8, '#11a39b', { capTop: true }), mat(0.06, -0.02, 0, 0, Math.PI));
    this.toolKey = this._set(key, 2, { spec: 0.5 });
    const grip = new Builder();
    grip.add(box(0.18, 0.06, 0.18, '#d9d9d9'), mat(0, -0.03, 0));
    this.toolGrip = this._set(grip, 2, { spec: 0.5 });
    this.headlight = this._set(cylinder(0.02, 1, 1, 16, '#ffffff', { capTop: false }), n, { transparent: true, additive: true, unlit: true, castShadow: false, doubleSided: true, order: 4 });
    this.scanBar = this._set(box(1, 1, 1, '#ffffff'), 8, { spec: 0.5 });
    this.sheet = this._set(plane(1, 1, '#ffffff'), 2, { transparent: true, additive: true, unlit: true, castShadow: false, doubleSided: true, order: 3 });
    this.selRing = this._set(ring(0.92, 1.0, 40, '#ffffff'), 8, { unlit: true, castShadow: false });
    this.hl = this._set(box(1, 1, 1, '#ffffff'), 600, { unlit: true, castShadow: false });
    this.ghost = this._set(box(1, 1, 1, '#ffffff'), 4, { transparent: true, unlit: true, castShadow: false, order: 2 });
  }

  _buildPlants() {
    this.per = this.low ? 4 : RICE.perSite;
    const m = riceModel(7);
    const max = 72 * this.per;
    this.plantBody = this._set(m.body, max, { spec: 0.08, doubleSided: true });
    this.plantFruit = this._set(m.fruit, max, { spec: 0.3, doubleSided: true });
    this.stubble = this._set(riceStubble(), max, { spec: 0.1 });
  }

  _buildMarkers() {
    const g = new Builder();
    g.add(cylinder(0.065, 0.0, 0.17, 10, '#ffffff', { capTop: false, capBot: false }), mat(0, 0, 0));
    g.add(sphere(0.075, 10, 7, '#ffffff'), mat(0, 0.19, 0));
    this.markers = this._set(g, 80, { unlit: true, castShadow: false });
  }

  _buildLabels() {
    if (!this.labelLayer) return;
    this.labelLayer.innerHTML = '';
    this.labels.clear();
    const mk = (key, cls, html, pos) => {
      const el = document.createElement('div'); el.className = 'lbl ' + cls; el.innerHTML = html; el.setAttribute('aria-hidden', 'true');
      this.labelLayer.appendChild(el);
      this.labels.set(key, { el, pos, cls, last: '' });
    };
    const L = this.L;
    for (const name of ['A', 'B']) mk('field:' + name, 'bed', `Field ${name}`, [(L.fields[name].x[0] + L.fields[name].x[1]) / 2, BUND_H + 0.1, L.outer.z0 + 0.1]);
    L.arms.forEach((a) => mk('arm:' + a.id, 'arm', `<b style="--c:${esc(a.color)}" title="${esc(a.title)}">${a.id}</b>`, [0, 0, 0]));
    const st = L.stations;
    mk('st:seed', 'station', 'Seedling magazine', [st.seed.x, 1.6, st.seed.z]);
    mk('st:store', 'station', 'Fertiliser store', [st.store.x, 1.5, st.store.z]);
    mk('st:chute', 'station', 'Harvest chute', [st.chute.x, 1.5, st.chute.z]);
    mk('st:chem', 'station', 'Spray station', [st.chem.x, 1.5, st.chem.z]);
    mk('pump', 'station', 'Pump', [L.pump.x, 1.25, L.pump.z]);
    for (const name of ['A', 'B']) mk('tube:' + name, 'tube', '', [this.tubes[name].x, GAUGE_ZERO + 0.24, this.tubes[name].bz]);
    mk('battery', 'station', 'Battery', this.batteryPos);
    mk('biogas', 'station', 'Biogas', this.biogasPos);
    mk('solar', 'station', 'Solar PV', this.panelCenter);
  }

  // ── per-frame update ──
  // view: from view.js (viewAt); ui: { overlay, selected, highlight, preview, previewT, showLabels, dark }
  update(dt, timeS, cam, view, ui = {}) {
    this.view = view; this.ui = ui;
    this._updateGlow(timeS, view);
    this._updateArms(timeS, view, ui, dt);
    this._updateScan(view);
    this._updatePlants(view, ui.overlay || null, ui.themeKey || '');
    this._updateSoil(view, ui.overlay || null, ui.themeKey || '');
    this._updateWater(view, timeS, dt);
    this._updateMarkers(timeS, view);
    this._updateHighlights(timeS, view, ui);
    this._updateParticles(dt);
    this._updateRain(dt, cam, view);
    if (this.labelLayer) this._updateLabels(cam, view, ui);
  }

  _updateGlow(timeS, view) {
    const set = this.glow, list = this._glow;
    const pw = view.power || {};
    const pumping = view.fields.A?.pumping || view.fields.B?.pumping;
    list.forEach((b, i) => {
      M4.trs(set.M, i * 16, b[0], b[1], b[2], 0, b[3], b[4], b[5]);
      let c = hexLin(b[6]), k = 1.8;
      if (this._ledSet && this._ledSet.has(i)) k = this.reduced ? 1.6 : 1.2 + 1.6 * (0.5 + 0.5 * Math.sin(timeS * 3 + i * 1.7)) * (hash01(i) > 0.5 ? 1 : 0.6);
      if (i === this.pumpLamp) { c = hexLin(pumping ? '#5ef2a0' : '#8892a0'); k = pumping ? 2.4 : 0.6; }
      set.C.set([c[0], c[1], c[2], 1], i * 4); set.P.set([0, 0, k, 0], i * 4);
    });
    if (this.socBar != null) {
      const b = list[this.socBar], soc = pw.soc ?? 0.6;
      M4.trs(set.M, this.socBar * 16, b[0], 0.1 + soc * 0.9, b[2], 0, b[3], Math.max(0.02, soc * 1.8), b[5]);
      const c = hexLin(soc > 0.5 ? '#5ef2a0' : soc > 0.25 ? '#f2c94c' : '#ef5350'); set.C.set([c[0], c[1], c[2], 1], this.socBar * 4);
    }
    set.count = list.length; set.dirtyM = set.dirtyC = set.dirtyP = true;
    const pv = (this._sunPv ?? 0);
    if (this.panels) for (const idx of this.panels) { const c = mixLin(hexLin('#1d3557'), hexLin('#2f5f9a'), clamp(pv, 0, 1)); this.boxes.C.set([c[0], c[1], c[2], 1], idx * 4); }
    this.boxes.dirtyC = true;
    if (pw.gen_on) this.emit('smoke', this.exhaust[0], this.exhaust[1], this.exhaust[2], this._dtLast || 0.016);
  }

  // ── the four arms: trolley on the rails, telescopic mast, two links and the tool, posed by inverse kinematics ──
  _ik(sh, tgt, L1, L2, fwd) {
    let dx = tgt[0] - sh[0], dy = tgt[1] - sh[1], dz = tgt[2] - sh[2];
    let d = Math.hypot(dx, dy, dz);
    const dmax = L1 + L2 - 0.01, dmin = Math.abs(L1 - L2) + 0.05;
    const u = [dx / (d || 1), dy / (d || 1), dz / (d || 1)];
    d = clamp(d, dmin, dmax);
    let b = [0 - u[1] * u[0], 1 - u[1] * u[1], 0 - u[1] * u[2]];
    let bl = Math.hypot(b[0], b[1], b[2]);
    if (bl < 0.2) { const fd = fwd[0] * u[0] + fwd[2] * u[2]; b = [fwd[0] - fd * u[0], -fd * u[1], fwd[2] - fd * u[2]]; bl = Math.hypot(b[0], b[1], b[2]); }
    b = b.map((v) => v / (bl || 1));
    const a1 = (L1 * L1 - L2 * L2 + d * d) / (2 * d), hgt = Math.sqrt(Math.max(0, L1 * L1 - a1 * a1));
    const el = [sh[0] + u[0] * a1 + b[0] * hgt, sh[1] + u[1] * a1 + b[1] * hgt, sh[2] + u[2] * a1 + b[2] * hgt];
    const wr = [sh[0] + u[0] * d, sh[1] + u[1] * d, sh[2] + u[2] * d];
    return { el, wr };
  }
  _updateArms(timeS, view, ui, dt) {
    const L = this.L, rail = this.fr.rail, M = this.armTrolley.M;
    const night = this._night || 0;
    let wi = 0, mi = 0, ji = 0, li = 0, ei = 0, ai = 0, ni = 0, ki = 0, gi = 0;
    const wheels = this.armWheels, mast = this.armMast, joint = this.armJoint, link = this.armLink, extra = this.armExtra;
    const setCol = (set, i, hex, a = 1, em = 0) => { const c = typeof hex === 'string' ? hexLin(hex) : hex; set.C[i * 4] = c[0]; set.C[i * 4 + 1] = c[1]; set.C[i * 4 + 2] = c[2]; set.C[i * 4 + 3] = a; set.P[i * 4 + 2] = em; };
    const hiArm = ui.highlight?.arm ?? (ui.preview ? ui.preview.arm : null);
    this._armPos = [];
    view.arms.forEach((a, i) => {
      const S = a.scale || 1, L1 = L.link[0] * S, L2 = L.link[1] * S;
      const x = a.x, z = a.z, ry = -a.heading;
      const tw = 0.95 * S, th = 0.3 * S, td = 0.74 * S;
      const pulse = hiArm === a.id ? 0.35 + 0.35 * (this.reduced ? 1 : 0.5 + 0.5 * Math.sin(timeS * 5)) : 0.08;
      M4.trs(M, i * 16, x, rail - 0.02, z, ry, tw, th, td);
      setCol(this.armTrolley, i, a.color, 1, pulse);
      const cy = Math.cos(ry), sy = Math.sin(ry);
      for (const [dx, dz] of [[0.3, 0.26], [-0.3, 0.26], [0.3, -0.26], [-0.3, -0.26]]) {
        const ox = (dx * cy + dz * sy) * S, oz = (-dx * sy + dz * cy) * S;
        M4.trs(wheels.M, wi * 16, x + ox, rail + 0.13, z + oz, ry, 0.07 * S, 0.05 * S, 0.07 * S, Math.PI / 2, 0);
        setCol(wheels, wi, '#2b2f33'); wi++;
      }
      const mastLen = rail - 0.25 - a.shoulderY;
      M4.trs(mast.M, mi * 16, x, rail - 0.15 - 0.85, z, 0, 0.09 * S, 0.85, 0.09 * S); setCol(mast, mi, '#8b959b'); mi++;
      M4.trs(mast.M, mi * 16, x, a.shoulderY, z, 0, 0.06 * S, Math.max(0.05, mastLen - 0.1), 0.06 * S); setCol(mast, mi, '#d7dde0'); mi++;
      const sh = [x, a.shoulderY, z];
      const fwd = [Math.cos(a.heading), 0, Math.sin(a.heading)];
      let tgt = a.target.slice();
      // working motions (cosmetic: the plan fixes where and when, not the wiggle)
      const ph = a.code, wob = this.reduced ? 0 : 1;
      if (a.working) {
        if (ph === 'dig' || ph === 'puddle') tgt[1] += Math.abs(Math.sin(timeS * 9)) * 0.05 * wob;
        if (ph === 'spray' || ph === 'water' || ph === 'spread' || ph === 'amend') { tgt[0] += Math.sin(timeS * 2.2) * 0.1 * wob; tgt[2] += Math.cos(timeS * 1.7) * 0.07 * wob; }
        if (ph === 'cut') tgt[1] += Math.sin(timeS * 4) * 0.04 * wob;
        if (ph === 'valve') { const r = 0.05; tgt[0] += Math.cos(timeS * 3) * r * wob; tgt[2] += Math.sin(timeS * 3) * r * wob; }
      }
      const { el, wr } = this._ik(sh, tgt, L1, L2, fwd);
      const jc = '#3a4045';
      M4.trs(joint.M, ji * 16, sh[0], sh[1], sh[2], 0, 0.12 * S, 0.1 * S, 0.12 * S); setCol(joint, ji, jc); ji++;
      M4.trs(joint.M, ji * 16, el[0], el[1], el[2], 0, 0.085 * S, 0.085 * S, 0.085 * S); setCol(joint, ji, jc); ji++;
      M4.trs(joint.M, ji * 16, wr[0], wr[1], wr[2], 0, 0.06 * S, 0.06 * S, 0.06 * S); setCol(joint, ji, jc); ji++;
      M4.between(link.M, li * 16, sh, el, 0.11 * S, 0.09 * S); setCol(link, li, a.color); li++;
      M4.between(link.M, li * 16, el, wr, 0.08 * S, 0.07 * S); setCol(link, li, '#e6e9eb'); li++;
      const fa = [wr[0] - el[0], wr[1] - el[1], wr[2] - el[2]], fl = Math.hypot(fa[0], fa[1], fa[2]);
      const tipDir = fa.map((v) => v / (fl || 1));
      const roll = ph === 'dig' || ph === 'puddle' ? timeS * 18 * wob : ph === 'valve' ? timeS * 3 * wob : 0;
      const role = a.role;
      const toolSet = role === 'planter' ? this.toolAuger : role === 'protector' ? this.toolNozzle : role === 'waterer' ? this.toolKey : this.toolGrip;
      const tmp = this._tmp || (this._tmp = new Float32Array(16));
      M4.between(tmp, 0, wr, [wr[0] - tipDir[0], wr[1] - tipDir[1], wr[2] - tipDir[2]], 1.3 * S, 1.3 * S, roll);
      const ti = role === 'planter' ? ai++ : role === 'protector' ? ni++ : role === 'waterer' ? ki++ : gi++;
      toolSet.M.set(tmp, ti * 16); toolSet.C.set([1, 1, 1, 1], ti * 4);
      const put = (ox, oy, oz, sx, syy, sz, color, em = 0) => {
        const wx = x + (ox * cy + oz * sy) * S, wz = z + (-ox * sy + oz * cy) * S;
        M4.trs(extra.M, ei * 16, wx, rail - 0.15 + oy * S, wz, ry, sx * S, syy * S, sz * S); setCol(extra, ei, color, 1, em); ei++;
      };
      if (role === 'planter') { put(-0.2, -0.32, 0, 0.32, 0.22, 0.34, '#d8c79a'); put(-0.2, -0.1, 0, 0.26, 0.08, 0.26, '#5c9e3e'); }
      else if (role === 'waterer') { put(-0.2, -0.36, 0, 0.3, 0.3, 0.3, '#11a39b'); put(-0.2, -0.05, 0, 0.2, 0.05, 0.2, '#f1efe6'); }
      else if (role === 'protector') { put(-0.18, -0.36, 0, 0.3, 0.3, 0.3, '#e0662c'); put(-0.18, -0.05, 0, 0.18, 0.06, 0.18, '#fff3e0'); }
      else {
        put(0, -0.5, 0.45, 0.62, 0.36, 0.08, '#8a6a45'); put(0, -0.5, -0.45, 0.62, 0.36, 0.08, '#8a6a45'); put(0, -0.68, 0, 0.62, 0.04, 0.9, '#7a5a3a');
        put(0, -0.66, 0, 0.56, Math.max(0.01, (a.binFill || 0) * 0.32), 0.82, '#d9b24c');
        const open = ph === 'cut' ? 0.05 + 0.05 * Math.abs(Math.sin(timeS * 4)) * wob : 0.09;
        const side = [-tipDir[2], 0, tipDir[0]]; const sl = Math.hypot(side[0], side[2]) || 1;
        for (const sgn of [1, -1]) {
          const base = [wr[0] + side[0] / sl * open * sgn - tipDir[0] * 0.05, wr[1] - tipDir[1] * 0.05, wr[2] + side[2] / sl * open * sgn - tipDir[2] * 0.05];
          const tip = [base[0] + tipDir[0] * 0.2 * S, base[1] + tipDir[1] * 0.2 * S, base[2] + tipDir[2] * 0.2 * S];
          M4.between(extra.M, ei * 16, base, tip, 0.03 * S, 0.06 * S); setCol(extra, ei, '#cfd5d6'); ei++;
        }
      }
      const led = a.working || a.code === 'refill' || a.code === 'unload' || a.code === 'station' ? '#5ef2a0' : a.code === 'wait' || a.code === 'yield' ? '#f2c94c'
        : a.code === 'parked' ? '#8892a0' : '#4fc3f7';
      put(0.25, 0.16, 0, 0.08, 0.06, 0.08, led, 2.2);
      const on = night > 0.4 && a.code !== 'parked';
      M4.trs(this.headlight.M, i * 16, x, 0.02, z, 0, on ? 0.9 * S : 0.001, rail - 0.2, on ? 0.9 * S : 0.001);
      setCol(this.headlight, i, '#fff1c4', on ? 0.09 : 0);
      const eff = a.working ? EFFECT[ph] : null;
      if (eff && !(this.reduced && eff !== 'mist')) {
        const tip = [wr[0] + tipDir[0] * 0.28 * S, wr[1] + tipDir[1] * 0.28 * S, wr[2] + tipDir[2] * 0.28 * S];
        this.emit(eff, tip[0], tip[1], tip[2], dt, a);
      }
      this._armPos.push({ id: a.id, x, z, wr, el, sh });
    });
    this.armTrolley.count = view.arms.length; wheels.count = wi; mast.count = mi; joint.count = ji; link.count = li; extra.count = ei;
    this.toolAuger.count = ai; this.toolNozzle.count = ni; this.toolKey.count = ki; this.toolGrip.count = gi; this.headlight.count = view.arms.length;
    for (const s of [this.armTrolley, wheels, mast, joint, link, extra, this.toolAuger, this.toolNozzle, this.toolKey, this.toolGrip, this.headlight]) s.dirtyM = s.dirtyC = s.dirtyP = true;
  }

  // the scan bar rides along the top of the frame for the first two minutes of every camera pass (06:00 is also the
  // daily soil scan); otherwise it waits at the west end
  _updateScan(view) {
    const fr = this.fr, sb = this.scanBar, sc = view.scan || {};
    const xPark = fr.x0 + 0.5, x0 = this.L.outer.x0 - 0.3, x1 = this.L.outer.x1 + 0.3;
    const sx = sc.active ? lerp(x0, x1, sc.k) : xPark;
    let k = 0;
    M4.trs(sb.M, k * 16, sx, fr.top + 0.22, (fr.z0 + fr.z1) / 2, 0, 0.22, 0.2, fr.z1 - fr.z0 + 0.3); sb.C.set([...hexLin('#f2f4f5'), 1], k * 4); sb.P.set([0, 0, 0.05, 0], k * 4); k++;
    for (const zz of [fr.z0, fr.z1]) { M4.trs(sb.M, k * 16, sx, fr.top + 0.12, zz, 0, 0.5, 0.18, 0.34); sb.C.set([...hexLin('#3d464b'), 1], k * 4); sb.P.set([0, 0, 0, 0], k * 4); k++; }
    for (let q = 0; q < 5; q++) { const zz = lerp(fr.z0 + 1.5, fr.z1 - 1.5, q / 4); M4.trs(sb.M, k * 16, sx, fr.top + 0.06, zz, 0, 0.14, 0.1, 0.14); sb.C.set([...hexLin(sc.active ? '#5ef2a0' : '#1c2226'), 1], k * 4); sb.P.set([0, 0, sc.active ? 1.5 : 0, 0], k * 4); k++; }
    sb.count = k; sb.dirtyM = sb.dirtyC = sb.dirtyP = true;
    const sh = this.sheet, H = this.L.outer.z1 - this.L.outer.z0;
    if (sc.active) {
      const col = sc.soil ? [1.0, 0.78, 0.35] : [0.35, 1.0, 0.75];
      M4.trs(sh.M, 0, sx, fr.top / 2, 0, 0, fr.top, 1, H, 0, Math.PI / 2);
      sh.C.set([col[0], col[1], col[2], 0.1], 0); sh.P.set([0, 0, 0, 0], 0);
      M4.trs(sh.M, 16, sx, 0.035 + BUND_H * 0, 0, 0, 0.12, 1, H); sh.C.set([col[0], col[1], col[2], 0.55], 4); sh.P.set([0, 0, 0.5, 0], 4);
      sh.count = 2; sh.dirtyM = sh.dirtyC = sh.dirtyP = true;
    } else sh.count = 0;
  }

  // rice hills: 9 per square (4 on phones); harvested squares get stubble
  _updatePlants(view, overlay, themeKey) {
    const key = (overlay || '') + themeKey + ['A', 'B'].map((n) => { const F = view.fields[n]; return F ? F.sq.map((q) => q.state + (q.prog == null ? '' : Math.round(q.prog * 200)) + (q.health == null ? '' : Math.round(q.health * 20))
      + (overlay ? `${q.depth == null ? '' : Math.round(q.depth / 5)}${q.soil}${q.risk == null ? '' : Math.round(q.risk * 50)}${q.est == null ? '' : Math.round(q.est * 50)}` : '')).join('') : ''; }).join('|');
    if (key === this._plantKey) return;
    this._plantKey = key;
    // with a plan overlay on, the squares show the overlay's colours (as in the plan view) and the crop is hidden
    if (overlay) {
      for (const s of [this.plantBody, this.plantFruit, this.stubble]) { s.count = 0; s.dirtyM = s.dirtyC = s.dirtyP = true; }
      return;
    }
    const ovCol = null;
    const body = this.plantBody, fruit = this.plantFruit, stub = this.stubble, per = this.per;
    const leafC = hexLin(RICE.leaf), dryC = hexLin(RICE.leafDry), tint = hexLin('#c9b23a'), dead = hexLin('#6b4a2a');
    const unripe = hexLin(RICE.fruitUnripe), ripe = hexLin(RICE.fruit), straw = hexLin('#c9a54a');
    let n = 0, ns = 0, nf = 0;
    const grid = per <= 4 ? 2 : 3;
    for (const name of ['A', 'B']) {
      const F = view.fields[name];
      if (!F) continue;
      for (let j = 0; j < 36; j++) {
        const q = F.sq[j];
        if (q.state === '.') continue;
        const r = Math.floor(j / 6), c = j % 6;
        const ii = internalIndex(name, r, c), p0 = sqXZ(ii);
        const frac = Math.min(1.15, q.prog ?? 1);
        const alive = q.state === 'L';
        for (let k = 0; k < per; k++) {
          const gx = ((k % grid) + 0.5) / grid - 0.5, gz = (Math.floor(k / grid) + 0.5) / grid - 0.5;
          const j1 = hash01(ii, k, 1) - 0.5, j2 = hash01(ii, k, 2) - 0.5, j3 = hash01(ii, k, 3);
          const x = p0.x + gx * 0.82 + j1 * 0.08, z = p0.z + gz * 0.82 + j2 * 0.08;
          if (q.state === 'H') {
            M4.trs(stub.M, ns * 16, x, 0, z, j3 * 6.28, 1.4, 1.0 + 0.3 * j3, 1.4);
            const sc = ovCol ? mixLin(straw, ovCol[j + (name === 'B' ? 36 : 0)], 0.8) : straw;
            stub.C.set([sc[0], sc[1], sc[2], 1], ns * 4); stub.P.set([0, 0, ovCol ? 0.5 : 0, 0], ns * 4); ns++;
            continue;
          }
          // seedling tufts are drawn at least 20 cm tall (transplanted seedlings are 20–25 cm; IRRI Rice Knowledge Bank)
          const can = alive ? Math.max(0.2, canopy(Math.min(1, frac))) : 0.3;
          const size = 0.85 + 0.3 * j3;
          const hS = can * size, wS = (0.25 + 0.75 * can) * size;
          const sen = smoothstep(0.72, 1.0, frac);
          let lc = mixLin(leafC, dryC, sen);
          lc = mixLin(lc, tint, clamp((1 - (q.health ?? 1)) * 0.9, 0, 0.8));
          if (!alive) lc = dead;
          if (ovCol) lc = mixLin(lc, ovCol[j + (name === 'B' ? 36 : 0)], 0.8);
          M4.trs(body.M, n * 16, x, 0.0, z, j3 * 6.28, wS, hS, wS);
          body.C[n * 4] = lc[0]; body.C[n * 4 + 1] = lc[1]; body.C[n * 4 + 2] = lc[2]; body.C[n * 4 + 3] = 1;
          body.P[n * 4] = this.reduced ? 0 : 0.22; body.P[n * 4 + 1] = j3 * 20 + ii * 0.37; body.P[n * 4 + 2] = ovCol ? 0.5 : 0;
          // panicles from late panicle initiation, green turning gold through grain filling
          const fStart = 0.48;
          const fs = alive && frac > fStart ? smoothstep(fStart, fStart + 0.2, frac) : 0;
          const fc = mixLin(unripe, ripe, smoothstep(0.7, 1.0, frac));
          if (fs > 0.01) {
            M4.trs(fruit.M, nf * 16, x, 0.0, z, j3 * 6.28, wS * (0.6 + 0.4 * fs), hS, wS * (0.6 + 0.4 * fs));
            fruit.C[nf * 4] = fc[0]; fruit.C[nf * 4 + 1] = fc[1]; fruit.C[nf * 4 + 2] = fc[2]; fruit.C[nf * 4 + 3] = 1;
            fruit.P[nf * 4] = body.P[n * 4]; fruit.P[nf * 4 + 1] = body.P[n * 4 + 1]; fruit.P[nf * 4 + 2] = frac > 0.6 && frac < 0.7 ? 0.15 : 0;
            nf++;
          }
          n++;
        }
      }
    }
    body.count = n; fruit.count = nf; stub.count = ns;
    for (const s of [body, fruit, stub]) s.dirtyM = s.dirtyC = s.dirtyP = true;
  }

  // soil colour from each square's true water depth: dark mud when saturated, lighter as it dries, hairline cracks
  // once the water is well below the AWD line (display rule, see the README); or a plan-view overlay
  _updateSoil(view, overlay, themeKey) {
    const key = (overlay || 'natural') + '|' + themeKey + '|' + (overlay ? Math.round((this._exposure || 1) * 20) : '') + '|' + ['A', 'B'].map((n) => {
      const F = view.fields[n];
      if (!F) return '';
      return overlay ? F.sq.map((q) => [q.state, q.prog == null ? '' : Math.round(q.prog * 100), q.health == null ? '' : Math.round(q.health * 50), q.depth == null ? '' : Math.round(q.depth / 5),
        q.soil, q.risk == null ? '' : Math.round(q.risk * 50), q.est == null ? '' : Math.round(q.est * 50)].join(',')).join(';')
        : F.sq.map((q) => Math.round(q.wd / 4)).join(',');
    }).join('|');
    for (const name of ['A', 'B']) this._updateElevation(name, view.fields[name]);
    if (key === this._soilKey) return;
    this._soilKey = key;
    const wetC = [78, 60, 46], moistC = [104, 80, 60], dryC = [158, 120, 90], crackC = [50, 37, 28];
    let panel = null;
    if (overlay) panel = cssLin('--panel');
    // overlay texels hold the colour the unlit shader must be given so that, after its tone mapping
    // (1 − e^(−c·exposure)), the square shows the legend's colour: c = −ln(1 − L) / exposure, stored ÷ 4 (the
    // instance colour is 4)
    const E = this._exposure || 1;
    const inv = (L) => clamp(-Math.log(Math.max(1e-4, 1 - Math.min(0.979, L))) / E / 4, 0, 1);
    for (const name of ['A', 'B']) {
      const B = this.basin[name], F = view.fields[name];
      if (!F) continue;
      const d = B.data, tw = B.tw, PX = this.PX;
      for (let j = 0; j < 36; j++) {
        const q = F.sq[j], r = Math.floor(j / 6), c = j % 6;
        let base, crackK = 0, flat = false;
        if (overlay) {
          const [v, op] = cellFill(overlay, toPlanSquare(q));
          const col = cssLin(v.replace(/^var\((--[a-z0-9-]+)\)$/, '$1'));
          base = [0, 1, 2].map((k) => 255 * inv(panel[k] + (col[k] - panel[k]) * op));
          flat = true;
        } else {
          const wd = q.wd;
          const w = smoothstep(-20, -260, wd);
          base = w < 0.5 ? [0, 1, 2].map((k) => lerp(wetC[k], moistC[k], w * 2)) : [0, 1, 2].map((k) => lerp(moistC[k], dryC[k], (w - 0.5) * 2));
          crackK = smoothstep(-150, -300, wd);
        }
        for (let y = r * PX; y < (r + 1) * PX; y++) for (let x = c * PX; x < (c + 1) * PX; x++) {
          const o = (y * tw + x) * 4, ti = y * tw + x;
          let R = base[0], G = base[1], Bl = base[2];
          if (flat) {
            const edge = x === c * PX || y === r * PX;
            if (edge) { R *= 0.72; G *= 0.72; Bl *= 0.72; }
          } else {
            const nn = 1 + B.noise[ti] * 0.22;
            R *= nn; G *= nn; Bl *= nn;
            const ck = crackK * B.crack[ti] * 0.9;
            if (ck > 0) { R = lerp(R, crackC[0], ck); G = lerp(G, crackC[1], ck); Bl = lerp(Bl, crackC[2], ck); }
          }
          d[o] = clamp(R, 0, 255); d[o + 1] = clamp(G, 0, 255); d[o + 2] = clamp(Bl, 0, 255); d[o + 3] = 255;
        }
      }
      this.e.updateTexture(B.tex, d, !!overlay);
      B.soil.count = overlay ? 0 : 1;
      B.flat.count = overlay ? 1 : 0;
      B.flat.C.set([4, 4, 4, 1], 0); B.flat.P.set([0, 0, 0, 0], 0);
      B.soil.dirtyM = B.soil.dirtyC = B.soil.dirtyP = B.flat.dirtyM = B.flat.dirtyC = B.flat.dirtyP = true;
    }
  }
  // the soil surface follows each square's true height (± a few cm: puddling smooths it)
  _updateElevation(name, F) {
    if (!F) return;
    const k = F.sq.map((q) => Math.round(q.elev)).join(',');
    if (k === this._elevKey[name]) return;
    this._elevKey[name] = k;
    const B = this.basin[name], n = B.n, SUB = this.SUB, pos = B.pos;
    const el = (r, c) => F.sq[clamp(r, 0, 5) * 6 + clamp(c, 0, 5)].elev / 1000;
    for (let j = 0; j <= n; j++) for (let i = 0; i <= n; i++) {
      // bilinear between square centres
      const fx = i / SUB - 0.5, fz = j / SUB - 0.5;
      const c0 = Math.floor(fx), r0 = Math.floor(fz), tx = fx - c0, tz = fz - r0;
      const h = lerp(lerp(el(r0, c0), el(r0, c0 + 1), tx), lerp(el(r0 + 1, c0), el(r0 + 1, c0 + 1), tx), tz);
      pos[(j * (n + 1) + i) * 3 + 1] = h;
    }
    const gl = this.e.gl;
    gl.bindVertexArray(null);
    gl.bindBuffer(gl.ARRAY_BUFFER, B.soil.meshRef.bufs[0]);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, pos);
  }

  _updateWater(view, timeS, dt) {
    let anyDrain = false;
    for (const name of ['A', 'B']) {
      const B = this.basin[name], F = view.fields[name], s = B.water;
      if (!F) { s.count = 0; continue; }
      const lvl = F.lvl / 1000;
      // the water surface: at the true level, wherever it stands above the soil (the soil hides it elsewhere)
      const maxDepth = Math.max(...F.sq.map((q) => q.wd));
      if (maxDepth <= 0.5 || (this.ui && this.ui.overlay)) s.count = 0;
      else {
        M4.trs(s.M, 0, B.cx, lvl + 0.003, 0, 0, B.w - 0.01, 1, B.d - 0.01);
        const mean = F.sq.reduce((a, q) => a + Math.max(0, q.wd), 0) / 36;
        const c = hexLin('#3f6a72');
        s.C.set([c[0], c[1], c[2], clamp(0.42 + mean / 150, 0.42, 0.72)], 0); s.P.set([0, 0, 0, 0], 0); s.count = 1;
      }
      s.dirtyM = s.dirtyC = s.dirtyP = true;
      // gates: a wheel turned a quarter turn when open; water pours in or out
      for (const kind of ['inlet', 'outlet']) {
        const g = this.gates[`${kind}_${name}`];
        const open = kind === 'inlet' ? F.pumping : F.draining;
        g.open += ((open ? 1 : 0) - g.open) * Math.min(1, dt * 3);
        M4.trs(g.wheel.M, 0, g.x + 0.18, this.L.gate_handle_y - 0.02, g.z, g.open * Math.PI / 2, 1, 1, 1);
        const col = hexLin(open ? '#3f9f5f' : '#c0392b');
        g.wheel.C.set([col[0], col[1], col[2], 1], 0); g.wheel.P.set([0, 0, 0.15, 0], 0); g.wheel.count = 1; g.wheel.dirtyM = g.wheel.dirtyC = g.wheel.dirtyP = true;
        if (open && !this.reduced) {
          if (kind === 'inlet') this.emit('flow', g.x, 0.14, g.z + 0.48, dt, { id: 'in' + name, dir: [0, 0, 1] });
          else this.emit('flow', g.x, 0.1, g.z + 0.3, dt, { id: 'out' + name, dir: [0, 0, 1] });
        }
      }
      if (F.draining) anyDrain = true;
      // the tube's reading on its board: a blue column up to the reading, amber once it is at the AWD line
      const tb = this.tubes[name], gc = this.gaugeCol;
      const rd = clamp(F.tube, -300, 150) / 1000, bot = GAUGE_ZERO - 0.3;
      const hgt = Math.max(0.004, GAUGE_ZERO + rd - bot);
      M4.trs(gc.M, (name === 'A' ? 0 : 1) * 16, tb.x + 0.012, bot, tb.bz - 0.009, 0, 0.03, hgt, 0.004);
      const line = awdLine(F, this.L.awd_threshold_mm ?? -150);
      const cc = hexLin(line != null && F.tube <= line + 10 ? '#e0a83a' : '#2f7fc1');
      gc.C.set([cc[0], cc[1], cc[2], 1], (name === 'A' ? 0 : 1) * 4); gc.P.set([0, 0, 0.6, 0], (name === 'A' ? 0 : 1) * 4);
    }
    this.gaugeCol.count = 2; this.gaugeCol.dirtyM = this.gaugeCol.dirtyC = this.gaugeCol.dirtyP = true;
    const dw = this.drainWater, o = this.L.outer;
    if (anyDrain) {
      M4.trs(dw.M, 0, (o.x0 + o.x1) / 2, 0.012, this.L.drain_z, 0, o.x1 - o.x0 + 1.2, 0.01, 0.3);
      const c = hexLin('#3f6a72'); dw.C.set([c[0], c[1], c[2], 0.7], 0); dw.P.set([0, 0, 0, 0], 0); dw.count = 1;
    } else dw.count = 0;
    dw.dirtyM = dw.dirtyC = dw.dirtyP = true;
  }

  _updateMarkers(timeS, view) {
    const s = this.markers;
    let n = 0;
    for (const name of ['A', 'B']) {
      const F = view.fields[name];
      if (!F) continue;
      for (let j = 0; j < 36 && n < s.max; j++) {
        const q = F.sq[j];
        if (!(q.pest > 0) || q.state !== 'L') continue;
        const p = sqXZ(internalIndex(name, Math.floor(j / 6), j % 6));
        const h = RICE.height * Math.max(0.2, canopy(Math.min(1, q.prog ?? 0)));
        const k = 0.9 + Math.min(1.2, q.pest * 4);
        M4.trs(s.M, n * 16, p.x, h + 0.3 + (this.reduced ? 0 : 0.05 * Math.sin(timeS * 3 + j)), p.z, 0, k, k, k);
        const col = q.pest >= ETL ? hexLin('#e5392b') : hexLin('#f2a33a');
        s.C.set([col[0], col[1], col[2], 1], n * 4); s.P.set([0, 0, 0.6, 0], n * 4); n++;
      }
    }
    s.count = n; s.dirtyM = s.dirtyC = s.dirtyP = true;
  }

  // selection rings, the selected decision's squares and route, and approve-mode previews (dashed path, ghost arm)
  _updateHighlights(timeS, view, ui) {
    const ring_ = this.selRing, hl = this.hl, Lb = this.lines, D = this.dashes;
    let nr = 0, nh = 0;
    Lb.n = 0; D.n = 0;
    const sqOutline = (f, r, c, col, y = 0.06, w = 0.035) => {
      const p = sqXZ(internalIndex(f, r, c));
      for (const [dx, dz, sx, sz] of [[0, -0.47, 0.94, w], [0, 0.47, 0.94, w], [-0.47, 0, w, 0.94], [0.47, 0, w, 0.94]]) {
        if (nh >= hl.max) return;
        M4.trs(hl.M, nh * 16, p.x + dx, y, p.z + dz, 0, sx, 0.02, sz); hl.C.set([col[0], col[1], col[2], 1], nh * 4); hl.P.set([0, 0, 1.0, 0], nh * 4); nh++;
      }
    };
    const sel = ui.selected;
    if (sel && sel.type === 'arm') {
      const a = view.arms.find((x) => x.id === sel.id);
      if (a) { const r = a.radius; M4.trs(ring_.M, 0, a.x, 0.08, a.z, timeS, r, 1, r); const c = hexLin(a.color); ring_.C.set([c[0] * 1.6, c[1] * 1.6, c[2] * 1.6, 1], 0); ring_.P.set([0, 0, 0.5, 0], 0); nr = 1; }
    } else if (sel && sel.type === 'square') {
      sqOutline(sel.f, sel.r, sel.c, [1.4, 1.2, 0.5], 0.09, 0.05);
    }
    const drawJobRoute = (job, color, dashed, fromXZ) => {
      const c = hexLin(color);
      const pts = [];
      if (fromXZ) pts.push(fromXZ);
      for (const s of job.stops) pts.push([s.x, s.z]);
      const buf = dashed ? D : Lb;
      for (let k = 0; k + 1 < pts.length; k++) {
        const [x0, z0] = pts[k], [x1, z1] = pts[k + 1];
        if (dashed) {
          const len = Math.hypot(x1 - x0, z1 - z0), nseg = Math.max(1, Math.floor(len / 0.25));
          const off = this.reduced ? 0 : (timeS * 0.8) % 1;
          for (let q = 0; q < nseg; q += 2) {
            const a0 = Math.min(1, (q + off) / nseg), a1 = Math.min(1, (q + 1 + off) / nseg);
            buf.push(lerp(x0, x1, a0), 0.32, lerp(z0, z1, a0), lerp(x0, x1, a1), 0.32, lerp(z0, z1, a1), c[0] * 1.8, c[1] * 1.8, c[2] * 1.8, 0.95);
          }
        } else buf.push(x0, 0.3, z0, x1, 0.3, z1, c[0] * 1.6, c[1] * 1.6, c[2] * 1.6, 0.9, 0.9);
      }
      for (const s of job.stops) {
        if (nr >= ring_.max) break;
        M4.trs(ring_.M, nr * 16, s.x, 0.3, s.z, 0, 0.18, 1, 0.18); ring_.C.set([c[0] * 1.5, c[1] * 1.5, c[2] * 1.5, 1], nr * 4); ring_.P.set([0, 0, 0.8, 0], nr * 4); nr++;
      }
    };
    const h = ui.highlight;
    if (h) {
      const col = hexLin('#ffd34d');
      for (const s of h.squares || []) sqOutline(s.f, s.r, s.c, col);
      if (h.job) drawJobRoute(h.job, this.L.arms[h.job.arm - 1]?.color || '#ffffff', false, null);
    }
    const pv = ui.preview;
    const gh = this.ghost;
    gh.count = 0;
    if (pv) {
      const a = view.arms.find((x) => x.id === pv.arm);
      const col = this.L.arms[pv.arm - 1].color;
      for (const t of pv.tasks) if (!t.gate) { const s = sqOfIdx(t.key); if (s) sqOutline(s.f, s.r, s.c, hexLin(col), 0.07, 0.03); }
      drawJobRoute(pv, col, true, a ? [a.x, a.z] : null);
      // the ghost arm runs along the previewed keyframes, looping
      if (pv.track && pv.track.n > 1) {
        const t0 = pv.track.t[0], t1 = pv.track.t[pv.track.n - 1], span = Math.max(1, t1 - t0);
        const loop = Math.max(6, Math.min(20, span / 30));
        const tt = t0 + ((ui.previewT ?? timeS) % loop) / loop * span;
        let i = 0;
        while (i < pv.track.n - 1 && pv.track.t[i + 1] <= tt) i++;
        const k = i < pv.track.n - 1 ? clamp((tt - pv.track.t[i]) / Math.max(1e-6, pv.track.t[i + 1] - pv.track.t[i]), 0, 1) : 0;
        const gx = lerp(pv.track.x[i], pv.track.x[Math.min(pv.track.n - 1, i + 1)], k), gz = lerp(pv.track.z[i], pv.track.z[Math.min(pv.track.n - 1, i + 1)], k);
        const S = this.L.arms[pv.arm - 1].scale || 1, c = hexLin(col);
        M4.trs(gh.M, 0, gx, this.fr.rail - 0.02, gz, 0, 0.95 * S, 0.3 * S, 0.74 * S);
        gh.C.set([c[0] * 1.4, c[1] * 1.4, c[2] * 1.4, 0.45], 0); gh.P.set([0, 0, 0.6, 0], 0); gh.count = 1;
        D.push(gx, this.fr.rail - 0.2, gz, gx, 0.3, gz, c[0] * 1.6, c[1] * 1.6, c[2] * 1.6, 0.6, 0.15);
      }
    }
    gh.dirtyM = gh.dirtyC = gh.dirtyP = true;
    // drop lines from travelling arms to the floor
    for (const a of view.arms) {
      if (a.code === 'travel' || a.code === 'return' || a.code === 'tostation' || a.code === 'yield') {
        const c = hexLin(a.color);
        Lb.push(a.x, this.fr.rail - 0.3, a.z, a.x, 0.15, a.z, c[0] * 1.5, c[1] * 1.5, c[2] * 1.5, 0.35, 0.05);
      }
    }
    ring_.count = nr; hl.count = nh;
    ring_.dirtyM = ring_.dirtyC = ring_.dirtyP = true; hl.dirtyM = hl.dirtyC = hl.dirtyP = true;
  }

  // ── particles ──
  emit(type, x, y, z, dt, src) {
    const T = PARTICLE_TYPES[type]; if (!T) return;
    const key = (src ? src.id : 'g') + type;
    const acc = (this._emitAcc.get(key) || 0) + T.rate * Math.min(dt, 0.1) * (this.low ? 0.5 : 1);
    let n = Math.floor(acc); this._emitAcc.set(key, acc - n);
    const P = this.particles, rnd = Math.random;
    while (n-- > 0) {
      if (P.n >= P.max) return;
      const i = P.n++;
      P.x[i] = x + (rnd() - 0.5) * T.spread * 0.3; P.y[i] = y; P.z[i] = z + (rnd() - 0.5) * T.spread * 0.3;
      P.vx[i] = (rnd() - 0.5) * T.spread * 2; P.vy[i] = T.vy * (0.6 + 0.8 * rnd()); P.vz[i] = (rnd() - 0.5) * T.spread * 2;
      if (src && src.dir) { P.vx[i] += src.dir[0] * 1.1; P.vz[i] += src.dir[2] * 1.1; }
      P.life[i] = T.life * (0.6 + 0.6 * rnd()); P.max0[i] = P.life[i]; P.g[i] = T.grav; P.s[i] = T.size * (0.7 + 0.6 * rnd());
      const c = T.col;
      P.c[i * 4] = c[0]; P.c[i * 4 + 1] = c[1]; P.c[i * 4 + 2] = c[2]; P.c[i * 4 + 3] = c[3];
      if (type === 'harvest' && src && src.x != null) { P.vy[i] = 2.6; P.vx[i] = (src.x - x) * 0.9; P.vz[i] = (src.z - z) * 0.9; }
    }
  }
  _updateParticles(dt) {
    this._dtLast = dt;
    const P = this.particles, B = this.pts;
    let j = 0;
    for (let i = 0; i < P.n; i++) {
      P.life[i] -= dt; if (P.life[i] <= 0 || P.y[i] < -0.05) continue;
      P.vy[i] += P.g[i] * dt; P.x[i] += P.vx[i] * dt; P.y[i] += P.vy[i] * dt; P.z[i] += P.vz[i] * dt;
      if (P.y[i] < 0.03) { P.y[i] = 0.03; P.vy[i] = 0; P.vx[i] *= 0.5; P.vz[i] *= 0.5; }
      if (j !== i) { P.x[j] = P.x[i]; P.y[j] = P.y[i]; P.z[j] = P.z[i]; P.vx[j] = P.vx[i]; P.vy[j] = P.vy[i]; P.vz[j] = P.vz[i]; P.life[j] = P.life[i]; P.max0[j] = P.max0[i]; P.g[j] = P.g[i]; P.s[j] = P.s[i]; for (let q = 0; q < 4; q++) P.c[j * 4 + q] = P.c[i * 4 + q]; }
      j++;
    }
    P.n = j;
    for (let i = 0; i < j; i++) {
      B.P[i * 3] = P.x[i]; B.P[i * 3 + 1] = P.y[i]; B.P[i * 3 + 2] = P.z[i];
      const fade = Math.min(1, P.life[i] / (P.max0[i] * 0.4));
      B.C[i * 4] = P.c[i * 4]; B.C[i * 4 + 1] = P.c[i * 4 + 1]; B.C[i * 4 + 2] = P.c[i * 4 + 2]; B.C[i * 4 + 3] = P.c[i * 4 + 3] * fade;
      B.S[i] = P.s[i];
    }
    B.n = j;
  }
  // rain from the hourly record (mm in the hour); the streaks follow the camera so there is always rain in view
  _updateRain(dt, cam, view) {
    const L = this.rain, w = view.weather || {};
    const rate = w.rain || 0;
    const want = rate > 0.05 ? Math.min(this.low ? 500 : 1200, Math.round(150 + rate * 60)) : 0;
    if (!this.drops) this.drops = Array.from({ length: 1200 }, () => [Math.random() * 60 - 30, Math.random() * 22, Math.random() * 60 - 30]);
    L.n = 0;
    if (!want) return;
    const cx = cam.target[0], cz = cam.target[2];
    const fall = (this.reduced ? 4 : 11) * dt, wind = (w.wind || 2) * 0.08;
    for (let i = 0; i < want; i++) {
      const d = this.drops[i];
      d[1] -= fall; d[0] += wind * dt * 8;
      if (d[1] < 0) { d[1] = 18 + Math.random() * 6; d[0] = Math.random() * 60 - 30; d[2] = Math.random() * 60 - 30; }
      const x = cx + d[0], z = cz + d[2];
      L.push(x, d[1], z, x - wind * 0.4, d[1] - 0.55, z, 0.75, 0.82, 0.95, 0.0, 0.35);
    }
  }

  _updateLabels(cam, view, ui) {
    const narrow = this.labelLayer.clientWidth < 640;
    const shown = [];
    const pw = view.power || {};
    const awd = this.L.awd_threshold_mm ?? -150;
    for (const [key, Lb] of this.labels) {
      let pos = Lb.pos, html = null, show = ui.showLabels !== false;
      if (key.startsWith('arm:')) {
        const a = view.arms.find((x) => x.id === +key.slice(4));
        pos = [a.x, this.fr.rail + 0.55, a.z]; show = true;
      } else if (key.startsWith('field:')) {
        const F = view.fields[key.slice(6)];
        html = `Field ${key.slice(6)}<small>${esc(F ? fieldLine(F) : '')}</small>`;
      } else if (key.startsWith('tube:')) {
        const F = view.fields[key.slice(5)];
        const line = F ? awdLine(F, awd) : null;
        if (F) {
          html = `${Math.round(F.tube)} mm${line == null ? '' : line === 0 ? (F.tube < -5 ? ' · below the surface (kept ponded around flowering)' : '')
            : F.tube <= line - 10 ? ' · below the AWD line' : F.tube <= line + 10 ? ' · at the AWD line' : ''}`;
        }
      } else if (key === 'battery') html = `Battery ${Math.round((pw.soc ?? 0) * 100)}%`;
      else if (key === 'solar') html = `Solar ${(pw.pv_kw ?? 0).toFixed(2)} kW`;
      else if (key === 'biogas') html = `Biogas ${(pw.biogas_m3 ?? 0).toFixed(1)} m³${pw.gen_on ? ' · running' : ''}`;
      else if (key === 'pump') html = (view.fields.A?.pumping || view.fields.B?.pumping) ? 'Pump · running' : 'Pump';
      const p = show ? cam.project(pos) : null;
      const far = (Lb.cls === 'station' && p && p.depth > (narrow ? 10 : 13)) || (Lb.cls === 'tube' && p && p.depth > (narrow ? 16 : 30))
        || (Lb.cls === 'bed' && narrow && p && p.depth > 30);
      if (!p || p.depth > 90 || far) { if (Lb.el.style.display !== 'none') Lb.el.style.display = 'none'; continue; }
      if (html != null && html !== Lb.last) { Lb.el.innerHTML = html; Lb.last = html; Lb.w = null; }
      shown.push([Lb, p, key.startsWith('arm:')]);
    }
    // labels that would overlap a nearer one are hidden (on a narrow screen two field names can land on each other);
    // the arms' markers always show. Sizes are measured once per text, so the frame does no layout work otherwise.
    shown.sort((u, v) => (v[2] - u[2]) || (u[1].depth - v[1].depth));
    const boxes = [];
    for (const [Lb, p, isArm] of shown) {
      if (Lb.el.style.display === 'none') Lb.el.style.display = '';
      if (Lb.w == null) { Lb.w = Lb.el.offsetWidth || 60; Lb.h = Lb.el.offsetHeight || 20; }
      const box = [p.x - Lb.w / 2, p.y - Lb.h, p.x + Lb.w / 2, p.y];
      if (!isArm && boxes.some((q) => box[0] < q[2] + 2 && box[2] > q[0] - 2 && box[1] < q[3] + 2 && box[3] > q[1] - 2)) {
        Lb.el.style.display = 'none';
        continue;
      }
      boxes.push(box);
      Lb.el.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px) translate(-50%, -100%)`;
      Lb.el.style.opacity = String(clamp(1.4 - p.depth / 70, 0.35, 1));
    }
  }

  // ── environment: sun over Bengaluru for the season's date and hour, the hour's cloud and rain ──
  env(view, timeS) {
    const doy = seasonDoy(view.month || 6, view.day || 0), h = view.hour || 12;
    const sp = sunPosition(doy, h);
    const ce = Math.cos(sp.el);
    let sunDir = [Math.sin(sp.az) * ce, Math.sin(sp.el), -Math.cos(sp.az) * ce];
    const sinEl = Math.sin(sp.el);
    const day = smoothstep(-0.08, 0.22, sinEl);
    const w = view.weather || {};
    const cloud = clamp(w.cloud ?? 0.3, 0, 1);
    const raining = (w.rain || 0) > 0.05;
    const night = 1 - smoothstep(-0.12, 0.05, sinEl);
    this._night = night;
    this._sunPv = clamp(sinEl * 1.2, 0, 1) * (1 - 0.7 * cloud);
    const warm = 1 - smoothstep(0.04, 0.45, sinEl);
    let sunCol = mixLin(hexLin('#fff4e2'), hexLin('#ffb070'), warm).map((v) => v * 3.0 * day * (1 - 0.72 * Math.pow(cloud, 1.6)));
    if (sinEl < 0.04) { sunDir = [0.35, 0.82, -0.45]; sunCol = hexLin('#9fb4e0').map((v) => v * 0.4 * night); }
    const Lc = (hex, k = 1) => hexLin(hex).map((v) => v * k);
    const zenithDay = mixLin(Lc('#2f6db8'), Lc('#7c8894'), cloud * 0.85), horizonDay = mixLin(Lc('#cfe3f3'), Lc('#c3c7cb'), cloud * 0.8);
    let zenith = mixLin(zenithDay, Lc('#4a5f9a'), warm * day), horizon = mixLin(horizonDay, Lc('#f2a35e'), warm * day * (1 - cloud * 0.6));
    zenith = mixLin(Lc('#04081a'), zenith, day); horizon = mixLin(Lc('#141d34'), horizon, day);
    const sky = mixLin(Lc('#22324f'), mixLin(Lc('#9fc0e0'), Lc('#a3a8ad'), cloud), day).map((v) => v * (0.75 + 0.25 * day));
    const ground = mixLin(Lc('#151b24'), Lc('#5d5038'), day);
    const fr = this.fr;
    this._exposure = 1.0 + 1.3 * night;
    return {
      sunDir, sunCol, sky, ground, zenith, horizon, fog: horizon.map((v) => v * 0.95), fogD: raining ? 0.016 : 0.0055 + 0.004 * cloud,
      exposure: 1.0 + 1.3 * night, cloud: raining ? 0.97 : cloud, night, time: timeS, wind: this.reduced ? 0 : 0.6 + (w.wind || 2) * 0.35,
      shadowBox: { cx: (fr.x0 + fr.x1) / 2, cz: (fr.z0 + fr.z1) / 2 + 2, r: Math.max(fr.x1 - fr.x0, fr.z1 - fr.z0) * 0.62 + 5 },
      sunVis: day, shadows: sinEl > 0.05 && !this.low,
    };
  }

  // ── picking: an arm (its trolley and mast as spheres) or a square ──
  pick(ray, view) {
    let best = null, bt = Infinity;
    for (const a of view.arms) {
      for (const y of [this.fr.rail - 0.1, (this.fr.rail + a.shoulderY) / 2, a.shoulderY]) {
        const c = [a.x, y, a.z], r = 0.55 * (a.scale || 1);
        const oc = [ray.o[0] - c[0], ray.o[1] - c[1], ray.o[2] - c[2]];
        const b = oc[0] * ray.d[0] + oc[1] * ray.d[1] + oc[2] * ray.d[2], cc = oc[0] ** 2 + oc[1] ** 2 + oc[2] ** 2 - r * r;
        const disc = b * b - cc; if (disc < 0) continue;
        const t = -b - Math.sqrt(disc); if (t > 0 && t < bt) { bt = t; best = { type: 'arm', id: a.id }; }
      }
    }
    if (best) return best;
    if (Math.abs(ray.d[1]) < 1e-6) return null;
    const t = (0.05 - ray.o[1]) / ray.d[1]; if (t < 0) return null;
    const x = ray.o[0] + ray.d[0] * t, z = ray.o[2] + ray.d[2] * t;
    const col = Math.floor(x + 6.5), row = Math.floor(z + 3);
    if (row < 0 || row > 5 || col < 0 || col > 12 || col === 6) return null;
    const f = col < 6 ? 'A' : 'B', c = col < 6 ? col : col - 7;
    return { type: 'square', f, r: row, c };
  }
}

// A rice hill, built at full size (1 m) and scaled per instance: the playground's grass tuft for the tillers and
// leaves, and four drooping panicles drawn as curved strips instead of the playground's 24 grain spheres (a 20th of
// the triangles, so the 3D view stays smooth on phones and software renderers).
function riceModel(seed) {
  let sd = seed;
  const rnd = () => { sd = (sd * 16807) % 2147483647; return (sd - 1) / 2147483646; };
  const body = new Builder(), fruit = new Builder();
  const leafC = '#ffffff', leafDark = '#c8d6c4', H = RICE.height;
  for (let k = 0; k < 14; k++) {
    const a = k / 14 * Math.PI * 2 + rnd() * 0.5, len = H * (0.7 + 0.35 * rnd());
    body.add(blade(len, 0.026 + 0.01 * rnd(), 0.7 + 0.9 * rnd(), leafDark, leafC, 4), mat(0, 0, 0, a, 0, 0.04 + 0.2 * rnd()));
  }
  for (let k = 0; k < 4; k++) {
    const a = k / 4 * Math.PI * 2 + 0.4;
    fruit.add(blade(0.2, 0.028, 2.2, '#ffffff', '#f2f2f2', 4, 0.5), mat(Math.cos(a) * 0.03, H * 0.93, Math.sin(a) * 0.03, -a, 0, 0));
  }
  return { body, fruit };
}

// a square from the view model, in the shape the plan view's colouring takes
export function toPlanSquare(q) {
  return { depth_mm: q.depth, planted: q.state !== '.', alive: q.state === 'L', harvested: q.state === 'H', progress: q.prog, soil: { code: q.soil },
    risk: q.risk, establish: q.est, health: q.health };
}
function sqOfIdx(i) {
  const col = i % 13, r = Math.floor(i / 13);
  if (col === 6) return null;
  return { f: col < 6 ? 'A' : 'B', r, c: col < 6 ? col : col - 7 };
}
const STAGE_NAMES = ['seedlings', 'tillering', 'panicle initiation', 'flowering', 'grain filling', 'ripe'];
// The AWD line that applies to a field now (the engine's rule, Engine._awd_threshold): none before transplanting and from
// the drain-down on; the surface from about a week before flowering to a week after it; otherwise the safe-AWD line.
function awdLine(F, base) {
  if (!F || F.phase !== 'growing' || F.progress == null) return null;
  return F.progress >= 0.5 && F.progress < 0.78 ? 0 : base;
}

function fieldLine(F) {
  const live = F.sq.filter((q) => q.state === 'L');
  if (F.phase === 'harvested' || (F.sq.some((q) => q.state === 'H') && !live.length)) return 'harvested';
  if (!live.length) return F.phase === 'land_prep' ? 'land preparation' : F.phase === 'abandoned' ? 'abandoned' : 'not planted';
  const p = live.reduce((a, q) => a + (q.prog || 0), 0) / live.length;
  const ends = [0.14, 0.45, 0.6, 0.7, 0.9];
  let k = 0; while (k < ends.length && p >= ends[k]) k++;
  return STAGE_NAMES[k];
}
