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
        for (const width of [390, 1440]) for (const theme of ['light','dark','system-light','system-dark']) for (const state of states) {
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
            for (const button of await page.locator('button[name="test_type"]').all()) assert((await button.boundingBox()).height >= 44);
            if (process.env.SCREENSHOT_DIR && ['initial','error','unknown','long'].includes(state) && ['light','dark'].includes(theme)) {
                fs.mkdirSync(process.env.SCREENSHOT_DIR, {recursive:true});
                await page.screenshot({path:path.join(process.env.SCREENSHOT_DIR, `${width}-${theme}-${state}.png`), fullPage:true});
            }
            cases++; await context.close();
        }
        const context = await browser.newContext({viewport:{width:390,height:900}});
        const page = await context.newPage();
        await page.route('**/*', route => route.request().url().startsWith('file:') ? route.continue() : route.abort());
        await page.goto('file://' + path.join(folder, 'initial.html'));
        await page.locator('button[value="mock"]').click();
        assert.match(await page.locator('#llm-submit-status').innerText(), /Введите вопрос/);
        await page.locator('button[value="real"]').click();
        assert.match(await page.locator('#llm-submit-status').innerText(), /Сначала разрешите/);
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
        assert.match(await page.locator('#llm-submit-status').innerText(), /локальную проверку/);
        await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
        assert.equal(await page.locator('form').getAttribute('data-busy'), null);
        await page.locator('input#llm-confirm-real').check();
        await page.locator('button[value="real"]').click();
        assert.equal(await page.evaluate(() => window.submissions[1].some(([k,v]) => k === 'test_type' && v === 'real')), true);
        // Native details and keyboard operation, no modal/focus trap dependency.
        await page.locator('summary').first().focus(); await page.keyboard.press('Enter');
        assert.equal(await page.locator('details').first().getAttribute('open'), '');
        await context.close();
        console.log(`PASS: ${cases} viewport/theme/state cases + submission, confirmation, busy guard, bfcache and keyboard checks`);
    } finally { await browser.close(); fs.rmSync(folder, {recursive:true,force:true}); }
})().catch(error => { console.error(error); process.exit(1); });
