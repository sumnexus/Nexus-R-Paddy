// Frame rates of the 3D farm on the replay page, by render quality and window size (software WebGL here: SwiftShader on
// the build machine's CPU cores, no GPU):
//   node tests/e2e/farm3d_fps.js frontend/dist-replay/paddy-replay.html [tests/results/farm3d_fps.json]
// For each setting: open the page, let the view warm up (and its adaptive resolution settle) for 12 s, then read the
// view's own counter (frames in the last second) ten times, once a second. "auto" is what the page picks by itself on
// this machine (two cores → the light render); "high" forces the full render (shadows, 9 hills a square).
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const file = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', 'frontend', 'dist-replay', 'paddy-replay.html'));
const out = process.argv[3] || path.join(__dirname, '..', 'results', 'farm3d_fps.json');
const GL_ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];
const SETTINGS = [
  ['desktop 1440×1000, auto', { width: 1440, height: 1000 }, null, false],
  ['desktop 1440×1000, high', { width: 1440, height: 1000 }, 'high', false],
  ['laptop 1280×720, high', { width: 1280, height: 720 }, 'high', false],
  ['phone 400×860, auto', { width: 400, height: 860 }, null, true],
];

(async () => {
  const browser = await chromium.launch({ args: GL_ARGS });
  const res = [];
  for (const [label, vp, quality, phone] of SETTINGS) {
    const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, isMobile: phone, hasTouch: phone });
    if (quality) await ctx.addInitScript((q) => { try { localStorage.setItem('paddy-3d-quality', q); } catch (e) {} }, quality);
    const page = await ctx.newPage();
    await page.goto('file://' + file, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.farm3d-canvas', { timeout: 30000 });
    await page.waitForTimeout(12000);
    const reads = [];
    for (let k = 0; k < 10; k++) { await page.waitForTimeout(1000); reads.push(parseFloat(await page.locator('.farm3d-stats').textContent())); }
    const st = await page.evaluate(() => window.__PADDY_3D__ && { low: window.__PADDY_3D__.low, tris: window.__PADDY_3D__.tris, draws: window.__PADDY_3D__.draws });
    const v = reads.filter((x) => !Number.isNaN(x)).sort((a, b) => a - b);
    const r = { setting: label, median_fps: v.length ? v[Math.floor(v.length / 2)] : null, reads, light_render: st && st.low, triangles: st && st.tris, draws: st && st.draws };
    console.log(JSON.stringify(r));
    res.push(r);
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(out, JSON.stringify({ machine: 'two CPU cores, no GPU; Chromium with SwiftShader (software WebGL 2)', results: res }, null, 1));
})().catch((e) => { console.error(e); process.exit(2); });
