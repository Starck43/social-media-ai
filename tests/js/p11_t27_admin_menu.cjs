/* Admin console menu contract: one open sidebar category at a time.
 *
 * Runs `app/static/js/admin_menu.js` against a minimal sidebar DOM, with a
 * stand-in for Bootstrap's dropdown data-api (the real bundle executes against
 * a live document on load, which a stub cannot provide). Covers the closure
 * rule and the `sqladmin:menu:*` storage the console also reads.
 */
const fs = require('fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const vm = require('node:vm');

const SCRIPT = path.join(__dirname, '../../app/static/js/admin_menu.js');
const KEY = 'sqladmin:menu:/admin/';
const SHOW = 'show';

function classList() {
    const set = new Set();
    return {
        add: name => set.add(name),
        remove: name => set.delete(name),
        contains: name => set.has(name),
        toggle(name, force) { if (force) set.add(name); else set.delete(name); },
        set,
    };
}

function node(type, attributes = {}, parent = null) {
    const element = { type, parent, children: [], attributes, listeners: {}, classList: classList() };
    const api = {
        setAttribute(name, value) { attributes[name] = String(value); },
        getAttribute(name) { return name in attributes ? attributes[name] : null; },
        contains(target) { for (let item = target; item; item = item.parent) if (item === this) return true; return false; },
        toggleAttribute() {},
        closest(selector) {
            for (let item = this; item; item = item.parent) if (item.matches(selector)) return item;
            return null;
        },
        matches(selector) {
            if (selector === '[data-sqladmin-menu]') return 'data-sqladmin-menu' in attributes;
            if (selector === '[data-sqladmin-menu-category]') return 'data-sqladmin-menu-category' in attributes;
            if (selector === '[data-bs-toggle]') return 'data-bs-toggle' in attributes;
            if (selector === '[data-bs-toggle="dropdown"]') return attributes['data-bs-toggle'] === 'dropdown';
            if (selector === '.dropdown-menu') return type === 'menu';
            if (selector === '.active') return element.classList.contains('active');
            if (selector === 'a[href]') return type === 'a' && 'href' in attributes;
            return false;
        },
        querySelector(selector) {
            if (selector === '[data-sqladmin-menu]') return this.queryInTree(selector);
            if (selector === '[data-bs-toggle="dropdown"]') return this.children.find(child => child.matches(selector)) || null;
            if (selector === '.dropdown-menu') return this.children.find(child => child.type === 'menu') || null;
            return this.queryInTree(selector);
        },
        queryInTree(selector) {
            for (const child of this.children) {
                if (child.matches(selector)) return child;
                const found = child.querySelector(selector);
                if (found && found !== child) return found;
            }
            return null;
        },
        querySelectorAll(selector) {
            const found = [];
            const walk = current => current.children.forEach(child => {
                if (child.matches(selector)) found.push(child);
                walk(child);
            });
            walk(this);
            return found;
        },
        addEventListener(type, fn, options) {
            const captureValue = typeof options === 'boolean' ? options : Boolean(options && options.capture);
            (this.listeners[type] = this.listeners[type] || []).push({ fn, capture: captureValue });
        },
        // Bubbles from the target up to the root, as a real click does.
        dispatch(event) {
            const chain = [];
            for (let item = this; item; item = item.parent) chain.unshift(item);
            for (const current of chain) (current.listeners[event.type] || []).forEach(listener => listener.fn.call(current, event));
        },
    };
    Object.assign(element, api);
    Object.defineProperty(element, 'href', {
        get() { return 'href' in attributes ? 'http://console.test' + attributes.href : undefined; },
    });
    if (parent) parent.children.push(element);
    return element;
}

function sidebar(remembered = [], activeGroup = null, options = {}) {
    globalThis.__sawBundle = false;
    const store = new Map();
    if (remembered.length) store.set(KEY, JSON.stringify(remembered));
    const root = node('html');
    const menu = node('nav', { 'data-sqladmin-menu': '/admin/' }, root);
    // A dropdown outside the sidebar (`data-outside-menu`) stays untouched.
    const outside = node('div', { 'data-outside-menu': '' }, root);
    const outsideToggle = node('toggle', { 'data-bs-toggle': 'dropdown' }, outside);
    const outsidePanel = node('menu', {}, outside);
    // `div.nav-item[data-sqladmin-menu-category]` wraps the toggle and panel,
    // as sqladmin's `menu_category` macro renders them.
    const groups = ['System', 'Content'].map(name => {
        const item = node('div', { 'data-sqladmin-menu-category': name }, menu);
        // The hidden collapse instance header the macro also renders.
        node('span', { 'data-bs-toggle': 'collapse' }, item);
        const toggle = node('toggle', { 'data-bs-toggle': 'dropdown', 'aria-expanded': 'false' }, item);
        const panel = node('menu', {}, item);
        // A real console link carries the full URL of the page it opens.
        const childLink = node('a', { href: '/admin/' + name.toLowerCase() + '/list' }, panel);
        if (activeGroup === name) { toggle.classList.add('active'); childLink.classList.add('active'); }
        return { name, item, toggle, panel, childLink };
    });
    const plain = node('a', {}, node('div', {}, menu));
    groups.forEach(group => {
        group.item.getClientRects = () => [1];
        group.toggle.getClientRects = () => [1];
        group.panel.getClientRects = () => [1];
        group.childLink.getClientRects = () => [1];
    });
    root.getClientRects = () => [1];
    const readyHooks = [];
    // Bootstrap's data-api binds one click handler here; the stub keeps them in
    // a plain list next to `type`-tagged entries added later.
    const document = {
        documentElement: root, listeners: [{ type: 'click', fn: () => {} }],
        addEventListener(type, fn) {
            if (type === 'DOMContentLoaded') readyHooks.push(fn);
            else this.listeners.push({ type, fn });
        },
        querySelector(selector) { return root.querySelector(selector); },
        querySelectorAll(selector) { return root.querySelectorAll(selector); },
        dispatch(type, detail = {}) {
            if (type === 'DOMContentLoaded') return;
            this.listeners.filter(listener => listener.type === type)
                .forEach(listener => listener.fn({ type, stopPropagation() {}, ...detail }));
        },
    };
    const localStorage = {
        getItem: key => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => store.set(key, String(value)),
    };
    // The sandbox literal would otherwise expose a different store per object.
    // The page is `/admin/<group>/list` unless the case says otherwise, so a
    // link's href can match the address like it does in a browser.
    const path = '/admin/' + (options.page || 'dashboard');
    // The sidebar script listens on `window` capture, which is the only place
    // that is guaranteed to run before the bundle's `document` handler.
    const win = {
        location: { pathname: path, href: 'http://console.test' + path, search: '', hash: '' },
        listeners: {},
        addEventListener(type, fn, opts) {
            const capture = typeof opts === 'boolean' ? opts : Boolean(opts && opts.capture);
            (this.listeners[type] = this.listeners[type] || []).push({ fn, capture });
        },
    };
    const sandbox = { document, console, JSON, Array, localStorage, window: win, location: win.location };
    // Every element is rendered in this stub; the real helper also treats a
    // zero-box element as hidden.
    win.getComputedStyle = () => ({ visibility: 'visible', display: 'block' });
    [root, menu, outside, outsideToggle, outsidePanel].forEach(element => { element.getClientRects = () => [1]; });
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(SCRIPT, 'utf8'), sandbox);
    const open = () => groups.filter(group => group.panel.classList.contains(SHOW)).map(group => group.name);
    const readNamesFromStorage = () => {
        try { const names = JSON.parse(store.get(KEY)); return Array.isArray(names) ? names : []; }
        catch (_) { return []; }
    };
    // Markup starts closed; run the restore script as the browser does after
    // its own stored-state restore.
    const render = (names) => names.forEach(name => {
        const group = groups.find(entry => entry.name === name);
        if (!group) return;
        [group.toggle, group.panel].forEach(element => element.classList.add(SHOW));
    });
    // On load the native script first restores every remembered group; only
    // then does the sidebar script get its DOMContentLoaded and fold the rest.
    const ready = () => {
        render(readNamesFromStorage());
        readyHooks.splice(0).forEach(fn => fn({ type: 'DOMContentLoaded' }));
    };
    // The shipped Tabler bundle binds Bootstrap's dropdown to `document` in the
    // capture phase, which runs *after* a capture listener on `window` and
    // *before* one on `documentElement` — that ordering is the defect this file
    // guards. The mirror keeps its own per-toggle instance state, as the real
    // one does, so a category the sidebar folded stays stale inside the bundle.
    const bundleShown = new WeakMap();
    document.listeners.unshift({ type: 'click', fn: event => {
        const toggle = event.target.matches('[data-bs-toggle="dropdown"]') ? event.target : null;
        if (!toggle) return;
        if (!globalThis.__editFinal) return;
        const item = toggle.closest('[data-sqladmin-menu-category]');
        const panel = item && item.querySelector('.dropdown-menu');
        globalThis.__sawBundle = true;
        if (bundleShown.get(toggle) === true) {
            bundleShown.set(toggle, false);
            [toggle, panel].forEach(element => element && element.classList.remove(SHOW));
            toggle.setAttribute('aria-expanded', 'false');
        } else {
            bundleShown.set(toggle, true);
            [toggle, panel].forEach(element => element && element.classList.add(SHOW));
            toggle.setAttribute('aria-expanded', 'true');
        }
    } });
    // A click reaches the window's capture listener first, then the bundle's
    // document handler; `bootstrap` says whether that handler is bound. Like a
    // real event, a handler that stops propagation ends the dispatch.
    const click = (element, extra = {}) => {
        const event = { type: 'click', target: element, button: 0, bootstrap: false, ...extra };
        let stopped = false;
        event.stopPropagation = () => { stopped = true; };
        event.preventDefault = () => {};
        (win.listeners.click || []).filter(listener => listener.capture).forEach(listener => listener.fn.call(win, event));
        if (event.bootstrap && !stopped) document.dispatch('click', event);
    };
    const toggle = name => groups.find(group => group.name === name).toggle;
    const childLink = name => groups.find(group => group.name === name).childLink;
    return {
        root, menu, groups, plain, outsideToggle, outsidePanel, store, open, click, toggle, childLink, ready,
        win, group: name => groups.find(entry => entry.name === name),
        // The console's final revision of the page: the bundle waits for clicks.
        editFinal: () => { globalThis.__editFinal = true; },
        get sawBundle() { return globalThis.__sawBundle === true; },
    };
}

let checks = 0;
const check = (actual, expected, label) => { assert.deepEqual(actual, expected, label); checks++; };

// The reported defect: a second category opened while the first stayed open.
let console_page = sidebar();
console_page.ready();
console_page.click(console_page.toggle('System'));
check(console_page.open(), ['System'], 'a click opens its own group');
console_page.click(console_page.toggle('Content'));
check(console_page.open(), ['Content'], 'a second category closes the first');
check(JSON.parse(console_page.store.get(KEY)), ['Content'], 'one remembered group');

// A header toggles its own group, independent of the bundle.
console_page.click(console_page.toggle('Content'));
check(console_page.open(), [], 're-clicking an open header closes it');
check(JSON.parse(console_page.store.get(KEY)), [], 'a closed menu remembers nothing');
console_page.click(console_page.toggle('Content'));
check(console_page.open(), ['Content'], 'the collapsed group opens again');

// The bundle keeps running in the console, so the sidebar must end a header
// click before it. Otherwise the bundle's own instance answers for a category
// the sidebar already folded: A, then B, then A again reopens nothing.
console_page = sidebar();
console_page.editFinal();
console_page.ready();
console_page.click(console_page.toggle('System'), { bootstrap: true });
check(console_page.open(), ['System'], 'a click opens the group with the bundle running');
console_page.click(console_page.toggle('Content'), { bootstrap: true });
check(console_page.open(), ['Content'], 'a second category closes the first with the bundle running');
console_page.click(console_page.toggle('System'), { bootstrap: true });
check(console_page.open(), ['System'], 'a folded category still expands when the bundle is bound');
check(console_page.sawBundle, false, 'the sidebar ends a header click before the bundle sees it');
console_page.click(console_page.toggle('Content'), { bootstrap: true });
check(console_page.open(), ['Content'], 'switching back still works with the bundle bound');
console_page.click(console_page.toggle('Content'), { bootstrap: true });
check(console_page.open(), [], 'the open header closes with the bundle running');
check(JSON.parse(console_page.store.get(KEY)), [], 'the closed menu is not remembered');

// A plain sidebar link closes the groups.
console_page.click(console_page.toggle('System'));
console_page.click(console_page.plain);
check(console_page.open(), [], 'any other menu item closes the groups');

// Clicks on a page dropdown outside the menu leave the sidebar alone.
console_page = sidebar();
console_page.ready();
console_page.click(console_page.toggle('System'));
console_page.click(console_page.outsideToggle);
check(console_page.open(), ['System'], 'a page dropdown does not rearrange the sidebar');

// Modified clicks (open in a new tab) leave the menu untouched.
console_page.click(console_page.toggle('Content'), { ctrlKey: true });
check(console_page.open(), ['System'], 'a modified click does not rearrange groups');

// A reload restores exactly the remembered group.
console_page = sidebar(['System']);
console_page.ready();
check(console_page.open(), ['System'], 'the remembered group is restored');

// A stack left by the old behavior keeps only its newest group.
console_page = sidebar(['System', 'Content']);
console_page.ready();
check(JSON.parse(console_page.store.get(KEY)), ['Content'], 'a stored stack collapses to its newest group');
check(console_page.open(), ['Content'], 'only the newest remembered group stays open');

// Unreadable storage keeps the menu usable.
console_page = sidebar();
console_page.store.set(KEY, '{not json');
console_page.ready();
console_page.click(console_page.toggle('System'));
check(console_page.open(), ['System'], 'broken storage does not break the menu');

// Clicking a child model link inside an open submenu keeps that category open and stored.
console_page = sidebar();
console_page.ready();
console_page.click(console_page.toggle('Content'));
console_page.click(console_page.childLink('Content'));
check(console_page.open(), ['Content'], 'clicking a submenu model link keeps its category open');
check(JSON.parse(console_page.store.get(KEY)), ['Content'], 'clicking a submenu model link keeps its category remembered');

// Active route category auto-expands when storage is empty.
console_page = sidebar([], 'System');
console_page.ready();
check(console_page.open(), ['System'], 'active route category auto-expands when storage is empty');
check(JSON.parse(console_page.store.get(KEY)), ['System'], 'active route category is remembered');

// A browser never marks a console model link with an "active" class, and a
// collapsed panel hides every link in it: only a link to this very page counts,
// and only when it is actually rendered.
console_page = sidebar([], null, { page: 'content/list' });
console_page.ready();
check(console_page.open(), ['Content'], 'the group whose visible model link points at this page opens');
check(JSON.parse(console_page.store.get(KEY)), ['Content'], 'the current page group is remembered');

console_page = sidebar(['Content'], null, { page: 'dashboard' });
console_page.ready();
check(console_page.open(), ['Content'], 'with no link to this page the remembered group stays');

// The link to this page lives inside a collapsed panel, so it is not visible.
// The older menu opened no group on arrival, which is the case where a hidden
// link would wrongly count as the current page.
console_page = sidebar();
console_page.win.location.href = 'http://console.test/admin/content/list';
console_page.win.location.pathname = '/admin/content/list';
console_page.group('Content').childLink.getClientRects = () => [];
console_page.ready();
check(console_page.open(), [], 'a link hidden inside a collapsed panel does not mark the current page');

// The current page wins over a group remembered from somewhere else: the
// console marks the category of the open view as active.
console_page = sidebar(['Content'], 'System');
console_page.ready();
check(console_page.open(), ['System'], 'the category of the current page wins over stale storage');
check(JSON.parse(console_page.store.get(KEY)), ['System'], 'the current page category replaces stale storage');

// A stack written by the old behavior with no markup state keeps its newest.
console_page = sidebar();
console_page.ready();
console_page.store.set(KEY, JSON.stringify(['System', 'Content']));
check(console_page.open(), [], 'nothing is open before the page renders the stack');

// A remembered category that no longer exists leaves the menu closed, without
// resurrecting the old group.
console_page = sidebar(['Removed']);
console_page.ready();
check(console_page.open(), [], 'a remembered category that no longer exists opens nothing');

console.log(`${checks} admin menu single-open/restore checks passed.`);
