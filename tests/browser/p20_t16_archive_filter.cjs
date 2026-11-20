// Real local FastAPI + PostgreSQL; fixture creates only synthetic threads.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
(async () => {
 const base=process.env.P20_BASE_URL; assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''));
 const a=process.env.P20_ARCHIVED_THREAD, n=process.env.P20_ACTIVE_THREAD; assert(a && n && process.env.P20_STORAGE_STATE);
 const browser=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});
 try { const context=await browser.newContext({storageState:process.env.P20_STORAGE_STATE}); const page=await context.newPage();
   for (const [params,expected] of [[`is_archived=true&thread_id=${a}`,a],[`is_archived=false&thread_id=${n}`,n],[`is_archived=true&thread_id=${n}`,a],[`is_archived=false&thread_id=${a}`,n],[`thread_id=${a}`,a]]) {
     const response=await page.goto(`${base}/app/communications?${params}`); assert.equal(response.status(),200,params);
     assert.equal(await page.locator('[data-inbox-thread-detail]').getAttribute('data-inbox-thread-detail'),expected,params);
   }
   await context.close(); console.log('PASS real HTTP: explicit archive filters, excluded links and ordinary deep link');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
