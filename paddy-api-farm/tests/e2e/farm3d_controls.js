// The 3D farm's camera controls in a browser: orbit, pan and zoom with the mouse, the keyboard and touch.
//   node tests/e2e/farm3d_controls.js [frontend/dist-replay/paddy-replay.html] [tests/results/farm3d_controls.json]
// The camera is read from the outside: the view's test hook projects squares to the screen (window.__PADDY_3D__.square),
// so each gesture is judged by what it does to the picture. Three squares are followed — A1,1 and B6,6 (the two far
// corners) and A1,6 — and three numbers are taken from them: the span (screen distance A1,1 → B6,6; zooming in makes it
// longer), the bearing (the screen angle of A1,1 → B6,6; orbiting turns it) and the middle (where the midpoint of the
// two corners is on the screen; panning moves it). After each gesture the test waits 2.5 s for the camera's easing.
// Touch gestures are sent as real multi-touch input through the Chrome DevTools protocol on a phone-sized page.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const file = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', 'frontend', 'dist-replay', 'paddy-replay.html'));
const out = process.argv[3] || path.join(__dirname, '..', 'results', 'farm3d_controls.json');
const GL_ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];
const results = [];
let failed = 0;
const check = (name, ok, detail) => { results.push({ name, ok: !!ok, detail }); if (!ok) failed++; console.log(`${ok ? 'ok  ' : 'FAIL'} ${name} — ${detail}`); };

async function probe(page) {
  return page.evaluate(() => {
    const h = window.__PADDY_3D__;
    if (!h || !h.square) return null;
    const a = h.square('A1,1'), b = h.square('B6,6'), c = h.square('A1,6'), d = h.square('A6,1'), e = h.square('A6,6');
    if (!a || !b || !c || !d || !e) return null;
    return { a, b, c, d, e, scrollY: window.scrollY, scrollX: window.scrollX, panel: !!document.querySelector('.farm3d-inspect, .inspect-panel, [data-inspect]') };
  });
}
const span = (p) => Math.hypot(p.b.x - p.a.x, p.b.y - p.a.y);
const bearing = (p) => Math.atan2(p.b.y - p.a.y, p.b.x - p.a.x) * 180 / Math.PI;
const middle = (p) => ({ x: (p.a.x + p.b.x) / 2, y: (p.a.y + p.b.y) / 2 });
const dAngle = (x, y) => { let d = Math.abs(x - y) % 360; return d > 180 ? 360 - d : d; };
const moved = (p, q) => Math.hypot(middle(p).x - middle(q).x, middle(p).y - middle(q).y);
const fmt = (p, q) => `span ${span(p).toFixed(0)} → ${span(q).toFixed(0)} px (×${(span(q) / span(p)).toFixed(2)}), bearing ${bearing(p).toFixed(1)}° → ${bearing(q).toFixed(1)}°, middle moved ${moved(p, q).toFixed(0)} px`;

async function settled(page) { await page.waitForTimeout(2500); return probe(page); }

async function open(browser, vp, phone) {
  const ctx = await browser.newContext({ viewport: vp, deviceScaleFactor: 1, isMobile: phone, hasTouch: phone });
  await ctx.addInitScript(() => { try { localStorage.setItem('paddy-farm-view', '3d'); } catch (e) {} });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('file://' + file, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.farm3d-canvas', { timeout: 30000 });
  await page.waitForFunction(() => window.__PADDY_3D__ && window.__PADDY_3D__.square && window.__PADDY_3D__.square('A1,1'), null, { timeout: 30000 });
  await page.waitForTimeout(4000);
  const box = await page.locator('.farm3d-canvas').boundingBox();
  return { ctx, page, errors, box, cx: box.x + box.width / 2, cy: box.y + box.height / 2 };
}

async function drag(page, x, y, dx, dy, opts = {}) {
  await page.mouse.move(x, y);
  await page.mouse.down(opts);
  for (let k = 1; k <= 12; k++) await page.mouse.move(x + dx * k / 12, y + dy * k / 12);
  await page.mouse.up(opts);
}

async function touch(cdp, type, points) {
  await cdp.send('Input.dispatchTouchEvent', { type, touchPoints: points.map(([x, y], id) => ({ x, y, id, radiusX: 4, radiusY: 4, force: 1 })) });
}

(async () => {
  const browser = await chromium.launch({ args: GL_ARGS });

  // ── desktop: mouse and keyboard ──
  {
    const { ctx, page, errors, cx, cy } = await open(browser, { width: 1440, height: 1000 }, false);
    // every gesture starts from the overview preset, so one gesture's result can't confound the next
    const reset = async () => { await page.locator('.farm3d-bar button', { hasText: 'Overview' }).click(); return settled(page); };
    // a pan keeps the camera's distance and angles: the picture shifts but neither turns nor scales much
    const isPan = (a, b) => moved(a, b) > 40 && Math.abs(span(b) / span(a) - 1) < 0.2 && dAngle(bearing(b), bearing(a)) < 6;
    // tilting changes how deep field A looks: the height of its four corner squares' box on the screen over its width
    const tilt = (p) => { const q = [p.a, p.c, p.d, p.e], ys = q.map((v) => v.y), xs = q.map((v) => v.x);
      return (Math.max(...ys) - Math.min(...ys)) / (Math.max(...xs) - Math.min(...xs)); };
    let p0 = await probe(page), p1 = await settled(page);
    check('desktop: the view is still when nobody touches it', p0 && p1 && Math.abs(span(p1) - span(p0)) < 1 && dAngle(bearing(p1), bearing(p0)) < 0.2 && moved(p0, p1) < 1, p0 && p1 ? fmt(p0, p1) : 'no projection');

    p0 = await reset(); await drag(page, cx, cy, 220, 0); p1 = await settled(page);
    check('mouse: drag orbits the view', dAngle(bearing(p1), bearing(p0)) > 5, fmt(p0, p1));

    p0 = await reset(); await drag(page, cx, cy, 0, -40); p1 = await settled(page);
    check('mouse: vertical drag tilts the view', tilt(p1) / tilt(p0) < 0.95, `${fmt(p0, p1)}; field A's height over width on the screen ${tilt(p0).toFixed(3)} → ${tilt(p1).toFixed(3)}`);

    p0 = await reset(); await drag(page, cx, cy, 100, 50, { button: 'right' }); p1 = await settled(page);
    check('mouse: right-drag pans the view', isPan(p0, p1), fmt(p0, p1));

    p0 = await reset(); await page.keyboard.down('Shift'); await drag(page, cx, cy, -100, -50); await page.keyboard.up('Shift'); p1 = await settled(page);
    check('mouse: Shift-drag pans the view', isPan(p0, p1), fmt(p0, p1));

    p0 = await reset(); await page.mouse.move(cx, cy); await page.mouse.wheel(0, -400); p1 = await settled(page);
    check('mouse: the wheel zooms in', span(p1) / span(p0) > 1.25, fmt(p0, p1));
    p0 = p1; await page.mouse.wheel(0, 400); p1 = await settled(page);
    check('mouse: the wheel zooms out', span(p1) / span(p0) < 0.8, fmt(p0, p1));

    const keyTest = async (keys, name, test) => {
      const q0 = await reset();
      await page.locator('.farm3d-canvas').focus();
      for (const k of keys) await page.keyboard.press(k);
      const q1 = await settled(page);
      check(name, test(q0, q1), `${keys.join(' ')}: ${fmt(q0, q1)}; field A's height over width ${tilt(q0).toFixed(3)} → ${tilt(q1).toFixed(3)}`);
    };
    await keyTest(['+', '+', '+'], 'keyboard: + zooms in', (a, b) => span(b) / span(a) > 1.3);
    await keyTest(['-', '-', '-'], 'keyboard: − zooms out', (a, b) => span(b) / span(a) < 0.77);
    await keyTest(['ArrowLeft', 'ArrowLeft', 'ArrowLeft'], 'keyboard: arrows orbit', (a, b) => dAngle(bearing(b), bearing(a)) > 5);
    await keyTest(['d', 'd', 'd'], 'keyboard: WASD pans', isPan);
    await keyTest(['Shift+ArrowRight', 'Shift+ArrowRight', 'Shift+ArrowRight'], 'keyboard: Shift+arrows pan', isPan);
    await keyTest(['PageUp', 'PageUp', 'PageUp'], 'keyboard: PageUp tilts', (a, b) => tilt(b) / tilt(a) > 1.05);
    await keyTest(['PageDown', 'PageDown', 'PageDown'], 'keyboard: PageDown tilts back', (a, b) => tilt(b) / tilt(a) < 0.95);
    p1 = await probe(page);
    check('desktop: the page did not scroll', p1.scrollY === 0 && p1.scrollX === 0, `scroll ${p1.scrollX}, ${p1.scrollY}`);
    check('desktop: no console errors', errors.length === 0, errors.length ? errors.slice(0, 3).join(' | ') : 'none');
    await ctx.close();
  }

  // ── phone: touch ──
  {
    const { ctx, page, errors, cx, cy } = await open(browser, { width: 400, height: 860 }, true);
    const cdp = await ctx.newCDPSession(page);
    const s0 = await probe(page);
    let p0 = await settled(page), p1;

    await touch(cdp, 'touchStart', [[cx, cy]]);
    for (let k = 1; k <= 12; k++) await touch(cdp, 'touchMove', [[cx + 150 * k / 12, cy]]);
    await touch(cdp, 'touchEnd', []);
    p1 = await settled(page);
    check('touch: one-finger drag orbits the view', dAngle(bearing(p1), bearing(p0)) > 5, fmt(p0, p1));

    p0 = p1;
    await touch(cdp, 'touchStart', [[cx - 40, cy], [cx + 40, cy]]);
    for (let k = 1; k <= 12; k++) { const d = 40 + 100 * k / 12; await touch(cdp, 'touchMove', [[cx - d, cy], [cx + d, cy]]); }
    await touch(cdp, 'touchEnd', []);
    p1 = await settled(page);
    check('touch: pinching out zooms in', span(p1) / span(p0) > 1.3, fmt(p0, p1));

    p0 = p1;
    await touch(cdp, 'touchStart', [[cx - 140, cy], [cx + 140, cy]]);
    for (let k = 1; k <= 12; k++) { const d = 140 - 100 * k / 12; await touch(cdp, 'touchMove', [[cx - d, cy], [cx + d, cy]]); }
    await touch(cdp, 'touchEnd', []);
    p1 = await settled(page);
    check('touch: pinching in zooms out', span(p1) / span(p0) < 0.77, fmt(p0, p1));

    p0 = p1;
    await touch(cdp, 'touchStart', [[cx - 50, cy], [cx + 50, cy]]);
    for (let k = 1; k <= 12; k++) { const m = 120 * k / 12; await touch(cdp, 'touchMove', [[cx - 50 + m, cy + m / 2], [cx + 50 + m, cy + m / 2]]); }
    await touch(cdp, 'touchEnd', []);
    p1 = await settled(page);
    check('touch: a two-finger drag pans the view', moved(p0, p1) > 30 && Math.abs(span(p1) / span(p0) - 1) < 0.15, fmt(p0, p1));

    p1 = await probe(page);
    check('touch: the page itself did not scroll or zoom', p1.scrollY === s0.scrollY && p1.scrollX === s0.scrollX
      && (await page.evaluate(() => (window.visualViewport ? window.visualViewport.scale : 1))) === 1, `scroll ${p1.scrollX}, ${p1.scrollY}`);
    check('phone: no console errors', errors.length === 0, errors.length ? errors.slice(0, 3).join(' | ') : 'none');
    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync(out, JSON.stringify({ page: path.basename(file), passed: results.length - failed, failed, results }, null, 1));
  console.log(`${results.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
