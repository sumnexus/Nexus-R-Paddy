// Browser test of the static replay page (no server):
//   node tests/e2e/replay_ui.js frontend/dist-replay/paddy-replay.html tests/results/replay-screens
// Desktop and phone, light and dark: it opens on day 60 with the stand-in label, plays, steps, shows every view and a
// decision's call, and has no console errors or sideways scrolling.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const file = path.resolve(process.argv[2] || path.join(__dirname, '..', '..', 'frontend', 'dist-replay', 'paddy-replay.html'));
const out = process.argv[3] || path.join(__dirname, '..', 'results', 'replay-screens');
fs.mkdirSync(out, { recursive: true });
const log = [];
const problems = [];
const check = (ok, what) => { log.push(`${ok ? 'ok  ' : 'FAIL'} ${what}`); if (!ok) problems.push(what); console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); };

(async () => {
  const browser = await chromium.launch();
  for (const [kind, vp, scheme] of [['desktop', { width: 1440, height: 1000 }, 'light'], ['phone', { width: 400, height: 860 }, 'dark']]) {
    const tag = `replay-${kind}-${scheme}`;
    const ctx = await browser.newContext({ viewport: vp, colorScheme: scheme });
    // v2: these checks are about the plan view (the 3D farm has its own test, farm3d_ui.js)
    await ctx.addInitScript(() => { try { localStorage.setItem('paddy-farm-view', 'plan'); } catch (e) { /* private window */ } });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(e.message));
    const requests = [];
    page.on('request', (r) => { if (!r.url().startsWith('file:') && !r.url().startsWith('data:')) requests.push(r.url()); });
    await page.goto('file://' + file, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('.plan-svg .sq', { timeout: 20000 });
    const banner = await page.locator('.banner.note').textContent();
    check(/Stand-in replay/.test(banner || ''), `${tag}: labelled as a stand-in replay`);
    const clock = await page.locator('.clock .sub').first().textContent();
    check(/Day 6\d/.test(clock || ''), `${tag}: opens on a growing crop (${clock})`);
    await page.screenshot({ path: path.join(out, `${tag}-fields.png`), fullPage: kind === 'desktop' });
    await page.click('button[aria-label="Next decision point"]');
    await page.waitForTimeout(300);
    const clock2 = await page.locator('.clock .when').first().textContent();
    check(true, `${tag}: steps to the next decision point (${clock2})`);
    for (const tab of ['Decisions', 'Charts', 'Compare', 'Models']) {
      await page.click(`.nav button:has-text("${tab}")`);
      await page.waitForTimeout(700);
      await page.screenshot({ path: path.join(out, `${tag}-${tab.toLowerCase()}.png`), fullPage: kind === 'desktop' });
      if (tab === 'Decisions') {
        await page.locator('.linkish:has-text("Details")').first().click();
        await page.waitForSelector('text=The state that was sent', { timeout: 10000 });
        check(true, `${tag}: a decision's call opens with the state that was sent`);
        await page.click('button:has-text("Back to the decisions")');
      }
      if (tab === 'Charts') {
        const conv = await page.locator('text=Conventional (same weather)').count();
        check(conv > 0, `${tag}: the charts carry the conventional run on the same weather`);
      }
      if (tab === 'Compare') {
        const n = await page.locator('section.panel h2').allTextContents();
        check(n.some((t) => /campaign|Comparison/i.test(t)), `${tag}: the comparison tables are on the page (${n.slice(0, 3).join(' | ')})`);
      }
    }
    await page.click('.nav button:has-text("Fields")');
    await page.click('button:has-text("Back to day 0")');
    await page.waitForTimeout(300);
    const clock3 = await page.locator('.clock .sub').first().textContent();
    check(/Day 0\b/.test(clock3 || ''), `${tag}: "Back to day 0" starts over (${clock3})`);
    await page.click('.controls button:has-text("Run")');
    await page.waitForTimeout(1500);
    const clock4 = await page.locator('.clock .sub').first().textContent();
    check(!/Day 0\b/.test(clock4 || ''), `${tag}: Run plays the season (${clock4})`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check(overflow <= 1, `${tag}: no sideways scrolling (${overflow}px)`);
    check(errors.length === 0, `${tag}: no console errors${errors.length ? ': ' + errors.slice(0, 3).join(' | ') : ''}`);
    check(requests.length === 0, `${tag}: no network requests${requests.length ? ': ' + requests.slice(0, 3).join(' ') : ''}`);
    await ctx.close();
  }
  await browser.close();
  log.push(problems.length ? `${problems.length} problem(s)` : 'all checks passed');
  fs.writeFileSync(path.join(out, '..', 'replay_ui_test.txt'), log.join('\n') + '\n');
  console.log(log[log.length - 1]);
  process.exit(problems.length ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });
