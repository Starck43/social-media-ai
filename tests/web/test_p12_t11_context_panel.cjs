// Offline DOM check using Chromium CLI; no Playwright/server/provider dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'p12-t11-dom-'));
try {
    const panelScript = fs.readFileSync(path.resolve(__dirname, '../../app/static/js/context_panel.js'), 'utf8');
    const html = `<!doctype html><html><body>
    <button data-evidence-trigger data-evidence-title="Test &lt;source&gt;"
        data-evidence-ref="fixture://source/11" data-evidence-hash="sha256:fixture"
        data-evidence-anchor="page 2" data-evidence-boundary="local_only"
        data-evidence-context-type="knowledge" data-evidence-context-id="11">Evidence</button>
    <dialog id="context-panel"><h2 data-evidence-title></h2><dl>
        <dd data-evidence-ref></dd><dd data-evidence-hash></dd>
        <dd data-evidence-anchor></dd><dd data-evidence-boundary></dd></dl>
        <a hidden data-chat-handoff>Chat</a></dialog>
    <output id="smoke-result">NOT RUN</output>
    <script>${panelScript}</script>
    <script>
    // file:// has no HTTPS origin: give the URL constructor a fixed offline base.
    const NativeURL = URL;
    window.URL = class extends NativeURL { constructor(url, base) { super(url, base === 'null' ? 'https://evidence.test' : base); } };
    document.addEventListener('DOMContentLoaded', () => {
        const result = document.getElementById('smoke-result');
        const check = (ok, what) => { if (!ok) throw new Error(what); };
        try {
            document.querySelector('[data-evidence-trigger]').click();
            const panel = document.getElementById('context-panel');
            check(panel.open, 'dialog did not open');
            for (const [field, value] of Object.entries({title: 'Test <source>', ref: 'fixture://source/11', hash: 'sha256:fixture', anchor: 'page 2', boundary: 'local_only'})) {
                check(panel.querySelector('[data-evidence-' + field + ']').textContent === value, field);
            }
            const link = panel.querySelector('[data-chat-handoff]');
            check(!link.hidden, 'handoff hidden');
            const url = new NativeURL(link.getAttribute('href'), 'https://evidence.test');
            check(url.pathname === '/app/chat', 'chat path');
            check(JSON.stringify(Object.fromEntries(url.searchParams)) === JSON.stringify({context_type: 'knowledge', context_id: '11', boundary: 'local_only'}), 'handoff parameters');
            panel.querySelector('[data-evidence-ref]').click();
            check(link.getAttribute('href') === url.pathname + url.search, 'metadata click rebound context');
            check(!panel.querySelector('source'), 'unescaped title');
            result.textContent = 'PASS';
        } catch (error) { result.textContent = 'FAIL: ' + error.message; }
    });
    </script></body></html>`;
    const file = path.join(dir, 'check.html');
    fs.writeFileSync(file, html);
    const run = spawnSync(process.env.CHROMIUM_PATH || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu', '--dump-dom', 'file://' + file], {encoding: 'utf8', timeout: 30000});
    assert.equal(run.status, 0, run.error?.message || run.stderr.slice(-1500));
    const result = run.stdout.match(/<output id="smoke-result">([^<]+)<\/output>/)?.[1];
    assert.equal(result, 'PASS');
    console.log('PASS: evidence click, dialog metadata, escaped title, boundary and handoff URL');
} finally {
    fs.rmSync(dir, {recursive: true, force: true});
}
