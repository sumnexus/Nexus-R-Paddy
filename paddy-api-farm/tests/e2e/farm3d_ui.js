// Browser test of the 3D farm on the static replay page:
//   node tests/e2e/farm3d_ui.js frontend/dist-replay/paddy-replay.html tests/results/screens3d [live-url]
// For desktop and phone, light and dark: every camera preset is screenshotted (to be looked at); clicking a square
// opens its panel and clicking an arm opens the arm's; "Show on the farm" highlights a decision's arm and squares; the
// 3D/plan switch works; no console errors and no sideways scrolling. Then: the page without WebGL 2 falls back to the
// plan with a note; prefers-reduced-motion is honoured; the frame rate is recorded (software rendering here).
// With a live server URL it also checks approve mode: a pending decision's planned path is drawn before approval.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const file = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', 'frontend', 'dist-replay', 'paddy-replay.html'));
const out = process.argv[3] || path.join(__dirname, '..', 'results', 'screens3d');
const live = process.argv[4] || null;
fs.mkdirSync(out, { recursive: true });
const log = [];
const problems = [];
const check = (ok, what) => { const line = `${ok ? 'ok  ' : 'FAIL'} ${what}`; log.push(line); console.log(line); if (!ok) problems.push(what); };
const PRESETS = ['Overview', 'Field A', 'Field B', 'Planting arm', 'Watering arm', 'Pest arm', 'Harvest arm'];
const GL_ARGS = ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'];

async function clickText(page, sel, text) {
  return page.evaluate(([sel, text]) => { const b = [...document.querySelectorAll(sel)].find((x) => x.textContent.trim() === text); if (b) b.click(); return !!b; }, [sel, text]);
}
async function shot(page, name) {
  const box = await page.evaluate(() => { const el = document.querySelector('.farm3d-stage') || document.querySelector('.farm-plan'); const r = el.getBoundingClientRect(); return { x: r.left + scrollX, y: r.top + scrollY, width: r.width, height: r.height }; });
  await page.screenshot({ path: path.join(out, name + '.png'), clip: box, fullPage: true, timeout: 120000 });
}

(async () => {
  const browser = await chromium.launch({ args: GL_ARGS });
  const fpsNotes = [];
  for (const [kind, vp, scheme] of [['desktop', { width: 1440, height: 1000 }, 'light'], ['desktop', { width: 1440, height: 1000 }, 'dark'],
    ['phone', { width: 400, height: 860 }, 'light'], ['phone', { width: 400, height: 860 }, 'dark']]) {
    const tag = `${kind}-${scheme}`;
    const ctx = await browser.newContext({ viewport: vp, colorScheme: scheme, deviceScaleFactor: 1, isMobile: kind === 'phone', hasTouch: kind === 'phone' });
    // desktops get the full render (this container's 2 cores would otherwise pick the lighter one); phones choose
    if (kind === 'desktop') await ctx.addInitScript(() => { try { localStorage.setItem('paddy-3d-quality', 'high'); } catch (e) {} });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('file://' + file, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.farm3d-canvas', { timeout: 30000 });
    await page.waitForTimeout(4000);
    const st0 = await page.evaluate(() => window.__PADDY_3D__ && { low: window.__PADDY_3D__.low, soft: window.__PADDY_3D__.soft, tris: window.__PADDY_3D__.tris, draws: window.__PADDY_3D__.draws });
    check(!!st0, `${tag}: the 3D farm renders (${st0 ? `${st0.tris} triangles in ${st0.draws} draws${st0.low ? ', light mode' : ''}` : 'no'})`);
    if (kind === 'phone') check(st0 && st0.low, `${tag}: phones get the lighter render`);
    for (const p of PRESETS) {
      await clickText(page, '.farm3d-bar button', p);
      await page.waitForTimeout(p.endsWith('arm') ? 2500 : 2000);
      await shot(page, `3d-${tag}-${p.toLowerCase().replace(/ /g, '-')}`);
    }
    check(true, `${tag}: all ${PRESETS.length} camera presets screenshotted`);
    await clickText(page, '.farm3d-bar button', 'Overview');
    await page.waitForTimeout(2500);
    // inspect: a square, then an arm
    const sq = await page.evaluate(() => window.__PADDY_3D__.square('A3,3'));
    if (sq) await page.mouse.click(sq.x, sq.y);
    await page.waitForTimeout(1500);
    const sqPanel = await page.locator('.square-panel').first().textContent().catch(() => '');
    check(/A3,3/.test(sqPanel || ''), `${tag}: clicking square A3,3 in 3D opens its panel`);
    const arm = await page.evaluate(() => window.__PADDY_3D__.arm(4));
    if (arm) await page.mouse.click(arm.x, arm.y);
    await page.waitForTimeout(1500);
    const armPanel = await page.locator('.arm-panel').first().textContent().catch(() => '');
    check(/Harvest arm/.test(armPanel || ''), `${tag}: clicking the harvest arm opens the arm panel`);
    // a decision from the feed: its arm and squares highlighted
    const showed = await page.evaluate(() => { const b = [...document.querySelectorAll('.feed-rail .linkish')].find((x) => /Show on the farm/.test(x.textContent)); if (b) b.click(); return !!b; });
    // the view reports what it draws from its render loop (every 250 ms of rendering): on a busy machine with software
    // WebGL a frame can take a second or more, so wait for the report rather than a fixed time
    await page.waitForFunction(() => window.__PADDY_3D__ && window.__PADDY_3D__.highlight, null, { timeout: 30000 }).catch(() => null);
    const hl = await page.evaluate(() => window.__PADDY_3D__ && window.__PADDY_3D__.highlight);
    check(showed && !!hl, `${tag}: "Show on the farm" highlights decision ${hl}${showed ? '' : ' (no link found in the feed)'}`);
    await shot(page, `3d-${tag}-highlight`);
    // the plan view and back
    await clickText(page, '.seg button', 'Plan');
    await page.waitForTimeout(800);
    const plan = await page.locator('.plan-svg .sq').count();
    check(plan >= 72, `${tag}: the plan view shows the ${plan} squares`);
    await clickText(page, '.seg button', '3D');
    await page.waitForSelector('.farm3d-canvas', { timeout: 20000 });
    await page.waitForTimeout(1500);
    // the frame rate: the view's own counter (frames in the last second), read six times over seven seconds after the view
    // has warmed up for eight (it was just rebuilt by the switch back from the plan)
    await page.waitForTimeout(8000);
    const reads = [];
    for (let k = 0; k < 6; k++) { await page.waitForTimeout(1200); reads.push(await page.locator('.farm3d-stats').textContent()); }
    const fps = reads.map((x) => parseFloat(x)).filter((x) => !Number.isNaN(x)).sort((x, y) => x - y);
    fpsNotes.push(`${tag}: median ${fps.length ? fps[Math.floor(fps.length / 2)] : '?'} fps (${fps.join(', ')}; ${reads[reads.length - 1].replace(/^[\d.]+ fps · /, '')})`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 1, `${tag}: no sideways scrolling (${overflow}px)`);
    check(errors.length === 0, `${tag}: no console errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
    await ctx.close();
  }
  // no WebGL 2: the plan, with a note
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addInitScript(() => {
      const orig = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (kind, ...rest) { return kind === 'webgl2' ? null : orig.call(this, kind, ...rest); };
    });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('file://' + file, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.plan-svg .sq', { timeout: 20000 });
    const note = await page.locator('.farm-plan .note').first().textContent().catch(() => '');
    check(/WebGL 2/.test(note || ''), `no WebGL 2: the plan is shown with a note ("${(note || '').slice(0, 70)}…")`);
    check((await page.locator('.farm3d-canvas').count()) === 0, 'no WebGL 2: no 3D canvas');
    check(errors.length === 0, `no WebGL 2: no console errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
    await shot(page, 'fallback-no-webgl2');
    await ctx.close();
  }
  // reduced motion
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: 'reduce' });
    const page = await ctx.newPage();
    await page.goto('file://' + file, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.farm3d-canvas', { timeout: 20000 });
    await page.waitForFunction(() => window.__PADDY_3D__ && window.__PADDY_3D__.reduced !== undefined, null, { timeout: 30000 }).catch(() => null);
    const r = await page.evaluate(() => window.__PADDY_3D__ && window.__PADDY_3D__.reduced);
    check(r === true, 'prefers-reduced-motion: the 3D view turns off sway, blinking, bobbing and camera easing');
    await ctx.close();
  }
  // live server, approve mode: the planned path of a pending decision is drawn before approval
  if (live) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await ctx.addInitScript(() => { try { localStorage.setItem('paddy-3d-quality', 'low'); } catch (e) {} });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(live + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.farm3d-canvas', { timeout: 20000 });
    const api = (m, p, b) => page.evaluate(async ([m, p, b]) => (await fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined })).json(), [m, p, b]);
    await api('POST', '/api/sim/reset', { manager: 'api', mode: 'approve', season: 'kharif', seed: 20261024, weatherYear: 0 });
    await api('POST', '/api/sim/start', {});
    let pending = null;
    for (let k = 0; k < 40 && !pending; k++) {
      await page.waitForTimeout(1000);
      const d = await api('GET', '/api/decisions?status=pending_approval');
      pending = (d.decisions || []).find((x) => x.arm_preview);
    }
    if (pending) await page.waitForFunction((id) => window.__PADDY_3D__ && window.__PADDY_3D__.preview === id, pending.id, { timeout: 30000 }).catch(() => null);
    const pv = await page.evaluate(() => window.__PADDY_3D__ && window.__PADDY_3D__.preview);
    check(!!pending && pv === pending.id, `approve mode: decision ${pending && pending.id}'s planned path is drawn before approval (3D shows ${pv})`);
    await shot(page, 'approve-preview');
    // approve it from the page, and the other decisions of that call through the API (the clock waits while any decision
    // is pending): the arm then carries the planned path out
    await page.evaluate(() => { const b = document.querySelector('.feed-rail .btn.approve'); if (b) b.click(); });
    await page.waitForTimeout(1500);
    for (const d of ((await api('GET', '/api/decisions?status=pending_approval')).decisions || [])) await api('POST', `/api/decisions/${d.id}/approve`, {});
    await api('POST', '/api/sim/start', {});
    await page.waitForFunction(() => window.__PADDY_3D__ && window.__PADDY_3D__.arms.some((a) => a.code !== 'parked'), null, { timeout: 30000 }).catch(() => null);
    const busy = await page.evaluate(() => window.__PADDY_3D__ && window.__PADDY_3D__.arms.some((a) => a.code !== 'parked'));
    check(busy, 'approve mode: after approval the arm sets off');
    await shot(page, 'approve-arm-at-work');
    // the live page's arm slow-down control (as on the replay page): "don't slow down" reaches the server
    const hasRate = await page.locator('select.arm-rate').count();
    if (hasRate) await page.selectOption('select.arm-rate', '0');
    await page.waitForTimeout(1500);
    const wr = (await api('GET', '/api/health')).work_rate;
    check(hasRate > 0 && wr >= 3600, `live: the arm slow-down control reaches the server ("don't slow down" → work rate ${wr})`);
    check(errors.length === 0, `live: no console errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
    await api('POST', '/api/sim/pause', {});
    await ctx.close();
  }
  await browser.close();
  log.push('frame rates (software WebGL in this container): ' + fpsNotes.join('; '));
  log.push(problems.length ? `${problems.length} problem(s)` : 'all checks passed');
  fs.writeFileSync(path.join(out, '..', 'farm3d_ui_test.txt'), log.join('\n') + '\n');
  console.log(log[log.length - 1]);
  process.exit(problems.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
