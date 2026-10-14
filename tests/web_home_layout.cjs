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
            for (const width of [375, 390, 768, 1199, 1200, 1440])
                for (const theme of ['light', 'dark', 'system']) {
                    const context = await browser.newContext({
                        viewport: {
                            width,
                            height: 900
                        },
                        colorScheme: theme === 'system' ? 'dark' : theme
                    });
                    await context.addInitScript(mode => localStorage.setItem('theme', mode), theme);
                    const page = await context.newPage();
                    const errors = [];
                    let notificationRead = false;
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
                                    context: { workspace_id: 7, user_id: 101 }, count: notificationRead ? 0 : 1, complete: true, has_more: false,
                                    notifications: notificationRead ? [] : [{
                                        id: 1, key: 'notification:7:1', kind: 'notification', scope: 'workspace', workspace_id: 7,
                                        read_id: 1, action: 'read', state: 'unread', destination: '',
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
                            if (route.request().method() !== 'POST' || !route.request().postData().includes('_csrf=') || !route.request().postData().includes('workspace_id=7')) throw Error('Missing read POST/CSRF');
                            notificationRead = true;
                            await route.fulfill({
                                contentType: 'application/json',
                                body: JSON.stringify({ ok: true, context: { workspace_id: 7, user_id: 101 } })
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
                    const shellMetrics = await page.evaluate(() => {
                        const navbar = document.querySelector('.shell-navbar');
                        const main = document.querySelector('main');
                        const results = document.querySelector('[data-home-results]').getBoundingClientRect();
                        const tasks = document.querySelector('[data-home-tasks]')?.getBoundingClientRect();
                        return {fill:getComputedStyle(navbar).backgroundColor, mainWidth:main.clientWidth, mainScroll:main.scrollWidth,
                            homeWidth:document.querySelector('.ui-home').getBoundingClientRect().width,
                            available:main.clientWidth - parseFloat(getComputedStyle(main).paddingLeft) - parseFloat(getComputedStyle(main).paddingRight),
                            results:{x:results.x,y:results.y,right:results.right,bottom:results.bottom}, tasks:tasks?{x:tasks.x,y:tasks.y}:null};
                    });
                    if(shellMetrics.fill !== 'rgba(0, 0, 0, 0)') throw Error('Navbar has separate fill');
                    if(shellMetrics.mainScroll > shellMetrics.mainWidth+1) throw Error('Main horizontal overflow');
                    if(Math.abs(shellMetrics.homeWidth-shellMetrics.available)>1) throw Error('Home is not using available width');
                    if(shellMetrics.tasks && width>=1200 && Math.abs(shellMetrics.tasks.y-shellMetrics.results.y)>1) throw Error('Wide home cards not in one row');
                    if(shellMetrics.tasks && width<1200 && shellMetrics.tasks.y<shellMetrics.results.bottom) throw Error('Narrow home cards do not stack');
                    if (dimensions.document > width) throw Error(`Horizontal overflow ${state}/${width}/${theme}: ${dimensions.document}`);
                    const dock = await page.locator('.shell-notification-dock').boundingBox();
                    const content = await page.locator('main').boundingBox();
                    if (dock && content.y + content.height > dock.y + 1) throw Error('Floating bell overlaps content viewport');
                    const type = await page.evaluate(() => ({
                        summary: document.querySelector('.ui-home-summary') ? parseFloat(getComputedStyle(document.querySelector('.ui-home-summary')).fontSize) : 16,
                        meta: document.querySelector('.ui-home-meta') ? parseFloat(getComputedStyle(document.querySelector('.ui-home-meta')).fontSize) : 14
                    }));
                    if (type.summary < 16 || type.meta < 14) throw Error('Home text below readable size');
                    await page.evaluate(() => { const main = document.getElementById('page-content'); main.scrollTo(0, main.scrollHeight); });
                    if (width < 768) {
                        const bell = await page.locator('.shell-notification-dock').boundingBox();
                        const last = await page.locator('.ui-home').boundingBox();
                        if (bell && last.y + last.height > bell.y + 1) throw Error('Last content hidden by floating bell');
                    }
                    await page.evaluate(() => document.getElementById('page-content').scrollTo(0, 0));
                    if (dimensions.mode !== (theme === 'system' ? 'dark' : theme)) throw Error('Theme mismatch');
                    if (width >= 768) {
                        const controls = await page.evaluate(() => {
                            const sidebar = document.querySelector('.shell-sidebar').getBoundingClientRect();
                            const collapse = document.querySelector('[data-sidebar-collapse]').getBoundingClientRect();
                            const theme = document.querySelector('.shell-sidebar-footer [data-popover-trigger]').getBoundingClientRect();
                            return {rightGap:sidebar.right-collapse.right, bottomGap:sidebar.bottom-theme.bottom, themeLeft:theme.left-sidebar.left};
                        });
                        if(controls.rightGap>13 || controls.rightGap<0) throw Error('Collapse not at sidebar right edge');
                        if(controls.bottomGap>13 || controls.themeLeft>13) throw Error('Theme not at sidebar bottom left');
                        await page.locator('[data-sidebar-collapse]').click();
                        await page.waitForFunction(() => document.documentElement.dataset.sidebarCollapsed === 'true');
                        if (await page.locator('.shell-sidebar').evaluate(el => el.getBoundingClientRect().width) !== 64) throw Error('Compact rail width');
                        await page.locator('[data-sidebar-collapse]').click();
                    }
                    if (state === 'owner' && width === 1440 && theme === 'light') {
                        const account = page.locator('.shell-sidebar .shell-account-trigger');
                        await account.hover();
                        await page.locator('#account-desktop').waitFor({ state: 'visible' });
                        await account.click();
                        if (!(await page.locator('#account-desktop').isVisible())) throw Error('Hover then click closed account menu');
                        await page.keyboard.press('Escape');
                        await account.focus();
                        await page.keyboard.press('ArrowDown');
                        if (!(await page.locator('#account-desktop').isVisible())) throw Error('Keyboard account menu failed');
                        await page.keyboard.press('Escape');
                        report.interactions.push('desktop hover/click and ArrowDown/Escape account menu');
                    }
                    if (theme === 'system') {
                        await page.emulateMedia({ colorScheme: 'light' });
                        await page.waitForFunction(() => !document.documentElement.classList.contains('dark'));
                    }
                    await page.screenshot({ path: path.join(fixtures, `${state}-${width}-${theme}.png`), fullPage: true });
                    if (errors.length) throw Error(errors.join(';'));
                    if (state === 'owner' && width === 375 && theme === 'light') {
                        await page.getByRole('button', { name: 'Открыть навигацию', exact: true }).click();
                        await page.locator('#navigation-drawer .shell-account-trigger').click();
                        await page.locator('#navigation-drawer').getByRole('heading', { name: 'Пространства', exact: true }).waitFor({ state: 'visible' });
                        if (await page.locator('#navigation-drawer a[href="/app/invite"]').count()) throw Error('Rare join action remained in daily workspace menu');
                        await page.keyboard.press('Escape');
                        await page.keyboard.press('Escape');
                        await page.locator('#navigation-drawer').waitFor({ state: 'hidden' });
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
                        await dialog.getByRole('button', { name: 'Прочитать сообщение — для всего пространства', exact: true }).click();
                        await dialog.getByText('Нет показываемых уведомлений в текущем окне', { exact: true }).waitFor({ state: 'visible' });
                        if (!(await dialog.isVisible())) throw Error('Last read unexpectedly closed dialog');
                        await page.keyboard.press('Escape');
                        await dialog.waitFor({
                            state: 'hidden'
                        });
                        await page.getByRole('button', { name: 'Открыть навигацию', exact: true }).click();
                        await page.locator('#navigation-drawer').getByRole('button', { name: 'Тема оформления', exact: true }).click();
                        await page.locator('#navigation-drawer [data-theme-choice=dark]').click();
                        await page.keyboard.press('Escape');
                        await page.locator('#navigation-drawer').waitFor({state:'hidden'});
                        await page.waitForFunction(() => document.documentElement.classList.contains('dark'));
                        // Enter keyboard modality before asserting :focus-visible styling.
                        await page.keyboard.press('Tab');
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
