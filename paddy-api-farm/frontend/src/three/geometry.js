// ─────────────────────────────────────────────────────────────────────────────
// geometry.js — copied from the Agri Robotics Playground (src/ui/geometry.js) for the Paddy API Farm v2, as an ES
// module. Changes: `import`/`export`; a stubble model for harvested squares (riceStubble). Everything else unchanged.
// ─────────────────────────────────────────────────────────────────────────────
import { GX } from './gl.js';

export const GEO = (() => {
  'use strict';
  const { hexLin } = GX;
  const col = c => (typeof c === 'string' ? hexLin(c) : c);

  class Builder {
    constructor() { this.p = []; this.n = []; this.c = []; this.u = []; this.i = []; }
    v(x, y, z, nx, ny, nz, c, u = 0, w = 0) { this.p.push(x, y, z); this.n.push(nx, ny, nz); this.c.push(c[0], c[1], c[2]); this.u.push(u, w); return this.p.length / 3 - 1; }
    tri(a, b, c) { this.i.push(a, b, c); }
    quad(a, b, c, d) { this.i.push(a, b, c, a, c, d); }
    // append another builder transformed by a 4×4 (column-major) matrix
    add(g, m) {
      const base = this.p.length / 3;
      for (let k = 0; k < g.p.length; k += 3) {
        const x = g.p[k], y = g.p[k + 1], z = g.p[k + 2];
        if (m) {
          this.p.push(m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]);
          const nx = g.n[k], ny = g.n[k + 1], nz = g.n[k + 2];
          let ax = m[0] * nx + m[4] * ny + m[8] * nz, ay = m[1] * nx + m[5] * ny + m[9] * nz, az = m[2] * nx + m[6] * ny + m[10] * nz;
          const l = Math.hypot(ax, ay, az) || 1; this.n.push(ax / l, ay / l, az / l);
        } else { this.p.push(x, y, z); this.n.push(g.n[k], g.n[k + 1], g.n[k + 2]); }
      }
      this.c.push(...g.c); this.u.push(...g.u);
      for (const ix of g.i) this.i.push(ix + base);
      return this;
    }
    build() { return { pos: new Float32Array(this.p), nor: new Float32Array(this.n), col: new Float32Array(this.c), uv: new Float32Array(this.u), idx: this.i }; }
  }
  // 4×4 helpers (column-major): T · Ry · Rx · Rz · S
  function mat(tx = 0, ty = 0, tz = 0, ry = 0, rx = 0, rz = 0, sx = 1, sy = 1, sz = 1) { const o = new Float32Array(16); GX.M4.trs(o, 0, tx, ty, tz, ry, sx, sy, sz, rx, rz); return o; }

  // ── primitives (y-up) ──
  function box(w, h, d, c, opts = {}) {
    const g = new Builder(), C = col(c), y0 = opts.base ? 0 : -h / 2, y1 = y0 + h, x = w / 2, z = d / 2;
    const faces = [
      [[x, y0, -z], [x, y1, -z], [x, y1, z], [x, y0, z], [1, 0, 0]], [[-x, y0, z], [-x, y1, z], [-x, y1, -z], [-x, y0, -z], [-1, 0, 0]],
      [[-x, y1, -z], [-x, y1, z], [x, y1, z], [x, y1, -z], [0, 1, 0]], [[-x, y0, z], [-x, y0, -z], [x, y0, -z], [x, y0, z], [0, -1, 0]],
      [[-x, y0, z], [x, y0, z], [x, y1, z], [-x, y1, z], [0, 0, 1]], [[x, y0, -z], [-x, y0, -z], [-x, y1, -z], [x, y1, -z], [0, 0, -1]],
    ];
    const top = opts.top ? col(opts.top) : C;
    for (const f of faces) {
      const n = f[4], cc = n[1] > 0 ? top : C;
      const a = g.v(...f[0], ...n, cc, 0, 0), b = g.v(...f[1], ...n, cc, 0, 1), e = g.v(...f[2], ...n, cc, 1, 1), h2 = g.v(...f[3], ...n, cc, 1, 0);
      g.quad(a, b, e, h2);
    }
    return g;
  }
  function cylinder(rTop, rBot, h, seg, c, opts = {}) {
    const g = new Builder(), C = col(c), Ct = opts.topCol ? col(opts.topCol) : C;
    const slope = (rBot - rTop) / h;
    for (let s = 0; s <= seg; s++) {
      const a = s / seg * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a);
      const nl = Math.hypot(1, slope);
      g.v(ca * rBot, 0, sa * rBot, ca / nl, slope / nl, sa / nl, C, s / seg, 0);
      g.v(ca * rTop, h, sa * rTop, ca / nl, slope / nl, sa / nl, Ct, s / seg, 1);
    }
    for (let s = 0; s < seg; s++) { const k = s * 2; g.quad(k, k + 1, k + 3, k + 2); }
    if (opts.capTop !== false && rTop > 0) {
      const cIdx = g.v(0, h, 0, 0, 1, 0, Ct); const first = g.p.length / 3;
      for (let s = 0; s <= seg; s++) { const a = s / seg * Math.PI * 2; g.v(Math.cos(a) * rTop, h, Math.sin(a) * rTop, 0, 1, 0, Ct); }
      for (let s = 0; s < seg; s++) g.tri(cIdx, first + s + 1, first + s);
    }
    if (opts.capBot && rBot > 0) {
      const cIdx = g.v(0, 0, 0, 0, -1, 0, C); const first = g.p.length / 3;
      for (let s = 0; s <= seg; s++) { const a = s / seg * Math.PI * 2; g.v(Math.cos(a) * rBot, 0, Math.sin(a) * rBot, 0, -1, 0, C); }
      for (let s = 0; s < seg; s++) g.tri(cIdx, first + s, first + s + 1);
    }
    return g;
  }
  function sphere(r, seg, rings, c, opts = {}) {
    const g = new Builder(), C = col(c), C2 = opts.bottom ? col(opts.bottom) : C;
    const hemi = opts.hemi ? 0.5 : 1;
    for (let j = 0; j <= rings; j++) {
      const v = j / rings * hemi, th = v * Math.PI, y = Math.cos(th), s = Math.sin(th);
      const cc = [C[0] + (C2[0] - C[0]) * v, C[1] + (C2[1] - C[1]) * v, C[2] + (C2[2] - C[2]) * v];
      for (let i = 0; i <= seg; i++) { const a = i / seg * Math.PI * 2; g.v(Math.cos(a) * s * r, y * r, Math.sin(a) * s * r, Math.cos(a) * s, y, Math.sin(a) * s, cc, i / seg, v); }
    }
    for (let j = 0; j < rings; j++) for (let i = 0; i < seg; i++) { const a = j * (seg + 1) + i, b = a + seg + 1; g.quad(a, b, b + 1, a + 1); }
    return g;
  }
  function plane(w, d, c, sx = 1, sz = 1, colFn = null) {
    const g = new Builder(), C = col(c);
    for (let j = 0; j <= sz; j++) for (let i = 0; i <= sx; i++) {
      const u = i / sx, v = j / sz, x = (u - 0.5) * w, z = (v - 0.5) * d;
      g.v(x, 0, z, 0, 1, 0, colFn ? colFn(x, z) : C, u, v);
    }
    for (let j = 0; j < sz; j++) for (let i = 0; i < sx; i++) { const a = j * (sx + 1) + i, b = a + sx + 1; g.quad(a, b, b + 1, a + 1); }
    return g;
  }
  function ring(r0, r1, seg, c) {
    const g = new Builder(), C = col(c);
    for (let s = 0; s <= seg; s++) { const a = s / seg * Math.PI * 2, ca = Math.cos(a), sa = Math.sin(a); g.v(ca * r0, 0, sa * r0, 0, 1, 0, C); g.v(ca * r1, 0, sa * r1, 0, 1, 0, C); }
    for (let s = 0; s < seg; s++) { const k = s * 2; g.quad(k, k + 2, k + 3, k + 1); }
    return g;
  }
  // a curved, tapering blade (grass leaf, maize leaf, palm frond leaflet); base at origin, grows along +Y then bends toward +X
  function blade(len, width, bend, c0, c1, segs = 4, a0 = 0) {
    const g = new Builder(), A = col(c0), B = col(c1);
    let x = 0, y = 0, ang = a0;
    const pts = [];
    for (let s = 0; s <= segs; s++) {
      const t = s / segs;
      pts.push([x, y, t]);
      ang = a0 + bend * t * t; const step = len / segs;
      x += Math.sin(ang) * step; y += Math.cos(ang) * step;
    }
    for (let s = 0; s <= segs; s++) {
      const [px, py, t] = pts[s], w = width * (1 - t * 0.92) * (s === 0 ? 0.7 : 1);
      const cc = [A[0] + (B[0] - A[0]) * t, A[1] + (B[1] - A[1]) * t, A[2] + (B[2] - A[2]) * t];
      const tang = s < segs ? [pts[s + 1][0] - px, pts[s + 1][1] - py] : [px - pts[s - 1][0], py - pts[s - 1][1]];
      const tl = Math.hypot(...tang); const nx = -tang[1] / tl, ny = tang[0] / tl; // normal in the bend plane
      g.v(px, py, -w / 2, nx, ny, 0, cc); g.v(px, py, w / 2, nx, ny, 0, cc);
    }
    for (let s = 0; s < segs; s++) { const k = s * 2; g.quad(k, k + 2, k + 3, k + 1); }
    return g;
  }
  // flat elliptical leaf in the XZ plane with a slight cup, centre at origin, tip along +X
  function leaf(len, width, c, cup = 0.15) {
    const g = new Builder(), C = col(c), seg = 6;
    const mid = g.v(len * 0.5, cup * width, 0, 0, 1, 0, C);
    const ring = [];
    for (let s = 0; s < seg * 2; s++) {
      const a = s / (seg * 2) * Math.PI * 2;
      const x = len * 0.5 + Math.cos(a) * len * 0.5, z = Math.sin(a) * width * 0.5 * Math.pow(Math.abs(Math.sin(a)), 0.15);
      ring.push(g.v(x, 0, z, 0, 1, 0, C));
    }
    for (let s = 0; s < ring.length; s++) g.tri(mid, ring[(s + 1) % ring.length], ring[s]);
    return g;
  }
  const merge = (...gs) => { const b = new Builder(); for (const [g, m] of gs) b.add(g, m || null); return b; };

  // ── plant models ──
  // Every model is built at full size in metres; the renderer scales instances by growth.
  // Returns {body, fruit} builders; fruit holds ears/flowers/fruit and is coloured per instance.
  function plantModel(crop, rngSeed = 1) {
    const v = crop.visual, H = v.height, S = v.spread;
    let seed = rngSeed;
    const rnd = () => { seed = (seed * 16807) % 2147483647; return (seed - 1) / 2147483646; };
    const leafC = '#ffffff', leafDark = '#c8d6c4'; // greyscale-ish; tinted by the instance colour
    const body = new Builder(), fruit = new Builder();
    if (crop.form === 'grass') {
      // a tuft: thin arching blades
      const n = 9;
      for (let k = 0; k < n; k++) {
        const a = k / n * Math.PI * 2 + rnd() * 0.5, len = H * (0.75 + 0.3 * rnd());
        body.add(blade(len, 0.018 + 0.006 * rnd(), 0.9 + 0.8 * rnd(), leafDark, leafC, 5), mat(0, 0, 0, a, 0, 0.05 + 0.15 * rnd()));
      }
      if (crop.id === 'paddy' || /rice/i.test(crop.name)) {
        for (let k = 0; k < 4; k++) { // drooping panicles
          const a = k / 4 * Math.PI * 2 + 0.4;
          for (let s = 0; s < 6; s++) { const t = s / 5; fruit.add(sphere(0.022, 5, 3, '#ffffff'), mat(Math.cos(a) * (0.03 + t * 0.12), H * (1.0 - 0.18 * t * t), Math.sin(a) * (0.03 + t * 0.12), 0, 0, 0, 0.7, 1.4, 0.7)); }
        }
      } else { // finger-like ear heads (ragi)
        for (let k = 0; k < 6; k++) { const a = k / 6 * Math.PI * 2; fruit.add(cylinder(0.008, 0.012, 0.13, 5, '#ffffff', { capTop: true }), mat(0, H * 1.0, 0, a, 0, 0.9)); }
        fruit.add(sphere(0.02, 6, 4, '#ffffff'), mat(0, H, 0));
      }
    } else if (crop.form === 'tallgrass') {
      // maize: stalk, long arching leaves, tassel, one cob
      body.add(cylinder(0.012, 0.022, H, 6, '#e8efe2', { capTop: false }));
      for (let k = 0; k < 9; k++) {
        const y = H * (0.12 + 0.085 * k), a = k * 2.4 + rnd() * 0.3;
        body.add(blade(0.55 + 0.25 * Math.sin(k / 9 * Math.PI), 0.07, 1.6, leafDark, leafC, 5), mat(0, y, 0, a, 0, 0.35));
      }
      for (let k = 0; k < 5; k++) fruit.add(cylinder(0.004, 0.006, 0.22, 4, '#ffffff'), mat(0, H, 0, k * 1.25, 0, 0.35 + 0.2 * (k % 2)));
      fruit.add(cylinder(0.035, 0.04, 0.2, 7, '#ffffff', { capBot: true }), mat(0.05, H * 0.5, 0, 0, 0, -0.5));
    } else if (crop.form === 'creeper') {
      // groundnut: low mound of paired leaflets on spreading stems
      for (let k = 0; k < 7; k++) {
        const a = k / 7 * Math.PI * 2 + rnd() * 0.4, r = S * 0.45 * (0.6 + 0.4 * rnd());
        body.add(cylinder(0.006, 0.008, r, 4, '#d9e2d0', { capTop: false }), mat(0, 0.03, 0, a, 0, -1.2));
        for (let q = 0; q < 3; q++) {
          const t = 0.35 + q * 0.3, x = Math.cos(a) * r * t, z = Math.sin(a) * r * t, y = H * (0.45 + 0.55 * Math.sin(t * Math.PI) * 0.8);
          body.add(leaf(0.07, 0.045, leafC), mat(x, y, z, -a + 0.6, 0.15, 0.2));
          body.add(leaf(0.07, 0.045, leafC), mat(x, y, z, -a - 0.6, -0.15, 0.2));
        }
      }
      for (let k = 0; k < 6; k++) { const a = k * 1.05; fruit.add(sphere(0.014, 5, 3, '#ffffff'), mat(Math.cos(a) * S * 0.18, H * 0.8, Math.sin(a) * S * 0.18)); }
    } else { // bush: tomato, brinjal, chilli, beans and any imported bush crop
      body.add(cylinder(0.01, 0.016, H * 0.85, 5, '#dbe5d3', { capTop: false }));
      const tiers = 5;
      for (let t = 0; t < tiers; t++) {
        const y = H * (0.18 + 0.75 * t / tiers), rr = S * 0.5 * (1 - 0.35 * Math.abs(t / tiers - 0.45));
        const n = 4 + (t % 2);
        for (let k = 0; k < n; k++) {
          const a = k / n * Math.PI * 2 + t * 0.7 + rnd() * 0.3;
          const ll = (crop.id === 'brinjal' ? 0.2 : crop.id === 'chilli' ? 0.11 : 0.14) * (0.8 + 0.4 * rnd());
          body.add(cylinder(0.004, 0.006, rr * 0.6, 3, '#dbe5d3', { capTop: false }), mat(0, y, 0, -a, 0, -1.25));
          body.add(leaf(ll, ll * 0.6, k % 2 ? leafC : leafDark, 0.2), mat(Math.cos(a) * rr * 0.55, y + 0.02, Math.sin(a) * rr * 0.55, -a, 0, -0.25));
        }
      }
      if (crop.id === 'tomato') body.add(cylinder(0.008, 0.008, H * 1.05, 4, '#a07a4c', { capTop: true }), mat(0.05, 0, 0.04)); // stake
      const nf = crop.id === 'chilli' ? 9 : crop.id === 'beans' ? 8 : crop.id === 'brinjal' ? 4 : 6;
      for (let k = 0; k < nf; k++) {
        const a = k / nf * Math.PI * 2 + 0.3, y = H * (0.3 + 0.45 * ((k * 0.37) % 1)), r = S * 0.28;
        const m = mat(Math.cos(a) * r, y, Math.sin(a) * r);
        if (crop.id === 'tomato') fruit.add(sphere(0.035, 7, 5, '#ffffff'), m);
        else if (crop.id === 'brinjal') fruit.add(sphere(0.035, 7, 5, '#ffffff'), mat(Math.cos(a) * r, y - 0.05, Math.sin(a) * r, 0, 0, 0.2, 1, 2.6, 1));
        else if (crop.id === 'chilli') fruit.add(cylinder(0.003, 0.009, 0.075, 5, '#ffffff', { capBot: true }), mat(Math.cos(a) * r, y, Math.sin(a) * r, a, 0, 3.0));
        else if (crop.id === 'beans') fruit.add(cylinder(0.006, 0.007, 0.12, 4, '#ffffff', { capBot: true }), mat(Math.cos(a) * r, y, Math.sin(a) * r, a, 0, 2.9));
        else fruit.add(sphere(0.03, 6, 4, '#ffffff'), m);
      }
    }
    return { body, fruit };
  }

  // PADDY: what a harvested square looks like — a hill of straw stubs cut 15 cm above the mud
  function riceStubble() {
    const g = new Builder();
    for (let k = 0; k < 7; k++) {
      const a = k / 7 * Math.PI * 2, r = 0.012 + 0.018 * ((k * 0.37) % 1);
      g.add(cylinder(0.006, 0.007, 0.13 + 0.03 * ((k * 0.61) % 1), 5, '#ffffff', { capTop: true }), mat(Math.cos(a) * r, 0, Math.sin(a) * r, 0, 0.08 * Math.cos(a), 0.08 * Math.sin(a)));
    }
    return g;
  }

  return { Builder, mat, box, cylinder, sphere, plane, ring, blade, leaf, merge, plantModel, riceStubble, col };
})();
