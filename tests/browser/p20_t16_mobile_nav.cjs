const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const base = process.env.P20_BASE_URL;
  assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base || '') && process.env.P20_STORAGE_STATE);
  const browser = await chromium.launch({executablePath: process.env.P20_CHROMIUM || '/usr/local/bin/chromium', args: ['--no-sandbox']});
  const ctx = await browser.newContext({storageState: process.env.P20_STORAGE_STATE, viewport: {width:390, height:844}});
  const page = await ctx.newPage();
  for (const [route, active] of [['/app/','Сегодня'], ['/app/chat','Чат'], ['/app/communications','Входящие'], ['/app/planner','Задачи'], ['/app/task-hub/calendar','Задачи']]) {
    assert.equal((await page.goto(base + route)).status(), 200);
    const nav = page.locator('[data-work-mobile-nav]');
    assert(await nav.isVisible());
    assert.equal(await nav.locator('[aria-current="page"]').innerText(), active);
    for (const control of await nav.locator('a, button').all()) {
      const box = await control.boundingBox(); assert(box && box.width >= 40 && box.height >= 40, `small touch target ${route}`);
    }
    await nav.getByRole('button', {name:'Ещё'}).click();
    assert(await page.locator('#navigation-drawer').evaluate(el => el.open));
    await page.locator('#navigation-drawer [data-dialog-close]').click();
    assert.equal(await page.locator('#navigation-drawer').evaluate(el => el.open), false);
    await page.waitForFunction(() => document.activeElement?.textContent.trim() === 'Ещё');
    const overlaps = await page.evaluate(() => {
      const bar = document.querySelector('[data-work-mobile-nav]').getBoundingClientRect();
      return [...document.querySelectorAll('.chat-dock-launcher, .shell-notification-dock')].some(el => {
        const r=el.getBoundingClientRect(); return r.width && r.height && r.bottom > bar.top && r.top < bar.bottom;
      });
    });
    assert.equal(overlaps, false, `dock overlaps nav on ${route}`);
  }
  await page.setViewportSize({width: 1440, height: 900});
  await page.goto(base + '/app/');
  assert.equal(await page.locator('[data-work-mobile-nav]').isVisible(), false);
  await browser.close();
  console.log('PASS mobile nav, active state, drawer, focus, touch targets, dock and desktop');
})().catch(err => { console.error(err); process.exit(1); });
