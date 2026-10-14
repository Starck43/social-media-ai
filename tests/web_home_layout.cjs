/* Offline fixture browser checks only. No authentication/DB integration.
 * Generate fixtures with tests/home_overview_fixtures.py --out <fixtures>.
 * Supply local copies of tailwind.js, alpine.js and htmx.js, matching base.html.
 * All HTTP requests are fulfilled/aborted locally; no real endpoint is called. */
const {
    chromium
} = require('playwright');
const fs = require('fs');
const path = require('path');
(async () => {
    const browser = await chromium.launch({
        headless: true,
        executablePath: process.env.CHROMIUM_BIN || undefined,
        args: ['--no-sandbox']
    });
    const root = path.resolve(__dirname, '..');
    const fixtures = path.resolve(process.argv[2] || '');
    const vendor = path.resolve(process.argv[3] || '');
    if (!process.argv[2] || !process.argv[3]) throw Error('Usage: node tests/web_home_layout.cjs <fixtures> <offline-vendor-dir>');
    const report = {
        cases: [],
        interactions: []
    };
    try {
        for (const state of ['owner', 'empty', 'denied', 'long'])
            for (const width of [375, 390, 768, 1440])
                for (const theme of ['light', 'dark']) {
                    const context = await browser.newContext({
                        viewport: {
                            width,
                            height: 900
                        },
                        colorScheme: theme
                    });
                    const page = await context.newPage();
                    const errors = [];
                    page.on('pageerror', e => errors.push(e.message));
                    await page.route('**/*', async route => {
                        const u = new URL(route.request().url());
                        if (u.hostname !== 'fixture.test') {
                            await route.abort();
                            return;
                        }
                        if (u.pathname.startsWith('/static/')) {
                            const file = u.pathname.startsWith('/static/vendor/') ? path.join(vendor, path.basename(u.pathname)) : path.join(root, 'app', u.pathname);
                            await route.fulfill({
                                path: file
                            });
                            return;
                        }
                        if (u.pathname === '/app/notifications') {
                            await route.fulfill({
                                contentType: 'application/json',
                                body: JSON.stringify({
                                    notifications: [{
                                        id: 1,
                                        title: 'Тестовое уведомление',
                                        message: 'Проверка локального диалога',
                                        created_at: 'fixture',
                                        is_read: false
                                    }]
                                })
                            });
                            return;
                        }
                        if (u.pathname === '/app/notifications/read/1') {
                            await route.fulfill({
                                contentType: 'application/json',
                                body: '{}'
                            });
                            return;
                        }
                        if (u.pathname === `/${state}`) {
                            let html = fs.readFileSync(path.join(fixtures, `${state}.html`), 'utf8').replaceAll('file:///data/home-overview/app/static/', 'http://fixture.test/static/');
                            await route.fulfill({
                                contentType: 'text/html',
                                body: html
                            });
                            return;
                        }
                        await route.abort();
                    });
                    await page.goto(`http://fixture.test/${state}`);
                    await page.waitForFunction(() => typeof Alpine !== 'undefined' && document.querySelector('style') && getComputedStyle(document.querySelector('main')).display === 'block');
                    await page.waitForTimeout(350);
                    const dimensions = await page.evaluate(() => ({
                        viewport: innerWidth,
                        document: document.documentElement.scrollWidth,
                        main: document.querySelector('main').getBoundingClientRect().width,
                        mode: document.documentElement.classList.contains('dark') ? 'dark' : 'light'
                    }));
                    report.cases.push({
                        state,
                        width,
                        theme,
                        ...dimensions,
                        errors
                    });
                    if (dimensions.document > width) throw Error(`Horizontal overflow ${state}/${width}/${theme}: ${dimensions.document}`);
                    const type = await page.evaluate(() => ({
                        summary: document.querySelector('.ui-home-summary') ? parseFloat(getComputedStyle(document.querySelector('.ui-home-summary')).fontSize) : 16,
                        meta: document.querySelector('.ui-home-meta') ? parseFloat(getComputedStyle(document.querySelector('.ui-home-meta')).fontSize) : 14
                    }));
                    if (type.summary < 16 || type.meta < 14) throw Error('Home text below readable size');
                    await page.evaluate(() => scrollTo(0, document.documentElement.scrollHeight));
                    if (width < 768) {
                        const footer = await page.locator('nav[aria-label="Основные разделы"]').boundingBox();
                        const last = await page.locator('.ui-home').boundingBox();
                        if (last.y + last.height > footer.y + 1) throw Error('Last content hidden by mobile footer');
                    }
                    await page.evaluate(() => scrollTo(0, 0));
                    if (dimensions.mode !== theme) throw Error('Theme mismatch');
                    if (errors.length) throw Error(errors.join(';'));
                    if (state === 'owner' && width === 375 && theme === 'light') {
                        await page.getByRole('button', {
                            name: 'Мониторинг рынка',
                            exact: false
                        }).first().click();
                        await page.getByText('Присоединиться по коду', {
                            exact: true
                        }).waitFor({
                            state: 'visible'
                        });
                        await page.keyboard.press('Escape');
                        await page.locator('main').click({
                            position: {
                                x: 5,
                                y: 5
                            }
                        });
                        await page.getByTitle('Уведомления', {
                            exact: true
                        }).click();
                        const dialog = page.getByRole('dialog', {
                            name: 'Уведомления',
                            exact: true
                        });
                        await dialog.waitFor({
                            state: 'visible'
                        });
                        await page.getByText('Тестовое уведомление', {
                            exact: true
                        }).waitFor({
                            state: 'visible'
                        });
                        const box = await dialog.locator('.ui-notification-panel').boundingBox();
                        if (box.x < 0 || box.x + box.width > width) throw Error('Modal overflow');
                        await page.keyboard.press('Escape');
                        await dialog.waitFor({
                            state: 'hidden'
                        });
                        await page.locator('[data-theme-select]').last().selectOption('dark');
                        await page.waitForFunction(() => document.documentElement.classList.contains('dark'));
                        await page.getByRole('link', {
                            name: 'Открыть чат',
                            exact: true
                        }).focus();
                        const outline = await page.getByRole('link', {
                            name: 'Открыть чат',
                            exact: true
                        }).evaluate(el => getComputedStyle(el).outlineStyle);
                        if (outline === 'none') throw Error('Missing focus outline');
                        const size = await page.getByRole('link', {
                            name: 'Открыть чат',
                            exact: true
                        }).boundingBox();
                        if (size.height < 44) throw Error('Chat target too small');
                        report.interactions.push('workspace menu, notification mock/modal + Escape, theme switch, focus ring, 44px chat target');
                    }
                    await context.close();
                }
        fs.writeFileSync(path.join(fixtures, 'browser-report.json'), JSON.stringify(report, null, 2));
        console.log(`${report.cases.length} fixture viewport/theme cases passed; ${report.interactions.length} interaction scenario passed; no real HTTP endpoints contacted.`);
    } finally {
        await browser.close();
    }
})().catch(e => {
    console.error(e);
    process.exit(1)
});
