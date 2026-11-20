// Route-backed visual/overflow smoke. Requires a separately seeded local demo.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {
  const base = process.env.P20_BASE_URL;
  assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base || ''), 'Only an isolated local app is allowed');
  assert(process.env.P20_STORAGE_STATE, 'A local synthetic account is required');
  const out = process.env.P20_SCREENSHOT_DIR;
  if (out) fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.P20_CHROMIUM || '/usr/local/bin/chromium', args: ['--no-sandbox'] });
  const routes = [['today','/app/'], ['chat','/app/chat'], ['inbox','/app/communications'], ['planner','/app/planner'], ['tasks','/app/tasks'], ['calendar','/app/task-hub/calendar']];
  let checks = 0;
  for (const width of [390, 768, 1440]) for (const theme of ['light', 'dark']) {
    const context = await browser.newContext({storageState: process.env.P20_STORAGE_STATE, viewport: {width, height: width === 390 ? 844 : 900}});
    await context.addInitScript(value => localStorage.setItem('theme', value), theme);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('response', response => { if (response.status() >= 500 && response.url().startsWith(base)) errors.push(`${response.status()} ${response.url()}`); });
    for (const [name, route] of routes) {
      const response = await page.goto(base + route, {waitUntil:'domcontentloaded'});
      assert.equal(response.status(), 200, `${name}/${width}/${theme}`);
      if (name === 'calendar') await page.waitForSelector('[data-calendar-grid] .task-calendar-day');
      const size = await page.evaluate(() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth}));
      assert(size.scroll <= size.client + 1, `${name}/${width}/${theme}: horizontal viewport overflow ${size.scroll}/${size.client}`);
      if (out) await page.screenshot({path:`${out}/${name}-${width}-${theme}.png`});
      checks++;
    }
    assert.deepEqual(errors, [], `browser errors at ${width}/${theme}`);
    await context.close();
  }
  await browser.close();
  console.log(`PASS ${checks} route/viewport/theme combinations; no viewport overflow or page errors`);
})().catch(error => {console.error(error);process.exit(1)});
