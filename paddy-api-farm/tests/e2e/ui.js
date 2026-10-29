// Browser test of the website against a running backend with the stand-in API:
//   (cd backend && python -m paddy serve --port 8812 --manager api --mode approve --mock) &
//   node tests/e2e/ui.js http://127.0.0.1:8812/ tests/results/screens
// Desktop and phone, light and dark. From the page itself: approve and reject decisions, start a new season,
// run and pause the clock, add a storm, open a decision's call, every view, keyboard navigation of the squares,
// and finally run a whole season against the stand-in. No console errors, no sideways scrolling, and a page that
// says what to do when the backend is down.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const base = (process.argv[2] || 'http://127.0.0.1:8812/').replace(/\/?$/, '/');
const out = process.argv[3] || path.join(__dirname, '..', 'results', 'screens');
fs.mkdirSync(out, { recursive: true });

const log = [];
const problems = [];
const t0 = Date.now();
const check = (ok, what) => { log.push(`${ok ? 'ok  ' : 'FAIL'} ${what}`); if (!ok) problems.push(what); console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); };
async function asJson(r, what) {
  const text = await r.text();
  try { return JSON.parse(text); } catch (e) { throw new Error(`${what}: HTTP ${r.status}, not JSON: ${text.slice(0, 120)}`); }
}
const api = async (p) => asJson(await fetch(base + p.replace(/^\//, '')), `GET ${p}`);
async function post(p, body) {
  const r = await fetch(base + p.replace(/^\//, ''), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  return asJson(r, `POST ${p}`);
}
async function waitFor(fn, timeoutMs, everyMs = 400) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const v = await fn();
    if (v) return v;
    if (Date.now() > end) return null;
    await new Promise((r) => setTimeout(r, everyMs));
  }
}
async function openMenu(page, item) {
  await page.click('.controls button[aria-haspopup="menu"]');
  await page.click(`.menu-list button:has-text("${item}")`);
}

async function firstPass(page, tag) {
  // 1. The 06:00 decision point in approve mode: decisions wait for the person.
  await page.click('button[aria-label="Step one simulated hour"]');
  await page.waitForSelector('.dec.pending_approval', { timeout: 30000 });
  const n0 = await page.locator('.dec.pending_approval').count();
  check(n0 > 1, `${tag}: ${n0} decisions wait for approval`);
  const srcChip = await page.locator('.dec.pending_approval .chip.src-mock').first().textContent();
  check(/stand-in/.test(srcChip || ''), `${tag}: stand-in decisions are labelled "${srcChip}"`);
  await page.locator('.dec.pending_approval .btn.approve').first().click();
  await page.waitForTimeout(800);
  const second = page.locator('.dec.pending_approval').first();
  await second.locator('input').fill('not today, testing');
  await second.locator('.btn.reject').click();
  await page.waitForTimeout(800);
  let data = await api('api/decisions');
  // v2: an approved decision is handed to its arm at once and carried out as the clock moves (the arm takes real time)
  const appr = data.decisions.find((d) => d.approved_by === 'user');
  check(!!appr && ['queued', 'executing', 'executed'].includes(appr.status), `${tag}: approving from the page hands the decision to its arm (${appr ? appr.status : 'none'})`);
  check(data.decisions.some((d) => d.status === 'rejected_by_person' && d.rejection_reason === 'not today, testing'), `${tag}: rejecting from the page records the reason`);
  for (const d of data.decisions.filter((x) => x.status === 'pending_approval')) await post(`/api/decisions/${d.id}/approve`);
  // the rejection goes back to the decision maker in the next state (the 18:00 call: a decision point is handled
  // when the clock moves on from it, so 13 hours from 06:00)
  await post('/api/sim/step', { hours: 13 });
  const run = (await api('api/health')).run_id;
  const exp = await api(`api/runs/${run}/export?format=json`);
  const lastState = exp.calls[exp.calls.length - 1].state;
  check(JSON.stringify(lastState.recent_decisions || []).includes('not today, testing'), `${tag}: the person's reason reaches the decision maker in the next call`);
  const done = appr ? (await api('api/decisions')).decisions.find((d) => d.id === appr.id) : null;
  check(!!done && done.status === 'executed', `${tag}: the approved decision is carried out by its arm once the clock moves (${done ? done.status : 'none'})`);

  // 2. A new season from the page's own dialog: the API, carried out automatically.
  await openMenu(page, 'New season');
  await page.selectOption('dialog[open] select[name="manager"]', 'api');
  await page.selectOption('dialog[open] select[name="mode"]', 'auto');
  await page.selectOption('dialog[open] select[name="season"]', 'kharif');
  await page.fill('dialog[open] input[name="seed"]', '20261024');
  await page.fill('dialog[open] input[name="weatherYear"]', '0');
  await page.click('dialog[open] button[type="submit"]');
  const h1 = await waitFor(async () => { const h = await api('api/health'); return h.day === 0 && h.mode === 'auto' && h.manager === 'api' && h.calls === 0 ? h : null; }, 15000);
  check(!!h1, `${tag}: "New season" from the page resets to day 0 with the API in auto mode`);

  // 3. Run and pause from the page (4 days a second, so the page has time to show the Pause button). v2: the server slows
  // its clock while an arm works, so the live 3D view can show it; this check is about the buttons, so the arms' slow-down
  // is turned off first (what the page's "arms: don't slow down" does)
  await post('/api/sim/speed', { x: 96, work: 3600 });
  await page.selectOption('#speed', '96');
  await page.click('.controls button:has-text("Run")');
  await page.waitForSelector('.controls button:has-text("Pause")', { timeout: 15000 });
  const h2 = await waitFor(async () => { const h = await api('api/health'); return h.day >= 52 ? h : null; }, 180000, 250);
  await page.locator('.controls button:has-text("Pause")').click({ timeout: 10000 });
  const h3 = (await waitFor(async () => { const h = await api('api/health'); return h.status === 'paused' ? h : null; }, 8000, 200)) || (await api('api/health'));
  check(!!h2 && h3.status === 'paused', `${tag}: Run and Pause from the page (status ${h3.status} on day ${h3.day}, ${h3.calls} calls so far)`);
  await post('/api/sim/speed', { x: h3.speed || 96, work: 30 });   // back to the server's default slow-down for the arms

  // 4. A storm from the page: the decision maker is called at once, and the rain arrives.
  const farm0 = await api('api/farm');
  await openMenu(page, 'Add an event');
  await page.selectOption('dialog[open] select[name="kind"]', 'storm');
  await page.click('dialog[open] button[type="submit"]');
  const toast = await page.waitForSelector('.toast', { timeout: 10000 }).then((el) => el.textContent()).catch(() => '');
  check(/Event added/.test(toast || ''), `${tag}: adding a storm from the page is confirmed ("${toast}")`);
  const w = await api('api/weather');
  check(w.events.some((e) => /storm/i.test(e.text)), `${tag}: the storm is in the weather log: "${(w.events.find((e) => /storm/i.test(e.text)) || {}).text}"`);
  await page.click('button[aria-label="Step one simulated hour"]');
  await page.waitForTimeout(800);
  const exp2 = await api(`api/runs/${(await api('api/health')).run_id}/export?format=json`);
  const evCall = exp2.calls.find((c) => c.t >= 0 && (c.triggers || []).includes('event'));
  check(!!evCall, `${tag}: the storm triggers a decision call (${evCall ? evCall.time + ', triggers ' + evCall.triggers.join('+') : 'none'})`);
  await page.click('.controls button:has-text("1 day")');
  await page.waitForTimeout(1500);
  const farm1 = await api('api/farm');
  const rainA = (farm1.fields.A.rain_mm || 0) - (farm0.fields.A.rain_mm || 0);
  check(rainA >= 80, `${tag}: about 95 mm of storm rain reaches the fields (${rainA} mm on A in a day)`);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.plan-svg .sq', { timeout: 20000 });
}

async function wholeSeason(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, colorScheme: 'light' });
  // v2: these checks are about the plan view (the 3D farm has its own test, farm3d_ui.js)
  await ctx.addInitScript(() => { try { localStorage.setItem('paddy-farm-view', 'plan'); } catch (e) { /* private window */ } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(e.message));
  await post('/api/sim/reset', { manager: 'api', mode: 'auto', season: 'kharif', seed: 20261027, weatherYear: 0 });
  await page.goto(base + '#fields', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.plan-svg', { timeout: 20000 });
  await page.selectOption('#speed', '500');
  const started = Date.now();
  await page.click('.controls button:has-text("Run")');
  const fin = await waitFor(async () => { const h = await api('api/health'); return h.status === 'finished' ? h : null; }, 600000, 1000);
  const secs = Math.round((Date.now() - started) / 1000);
  check(!!fin, `whole season: a kharif season runs to the end from the page (${secs} s, ${fin ? fin.calls : '?'} calls to the stand-in)`);
  const farm = await api('api/farm');
  const ya = farm.fields.A.yield_t_ha, yb = farm.fields.B.yield_t_ha;
  check(farm.fields.A.harvested && farm.fields.B.harvested, `whole season: both fields harvested (A ${ya} t/ha, B ${yb} t/ha)`);
  const dec = await api('api/decisions');
  const bySource = {};
  for (const d of dec.decisions) bySource[d.source] = (bySource[d.source] || 0) + 1;
  check(Object.keys(bySource).length > 0 && Object.keys(bySource).every((s) => s === 'mock'), `whole season: every decision came from the stand-in (${JSON.stringify(bySource)})`);
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(out, 'season-end-fields.png'), fullPage: true });
  for (const tab of ['Decisions', 'Charts', 'Compare']) {
    await page.click(`.nav button:has-text("${tab}")`);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(out, `season-end-${tab.toLowerCase()}.png`), fullPage: true });
  }
  const base1 = await waitFor(async () => { const f = await api('api/farm'); return f.baseline && f.baseline.status === 'done' ? f.baseline : null; }, 120000, 1000);
  check(!!base1, `whole season: the conventional run on the same weather finished alongside (${base1 ? base1.summary.yield_t_ha : '?'} t/ha against ${((ya + yb) / 2).toFixed(2)})`);
  check(errors.length === 0, `whole season: no console errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
  await ctx.close();
}

(async () => {
  await post('/api/sim/reset', { manager: 'api', mode: 'approve', season: 'kharif', seed: 20261024, weatherYear: 0 });
  const browser = await chromium.launch();
  const runs = [['desktop', { width: 1440, height: 1000 }, 'light'], ['desktop', { width: 1440, height: 1000 }, 'dark'],
                ['phone', { width: 400, height: 860 }, 'light'], ['phone', { width: 400, height: 860 }, 'dark']];
  let first = true;
  for (const [kind, vp, scheme] of runs) {
    const tag = `${kind}-${scheme}`;
    const ctx = await browser.newContext({ viewport: vp, colorScheme: scheme });
    // v2: these checks are about the plan view (the 3D farm has its own test, farm3d_ui.js)
    await ctx.addInitScript(() => { try { localStorage.setItem('paddy-farm-view', 'plan'); } catch (e) { /* private window */ } });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto(base + '#fields', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.plan-svg', { timeout: 20000 });
    check(true, `${tag}: fields view loads`);
    if (first) {
      await firstPass(page, tag);
      first = false;
    }
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(out, `${tag}-fields.png`), fullPage: kind === 'desktop' });
    // keyboard: select a square and move with the arrows
    await page.locator('.plan-svg .sq').first().click();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(300);
    const title = await page.locator('.square-panel h2').first().textContent();
    check(/Square A2,2/.test(title || ''), `${tag}: arrow keys move the selection (${title})`);
    for (const layer of ['Crop stage', 'Soil', 'Pest risk', 'Establishment', 'Water']) {
      await page.click(`.layers button:has-text("${layer}")`);
      await page.waitForTimeout(150);
    }
    await page.click('.layers button:has-text("Soil")');
    await page.screenshot({ path: path.join(out, `${tag}-fields-soil.png`), fullPage: false });
    await page.click('.layers button:has-text("Water")');
    for (const tab of ['Decisions', 'Charts', 'Compare', 'Models']) {
      await page.click(`.nav button:has-text("${tab}")`);
      await page.waitForTimeout(900);
      await page.screenshot({ path: path.join(out, `${tag}-${tab.toLowerCase()}.png`), fullPage: kind === 'desktop' });
      if (tab === 'Decisions') {
        await page.locator('.linkish:has-text("Details")').first().click();
        await page.waitForSelector('text=The state that was sent', { timeout: 10000 });
        const hasState = await page.locator('pre.json').last().textContent();
        check((hasState || '').includes('"fields"'), `${tag}: a decision's detail shows the state that was sent`);
        await page.screenshot({ path: path.join(out, `${tag}-decision-detail.png`), fullPage: false });
        await page.click('button:has-text("Back to the decisions")');
      }
    }
    // keyboard only: Tab reaches the navigation and Enter switches views
    await page.keyboard.press('Home');
    await page.focus('.nav button:has-text("Fields")');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(300);
    check(await page.locator('.plan-svg').count() > 0, `${tag}: views switch from the keyboard`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 1, `${tag}: no sideways scrolling (${overflow}px)`);
    check(errors.length === 0, `${tag}: no console errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
    await ctx.close();
  }
  await wholeSeason(browser);
  // backend down: the page says so
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 700 } });
  // v2: these checks are about the plan view (the 3D farm has its own test, farm3d_ui.js)
  await ctx.addInitScript(() => { try { localStorage.setItem('paddy-farm-view', 'plan'); } catch (e) { /* private window */ } });
  const page = await ctx.newPage();
  await page.route('**/api/**', (r) => r.abort());
  await page.goto(base, { waitUntil: 'domcontentloaded' });
  const banner = await page.waitForSelector('.banner[role="alert"]', { timeout: 15000 }).then((el) => el.textContent()).catch(() => null);
  check(!!banner, `backend down: the page says so ("${(banner || '').slice(0, 90)}…")`);
  await page.screenshot({ path: path.join(out, 'backend-down.png') });
  await ctx.close();
  await browser.close();
  log.push(`${problems.length ? problems.length + ' problem(s)' : 'all checks passed'} in ${Math.round((Date.now() - t0) / 1000)} s`);
  fs.writeFileSync(path.join(out, '..', 'ui_test.txt'), log.join('\n') + '\n');
  console.log(log[log.length - 1]);
  process.exit(problems.length ? 1 : 0);
})().catch((e) => { console.error(e); fs.writeFileSync(path.join(out, '..', 'ui_test.txt'), log.join('\n') + `\nERROR ${e.stack}\n`); process.exit(2); });
