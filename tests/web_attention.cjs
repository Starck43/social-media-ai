/* Offline B projection/browser contracts only. Every request is intercepted.
 * node tests/web_attention.cjs <fixtures> <local-vendor-dir> */
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path'), assert = require('node:assert/strict');
const fixtures = path.resolve(process.argv[2]), vendor = path.resolve(process.argv[3]), root = path.resolve(__dirname, '..');
const snapshots = JSON.parse(fs.readFileSync(path.join(fixtures, 'attention-snapshots.json')));
const report = { cases: [], interactions: [], failures: [] };
(async () => {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_BIN, args: ['--no-sandbox'] });
    async function setup(mode, width, theme) {
        const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme: theme });
        await context.addInitScript(mode => localStorage.setItem('theme', mode), theme);
        const page = await context.newPage(), errors = [], posts = [];
        let current = mode, denied = false;
        page.on('pageerror', e => errors.push(e.message));
        await page.route('**/*', async route => {
            const req = route.request(), url = new URL(req.url());
            if (url.hostname !== 'fixture.test') return route.abort();
            if (url.pathname.startsWith('/static/')) return route.fulfill({ path: url.pathname.startsWith('/static/vendor/') ? path.join(vendor, path.basename(url.pathname)) : path.join(root, 'app', url.pathname) });
            if (url.pathname === '/app/notifications') {
                assert.equal(req.headers()['x-app-workspace'], '7'); assert.equal(req.headers()['x-app-user'], '101');
                return route.fulfill({ contentType: 'application/json', body: JSON.stringify(snapshots[current]) });
            }
            if (url.pathname.startsWith('/app/notifications/read/')) {
                const form = new URLSearchParams(req.postData());
                posts.push({ method: req.method(), csrf: form.get('_csrf'), workspace: form.get('workspace_id'), id: url.pathname.split('/').at(-1) });
                if (denied) return route.fulfill({ status: 403, contentType: 'application/json', body: '{"ok":false,"reset":true}' });
                current = 'after-read';
                return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true, context: snapshots[current].context }) });
            }
            if (url.pathname === '/attention') return route.fulfill({ contentType: 'text/html', path: path.join(fixtures, 'dashboard.html') });
            return route.abort();
        });
        await page.goto('http://fixture.test/attention');
        await page.waitForFunction(() => typeof Alpine !== 'undefined' && document.querySelector('style'));
        await page.waitForTimeout(250);
        await page.waitForFunction(() => !Alpine.$data(document.querySelector('[x-data^="notificationsBell"]')).loading);
        return { page, context, errors, posts, set(mode) { current = mode; }, deny() { denied = true; } };
    }
    try {
        for (const mode of ['mixed', 'readonly', 'empty', 'unknown', 'partial', 'window'])
            for (const width of [375, 390, 768, 1440])
                for (const theme of ['light', 'dark']) {
                    const f = await setup(mode, width, theme);
                    try {
                        const snapshot = snapshots[mode], dock = f.page.locator('.shell-notification-dock');
                        assert.equal(await dock.isVisible(), snapshot.count > 0 || !snapshot.complete);
                        assert.equal(await f.page.locator('.shell-navbar a[aria-label="Подключения требуют внимания"]').count(), 0);
                        assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
                        if (mode !== 'empty') {
                            await dock.getByRole('button', { name: 'Уведомления', exact: true }).click();
                            const dialog = f.page.getByRole('dialog', { name: 'Уведомления', exact: true });
                            await dialog.waitFor({ state: 'visible' });
                            const cards = dialog.locator('.shell-attention-item');
                            assert.equal(await cards.count(), snapshot.count);
                            assert.equal(await dialog.locator('button[aria-label="Прочитать сообщение — для всего пространства"]:visible').count(), snapshot.notifications.filter(n => n.read_id).length);
                            const box = await dialog.locator('.ui-notification-panel').boundingBox();
                            assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= width && box.y + box.height <= 900);
                            assert.equal(await dialog.locator('img[src="x"]').count(), 0);
                            assert.equal(await f.page.evaluate(() => window.attentionXss), undefined);
                            const text = await dialog.innerText();
                            assert.ok(!text.includes('raw exception') && !text.includes('secret-token'));
                            if (!snapshot.complete) {
                                assert.ok(await dialog.locator('[role="alert"]').isVisible());
                                assert.ok((await dock.locator('.shell-bell-count').innerText()).includes('?'));
                                assert.equal(await dialog.getByText('Нет показываемых уведомлений в текущем окне', { exact: true }).isVisible(), false);
                            }
                            if (mode === 'mixed' && width === 375 && theme === 'dark') await f.page.screenshot({ path: path.join(fixtures, 'attention-375-dark.png') });
                            if (mode === 'mixed' && width === 1440 && theme === 'light') await f.page.screenshot({ path: path.join(fixtures, 'attention-1440-light.png') });
                            await f.page.keyboard.press('Escape'); await dialog.waitFor({ state: 'hidden' });
                        }
                        assert.deepEqual(f.posts, [], 'GET/open/poll never writes'); assert.deepEqual(f.errors, []);
                        report.cases.push({ mode, width, theme });
                    } catch (e) { report.failures.push({ mode, width, theme, error: e.message, errors: f.errors }); }
                    finally { await f.context.close(); }
                }
        for (const width of [375, 1440]) {
            const f = await setup('mixed', width, 'dark');
            try {
                await f.page.getByRole('button', { name: 'Уведомления', exact: true }).click();
                const dialog = f.page.getByRole('dialog', { name: 'Уведомления', exact: true });
                const task = dialog.locator('[data-attention-kind="task"]');
                await task.getByRole('button', { name: 'Прочитать сообщение — для всего пространства', exact: true }).click();
                await task.locator('button').waitFor({ state: 'hidden' });
                assert.equal(await dialog.locator('.shell-attention-item').count(), 3, 'shared event read cannot resolve the task');
                assert.equal(await f.page.locator('.shell-bell-count').innerText(), '3');
                assert.deepEqual(f.posts, [{ method: 'POST', csrf: 'local-fixture-only', workspace: '7', id: '1' }]);
                f.set('cleared');
                await f.page.locator('[x-data^="notificationsBell"]').evaluate(el => Alpine.$data(el).load());
                await task.waitFor({ state: 'detached' });
                assert.equal(await dialog.locator('.shell-attention-item').count(), 1, 'verified source clearing removes only signals');
                assert.equal(await dialog.locator('[data-attention-kind="connection"]').count(), 0);
                f.set('foreign-context');
                await f.page.locator('[x-data^="notificationsBell"]').evaluate(el => Alpine.$data(el).load());
                assert.equal(await dialog.locator('.shell-attention-item').count(), 0, 'workspace mismatch clears old content');
                assert.equal(await dialog.getByRole('button', { name: 'Повторить', exact: true }).isDisabled(), true);
                assert.deepEqual(f.errors, []);
                report.interactions.push({ width, name: 'read-not-resolve-clear-and-context-reset' });
            } catch (e) { report.failures.push({ width, error: e.message, errors: f.errors }); }
            finally { await f.context.close(); }
            const denied = await setup('mixed', width, 'light');
            try {
                await denied.page.getByRole('button', { name: 'Уведомления', exact: true }).click();
                denied.deny();
                const dialog = denied.page.getByRole('dialog', { name: 'Уведомления', exact: true });
                await dialog.locator('[data-attention-kind="task"]').locator('button').click();
                await dialog.locator('[role="alert"]').waitFor();
                assert.equal(await dialog.locator('.shell-attention-item').count(), 0, 'revoked read clears old data, no optimistic dismissal');
                assert.equal(await dialog.getByRole('button', { name: 'Повторить', exact: true }).isDisabled(), true);
                assert.deepEqual(denied.errors, []);
                report.interactions.push({ width, name: 'read-revocation-reset' });
            } catch (e) { report.failures.push({ width, error: e.message, errors: denied.errors }); }
            finally { await denied.context.close(); }
        }
    } finally {
        await browser.close(); fs.writeFileSync(path.join(fixtures, 'attention-browser-report.json'), JSON.stringify(report, null, 2));
    }
    console.log(JSON.stringify({ cases: report.cases.length, interactions: report.interactions.length, failures: report.failures }, null, 2));
    if (report.failures.length) process.exitCode = 1;
})().catch(e => { console.error(e); process.exitCode = 1; });
