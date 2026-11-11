/* P20-T13 shell usability: real Chromium keyboard/focus/scroll checks.
   Uses the actual shell.js/context_panel.js sources on a synthetic file:// page
   over Chromium's local debugging pipe. No app import, DB, runtime, server,
   network, CDN or external URL: Fetch interception fails every non-file
   request, so a regression that reaches the network fails the run. */
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
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'p20-t13-shell-'));
const fixture = path.join(temp, 'fixture.html');
const readJs = name => fs.readFileSync(path.join(root, 'app/static/js', name), 'utf8');

// Mirrors popover_select in app/web/templates/web/_macros.html: hidden value
// field, trigger with data-popover-label, option buttons with aria-selected.
const popover = (id, options, current) => `
<div class="shell-popover" data-shell-popover data-popover-select>
    <input type="hidden" name="${id}" value="${current}" data-popover-value>
    <button id="${id}-trigger" type="button" data-popover-trigger aria-expanded="false" aria-controls="${id}-popover">
        <span data-popover-label>${(options.find(option => option[0] === current) || ['', 'Автовыбор'])[1]}</span>
    </button>
    <div id="${id}-popover" class="shell-popover-panel" hidden role="listbox">
        ${options.map(([value, label]) => `<button type="button" role="option" data-popover-option="${value}" aria-selected="${value === current}"><span>${label}</span><span aria-hidden="true">✓</span></button>`).join('')}
    </div>
</div>`;

const dialogs = `
<button type="button" id="open-invite" data-dialog-open="invite-dialog">Приглашения</button>
<button type="button" id="chat-dock-toggle" data-chat-dock-toggle aria-controls="chat-dock-drawer" aria-expanded="false">Спросить агента</button>
<button type="button" id="ask-agent" data-ask-agent-trigger>Спросить об объекте</button>
${popover('model', [['', 'Автовыбор'], ['fast', 'Быстрая'], ['balanced', 'Сбалансированная'], ['deep', 'Глубокая']], 'balanced')}
<div id="object-row" data-object-context data-context-type="object" data-context-id="42" data-boundary="cloud" data-context-title="Объект 42">Объект 42</div>
<main id="page-content" tabindex="-1"><h1 id="page-heading">Задачи</h1><p>content</p></main>
<dialog id="invite-dialog" class="shell-dialog" aria-labelledby="invite-title">
    <h2 id="invite-title">Приглашения</h2>
    <button type="button" id="invite-roving" tabindex="-1">Ровер</button>
    <button type="button" id="invite-first">Сохранить</button>
    <button type="button" id="open-context" data-dialog-open="context-panel">Контекст</button>
    <button type="button" id="invite-close" data-dialog-close>Закрыть</button>
</dialog>
<dialog id="context-panel" class="shell-right-panel" aria-label="Контекст страницы">
    <div data-evidence-body>
        <p data-evidence-boundary>cloud</p>
        <a data-chat-handoff href="/app/chat">Открыть в чате</a>
    </div>
    <button type="button" id="context-close" data-dialog-close>Закрыть</button>
</dialog>
<dialog id="chat-dock-drawer" class="shell-right-panel chat-dock-drawer" aria-labelledby="chat-dock-title" data-chat-dock>
    <h2 id="chat-dock-title">Спросить агента</h2>
    <button type="button" id="chat-dock-close" data-chat-dock-close>Закрыть</button>
    <div data-dock-context-chip><span data-dock-chip-title>Объект 42</span><button type="button" data-dock-remove-chip>Убрать</button></div>
    <p data-dock-local-only-refusal hidden>Локальный контекст не отправляется</p>
    <form action="/app/chat" method="post" data-dock-form>
        <input type="hidden" name="context_type" value="" data-dock-input-type>
        <input type="hidden" name="context_id" value="" data-dock-input-id>
        <input type="hidden" name="boundary" value="cloud" data-dock-input-boundary>
        <textarea id="chat-dock-input" data-dock-input></textarea>
        <button type="submit" id="chat-dock-send" data-dock-send>Отправить</button>
    </form>
</dialog>
<!-- A native dialog that is not a shell dialog: its opening never locks the page. -->
<dialog id="notification-overlay" class="shell-notification-modal" aria-label="Уведомления"><button type="button" id="notification-close" data-dialog-close>Закрыть</button></dialog>`;

fs.writeFileSync(fixture, `<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
    body { margin: 0; min-height: 200vh; }
    dialog { margin: 40px auto; }
    dialog.shell-right-panel { margin: 0 0 0 auto; }
    .shell-popover { position: relative; display: inline-block; }
    .shell-popover-panel { position: absolute; left: 0; width: 100%; background: #fff; border: 1px solid #ccc; z-index: 5; }
    .shell-popover-panel[hidden] { display: none; }
</style></head><body>
${dialogs}
<script>${readJs('shell.js')}</script>
<script>${readJs('context_panel.js')}</script>
<script>
window.__events = { input: 0, submits: 0, ignoredSubmits: 0, appOpens: {}, nativeShows: 0 };
document.querySelector('[data-popover-value]').addEventListener('input', () => window.__events.input++);
document.addEventListener('submit', event => {
    if (!event.defaultPrevented && event.target.hasAttribute('data-dock-form')) window.__events.submits++;
    event.preventDefault();
});
// Real native reality: a modal 'show' is not a DOM event. Count the app
// announcement the shell issues and any unexpected 'show' EVENT (a toggle
// always fires a non-event 'show' member, which says nothing).
document.addEventListener('app:shell-dialog-open', event => {
    const id = event.detail.dialog.id;
    window.__events.appOpens[id] = (window.__events.appOpens[id] || 0) + 1;
}, true);
document.addEventListener('show', () => { window.__events.nativeShows++; }, true);
// Ignored submissions still happen in a fixture browser: never navigate away.
window.addEventListener('beforeunload', event => { window.__events.ignoredSubmits++; event.preventDefault(); });
</script>
</body></html>`);

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

let nextId = 0, buffer = Buffer.alloc(0), sessionId = null, browserClosed = false;
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
    browserClosed = true;
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
        if (browserClosed) return reject(Error(`Chromium exited before ${method}`));
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

const CODES = { ArrowDown: 40, ArrowUp: 38, Home: 36, End: 35, Tab: 9, Escape: 27, Enter: 13 };
async function key(name, { shift = false } = {}) {
    const base = { key: name, code: name, windowsVirtualKeyCode: CODES[name], nativeVirtualKeyCode: CODES[name], modifiers: shift ? 8 : 0 };
    await send('Input.dispatchKeyEvent', { type: name === 'Enter' ? 'keyDown' : 'rawKeyDown', ...base,
        ...(name === 'Enter' ? { text: '\r', unmodifiedText: '\r' } : {}) });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', ...base });
    await settle();
}
async function clickAt(x, y) {
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
    await settle();
}
const settle = () => evaluate('new Promise(resolve => setTimeout(resolve, 30))');
const focusId = () => evaluate(`(() => {
    const el = document.activeElement;
    if (!el || el === document.body) return 'BODY';
    return el.id || (el.hasAttribute('data-popover-option') ? 'option:' + el.getAttribute('data-popover-option') : el.tagName);
})()`);
const overflow = () => evaluate('document.body.style.overflow');
// Native close queues its close event separately from clearing the open flag.
// Wait for the presentation cleanup before reopening or checking restored state.
const closed = id => `!document.getElementById('${id}').open && !document.getElementById('${id}').shellDepth`;
const open = id => evaluate(`document.getElementById(${JSON.stringify(id)}).open`);

async function waitFor(expression, label, attempts = 80) {
    for (let i = 0; i < attempts; i++) {
        if (await evaluate(expression)) return;
        await evaluate('new Promise(resolve => setTimeout(resolve, 25))');
    }
    throw Error(`Timed out waiting for ${label}`);
}

const checks = [];
function check(ok, label) { assert.ok(ok, `FAILED: ${label}`); checks.push(label); }

async function run() {
    const target = await send('Target.createTarget', { url: 'about:blank' }, null);
    sessionId = (await send('Target.attachToTarget', { targetId: target.targetId, flatten: true }, null)).sessionId;
    await send('Runtime.enable');
    await send('Page.enable');
    await send('Network.enable');
    // Fail every request that is not a synthetic file:// asset, then probe the
    // same interception: a network asset must be refused before it is sent.
    await send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    await send('Page.navigate', { url: pathToFileURL(fixture).href });
    await waitFor('document.readyState === "complete" && !!window.__events', 'fixture ready');
    check(blocked.length === 0, 'fixture loads without any network request');
    const denied = await evaluate(`Promise.all([
        fetch('https://example.invalid/blocked').then(() => 'allowed', () => 'blocked'),
        new Promise(resolve => { const probe = new Image(); probe.onload = () => resolve('allowed'); probe.onerror = () => resolve('blocked'); probe.src = 'http://127.0.0.1:9/probe.png'; }),
    ]).then(([http, image]) => ({ http, image }))`);
    check(denied.http === 'blocked' && denied.image === 'blocked', 'network requests are refused');
    blocked.length = 0;

    // 1. Standalone model picker (rendered page, no dialog): ArrowDown/ArrowUp
    //    focus the selection exactly once, bubbling does not skip an option.
    await evaluate(`document.getElementById('model-trigger').focus()`);
    await key('ArrowDown');
    check(await evaluate(`!document.getElementById('model-popover').hidden`), 'ArrowDown opens the model picker');
    check((await focusId()) === 'option:balanced', 'ArrowDown lands on the selected option, not past it');
    check(await evaluate(`document.getElementById('model-trigger').getAttribute('aria-expanded') === 'true'`), 'trigger reports expanded');
    await key('ArrowDown'); check((await focusId()) === 'option:deep', 'ArrowDown moves to the next option');
    await key('ArrowUp'); check((await focusId()) === 'option:balanced', 'ArrowUp returns to the selected option');
    await key('ArrowUp'); check((await focusId()) === 'option:fast', 'ArrowUp moves to the previous option');
    await key('Home'); check((await focusId()) === 'option:', 'Home moves to the first option');
    await key('End'); check((await focusId()) === 'option:deep', 'End moves to the last option');
    await key('Escape');
    check((await focusId()) === 'model-trigger', 'Escape restores trigger focus');
    check(await evaluate(`document.getElementById('model-popover').hidden`), 'Escape closes the option list');
    check(await evaluate(`document.querySelector('[data-popover-value]').value === 'balanced'`), 'Escape keeps the stored value');
    await key('ArrowUp');
    check((await focusId()) === 'option:balanced', 'ArrowUp from the trigger resumes on the stored selection');
    // Two ArrowDown presses move balanced -> deep -> placeholder, without
    // changing the stored value before activation.
    await key('ArrowDown'); await key('ArrowDown');
    check((await focusId()) === 'option:', 'two ArrowDown presses move exactly two options');
    await key('Enter');
    check(await evaluate(`document.querySelector('[data-popover-value]').value === ''`), 'Enter selects the highlighted option');
    check(await evaluate(`document.querySelector('[data-popover-label]').textContent === 'Автовыбор'`), 'trigger label follows the choice');
    check(await evaluate('window.__events.input === 1'), 'selection dispatches exactly one input event');
    check((await focusId()) === 'model-trigger', 'selection returns focus to the trigger');
    // Re-selecting the placeholder stays a valid empty value.
    await key('ArrowDown'); await key('ArrowUp');
    check((await focusId()) === 'option:deep', 'ArrowUp wraps from the new placeholder selection');
    await key('Home'); await key('Enter');
    check(await evaluate(`document.querySelector('[data-popover-value]').value === ''`), 'the placeholder option stores an empty value');
    check(await evaluate('window.__events.input === 2'), 'a second selection dispatches exactly one more input event');
    // A non-shell native dialog never claims the page lock, even if announced.
    await evaluate(`document.getElementById('notification-overlay').showModal(); document.getElementById('notification-overlay').dispatchEvent(new CustomEvent('app:shell-dialog-open', { detail: { dialog: document.getElementById('notification-overlay') } }))`);
    check((await overflow()) === '', 'a non-shell notification dialog never locks page scroll');
    await evaluate(`document.getElementById('notification-overlay').close()`);

    // 2. Invite dialog opened by a page launcher keeps scroll lock and focus.
    await evaluate(`document.body.style.overflow = 'auto'; window.scrollTo(0, 120); document.getElementById('open-invite').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('invite-dialog').open`, 'invite dialog opens');
    check(await evaluate(`window.__events.appOpens['invite-dialog'] === 1`), 'invite dialog announces exactly one opening');
    check(await evaluate('window.__events.nativeShows === 0'), 'showModal raises no native show event (the announcement is explicit)');
    check((await overflow()) === 'hidden', 'invite dialog locks page scroll');
    // Real boundaries, measured in the browser without hidden/inert/negative tabindex.
    const lastControl = await evaluate(`(() => {
        const dialog = document.getElementById('invite-dialog');
        const controls = [...dialog.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
            .filter(el => !el.disabled && !el.closest('[hidden], [inert]') && el.tabIndex >= 0 && el.getClientRects().length);
        return { first: controls[0].id, last: controls[controls.length - 1].id };
    })()`);
    check(lastControl.first === 'invite-first' && lastControl.last === 'invite-close',
        'the negative-tabindex control is not the first trap boundary');
    await evaluate(`document.getElementById('invite-close').focus()`);
    await key('Tab');
    check((await focusId()) === 'invite-first', 'Tab from the last real control wraps to the first');
    await evaluate(`document.getElementById('invite-first').focus()`);
    await key('Tab', { shift: true });
    check((await focusId()) === 'invite-close', 'Shift+Tab from the first real control wraps to the last');

    // 3. Nested shell dialogs share the scroll lock; inner close cannot unlock it.
    await evaluate(`document.getElementById('open-context').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('context-panel').open`, 'context panel opens');
    check((await overflow()) === 'hidden', 'nested dialog keeps the page locked');
    await evaluate(`document.getElementById('context-panel').close()`);
    await waitFor(closed('context-panel'), 'context panel closes');
    check((await overflow()) === 'hidden', 'closing the inner dialog does not unlock scroll');
    check((await focusId()) === 'open-context', 'inner dialog returns focus to its launcher inside the invite dialog');
    await key('Escape');
    await waitFor(closed('invite-dialog'), 'invite dialog closes with native Escape');
    check((await overflow()) === 'auto', 'closing the outermost dialog restores the prior overflow');
    check((await focusId()) === 'open-invite', 'closing the dialog returns focus to its page launcher');
    // The original value was empty: an empty marker is restored empty, byte for byte.
    await evaluate(`document.body.style.overflow = ''; document.getElementById('open-invite').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('invite-dialog').open`, 'invite dialog reopens');
    check((await overflow()) === 'hidden', 'an originally empty overflow is still locked');
    await key('Escape');
    await waitFor(closed('invite-dialog'), 'invite dialog closes again');
    check((await overflow()) === '', 'an originally empty overflow is restored as empty');
    check(await evaluate(`document.body.style.getPropertyValue('overflow') === ''`), 'no leftover overflow declaration remains');

    // 4. Chat dock opened by context_panel.js joins the same presentation.
    await evaluate(`document.getElementById('chat-dock-toggle').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('chat-dock-drawer').open`, 'chat dock opens');
    check(await evaluate(`window.__events.appOpens['chat-dock-drawer'] === 1`), 'dock announces exactly one opening per activation');
    check((await overflow()) === 'hidden', 'dock locks page scroll through the shared depth');
    check(await evaluate(`document.getElementById('chat-dock-toggle').getAttribute('aria-expanded') === 'true'`), 'dock launcher is marked expanded');
    check((await focusId()) === 'chat-dock-input', 'dock focuses its composer');
    await key('Tab');
    check((await focusId()) === 'chat-dock-send', 'dock Tab moves to send');
    await evaluate(`document.getElementById('chat-dock-input').focus()`);
    await key('Escape');
    await waitFor(closed('chat-dock-drawer'), 'dock closes with Escape');
    check((await overflow()) === '', 'closing the dock restores the previous overflow');
    check((await focusId()) === 'chat-dock-toggle', 'dock returns focus to its launcher');
    await evaluate(`document.getElementById('ask-agent').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('chat-dock-drawer').open`, 'dock opens from the page trigger');
    check(await evaluate(`window.__events.appOpens['chat-dock-drawer'] === 2`), 'dock announces one opening from the page trigger');
    await key('Escape');
    await waitFor(closed('chat-dock-drawer'), 'dock closes from the page trigger');
    check((await focusId()) === 'ask-agent', 'dock returns focus to the page trigger, not the fixed toggle');

    // 5. Local-only context keeps the refusal boundary.
    await evaluate(`document.getElementById('object-row').setAttribute('data-boundary', 'local_only'); document.getElementById('ask-agent').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('chat-dock-drawer').open`, 'dock reopens');
    check(await evaluate(`document.querySelector('[data-dock-input-boundary]').value === 'local_only'`), 'dock carries the local_only boundary');
    check(await evaluate(`!document.querySelector('[data-dock-local-only-refusal]').hidden`), 'refusal notice is visible');
    check(await evaluate(`document.getElementById('chat-dock-send').disabled`), 'send is disabled for local-only context');
    check((await focusId()) === 'chat-dock-close', 'blocked dock focuses its close control');
    await evaluate(`document.querySelector('[data-dock-form]').requestSubmit()`);
    await settle();
    check(await evaluate('window.__events.submits === 0'), 'local-only form cannot be submitted');
    await evaluate(`document.getElementById('object-row').setAttribute('data-boundary', 'cloud')`);
    await key('Escape');
    await waitFor(closed('chat-dock-drawer'), 'dock closes again');

    // 6. Real backdrop click closes the dialog and releases scroll.
    await evaluate(`document.getElementById('open-invite').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('invite-dialog').open`, 'invite dialog reopens');
    const rect = await evaluate(`(() => { const box = document.getElementById('invite-dialog').getBoundingClientRect(); return { left: box.left, top: box.top }; })()`);
    await clickAt(Math.max(1, rect.left - 12), Math.max(1, rect.top - 12));
    await waitFor(closed('invite-dialog'), 'backdrop click closes the dialog');
    check((await overflow()) === '', 'backdrop close releases the scroll lock');
    check((await focusId()) === 'open-invite', 'backdrop close restores the launcher focus');

    // 7. Mobile viewport keeps the dock on screen.
    await send('Emulation.setDeviceMetricsOverride', { width: 375, height: 667, deviceScaleFactor: 1, mobile: true });
    await evaluate(`document.getElementById('chat-dock-toggle').focus()`);
    await key('Enter');
    await waitFor(`document.getElementById('chat-dock-drawer').open`, 'dock opens on mobile');
    check(await evaluate(`(() => {
        const box = document.getElementById('chat-dock-drawer').getBoundingClientRect();
        return box.left >= -1 && box.top >= -1 && box.bottom <= innerHeight + 1 && box.width > 0;
    })()`), 'mobile dock stays within the viewport');
    await key('Escape');
    await waitFor(closed('chat-dock-drawer'), 'mobile dock closes');
    await send('Emulation.clearDeviceMetricsOverride');

    check(blocked.length === 0, 'no blocked network request was attempted');
    check(await evaluate('window.__events.ignoredSubmits === 0'), 'fixture never navigated away');
    console.log(`PASS ${checks.length} native Chromium shell usability checks`);
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
