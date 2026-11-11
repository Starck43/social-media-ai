/* P20-T13 shell layout: real Chromium rect checks on a synthetic file:// shell
   that uses the actual shell.css/theme.css/ui.css. No app import, DB, runtime,
   server, network, CDN or asset: Fetch interception fails every non-file request,
   so a regression that reaches the network fails the run.
   The fixture markup mirrors web shell base.html/_chat_dock.html/_shell_notifications.html:
   .shell-layout > (.shell-sidebar, .shell-page > .shell-navbar + .shell-content),
   a direct body .shell-mobile-bar with 5 a/button, data-chat-dock-container with a
   160px launcher (as the utility-class label renders), .shell-notification-dock bell.
   env(safe-area-inset-*) is 0 in Chromium emulation: this run does NOT prove iOS
   safe-area behaviour on real hardware (notch/gesture bar) - that needs a device. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');
const CHROME = process.env.CHROMIUM_BIN || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const TIMEOUT_MS = 90000;
const STEP_TIMEOUT = 15000;
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'p20-t13-layout-'));
const fixture = path.join(temp, 'fixture.html');
const readCss = name => fs.readFileSync(path.join(root, 'app/static/css', name), 'utf8');

const bar = `<nav class="shell-mobile-bar" aria-label="Быстрая навигация">
    ${[['Задачи', 'tasks'], ['Чат', 'chat'], ['Агенты', 'agents'], ['Лента', 'feed']]
        .map(([label, key]) => `<a href="/app/${key}" ${key === 'tasks' ? 'aria-current="page"' : ''}><span>${label}</span></a>`).join('')}
    <button type="button"><span>Ещё</span></button></nav>`;
const dock = `<div class="chat-dock-launcher z-30" data-chat-dock-container>
    <button type="button" id="chat-dock-toggle" data-chat-dock-toggle aria-label="Спросить агента"
        style="width:160px;height:44px;padding:0;border:0;border-radius:999px">Спросить агента</button></div>`;
const bell = `<div class="shell-notification-dock"><button type="button" class="shell-icon-button shell-bell"
    aria-label="Уведомления" title="Уведомления"><span aria-hidden="true">!</span></button></div>`;

// Many short rows: the content must genuinely scroll, and the last action is a
// real end-of-list target, not the only child.
const rows = Array.from({ length: 40 }, (_, i) => `    <p>Строка ${i + 1}: обычный контент страницы для проверки прокрутки.</p>`).join('\n');

function page({ mobile = true, launcher = true, bellOn = true } = {}) {
    return `<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<!-- Mirror Tailwind preflight sizing without loading the CDN. -->
<style>*, *::before, *::after { box-sizing: border-box; }</style>
<style>${readCss('theme.css')}</style>
<style>${readCss('ui.css')}</style>
<style>${readCss('shell.css')}</style>
<style>body { margin: 0; font-family: system-ui, sans-serif; }</style></head>
<body class="ui-shell" data-shell-section="tasks">
<div class="shell-layout">
    <aside class="shell-sidebar" aria-label="Боковая навигация"><p>Навигация</p></aside>
    <div class="shell-page">
        <header class="shell-navbar"><h1 id="page-heading">Задачи</h1></header>
        <main id="page-content" tabindex="-1" class="shell-content">${rows}
    <button type="button" id="last-action">Последнее действие</button></main>
    </div>
</div>
${mobile ? bar : ''}
${launcher ? dock : ''}
${bellOn ? bell : ''}
</body></html>`;
}

fs.writeFileSync(fixture, page());

if (!fs.existsSync(CHROME)) {
    console.error(`Chromium not found: ${CHROME} (set CHROMIUM_BIN)`);
    fs.rmSync(temp, { recursive: true, force: true });
    process.exit(2);
}

const child = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-background-networking', '--disable-sync', '--disable-component-update',
    '--disable-breakpad', '--disable-crash-reporter', '--mute-audio',
    '--remote-debugging-pipe', `--user-data-dir=${path.join(temp, 'profile')}`, 'about:blank',
], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });

let nextId = 0, buffer = Buffer.alloc(0), sessionId = null, closed = false;
const blocked = [];
const waiting = new Map();

function dispatch(message) {
    if (message.method === 'Fetch.requestPaused') {
        const { requestId, request } = message.params;
        if (request.url.startsWith('file://')) send('Fetch.continueRequest', { requestId }).catch(() => {});
        else { blocked.push(request.url); send('Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' }).catch(() => {}); }
        return;
    }
    if (!message.id) return;
    const pending = waiting.get(message.id);
    if (!pending) return;
    waiting.delete(message.id);
    if (message.error) pending.reject(Error(`${pending.method}: ${JSON.stringify(message.error)}`));
    else pending.resolve(message.result);
}

child.on('error', error => { console.error(`Chromium failed to start: ${error.message}`); process.exitCode = 1; });
child.on('exit', code => {
    closed = true;
    for (const [id, pending] of waiting) { pending.reject(Error(`Chromium exited (${code}) during ${pending.method}`)); waiting.delete(id); }
});
child.stdio[4].on('data', chunk => {
    buffer = Buffer.concat([buffer, chunk]);
    let zero;
    while ((zero = buffer.indexOf(0)) >= 0) {
        const raw = buffer.subarray(0, zero).toString('utf8');
        buffer = buffer.subarray(zero + 1);
        if (!raw) continue;
        try { dispatch(JSON.parse(raw)); } catch (error) { console.error(`Bad CDP frame: ${error.message}`); }
    }
});

function send(method, params = {}, session = sessionId) {
    return new Promise((resolve, reject) => {
        if (closed) return reject(Error(`Chromium exited before ${method}`));
        const id = ++nextId;
        const timer = setTimeout(() => { waiting.delete(id); reject(Error(`CDP timeout: ${method}`)); }, STEP_TIMEOUT);
        waiting.set(id, {
            method,
            resolve: value => { clearTimeout(timer); resolve(value); },
            reject: error => { clearTimeout(timer); reject(error); },
        });
        child.stdio[3].write(JSON.stringify({ id, method, params, ...(session ? { sessionId: session } : {}) }) + '\0');
    });
}

async function evaluate(expression) {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
}

async function waitFor(expression, label, attempts = 80) {
    for (let i = 0; i < attempts; i++) {
        if (await evaluate(expression)) return;
        await evaluate('new Promise(resolve => setTimeout(resolve, 25))');
    }
    throw Error(`Timed out waiting for ${label}`);
}

const VIEWPORTS = [
    { width: 390, height: 844, phone: true },
    { width: 375, height: 667, phone: true },
    { width: 1440, height: 900, phone: false },
    { width: 820, height: 1180, phone: false },
];

// One measurement per page load: geometry + scroll reach, all in CSS pixels.
const measure = () => evaluate(`(() => {
    const content = document.getElementById('page-content');
    const launcher = document.querySelector('[data-chat-dock-container]');
    const bell = document.querySelector('.shell-notification-dock');
    const bar = document.querySelector('.shell-mobile-bar');
    const last = document.getElementById('last-action');
    const rect = el => el ? el.getBoundingClientRect() : null;
    const shown = el => !!el && getComputedStyle(el).display !== 'none' && !!el.getClientRects().length;
    const overflowX = () => document.documentElement.scrollWidth - document.documentElement.clientWidth;
    const base = {
        barShown: shown(bar), launcherShown: shown(launcher), bellShown: shown(bell),
        launcher: rect(launcher), bell: rect(bell), bar: rect(bar), content: rect(content),
        marginBottom: parseFloat(getComputedStyle(content).marginBottom),
        overflowBefore: overflowX(),
    };
    content.scrollTop = content.scrollHeight;
    return {
        ...base,
        overflowAfter: overflowX(),
        scrollTop: content.scrollTop,
        maxScroll: content.scrollHeight - content.clientHeight,
        last: rect(last),
        viewport: { width: innerWidth, height: innerHeight },
    };
})()`);

// Fixed chrome currently visible over the page; the scrollport must end above it.
function chromeTops(m) {
    return [m.barShown && m.bar.top, m.launcherShown && m.launcher.top, m.bellShown && m.bell.top].filter(Boolean);
}

const checks = [];
function check(ok, label) { assert.ok(ok, `FAILED: ${label}`); checks.push(label); }

async function load(name, options) {
    fs.writeFileSync(fixture, page(options));
    await send('Page.navigate', { url: `${pathToFileURL(fixture).href}?${name}` });
    await waitFor('document.readyState === "complete" && !!document.getElementById("page-content")', `${name} fixture`);
}

async function layoutCase(label, { width, height, phone }, scenario = {}) {
    await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: phone });
    await waitFor(`innerWidth === ${width} && innerHeight === ${height}`, `${label} viewport`);
    const m = await measure();
    check(m.overflowBefore === 0 && m.overflowAfter === 0, `${label}: no horizontal overflow`);
    check(m.barShown === (phone && Boolean(scenario.mobile ?? true)), `${label}: mobile bar presence matches the viewport`);
    check(m.launcherShown === Boolean(scenario.launcher ?? true), `${label}: launcher presence matches ${scenario.launcher ?? true}`);
    if (m.launcherShown && m.bellShown) {
        const a = m.launcher, b = m.bell;
        check(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top, `${label}: launcher and bell do not overlap`);
        check(a.bottom === b.bottom, `${label}: launcher sits beside the bell on the same line`);
    }
    if (phone) {
        for (const [name, r] of [['launcher', m.launcherShown && m.launcher], ['bell', m.bellShown && m.bell]]) {
            if (!r) continue;
            if (m.barShown) check(r.bottom <= m.bar.top + 0.5, `${label}: ${name} sits above the mobile bar`);
            check(r.left >= 0 && r.right <= width && r.top >= 0, `${label}: ${name} stays in the viewport`);
        }
    }
    // The scrollport (its rect already excludes the reserved margin) ends above all
    // visible fixed chrome, so no row is permanently covered.
    const floor = Math.min(...chromeTops(m));
    if (Number.isFinite(floor)) check(m.content.bottom <= floor + 0.5, `${label}: scrollport ends above fixed chrome`);
    return m;
}

async function run() {
    const target = await send('Target.createTarget', { url: 'about:blank' }, null);
    sessionId = (await send('Target.attachToTarget', { targetId: target.targetId, flatten: true }, null)).sessionId;
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });

    // 1. Full shell at every viewport; the last action is reachable and fully visible.
    await load('full', {});
    for (const viewport of VIEWPORTS) {
        const label = `${viewport.width}x${viewport.height}`;
        const m = await layoutCase(label, viewport);
        check(m.maxScroll > 0, `${label}: fixture content actually scrolls`);
        check(m.scrollTop >= m.maxScroll - 1, `${label}: scrollContent reaches its end`);
        const floor = Math.min(...[m.barShown && m.bar.top, m.launcherShown && m.launcher.top, m.bellShown && m.bell.top].filter(Boolean));
        check(m.last.bottom <= floor + 0.5 && m.last.top >= m.content.top - 0.5, `${label}: last action wholly visible above fixed chrome`);
        if (viewport.phone) check(m.marginBottom >= 140, `${label}: phone reserves bar + launcher`);
        else check(m.marginBottom >= 80, `${label}: desktop/tablet reserves the launcher dock`);
    }

    // 2. --shell-dock-space drives the bell reservation: 0px reserves nothing extra,
    // the real shell.js value reserves the bell plus its offset.
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await evaluate(`document.documentElement.style.setProperty('--shell-dock-space', '0px')`);
    const none = await measure();
    check(!none.barShown && none.launcherShown && none.bellShown, 'dock-space 0: no mobile bar');
    await evaluate(`document.documentElement.style.setProperty('--shell-dock-space', 'calc(80px + env(safe-area-inset-bottom))')`);
    const docked = await measure();
    check(docked.marginBottom >= 80 && docked.content.bottom <= docked.bell.top + 0.5, 'dock-space calc(80px + safe-area) reserves the bell');

    // 3. Chat page without notifications: reserve only the mobile navigation bar.
    await load('no-launcher', { launcher: false, bellOn: false });
    for (const viewport of VIEWPORTS.filter(v => v.phone)) {
        const m = await layoutCase(`${viewport.width}x${viewport.height} bell only`, viewport, { launcher: false });
        check(m.marginBottom < 80, `${viewport.width}x${viewport.height}: bell-only phone reserves no launcher band`);
    }

    // 4. Embedded/guest pages omit all authenticated fixed chrome.
    await load('embedded', { mobile: false, launcher: false, bellOn: false });
    for (const viewport of VIEWPORTS) {
        const label = `${viewport.width}x${viewport.height} embedded`;
        const m = await layoutCase(label, viewport, { mobile: false, launcher: false });
        check(!m.barShown && !m.launcherShown, `${label}: no mobile bar and no launcher`);
        check(m.marginBottom < 80, `${label}: no phantom mobile space without bar/launcher`);
        check(m.last.bottom <= m.content.bottom + 0.5, `${label}: last action stays in the scrollport`);
    }

    // 5. Guest with no fixed chrome at all: nothing reserved.
    await load('bare', { mobile: false, launcher: false, bellOn: false });
    await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await waitFor('innerWidth === 390 && innerHeight === 844', 'bare viewport');
    const bare = await measure();
    check(bare.marginBottom === 0 && bare.barShown === false && bare.bellShown === false, 'no chrome: content reserves nothing');
    check(bare.overflowBefore === 0, 'no chrome: no horizontal overflow');
    await send('Emulation.clearDeviceMetricsOverride');

    check(blocked.length === 0, 'no blocked network request was attempted');
    console.log(`PASS ${checks.length} native Chromium shell layout checks (safe-area inset 0: real iOS not verified)`);
}

let timer = null;
function teardown() {
    if (timer) clearTimeout(timer);
    try { child.kill('SIGKILL'); } catch (_) { /* already gone */ }
    setTimeout(() => { try { fs.rmSync(temp, { recursive: true, force: true }); } catch (_) { /* best effort */ } }, 200);
}
timer = setTimeout(() => { console.error(`CDP run timed out after ${TIMEOUT_MS}ms`); process.exitCode = 1; teardown(); }, TIMEOUT_MS);
run()
    .catch(error => { console.error(error && error.stack ? error.stack : error); process.exitCode = 1; })
    .finally(teardown);
