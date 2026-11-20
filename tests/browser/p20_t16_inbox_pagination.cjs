// Requires a local synthetic inbox with >50 fyi threads and one older needs_reply.
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const base = process.env.P20_BASE_URL;
  assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base || ''));
  assert(process.env.P20_STORAGE_STATE);
  const browser = await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});
  const context = await browser.newContext({storageState:process.env.P20_STORAGE_STATE});
  const page = await context.newPage();
  let response = await page.goto(base+'/app/communications?triage_category=needs_reply');
  assert.equal(response.status(),200);
  assert.equal(await page.locator('[data-inbox-thread-item]').count(),0);
  const next = page.getByRole('link',{name:'Следующие входящие →'});
  const nextUrl = new URL(await next.getAttribute('href'),base);
  const cursor = nextUrl.searchParams.get('before_id');
  assert(cursor);
  await next.click();
  assert((await page.locator('[data-inbox-thread-detail]').innerText()).includes('Когда будет дайджест?'));
  await page.goto(base+`/app/communications?triage_category=fyi&before_id=${cursor}`);
  const excludedId = await page.locator('[data-inbox-thread-item]').first().getAttribute('data-inbox-thread-item');
  assert(excludedId);
  await page.goto(base+`/app/communications?triage_category=needs_reply&before_id=${cursor}&thread_id=${excludedId}`);
  assert((await page.locator('[data-inbox-thread-detail]').innerText()).includes('Когда будет дайджест?'));
  console.log('PASS filtered pagination and exclusion of out-of-filter thread');
  await context.close(); await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
