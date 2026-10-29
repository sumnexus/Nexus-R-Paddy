// Small helpers the 3D scene needs: the playground's hash, random and noise functions (copied from its
// src/core/util.js), the sun's position over Bengaluru (src/core/climate.js), and the rice crop's stages and canopy
// curve (the backend's library and agro.canopy). Display only: nothing here feeds the farm.

export const clamp = (x, a, b) => (x < a ? a : x > b ? b : x);
export const lerp = (a, b, t) => a + (b - a) * t;
export const invLerp = (a, b, x) => clamp((x - a) / (b - a), 0, 1);
export const smoothstep = (a, b, x) => { const t = invLerp(a, b, x); return t * t * (3 - 2 * t); };

export function hash32(x) {
  x = (x >>> 0) ^ 0x9e3779b9;
  x ^= x >>> 16; x = Math.imul(x, 0x21f0aaad);
  x ^= x >>> 15; x = Math.imul(x, 0x735a2d97);
  x ^= x >>> 15;
  return x >>> 0;
}
export function strHash(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
export function hashN(...vals) {
  let h = 0x811c9dc5;
  for (const v of vals) {
    const n = typeof v === 'string' ? strHash(v) : Math.floor(v * 1000003) | 0;
    h = hash32(h ^ hash32(n));
  }
  return h >>> 0;
}
export const hash01 = (...vals) => hashN(...vals) / 4294967296;

function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
export class RNG {
  constructor(seed) { this.seed = seed >>> 0; this._next = mulberry32(this.seed); }
  random() { return this._next(); }
  range(a, b) { return a + (b - a) * this._next(); }
  shuffle(arr) { for (let i = arr.length - 1; i > 0; i--) { const j = Math.floor(this._next() * (i + 1)); const t = arr[i]; arr[i] = arr[j]; arr[j] = t; } return arr; }
}
export function makeNoise2D(seed) {
  const perm = new Uint16Array(512), vals = new Float32Array(256), r = new RNG(seed), p = [];
  for (let i = 0; i < 256; i++) { p.push(i); vals[i] = r.random(); }
  r.shuffle(p);
  for (let i = 0; i < 512; i++) perm[i] = p[i & 255];
  const fade = (t) => t * t * t * (t * (t * 6 - 15) + 10);
  return function (x, y) {
    const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi, X = xi & 255, Y = yi & 255;
    const v00 = vals[perm[X + perm[Y]]], v10 = vals[perm[X + 1 + perm[Y]]], v01 = vals[perm[X + perm[Y + 1]]], v11 = vals[perm[X + 1 + perm[Y + 1]]];
    const u = fade(xf), v = fade(yf);
    return lerp(lerp(v00, v10, u), lerp(v01, v11, u), v);
  };
}
export function fbm(noise, x, y, octaves = 4, lacunarity = 2, gain = 0.5) {
  let amp = 1, freq = 1, sum = 0, norm = 0;
  for (let o = 0; o < octaves; o++) { sum += amp * noise(x * freq, y * freq); norm += amp; amp *= gain; freq *= lacunarity; }
  return sum / norm;
}

// ── the sun over Bengaluru (the playground's climate.sunPosition) ──
const LAT = 12.97, LON = 77.59, D2R = Math.PI / 180;
const declination = (doy) => 0.409 * Math.sin(2 * Math.PI / 365 * doy - 1.39);
export function sunPosition(doy, hour) {
  const d = declination(doy), phi = LAT * D2R;
  const B = 2 * Math.PI * (doy - 81) / 364;
  const eot = 9.87 * Math.sin(2 * B) - 7.53 * Math.cos(B) - 1.5 * Math.sin(B);
  const solarTime = hour + (4 * (LON - 82.5) + eot) / 60;
  const H = (solarTime - 12) * 15 * D2R;
  const sinEl = Math.sin(phi) * Math.sin(d) + Math.cos(phi) * Math.cos(d) * Math.cos(H);
  const el = Math.asin(clamp(sinEl, -1, 1));
  const cosAz = (Math.sin(d) - sinEl * Math.sin(phi)) / (Math.cos(el) * Math.cos(phi) + 1e-9);
  let az = Math.acos(clamp(cosAz, -1, 1));
  if (H > 0) az = 2 * Math.PI - az;
  return { el, az };
}
const MONTH_START = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
// day of year of day `day` of a season that starts on the 1st of `month` (1–12)
export function seasonDoy(month, day) { return ((MONTH_START[month - 1] + day) % 365) + 1; }

// ── rice (the backend library's paddy entry) ──
export const RICE = {
  stages: [['Establishment', 0.14], ['Tillering', 0.45], ['Panicle initiation', 0.6], ['Flowering', 0.7], ['Grain filling', 0.9], ['Maturity', 99]],
  height: 1.0, spread: 0.3, perSite: 9, leaf: '#5c9e3e', leafDry: '#c9a54a', fruit: '#d9b24c', fruitUnripe: '#9cb25a', flower: '#efe7c0',
};
export function stageIndex(frac) {
  for (let k = 0; k < RICE.stages.length; k++) if (frac < RICE.stages[k][1]) return k;
  return RICE.stages.length - 1;
}
export const stageName = (frac) => RICE.stages[stageIndex(frac)][0];
// agro.canopy for paddy: 0.06 + 0.94 (frac / veg_end)^0.85, veg_end = min(0.75, end of stage 3 + 0.05) = 0.65
export const canopy = (frac) => clamp(0.06 + 0.94 * clamp(frac / 0.65, 0, 1) ** 0.85, 0, 1);
export const ETL = 0.08;

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
