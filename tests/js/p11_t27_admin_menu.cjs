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
    const element = {
        type, parent, children: [], attributes, listeners: {},
        classList: classList(),
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
            if (selector === '[data-bs-toggle="dropdown"]') return 'data-bs-toggle' in attributes;
            if (selector === '.dropdown-menu') return type === 'menu';
            if (selector === '.active') return element.classList.contains('active');
            return false;
        },
        querySelector(selector) {
            if (selector === '[data-sqladmin-menu]') return this.querySelectorInTree(selector);
            if (selector === '[data-bs-toggle="dropdown"]') return this.children.find(child => child.type === 'toggle') || null;
            if (selector === '.dropdown-menu') return this.children.find(child => child.type === 'menu') || null;
            return this.querySelectorInTree(selector);
        },
        querySelectorInTree(selector) {
            const found = [];
            const walk = item => item.children.forEach(child => {
                if (child.matches(selector) && found.length === 0) found.push(child);
                walk(child);
            });
            walk(this);
            return found[0] || null;
        },
        querySelectorAll(selector) {
            const found = [];
            const walk = item => item.children.forEach(child => {
                if (child.matches(selector)) found.push(child);
                if (child.type === 'menu-item' || child.type === 'div' || child.type === 'nav' || child.type === 'li') walk(child);
            });
            walk(this);
            return found;
        },
        addEventListener(type, fn, options) {
            const capture = typeof options === 'boolean' ? options : Boolean(options && options.capture);
            (this.listeners[type] = this.listeners[type] || []).push({ fn, capture });
        },
        // Bubbles from the target up to the root, as a real click does.
        dispatch(event) {
            const chain = [];
            for (let item = this; item; item = item.parent) chain.unshift(item);
            for (const item of chain) (item.listeners[event.type] || []).forEach(listener => listener.fn.call(item, event));
        },
    };
    if (parent) parent.children.push(element);
    return element;
}

function sidebar(remembered = [], activeGroup = null) {
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
        const toggle = node('toggle', { 'data-bs-toggle': 'dropdown', 'aria-expanded': 'false' }, item);
        const panel = node('menu', {}, item);
        const childLink = node('a', {}, panel);
        if (activeGroup === name) { toggle.classList.add('active'); childLink.classList.add('active'); }
        return { name, item, toggle, panel, childLink };
    });
    const plain = node('a', {}, node('div', {}, menu));
    const readyHooks = [];
    const document = {
        documentElement: root, listeners: [],
        addEventListener(type, fn) {
            if (type === 'DOMContentLoaded') readyHooks.push(fn);
            else this.listeners.push({ type, fn });
        },
        querySelector(selector) { return root.querySelectorInTree(selector); },
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
    const sandbox = { document, console, JSON, Array, localStorage };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(SCRIPT, 'utf8'), sandbox);
    // Bootstrap's data-api listens on the document, after the capture-phase
    // sidebar listener has folded the groups.
    document.listeners.push({ type: 'click', fn: event => {
        const toggle = event.target.type === 'toggle' && event.target.closest?.('[data-sqladmin-menu]')
            && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey && Number(event.button) <= 0
            ? event.target : null;
        if (!toggle) return;
        const group = toggle.parent;
        const panel = group.querySelector('.dropdown-menu');
        const open = !toggle.classList.contains(SHOW);
        [toggle, panel].forEach(element => element.classList.toggle(SHOW, open));
        toggle.setAttribute('aria-expanded', String(open));
        document.dispatch(open ? 'shown.bs.dropdown' : 'hidden.bs.dropdown', { target: toggle });
    } });
    const readNamesFromStorage = () => {
        try { const names = JSON.parse(store.get(KEY)); return Array.isArray(names) ? names : []; }
        catch (_) { return []; }
    };
    const open = () => groups.filter(group => group.panel.classList.contains(SHOW)).map(group => group.name);
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
    // Capture runs from the root down, then the document-level handlers (the
    // sidebar script and Bootstrap's data-api).
    const click = (element, extra = {}) => {
        const event = { type: 'click', target: element, button: 0, ...extra };
        (root.listeners.click || []).filter(listener => listener.capture).forEach(listener => listener.fn.call(root, event));
        document.dispatch('click', event);
    };
    const toggle = name => groups.find(group => group.name === name).toggle;
    const childLink = name => groups.find(group => group.name === name).childLink;
    return { root, menu, groups, plain, outsideToggle, outsidePanel, store, open, click, toggle, childLink, ready };
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

// Re-clicking a header keeps exactly that group: the script folds it and
// Bootstrap, seeing a closed toggle, opens it again.
console_page.click(console_page.toggle('Content'));
check(console_page.open(), ['Content'], 're-clicking a header leaves its own group open');
check(JSON.parse(console_page.store.get(KEY)), ['Content'], 'the group stays remembered');

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

console.log(`${checks} admin menu single-open/restore checks passed.`);
