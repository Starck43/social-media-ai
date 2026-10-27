// Render-only Chromium check of the real chat template in the existing shell.
// No application server, database, providers or network access are required.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');

const root = path.resolve(__dirname, '../..');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'p8-t39-chat-'));
const fixture = path.join(scratch, 'chat.html');
const render = String.raw`
from types import SimpleNamespace
from app.web.deps import templates
from app.web.nav import NAV_ITEMS
context = {
    "user": SimpleNamespace(username="owner", role=None, has_admin_access=lambda: False),
    "tenant": SimpleNamespace(id=1, name="Fixture workspace"),
    "workspaces": [], "memberships": [], "unread_notifications": 0,
    "connection_alerts": [], "perms": None, "member_level": None,
    "nav": NAV_ITEMS, "mobile_nav": NAV_ITEMS, "csrf": "fixture",
    "flashes": [], "path": "/app/chat", "section": "chat",
    "plan_labels": {}, "_tz": "UTC",
    "messages": [], "is_owner": True,
    "request": SimpleNamespace(query_params={"embedded": "1"}),
    "url_for": lambda name, **kwargs: "/static/" + kwargs["path"],
    "static_v": lambda path: "/static/" + path,
}
print(templates.env.get_template("web/chat.html").render(**context))
`;

try {
    const py = spawnSync(process.env.PYTHON || 'python', ['-c', render], { cwd: root, encoding: 'utf8', timeout: 30000 });
    if (py.error) throw py.error;
    if (py.status !== 0) throw Error(py.stderr || 'Jinja chat template render failed');

    let html = py.stdout;
    html = html.replace(/<script\b[\s\S]*?<\/script>/gi, '');
    html = html.replace(/<link\b[^>]*rel="stylesheet"[^>]*>/gi, '');
    const css = ['theme.css', 'ui.css', 'shell.css', 'chat.css']
        .map(name => fs.readFileSync(path.join(root, 'app/static/css', name), 'utf8'))
        .join('\n');
    const js = fs.readFileSync(path.join(root, 'app/static/js/chat.js'), 'utf8');
    const browserChecks = `
      const checks = [];
      const check = (value, label) => { if (!value) throw Error(label); checks.push(label); };
      const app = chatApp();
      const starterRoot = document.querySelector('.chat-starters');
      for (const starter of app.starters) {
        const button = document.createElement('button');
        button.type = 'button'; button.className = 'chat-starter';
        button.innerHTML = '<span class="chat-starter-title"></span><span class="chat-starter-hint"></span>';
        button.querySelector('.chat-starter-title').textContent = starter.title;
        button.querySelector('.chat-starter-hint').textContent = starter.hint;
        starterRoot.append(button);
      }
      check(document.querySelector('.ui-shell'), 'existing shell renders');
      check(document.querySelector('#page-heading')?.textContent.includes('Чат с агентом'), 'chat remains primary page');
      check(starterRoot.querySelectorAll('.chat-starter').length === 6, 'six useful chat starters render');
      for (const path of ['/app/', '/app/sources', '/app/scenarios', '/app/tasks', '/app/digests']) {
        check([...document.querySelectorAll('a')].some(a => a.getAttribute('href') === path), 'direct route ' + path);
      }
      const guidance = document.querySelector('.chat-window').textContent;
      for (const phrase of ['цель', 'данные и источники', 'способ обработки', 'нужный результат', 'период или расписание', 'ограничения']) {
        check(guidance.includes(phrase), 'six-group guidance: ' + phrase);
      }
      const columns = getComputedStyle(starterRoot).gridTemplateColumns.split(' ').length;
      check(columns === (innerWidth < 640 ? 1 : 2), 'responsive starter grid');
      const result = document.createElement('pre');
      result.id = 'p8t39-results'; result.textContent = 'PASS ' + checks.length + ' checks';
      document.body.append(result);
    `;
    html = html.replace('</head>', `<style>${css}</style></head>`);
    html = html.replace('</body>', `<script>${js}</script><script>${browserChecks}</script></body>`);
    fs.writeFileSync(fixture, html);

    const browser = process.env.CHROMIUM_BIN || 'chromium';
    const profile = path.join(scratch, 'chrome-profile');
    fs.mkdirSync(profile);
    for (const [width, columns] of [[1280, 2], [390, 1]]) {
        const run = spawnSync(browser, [
            '--headless', '--no-sandbox', '--disable-gpu', '--disable-network',
            `--user-data-dir=${profile}`, `--window-size=${width},900`, '--dump-dom', pathToFileURL(fixture).href,
        ], { encoding: 'utf8', timeout: 30000 });
        if (run.error) throw run.error;
        const dom = run.stdout || '';
        const result = dom.match(/<pre id="p8t39-results">([^<]+)<\/pre>/)?.[1];
        if (run.status !== 0 || !result?.startsWith('PASS 15 checks')) {
            throw Error(dom.match(/Uncaught[^<]*/)?.[0] || run.stderr || 'Browser checks failed');
        }
        console.log(`PASS ${result} at ${width}px (${columns === 1 ? 'mobile' : 'desktop'})`);
    }
} finally {
    fs.rmSync(scratch, { recursive: true, force: true });
}
