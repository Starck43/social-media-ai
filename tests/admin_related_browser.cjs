/* Offline Chromium fixture: all requests intercepted; no app or providers. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const script = fs.readFileSync('app/static/js/admin-related.js', 'utf8');
let checks = 0;
const check = (value) => { assert.ok(value); checks++; };
(async () => {
  const browser = await chromium.launch({executablePath: process.env.CHROMIUM_PATH || '/usr/local/bin/chromium', headless: true, args: ['--no-sandbox']});
  try {
    const context = await browser.newContext();
    await context.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== 'https://offline.test') return route.abort();
      if (url.pathname === '/related.js') return route.fulfill({contentType:'text/javascript', body:script});
      if (url.pathname === '/saved') {
        const saved = {type:'admin-related-saved',token:url.searchParams.get('token'),identity:'target',id:'9',label:'<img src=x onerror="alert(1)"> saved'};
        const payload = JSON.stringify(saved).replaceAll('<', '\\u003c');
        return route.fulfill({contentType:'text/html', body:`<!doctype html><meta charset="utf-8"><p role="status">Saved</p><button id="related-close">Close</button><script type="application/json" id="admin-related-saved">${payload}</script><script src="/related.js" defer></script>`});
      }
      if (url.pathname === '/parent') {
        const controls = [{field:'target', label:'Target', identity:'target', create:'https://offline.test/create', edit:'https://offline.test/edit/__related_pk__'},
          {field:'targets', label:'Targets', identity:'target', create:'https://offline.test/create', edit:'https://offline.test/edit/__related_pk__'}];
        return route.fulfill({contentType:'text/html', body:`<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><form method="POST"><input id="draft" value="unsaved parent"><label for="target">Target</label><select id="target"><option value="">None</option><option value="1" selected>One</option></select><label for="targets">Targets</label><select id="targets" multiple><option value="1" selected>One</option><option value="2" selected>Two</option></select></form><script type="application/json" id="admin-related-controls">${JSON.stringify(controls)}</script><script src="/related.js" defer></script></html>`});
      }
      if (url.pathname.startsWith('/create') || url.pathname.startsWith('/edit/')) {
        return route.fulfill({contentType:'text/html', body:`<!doctype html><meta charset="utf-8"><form method="POST"><input name="name" value="Saved"><button>Save</button></form><script type="application/json" id="admin-related-controls">[]</script><script src="/related.js" defer></script>`});
      }
      return route.abort();
    });
    const page = await context.newPage();
    for (const width of [390, 1440]) {
      await page.setViewportSize({width, height:900});
      await page.goto('https://offline.test/parent');
      await page.waitForSelector('button');
      const buttons = page.locator('button');
      check(await buttons.count() === 4);
      check(await buttons.nth(3).isDisabled());
      // Actual popup object must match event.source; attacker messages cannot.
      const popupPromise = page.waitForEvent('popup');
      await buttons.nth(0).click();
      const popup = await popupPromise;
      await popup.waitForSelector('input[name="_related_token"]', {state:'attached'});
      const token = new URL(popup.url()).searchParams.get('_related_token');
      check(await popup.locator('input[name="_related_token"]').inputValue() === token);
      await page.evaluate((token) => {
        const data={type:'admin-related-saved',token,identity:'target',id:'9',label:'forged'};
        dispatchEvent(new MessageEvent('message',{origin:'https://evil.test',source:window,data}));
        dispatchEvent(new MessageEvent('message',{origin:location.origin,source:window,data}));
      }, token);
      check(await page.locator('#target').inputValue() === '1');
      await popup.evaluate((token) => {
        opener.postMessage({type:'admin-related-saved',token,identity:'wrong-target',id:'9',label:'wrong'},location.origin);
      }, token);
      await page.waitForTimeout(30);
      check(await page.locator('#target').inputValue() === '1');
      const label = '<img src=x onerror="alert(1)"> saved';
      // Execute the actual child completion branch, not just a hand-built event.
      await popup.goto(`https://offline.test/saved?token=${token}`).catch(error => {
        if (!popup.isClosed() && !error.message.includes('closed')) throw error;
      });
      await page.waitForFunction(() => document.getElementById('target').value === '9');
      check(await page.locator('#target option:checked').textContent() === label);
      check(await page.locator('img').count() === 0);
      check(await page.locator('#draft').inputValue() === 'unsaved parent');
      if (!popup.isClosed()) await popup.close();
      // M2M add preserves all existing selections, then edit a single selection.
      const multiPopup = page.waitForEvent('popup');
      await buttons.nth(2).click();
      const multi = await multiPopup;
      await multi.waitForLoadState();
      const mt = new URL(multi.url()).searchParams.get('_related_token');
      await multi.evaluate((token) => opener.postMessage({type:'admin-related-saved',token,identity:'target',id:'3',label:'Three'},location.origin),mt);
      await page.waitForFunction(() => document.getElementById('targets').selectedOptions.length === 3);
      check(JSON.stringify(await page.locator('#targets').evaluate(s => Array.from(s.selectedOptions,o=>o.value))) === '["1","2","3"]');
      await multi.close();
      await page.selectOption('#targets',['3']);
      check(await buttons.nth(3).isEnabled());
      const editPopup = page.waitForEvent('popup');
      await buttons.nth(3).click();
      const edit = await editPopup;
      await edit.waitForLoadState();
      check(new URL(edit.url()).pathname === '/edit/3');
      const et = new URL(edit.url()).searchParams.get('_related_token');
      await edit.evaluate(token => opener.postMessage({type:'admin-related-saved',token,identity:'target',id:'99',label:'wrong id'},location.origin),et);
      await page.waitForTimeout(30);
      check(await page.locator('#targets option[value="99"]').count() === 0);
      await edit.evaluate(token => opener.postMessage({type:'admin-related-saved',token,identity:'target',id:'3',label:'Renamed'},location.origin),et);
      await page.waitForFunction(() => document.querySelector('#targets option[value="3"]').textContent === 'Renamed');
      check(await page.locator('#targets').inputValue() === '3');
      await edit.close();
      // Cancellation and popup blocker do not alter selections or draft input.
      const cancelled = page.waitForEvent('popup');
      await buttons.nth(0).click();
      await (await cancelled).close();
      check(await page.locator('#target').inputValue() === '9');
      await page.evaluate(() => { window.open = () => null; });
      await buttons.nth(0).click();
      check((await page.locator('[role="status"]').first().textContent()).includes('заблокировано'));
      check(await page.locator('#draft').inputValue() === 'unsaved parent');
    }
    console.log(`PASS: ${checks} offline browser assertions; 390/1440px; all requests intercepted`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
