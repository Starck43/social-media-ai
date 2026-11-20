// Requires the task-owned local demo and 55 temporary same-time agent schedules.
const {chromium}=require('playwright'); const assert=require('node:assert/strict');
(async()=>{const base=process.env.P20_BASE_URL, prefix=process.env.P20_AGENT_PREFIX;assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''));assert(prefix&&process.env.P20_STORAGE_STATE);
const browser=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});try {const c=await browser.newContext({storageState:process.env.P20_STORAGE_STATE});const p=await c.newPage();
assert.equal((await p.goto(base+'/app/task-hub/calendar?timezone=UTC&year=2033&month=4')).status(),200);const panel=p.locator('[data-calendar-provider="agent"]');
const first=await panel.locator('[data-calendar-item]').allTextContents(); assert.equal(first.filter(v=>v.includes(prefix)).length,50);assert(await panel.getByText('Показаны не все расписания за месяц', {exact:false}).isVisible());
await panel.getByRole('link',{name:'Следующие расписания'}).click();await p.waitForLoadState('domcontentloaded'); const second=await p.locator('[data-calendar-provider="agent"] [data-calendar-item]').allTextContents();assert.equal(second.filter(v=>v.includes(prefix)).length,5); assert.equal(new Set([...first,...second]).size,55); assert.equal(new URL(p.url()).searchParams.get('timezone'),'UTC');
assert.equal((await p.goto(base+'/app/task-hub/calendar?timezone=UTC&year=2033&month=3&after_agent_at=2033-04-12T00%3A00%3A00%2B00%3A00&after_agent_id=1')).status(),422);
console.log('PASS agent calendar: two HTTP pages, keyset cursor and month validation');await c.close();}finally{await browser.close()}})().catch(e=>{console.error(e);process.exit(1)});
