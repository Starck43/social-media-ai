// Real month data plus a synthetic non-range inventory injected into the HTML.
const {chromium}=require('playwright'); const assert=require('node:assert/strict');
(async()=>{const base=process.env.P20_BASE_URL;assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''));assert(process.env.P20_STORAGE_STATE);const b=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});try{
 const c=await b.newContext({storageState:process.env.P20_STORAGE_STATE});const p=await c.newPage();
 await p.route('**/app/task-hub/calendar?*',async route=>{const res=await route.fetch();let html=await res.text();html=html.replace('</body>', '<section data-calendar-provider="event" data-calendar-grid-eligible="false"><p>Неподтверждённый список</p><ul><li data-calendar-item><span data-calendar-title>ИНВЕНТАРЬ НЕ МЕСЯЦ</span><time datetime="2026-11-20">2026-11-20</time></li></ul></section></body>');await route.fulfill({response:res,body:html});});
 assert.equal((await p.goto(base+'/app/task-hub/calendar?year=2026&month=11')).status(),200);const grid=p.getByRole('list',{name:'Дни выбранного месяца'});await grid.locator('.task-calendar-day').first().waitFor();
 assert(!(await grid.innerText()).includes('ИНВЕНТАРЬ НЕ МЕСЯЦ'));assert(await p.getByText('ИНВЕНТАРЬ НЕ МЕСЯЦ',{exact:true}).isVisible());
 const cells=grid.getByRole('listitem');assert((await cells.count())>=28);assert((await cells.nth(1).getAttribute('aria-label')).includes('2026'));
 const task=grid.getByRole('link',{name:/Проверить цифры в отчёте.*ноября.*2026/});assert(await task.isVisible());assert((await task.getAttribute('href')).startsWith('/app/planner/'));
 await c.close();console.log('PASS accessible dated entries; unverified inventory stays outside grid');
 }finally{await b.close()}})().catch(e=>{console.error(e);process.exit(1)});
