// Offline fixture: verify fail-closed against the real app, then mock success UI only.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.P20_BASE_URL;
 assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''));
 assert(process.env.P20_STORAGE_STATE);
 const browser=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});
 const context=await browser.newContext({storageState:process.env.P20_STORAGE_STATE});
 const page=await context.newPage();
 await page.goto(base+'/app/communications?triage_category=needs_reply');
 const form=page.locator('[data-triage-reply-draft-form]');
 assert.equal(await form.count(),1);
 await form.locator('button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('[data-reply-draft-status]')?.textContent.includes('не создан'));
 assert((await form.locator('[data-reply-draft-status]').innerText()).includes('исходное сообщение'));
 assert.equal(new URL(page.url()).pathname,'/app/communications');
 console.log('PASS real offline missing-contact draft is refused without sending');
 await page.route('**/app/communications/reply', route=>route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({ok:true})}));
 await form.locator('button[type=submit]').click();
 await form.getByRole('link',{name:'Открыть исходящие'}).waitFor();
 assert(await form.locator('button[type=submit]').isDisabled());
 assert((await form.locator('[data-reply-draft-status]').innerText()).includes('отправка не выполнялась'));
 console.log('PASS mocked draft success offers outbox link and blocks duplicate');
 await context.close();await browser.close();
})().catch(error=>{console.error(error);process.exit(1)});
