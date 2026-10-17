/* Offline fixture QA: real Jinja/SQLAdmin shell; all non-file traffic is aborted. */
const {chromium} = require('playwright');
const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');
(async () => {
    const root = path.resolve(__dirname, '..');
    const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'llm-admin-fixtures-'));
    const rendered = spawnSync(process.env.PYTHON_BIN || path.join(root, '.venv/bin/python'), [path.join(__dirname, 'admin_model_test_fixture.py'), folder], {encoding:'utf8'});
    assert.equal(rendered.status, 0, rendered.stderr);
    const {states} = JSON.parse(rendered.stdout);
    const browser = await chromium.launch({headless:true, executablePath:process.env.CHROMIUM_BIN || undefined, args:['--no-sandbox']});
    let cases = 0;
    try {
        for (const width of [320, 390, 1440]) for (const theme of ['light','dark','system-light','system-dark']) for (const state of states) {
            const context = await browser.newContext({viewport:{width,height:1000}, colorScheme:theme.endsWith('dark') || theme === 'dark' ? 'dark':'light'});
            await context.addInitScript(mode => localStorage.setItem('theme', mode.startsWith('system') ? 'system' : mode), theme);
            const page = await context.newPage();
            await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
            const errors = []; page.on('pageerror', error => errors.push(error.message));
            await page.goto('file://' + path.join(folder, state + '.html')); await page.waitForTimeout(75);
            assert.equal(await page.locator('.llm-test-heading h1').count(), 1);
            assert.equal(await page.locator('button[name="test_type"]').count(), 3);
            assert.equal(await page.locator('form#llm-test-form').getAttribute('method'), 'post');
            assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `${width}/${theme}/${state} overflow`);
            const expectedDark = theme === 'dark' || theme === 'system-dark';
            assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), expectedDark);
            assert.deepEqual(errors, [], `${width}/${theme}/${state} script errors`);
            assert((await page.locator('.llm-footer').boundingBox()).height < 140, 'global nav styles must not stretch the footer');
            const contrasts = await page.evaluate(() => {
                const color = text => text.startsWith('#') ? text.slice(1).match(/../g).map(v => parseInt(v,16)) : text.match(/[\d.]+/g).slice(0,3).map(Number);
                const luminance = text => color(text).map(v => {v/=255; return v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4;}).reduce((sum,v,i) => sum+v*[.2126,.7152,.0722][i],0);
                const ratio = (a,b) => {a=luminance(a); b=luminance(b); return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);};
                const css = selector => getComputedStyle(document.querySelector(selector));
                const gradient = css('main').getPropertyValue('--auth-bg-gradient').match(/#[0-9a-f]{6}/gi);
                const pairs = gradient.map(bg => [css('.llm-test-heading .llm-muted').color,bg]);
                pairs.push([css('.llm-panel .llm-muted').color,css('.llm-panel').backgroundColor]);
                pairs.push([css('.llm-mode p').color,css('.llm-mode').backgroundColor]);
                pairs.push([css('.llm-primary').color,css('.llm-primary').backgroundColor]);
                document.querySelectorAll('.llm-state, #llm-result-title').forEach(node => {
                    pairs.push([getComputedStyle(node).color, css('.llm-panel').backgroundColor]);
                });
                const copy = document.querySelector('.llm-copy');
                if (copy) pairs.push([getComputedStyle(copy).color, getComputedStyle(copy).backgroundColor]);
                return pairs.map(([a,b]) => ratio(a,b));
            });
            contrasts.forEach(value => assert(value >= 4.5, `${width}/${theme}/${state} text contrast ${value}`));
            for (const button of await page.locator('button[name="test_type"]').all()) assert((await button.boundingBox()).height >= 44);
            if (process.env.SCREENSHOT_DIR && states.includes(state) && ['light','dark'].includes(theme)) {
                fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive:true});
                await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, `${width}-${theme}-${state}.png`), fullPage:true});
            }
            const copy = page.locator('.llm-copy');
            if (await copy.count()) {
                assert.equal(await copy.getAttribute('aria-label'), 'Копировать JSON');
                assert.equal(await copy.innerText(), '');
                assert.equal(await copy.locator('svg:visible').count(), 1);
                // Read both rectangles in one layout snapshot while smooth result focus scrolls.
                const {box, jsonBox} = await copy.evaluate(node => ({
                    box: node.getBoundingClientRect().toJSON(),
                    jsonBox: node.closest('.llm-json-code').getBoundingClientRect().toJSON(),
                }));
                assert(box.width >= 44 && box.height >= 44);
                assert(Math.abs(jsonBox.x + jsonBox.width - (box.x + box.width) - 8) <= 1);
                assert(Math.abs(box.y - jsonBox.y - 8) <= 1);
            }
            cases++; await context.close();
        }
        const context = await browser.newContext({viewport:{width:390,height:900}});
        const page = await context.newPage();
        await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
        await page.goto('file://' + path.join(folder, 'initial.html'));
        await page.locator('button[value="mock"]').click();
        assert.match(await page.locator('#llm-submit-status').innerText(), /Введите вопрос/);
        assert.equal(await page.locator('[name="confirm_real"]').count(), 0);
        // Intercept submission without a server, preserving submitter/form serialization.
        await page.evaluate(() => {
            window.submissions = [];
            document.querySelector('form#llm-test-form').addEventListener('submit', e => {
                if (!e.defaultPrevented) { window.submissions.push([...new FormData(e.target, e.submitter)]); e.preventDefault(); }
            });
        });
        await page.locator('button[value="short"]').click();
        assert.equal(await page.evaluate(() => window.submissions[0].some(([k,v]) => k === 'test_type' && v === 'short')), true);
        await page.locator('button[value="short"]').evaluate(button => button.click());
        assert.equal(await page.evaluate(() => window.submissions.length), 1);
        assert.match(await page.locator('#llm-submit-status').innerText(), /API без генерации/);
        await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
        assert.equal(await page.locator('form').getAttribute('data-busy'), null);
        await page.locator('#llm-billing-tenant').selectOption('');
        await page.locator('button[value="real"]').click();
        assert.match(await page.locator('#llm-submit-status').innerText(), /Выберите рабочее пространство/);
        await page.locator('#llm-billing-tenant').selectOption('3');
        await page.locator('button[value="real"]').click();
        assert.equal(await page.evaluate(() => window.submissions[1].some(([k,v]) => k === 'test_type' && v === 'real')), true);
        assert.equal(await page.locator('button[value="real"]').innerText(), 'Реальный запрос');
        assert.equal(await page.evaluate(() => window.submissions[1].find(([k]) => k === 'prompt')[1]), '');
        await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
        await page.locator('#llm-prompt').fill('Объясни API кратко');
        await page.locator('button[value="real"]').click();
        assert.equal(await page.evaluate(() => window.submissions[2].find(([k]) => k === 'prompt')[1]), 'Объясни API кратко');
        assert.equal(await page.evaluate(() => window.submissions[2].some(([k]) => k === 'confirm_real')), false);
        // Native details and keyboard operation, no modal/focus trap dependency.
        await page.locator('.llm-checks > summary').focus(); await page.keyboard.press('Enter');
        assert.equal(await page.locator('.llm-checks').getAttribute('open'), '');
        await page.goto('file://' + path.join(folder, 'real.html'));
        assert.equal(await page.locator('.llm-answer-text').count(), 1);
        assert.equal(await page.locator('#llm-result').evaluate(node => document.activeElement === node), true);
        assert.equal(await page.locator('script').filter({hasText:'bad()'}).count(), 0);
        const actualJson = await page.locator('#llm-response-json').textContent();
        assert.equal(JSON.parse(actualJson).id, 'fixture-reply');
        await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async text => { window.copiedJson = text; }}}));
        await page.locator('[data-copy-target="llm-response-json"]').click();
        assert.equal(await page.evaluate(() => window.copiedJson), actualJson);
        const copyButton = page.locator('[data-copy-target="llm-response-json"]');
        assert.equal(await copyButton.getAttribute('data-copy-state'), 'success');
        assert.equal(await copyButton.getAttribute('aria-label'), 'JSON скопирован');
        assert.equal(await copyButton.locator('.llm-copy-done').isVisible(), true);
        assert.equal(await copyButton.locator('.llm-copy-icon').isVisible(), false);
        const liveBox = await page.locator('#llm-copy-status').boundingBox();
        assert(liveBox.width <= 1 && liveBox.height <= 1, 'success announcement must not create visible text below JSON');
        assert.equal(await page.locator('#llm-copy-fallback').isVisible(), false);
        await page.waitForTimeout(2100);
        assert.equal(await copyButton.getAttribute('data-copy-state'), null);
        assert.equal(await copyButton.getAttribute('aria-label'), 'Копировать JSON');
        await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async () => { throw new Error('offline clipboard'); }}}));
        await page.locator('[data-copy-target="llm-response-json"]').click();
        assert.equal(await copyButton.getAttribute('data-copy-state'), 'error');
        assert.equal(await copyButton.locator('.llm-copy-error').isVisible(), true);
        assert.match(await page.locator('#llm-copy-fallback').innerText(), /выделен/);
        assert.equal(await page.evaluate(() => window.getSelection().toString()), actualJson);
        await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText: async text => { window.copiedJson = text; }}}));
        await copyButton.focus(); await page.keyboard.press('Enter');
        assert.equal(await copyButton.getAttribute('data-copy-state'), 'success');
        assert.equal(await page.locator('#llm-copy-fallback').isVisible(), false);
        assert.equal(await page.evaluate(() => document.activeElement.getAttribute('data-copy-target')), 'llm-response-json');
        await page.goto('file://' + path.join(folder, 'owner.html'));
        assert.equal(await page.locator('[name="tenant_id"]').count(), 0);
        assert.equal(await page.locator('body').innerText().then(text => text.includes('PERSONAL_BILLING_ENABLED')), false);
        await page.evaluate(() => {
            window.ownerSubmissions = [];
            document.querySelector('#llm-test-form').addEventListener('submit', e => {
                if (!e.defaultPrevented) { window.ownerSubmissions.push([...new FormData(e.target, e.submitter)]); e.preventDefault(); }
            });
        });
        await page.locator('button[value="real"]').click();
        assert.equal(await page.evaluate(() => window.ownerSubmissions.length), 1);
        assert.equal(await page.evaluate(() => window.ownerSubmissions[0].find(([k]) => k === 'prompt')[1]), '');
        assert.match(await page.locator('#llm-submit-status').innerText(), /Ожидаем ответ модели/);
        await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
        await page.locator('#llm-prompt').fill('Проверка владельца');
        await page.locator('button[value="real"]').click();
        assert.equal(await page.evaluate(() => window.ownerSubmissions[1].find(([k]) => k === 'prompt')[1]), 'Проверка владельца');
        await context.close();
        const noJS = await browser.newContext({viewport:{width:390,height:900}, javaScriptEnabled:false});
        const native = await noJS.newPage();
        await native.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
        await native.goto('file://' + path.join(folder, 'initial.html'));
        assert.equal(await native.locator('button[type="submit"][name="test_type"]').count(), 3);
        assert.equal(await native.locator('form#llm-test-form').getAttribute('method'), 'post');
        assert.equal(await native.locator('input[name="csrf_token"]').count(), 1);
        await native.goto('file://' + path.join(folder, 'owner.html'));
        assert.equal(await native.locator('.llm-copy').isVisible(), false);
        assert.equal(await native.locator('[name="tenant_id"]').count(), 0);
        assert.equal(await native.locator('button[value="real"]:not([disabled])').count(), 1);
        await noJS.close();
        console.log(`PASS: ${cases} viewport/theme/state cases + submission, no redundant consent, contrast, native no-JS controls, busy guard, bfcache and keyboard checks`);
    } finally { await browser.close(); fs.rmSync(folder, {recursive:true,force:true}); }
})().catch(error => { console.error(error); process.exit(1); });
