/* Offline Chromium fixture: all requests intercepted; no app or providers. */
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const path = require('node:path');
const adminStatics = process.env.SQLADMIN_STATIC_DIR;
if (!adminStatics) throw new Error('Set SQLADMIN_STATIC_DIR to the isolated SQLAdmin statics directory');
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
      const assets = {
        '/jquery.js': ['js/jquery.min.js', 'text/javascript'],
        '/select2.js': ['js/select2.full.min.js', 'text/javascript'],
        '/select2.css': ['css/select2.min.css', 'text/css'],
        '/tabler.css': ['css/tabler.min.css', 'text/css'],
        '/admin-main.css': ['css/main.css', 'text/css'],
      };
      if (assets[url.pathname]) {
        const [file, contentType] = assets[url.pathname];
        return route.fulfill({contentType, body:fs.readFileSync(path.join(adminStatics,file))});
      }
      if (url.pathname === '/related.css') return route.fulfill({contentType:'text/css', body:fs.readFileSync('app/static/css/admin-related.css')});
      if (url.pathname === '/theme.css') return route.fulfill({contentType:'text/css', body:fs.readFileSync('app/static/css/theme.css')});
      if (url.pathname === '/custom.css') return route.fulfill({contentType:'text/css', body:fs.readFileSync('app/static/css/custom.css')});
      if (url.pathname === '/related.js') return route.fulfill({contentType:'text/javascript', body:script});
      if (url.pathname === '/saved') {
        const saved = {type:'admin-related-saved',token:url.searchParams.get('token'),identity:'target',id:'9',label:'<img src=x onerror="alert(1)"> saved'};
        const payload = JSON.stringify(saved).replaceAll('<', '\\u003c');
        return route.fulfill({contentType:'text/html', body:`<!doctype html><meta charset="utf-8"><p role="status">Saved</p><button id="related-close">Close</button><script type="application/json" id="admin-related-saved">${payload}</script><script src="/related.js" defer></script>`});
      }
      if (url.pathname === '/parent') {
        const controls = [{field:'target', label:'Target', identity:'target', create:'https://offline.test/create', edit:'https://offline.test/edit/__related_pk__'},
          {field:'targets', label:'Targets', identity:'target', create:'https://offline.test/create', edit:'https://offline.test/edit/__related_pk__'}];
        const theme = url.searchParams.get('theme') || 'light';
        const useSelect2 = url.searchParams.get('widget') === 'select2';
        const enhancement = useSelect2 ? '<script src="/jquery.js"></script><script src="/select2.js"></script><script>jQuery("#target,#targets").select2({width:"100%"});</script>' : '';
        return route.fulfill({contentType:'text/html', body:`<!doctype html><html lang="ru" data-theme="${theme}" data-bs-theme="${theme}"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/tabler.css"><link rel="stylesheet" href="/select2.css"><link rel="stylesheet" href="/admin-main.css"><link rel="stylesheet" href="/theme.css"><link rel="stylesheet" href="/custom.css"><link rel="stylesheet" href="/related.css"><body><main class="admin-console" style="padding:20px;width:100%"><section class="card" style="width:100%;max-width:1050px;margin:auto"><div class="card-header"><h2 class="card-title">Редактировать задачу</h2></div><div class="card-body"><form method="POST"><div class="mb-3"><label class="form-label" for="draft">Название</label><input class="form-control" id="draft" value="unsaved parent"></div><div class="mb-3 form-group row"><label class="form-label col-sm-2 col-form-label" for="target">Рабочее пространство</label><div class="col-sm-10"><select class="form-control" id="target"><option value="">None</option><option value="1" selected>Основной проект</option></select></div></div><div class="mb-3 form-group row"><label class="form-label col-sm-2 col-form-label" for="targets">Источники</label><div class="col-sm-10"><select class="form-control" id="targets" multiple><option value="1" selected>One</option><option value="2" selected>Two</option></select></div></div></form></div></section></main>${enhancement}<script type="application/json" id="admin-related-controls">${JSON.stringify(controls)}</script><script src="/related.js" defer></script></html>`});
      }
      if (url.pathname.startsWith('/create') || url.pathname.startsWith('/edit/')) {
        return route.fulfill({contentType:'text/html', body:`<!doctype html><meta charset="utf-8"><form method="POST"><input name="name" value="Saved"><button>Save</button></form><script type="application/json" id="admin-related-controls">[]</script><script src="/related.js" defer></script>`});
      }
      return route.abort();
    });
    for (const widget of ["native", "select2"]) for (const theme of ["light", "dark"]) for (const width of [390, 1440]) {
      const page = await context.newPage();
      await page.setViewportSize({width, height:900});
      await page.goto(`https://offline.test/parent?widget=${widget}&theme=${theme}`);
      await page.waitForSelector('button');
      const buttons = page.locator('.admin-related-actions button');
      check(await buttons.count() === 4);
      check(await buttons.nth(3).isDisabled());
      await buttons.nth(0).focus();
      const focus = await buttons.nth(0).evaluate(button => ({color:getComputedStyle(button).outlineColor, style:getComputedStyle(button).outlineStyle}));
      check(focus.style === 'solid' && focus.color === (theme === 'dark' ? 'rgb(94, 159, 232)' : 'rgb(39, 131, 222)'));
      check(await page.locator('.admin-related-icon svg').count() === 4);
      check((await buttons.allTextContents()).every(text => !text.trim()));
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({path:`/data/admin-related-${widget}-${theme}-${width}.png`,fullPage:true});
      for (const row of await page.locator('.admin-related-field').all()) {
        const input = await row.locator('.admin-related-input').boundingBox();
        const button = await row.locator('.admin-related-icon').first().boundingBox();
        check(button.x >= input.x + input.width);
        check(Math.abs((button.y + button.height/2) - (input.y + input.height/2)) < 1);
        check(button.width >= 44 && button.height >= 44 && input.width >= 100);
      }
      if (widget === 'select2') {
        check(await page.locator('.admin-related-input > .select2-container').count() === 2);
        await page.locator('.select2-selection--single').click();
        check(await page.locator('.select2-dropdown').isVisible());
        await page.keyboard.press('Escape');
        check(await page.locator('.select2-dropdown').count() === 0);
        const selection = await page.locator('.select2-selection--single').boundingBox();
        const label = await page.locator('#select2-target-container').boundingBox();
        check(label.y >= selection.y && label.y + label.height <= selection.y + selection.height);
      }
      await page.screenshot({path:`/data/admin-related-${widget}-${theme}-${width}.png`,fullPage:true});
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
      if (widget === 'select2') check((await page.locator('#select2-target-container').textContent()).includes(label));
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
      await page.selectOption('#targets',['3'],{force:true});
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
      await page.close();
    }
    console.log(`PASS: ${checks} offline browser assertions; 390/1440px; all requests intercepted`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
