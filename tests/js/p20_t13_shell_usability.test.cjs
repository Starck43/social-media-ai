/* Shell usability contracts: keyboard model picker and shared dialog
   presentation. No application imports, database, browser packages or network.
   Real browser keyboard/focus behavior is covered by the offline CDP check. */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = name => fs.readFileSync(path.join(__dirname, '../../app/static/js', name), 'utf8');

// Active document; `focus()` resolves the owner document from the node itself,
// exactly like the browser (a detached synthetic node owns no document).
let activeDocument = null;

// Minimal DOM double: enough for shell.js wiring and context_panel.js boot.
// Focus and key event bubbling follow the browser closely enough that trigger
// versus option focus moves are actually observable, not assumed away.
class Node_ {
    constructor(tag = 'div') {
        this.tagName = tag.toUpperCase(); this.dataset = {}; this.attributes = {}; this.children = [];
        this.listeners = {}; this.parent = null; this.hidden = false; this.disabled = false;
        this.isConnected = true; this.textContent = ''; this.value = '';
        // Inline style is a real box the product writes into; scrollHeight is the
        // unscrolled content height the popover placement measures.
        this.style = { overflow: '', top: '', bottom: '', maxHeight: '' };
        this.scrollHeight = 0;
    }
    get classList() {
        const self = this;
        return {
            names: new Set((self.attributes.class || '').split(' ').filter(Boolean)),
            add(...names) { names.forEach(name => this.names.add(name)); },
            remove(...names) { names.forEach(name => this.names.delete(name)); },
            contains(name) { return this.names.has(name); },
        };
    }
    setAttribute(name, value) { this.attributes[name] = String(value); if (name === 'hidden') this.hidden = true; if (name === 'disabled') this.disabled = true; if (name === 'tabindex') this._tabindex = Number(value); if (name === 'type') this.type = String(value); }
    getAttribute(name) { return Object.hasOwn(this.attributes, name) ? this.attributes[name] : (name === 'type' ? this.type || null : null); }
    removeAttribute(name) { delete this.attributes[name]; if (name === 'hidden') this.hidden = false; if (name === 'open') this.open = false; }
    hasAttribute(name) { return name === 'hidden' ? this.hidden : Object.hasOwn(this.attributes, name); }
    get tabIndex() { return this._tabindex ?? (this.hasAttribute('tabindex') ? Number(this.getAttribute('tabindex')) : (this.disabled || this.type === 'hidden' ? -1 : 0)); }
    set innerHTML(value) { this.children = []; this._html = value; }
    get innerHTML() { return this._html || ''; }
    append(...nodes) { nodes.forEach(node => { node.parent = this; this.children.push(node); }); }
    addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
    removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(fn2 => fn2 !== fn); }
    // Dispatches on this target and bubbles to connected ancestors until a
    // listener stops propagation, exactly like a non-composed DOM event.
    fire(name, extra = {}) {
        const event = {
            type: name, target: this, currentTarget: this, defaultPrevented: false, propagationStopped: false,
            preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; },
            ...extra,
        };
        for (let node = this; node; node = node.parent) {
            event.currentTarget = node;
            for (const fn of node.listeners[name] || []) fn(event);
            if (event.propagationStopped) break;
        }
        return event;
    }
    // dispatchEvent forwards the caller's actual Event/CustomEvent: type,
    // detail, bubbles and target default exactly as the browser does, and
    // preventDefault state stays readable on the same object.
    dispatchEvent(event) {
        if (!event || typeof event.type !== 'string') throw new TypeError('dispatchEvent expects an event');
        const extra = { target: this, currentTarget: this, defaultPrevented: false, propagationStopped: false };
        if (event instanceof Object) {
            for (const name of ['preventDefault', 'stopPropagation', 'stopImmediatePropagation']) {
                if (typeof event[name] === 'function') extra[name] = event[name].bind(event);
            }
        }
        this.fire(event.type, { ...extra, ...(event.detail !== undefined ? { detail: event.detail } : {}) });
        return !event.defaultPrevented;
    }
    contains(other) { for (let node = other; node; node = node.parent) if (node === this) return true; return false; }
    // Compound selector support for the selectors the shell actually queries:
    // `.class`, `tag`, `[attr]`, `tag:not(...)`, `input[type="x"]`,
    // `[contenteditable]:not([contenteditable="false"])` and combinations.
    closest(selector) {
        for (let node = this; node; node = node.parent) if (matches(node, selector)) return node;
        return null;
    }
    querySelectorAll(selector) {
        const found = [];
        const visit = el => el.children.forEach(child => { if (matches(child, selector)) found.push(child); visit(child); });
        visit(this);
        return found;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    // Rendered only when the element and every ancestor are visible: a closed
    // <dialog> and a closed <details> hide their whole subtree, as in the browser.
    getClientRects() { return this.isConnected && rendered(this) ? [{ width: 10, height: 10 }] : []; }
    focus() { if (this.isConnected) (this.ownerDocument || activeDocument).activeElement = this; }
    getBoundingClientRect() { return { left: 0, right: 100, top: 0, bottom: 100 }; }
    matches(selector) { return matches(this, selector); }
}

const rendered = node => {
    for (let el = node; el; el = el.parent) {
        if (el.hidden || el.tagName === 'INERT') return false;
        if (el.tagName === 'DIALOG' && !el.open) return false;
        if (el.tagName === 'DETAILS' && !el.open && el !== node) return false;
    }
    return true;
};

// One simple-selector matcher: `tag`, `.cls`, `[attr]`, `[attr="value"]` and
// the `:not(compound)` / `:first-child` pseudo classes the shell selects with.
const matches = (el, selector) => String(selector).split(',').map(part => part.trim()).filter(Boolean).some(part => {
    if (!(el instanceof Node_)) return false;
    const not = [];
    const bare = part.replace(/:not\(([^)]*)\)/g, (_, inner) => { not.push(inner); return ''; });
    const firstChild = /:first-child/.test(bare);
    if (firstChild && el.parent?.children[0] !== el) return false;
    const checks = bare.replace(/:first-child/g, '')
        .match(/(^[a-zA-Z][\w-]*)|(\.[\w-]+)|(\[[^\]]+\])/g) || [];
    const ok = checks.every(token => {
        if (token.startsWith('.')) return (el.attributes.class || '').split(/\s+/).includes(token.slice(1));
        if (token.startsWith('[')) {
            const [, name, , value] = token.slice(1, -1).match(/^([\w-]+)(?:=("?)([^"\]]*)\2)?$/) || [];
            if (!name) return false;
            return value === undefined ? el.hasAttribute(name) : el.getAttribute(name) === value;
        }
        return el.tagName === token.toUpperCase();
    });
    return ok && not.every(inner => !matches(el, inner));
});

function build({ issuePost = false } = {}) {
    const body = new Node_('body');
    const pageContent = new Node_('main'); pageContent.attributes.tabindex = '-1';
    const html = new Node_('html');
    html.style = { setProperty() {} };
    let issues = [];
    const document = {
        body, documentElement: html, activeElement: body, readyState: 'complete',
        listeners: {},
        createElement: tag => new Node_(tag),
        getElementById: id => (id === 'page-content' ? pageContent : null),
        querySelectorAll: () => [],
        querySelector: () => null,
        addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); },
        removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(fn2 => fn2 !== fn); },
        // Real events carry callable preventDefault/stopPropagation; the page
        // click that opens a dock must be cancellable.
        fire(name, extra = {}) {
            const event = {
                type: name, defaultPrevented: false, propagationStopped: false,
                preventDefault() { this.defaultPrevented = true; }, stopPropagation() { this.propagationStopped = true; },
                ...extra,
            };
            (this.listeners[name] || []).forEach(fn => fn(event));
            return event;
        },
    };
    // Ancestors connect to the document: body -> html -> document element chain.
    html.append(body);
    html.parent = document;
    body.append(pageContent);
    body.ownerDocument = html.ownerDocument = document;
    activeDocument = document;
    const storage = new Map();
    const window = {
        addEventListener() {}, location: { origin: 'http://shell.test' }, setInterval: () => 0, clearInterval() {},
        // App announcements dispatched on window must reach registered listeners.
        listeners: {},
        dispatchEvent(event) { (this.listeners[event?.type] || []).forEach(fn => fn(event)); return true; },
    };
    const context = {
        document, window, localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
        URL, URLSearchParams,
        // Events keep their defaults: an `input` Event bubbles, preventDefault is
        // callable, and `target` is the dispatch target set by Node_.dispatchEvent.
        Event: class Event { constructor(type, options = {}) { this.type = type; this.bubbles = !!options.bubbles; this.cancelable = !!options.cancelable; this.defaultPrevented = false; } preventDefault() { this.defaultPrevented = true; } stopPropagation() {} },
        CustomEvent: class CustomEvent extends Event { constructor(type, options = {}) { super(type, options); this.detail = options.detail; } },
        matchMedia: () => ({ matches: false }), innerHeight: 800, setTimeout: (fn) => { fn(); return 0; }, clearTimeout() {},
        // Every <dialog> instance is recognized as a dialog, as in the browser.
        HTMLDialogElement: class HTMLDialogElement {
            static [Symbol.hasInstance](value) { return value instanceof Node_ && value.tagName === 'DIALOG'; }
        },
        console,
        fetch: issuePost ? async url => { issues.push(String(url)); return { ok: false, status: 500, redirected: false, text: async () => '' }; } : async () => { throw Error('offline'); },
    };
    vm.createContext(context);
    return { document, window, context, pageContent, body, html, issues: () => issues };
}

// Native reality: HTMLDialogElement.showModal() fires no 'show' event, only a
// native 'close' event on close(). The double reproduces exactly that.
class FakeDialog extends Node_ {
    constructor(id, klass = 'shell-dialog') {
        super('dialog');
        this.id = id; this.attributes.class = klass; this.open = false;
        this.showModal = () => {
            assert.equal(this.open, false, 'showModal on closed dialog');
            this.open = true;
        };
        this.close = () => { if (!this.open) return; this.open = false; this.fire('close'); };
    }
}

function selectedOption(popover) { return popover.querySelectorAll('[data-popover-option]').find(o => o.getAttribute('aria-selected') === 'true'); }
const key = keyName => ({ key: keyName });

function buildShell() {
    const env = build();
    const { document } = env;
    // Root and panel children resolved through explicit selectors.
    const popover = new Node_('div');
    popover.dataset.shellPopover = ''; popover.setAttribute('data-shell-popover', ''); popover.setAttribute('data-popover-select', '');
    const hidden = new Node_('input'); hidden.setAttribute('data-popover-value', ''); hidden.type = 'hidden'; hidden.value = 'm2';
    const label = new Node_('span'); label.setAttribute('data-popover-label', ''); label.textContent = 'Модель 2';
    const trigger = new Node_('button'); trigger.setAttribute('data-popover-trigger', ''); trigger.setAttribute('aria-expanded', 'false');
    trigger.append(label);
    const panel = new Node_('div'); panel.attributes.class = 'shell-popover-panel'; panel.setAttribute('role', 'listbox'); panel.hidden = true;
    const options = [['', 'Автовыбор', false], ['m1', 'Модель 1', false], ['m2', 'Модель 2', true], ['m3', 'Модель 3', false]]
        .map(([value, text, selected]) => {
            const option = new Node_('button'); option.setAttribute('type', 'button'); option.setAttribute('role', 'option');
            option.dataset.popoverOption = value; option.setAttribute('data-popover-option', value);
            option.setAttribute('aria-selected', String(selected));
            const span = new Node_('span'); span.textContent = text; option.append(span);
            panel.append(option); return option;
        });
    popover.append(hidden, trigger, panel);
    // Real popovers are children of a rendered root, so key events bubble and
    // the document click listener can see them.
    const root = new Node_('div'); root.append(popover); env.body.append(root);
    document.querySelectorAll = selector => {
        if (selector === '[data-shell-popover]') return [popover];
        if (selector === '[data-dialog-open]') return [];
        if (selector.startsWith('dialog.')) return [];
        return [];
    };
    vm.runInContext(read('shell.js'), env.context);
    document.fire('DOMContentLoaded', {});
    return { env, document, popover, panel, trigger, hidden, label, options };
}

test('popover select: ArrowDown from trigger focuses the current selection once (event bubbles)', () => {
    const s = buildShell();
    assert.equal(s.panel.hidden, true);
    const event = s.trigger.fire('keydown', key('ArrowDown'));
    assert.equal(event.defaultPrevented, true);
    assert.equal(event.propagationStopped, true, 'the trigger keydown never reaches the option handler');
    assert.equal(s.panel.hidden, false);
    assert.equal(s.trigger.getAttribute('aria-expanded'), 'true');
    assert.equal(s.document.activeElement, s.options[2], 'one press moves once, not past the selection');
    // Placement stays inside the viewport: the panel writes a real inline box.
    assert.equal(s.panel.style.top, 'calc(100% + 4px)');
    assert.equal(s.panel.style.bottom, 'auto');
    assert.match(s.panel.style.maxHeight, /^\d+px$/);
});

test('popover select: hidden or inert options are skipped by entry and movement', () => {
    const s = buildShell();
    // The selected option lives in a hidden branch (e.g. a collapsed section).
    const branch = new Node_('div'); branch.hidden = true;
    s.panel.children = s.panel.children.filter(option => option !== s.options[2]);
    s.options[2].parent = branch; branch.append(s.options[2]);
    s.panel.append(branch);
    s.trigger.fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[0], 'entry skips the hidden selection, and does not move twice');
    s.options[1].focus(); s.options[1].fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[3], 'movement skips the hidden option');
    s.options[3].focus(); s.options[3].fire('keydown', key('ArrowUp'));
    assert.equal(s.document.activeElement, s.options[1]);
    const inert = new Node_('section'); inert.setAttribute('inert', '');
    const inertOption = new Node_('button'); inertOption.setAttribute('data-popover-option', 'm4');
    inertOption.setAttribute('role', 'option'); inert.append(inertOption); s.panel.append(inert);
    s.options[3].focus(); s.options[3].fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[0], 'inert options are not in the list');
});

test('popover select: ArrowDown opens and focuses the first enabled option without a selection', () => {
    const s = buildShell();
    s.options.forEach(option => option.setAttribute('aria-selected', 'false'));
    s.options[1].disabled = true;
    s.trigger.fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[0]);
    s.options[1].disabled = false;
    s.panel.hidden = true;
    s.trigger.fire('keydown', key('ArrowUp'));
    assert.equal(s.document.activeElement, s.options[3]);
});

test('popover select: a disabled selection falls back to the first usable option', () => {
    const s = buildShell();
    s.options[2].disabled = true;
    s.trigger.fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[0]);
});

test('popover select: ArrowUp from trigger resumes on the selected option', () => {
    const s = buildShell();
    const event = s.trigger.fire('keydown', key('ArrowUp'));
    assert.equal(event.defaultPrevented, true);
    assert.equal(event.propagationStopped, true, 'one move per key press');
    assert.equal(s.panel.hidden, false);
    assert.equal(s.document.activeElement, s.options[2]);
});

test('popover select: options navigate with arrows, wrap, Home and End', () => {
    const s = buildShell();
    s.trigger.fire('keydown', key('ArrowDown'));
    s.options[2].fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[3]);
    s.options[3].fire('keydown', key('ArrowDown'));
    assert.equal(s.document.activeElement, s.options[0], 'ArrowDown wraps to the first option');
    s.options[0].fire('keydown', key('ArrowUp'));
    assert.equal(s.document.activeElement, s.options[3], 'ArrowUp wraps to the last option');
    s.options[3].fire('keydown', key('End'));
    assert.equal(s.document.activeElement, s.options[3]);
    s.options[3].fire('keydown', key('Home'));
    assert.equal(s.document.activeElement, s.options[0]);
    s.options[0].fire('keydown', key('End'));
    assert.equal(s.document.activeElement, s.options[3]);
    const tab = s.options[3].fire('keydown', key('Tab'));
    assert.equal(tab.defaultPrevented, false, 'Tab keeps native meaning');
    const letter = s.options[3].fire('keydown', key('a'));
    assert.equal(letter.defaultPrevented, false);
});

test('popover select: typing inside an editable field is not hijacked', () => {
    const s = buildShell();
    s.trigger.fire('keydown', key('ArrowDown'));
    const input = new Node_('input'); input.setAttribute('type', 'text');
    s.panel.append(input);
    const event = input.fire('keydown', key('ArrowDown'));
    assert.equal(event.defaultPrevented, false);
    assert.equal(s.document.activeElement, s.options[2]);
    // Rich editable fields (and their inner elements) keep the key too.
    const editable = new Node_('div'); editable.setAttribute('contenteditable', 'true');
    const inner = new Node_('span'); editable.append(inner); s.panel.append(editable);
    assert.equal(inner.fire('keydown', key('ArrowUp')).defaultPrevented, false);
    assert.equal(s.document.activeElement, s.options[2]);
    // A hidden input is not editable content: navigation still applies.
    assert.equal(s.hidden.fire('keydown', key('Home')).defaultPrevented, true);
});

test('popover select: Escape closes and restores trigger focus without selecting', () => {
    const s = buildShell();
    s.trigger.fire('keydown', key('ArrowDown'));
    const event = s.options[2].fire('keydown', key('Escape'));
    assert.equal(event.defaultPrevented, true);
    assert.equal(event.propagationStopped, true, 'Escape does not reach the enclosing dialog');
    assert.equal(s.panel.hidden, true);
    assert.equal(s.trigger.getAttribute('aria-expanded'), 'false');
    assert.equal(s.document.activeElement, s.trigger);
    assert.equal(s.hidden.value, 'm2', 'Escape keeps the previous value');
});

test('popover select: choosing an option writes value, input event and label', () => {
    const s = buildShell();
    let dispatched = 0, observed = null;
    s.hidden.dispatchEvent = event => { dispatched++; observed = event; return true; };
    s.options[3].fire('click');
    assert.equal(s.hidden.value, 'm3');
    assert.equal(dispatched, 1);
    assert.equal(observed.type, 'input'); assert.equal(observed.bubbles, true);
    assert.equal(s.label.textContent, 'Модель 3');
    assert.equal(selectedOption(s.popover), s.options[3]);
    assert.equal(s.options[2].getAttribute('aria-selected'), 'false');
    assert.equal(s.panel.hidden, true);
    assert.equal(s.document.activeElement, s.trigger);
    // Empty placeholder value remains a valid selection.
    s.options[0].fire('click');
    assert.equal(s.hidden.value, '');
    assert.equal(selectedOption(s.popover), s.options[0]);
});

test('popover select: hover-opened panel does not double-toggle on click, no duplicate openings', () => {
    const s = buildShell();
    s.popover.setAttribute('data-hover-popover', '');
    s.trigger.fire('click');
    assert.equal(s.panel.hidden, false);
    s.trigger.fire('click');
    assert.equal(s.panel.hidden, true, 'second click closes, not reopens');
    s.trigger.fire('keydown', key('ArrowDown'));
    s.trigger.fire('keydown', key('ArrowDown'));
    assert.equal(s.panel.hidden, false, 'keyboard open is idempotent while open');
    assert.equal(s.document.activeElement, s.options[2]);
});

function buildDialogs() {
    const env = build();
    const { document } = env;
    const drawer = new FakeDialog('navigation-drawer', 'shell-drawer');
    const invite = new FakeDialog('invite-dialog');
    // Focus trap content: hidden field must not count, negative tabindex must
    // not count, and a hidden or inert branch must not define a boundary.
    const hiddenField = new Node_('input'); hiddenField.type = 'hidden';
    const roving = new Node_('button'); roving.setAttribute('tabindex', '-1');
    const first = new Node_('button'); first.id = 'first';
    const last = new Node_('button'); last.id = 'last';
    const huge = new Node_('textarea');
    const concealed = new Node_('div'); concealed.hidden = true;
    const hiddenTail = new Node_('button'); hiddenTail.id = 'hidden-tail'; concealed.append(hiddenTail);
    const inert = new Node_('section'); inert.setAttribute('inert', '');
    const inertTail = new Node_('button'); inertTail.id = 'inert-tail'; inert.append(inertTail);
    // Nested launcher lives inside the dialog, never on the inert page behind it.
    const drawerTrigger = new Node_('button'); drawerTrigger.dataset.dialogOpen = 'navigation-drawer'; drawerTrigger.setAttribute('data-dialog-open', 'navigation-drawer');
    invite.append(hiddenField, roving, first, huge, last, concealed, inert, drawerTrigger);
    env.body.append(invite, drawer);
    // A notification-style overlay: a native dialog that is not a shell dialog.
    const notification = new FakeDialog('notification-overlay', 'shell-notification-modal');
    const openTrigger = new Node_('button'); openTrigger.dataset.dialogOpen = 'invite-dialog'; openTrigger.setAttribute('data-dialog-open', 'invite-dialog');
    const closeButton = new Node_('button'); closeButton.setAttribute('data-dialog-close', '');
    document.querySelectorAll = selector => {
        if (selector === '[data-dialog-open]') return [openTrigger, drawerTrigger];
        if (selector.startsWith('dialog.')) return [drawer, invite];
        return [];
    };
    document.getElementById = id => ({ 'invite-dialog': invite, 'navigation-drawer': drawer, 'notification-overlay': notification, 'page-content': env.pageContent })[id] || null;
    vm.runInContext(read('shell.js'), env.context);
    document.fire('DOMContentLoaded', {});
    const counts = { appOpen: 0, nativeShow: 0 };
    document.addEventListener('app:shell-dialog-open', () => counts.appOpen++);
    document.addEventListener('show', () => counts.nativeShow++);
    const open = target => { target.fire('click'); document.fire('click', { target }); };
    return { env, document, drawer, invite, notification, hiddenField, roving, first, last, huge, hiddenTail, inertTail, openTrigger, drawerTrigger, closeButton, open, counts };
}

test('dialog: markup triggers mirror aria attributes; a bare showModal is not managed', () => {
    const d = buildDialogs();
    assert.equal(d.openTrigger.getAttribute('aria-controls'), 'invite-dialog');
    assert.equal(d.openTrigger.getAttribute('aria-expanded'), 'false');
    // Native showModal fires no 'show' event, so only the app announcement
    // (after a successful showModal) claims the shared scroll depth.
    d.invite.showModal();
    d.invite.fire('show');
    assert.equal(d.env.document.body.style.overflow, '', 'a native-only opening claims nothing');
    d.document.fire('app:shell-dialog-open', { detail: { dialog: d.invite } });
    assert.equal(d.env.document.body.style.overflow, 'hidden', 'the app announcement claims the lock');
    assert.equal(d.openTrigger.getAttribute('aria-expanded'), 'true');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, '', 'native close releases it');
});

test('dialog: announcements only claim registered shell dialogs', () => {
    const d = buildDialogs();
    d.notification.showModal();
    // The notification overlay is a plain HTMLDialogElement, not a shell dialog.
    d.notification.fire('app:shell-dialog-open', { detail: { dialog: d.notification } });
    assert.equal(d.env.document.body.style.overflow, '', 'a notification overlay never claims the scroll lock');
    // An unregistered element that merely claims to be a dialog is ignored too.
    const impostor = new Node_('div');
    d.document.fire('app:shell-dialog-open', { detail: { dialog: impostor } });
    assert.equal(d.env.document.body.style.overflow, '');
    // A registered dialog left closed cannot claim either.
    d.document.fire('app:shell-dialog-open', { detail: { dialog: d.invite } });
    assert.equal(d.env.document.body.style.overflow, '', 'closed shell dialog claims nothing');
    d.invite.showModal();
    d.invite.fire('app:shell-dialog-open', { detail: { dialog: d.invite } });
    assert.equal(d.env.document.body.style.overflow, 'hidden', 'an open registered shell dialog claims it');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, '');
});

test('dialog: Tab trap uses the real first and last controls', () => {
    const d = buildDialogs();
    d.open(d.openTrigger);
    // Hidden input, negative tabindex and the concealed/inert tails are not
    // boundaries: the trap wraps around `first` and the last real control.
    assert.notEqual(d.hiddenTail, d.last); assert.notEqual(d.inertTail, d.last);
    d.drawerTrigger.focus();
    d.invite.fire('keydown', key('Tab'));
    assert.equal(d.document.activeElement, d.first, 'Tab from the last real control wraps to the first');
    d.first.focus();
    d.invite.fire('keydown', { key: 'Tab', shiftKey: true });
    assert.equal(d.document.activeElement, d.drawerTrigger, 'Shift+Tab from the first wraps to the last real control');
    // Interior controls keep native movement.
    d.last.focus();
    const interior = d.last.fire('keydown', key('Tab'));
    assert.equal(interior.defaultPrevented, false, 'a control before the last keeps native Tab');
});

test('dialog: opened by a data-dialog-open click keeps scroll depth and restores its launcher', () => {
    const d = buildDialogs();
    d.env.document.body.style.overflow = 'auto';
    d.open(d.openTrigger);
    assert.equal(d.invite.open, true);
    assert.equal(d.env.document.body.style.overflow, 'hidden');
    assert.equal(d.openTrigger.getAttribute('aria-expanded'), 'true');
    assert.equal(d.invite.shellLauncher, d.openTrigger);
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, 'auto', 'scroll restored to the prior value');
    assert.equal(d.openTrigger.getAttribute('aria-expanded'), 'false');
    assert.equal(d.document.activeElement, d.openTrigger);
});

test('dialog: an empty original overflow is restored empty, not replaced', () => {
    const d = buildDialogs();
    assert.equal(d.env.document.body.style.overflow, '');
    d.open(d.openTrigger);
    assert.equal(d.env.document.body.style.overflow, 'hidden');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, '', 'the exact saved value comes back');
    // A second opening saves the current value again instead of reusing a stale one.
    d.env.document.body.style.overflow = 'scroll';
    d.open(d.openTrigger);
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, 'scroll');
    d.env.document.body.style.overflow = '';
    d.open(d.openTrigger);
    assert.equal(d.env.document.body.style.overflow, 'hidden', 'an empty value is locked like any other');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, '', 'and restored as empty');
});

test('dialog: native cancel uses the explicit close and cleanup path', () => {
    const d = buildDialogs();
    d.env.document.body.style.overflow = 'auto';
    d.open(d.openTrigger);
    const event = d.invite.fire('cancel');
    assert.equal(event.defaultPrevented, true);
    assert.equal(d.invite.open, false);
    assert.equal(d.env.document.body.style.overflow, 'auto');
    assert.equal(d.document.activeElement, d.openTrigger);
});

test('dialog: a queued close cannot release a dialog that has reopened', () => {
    const d = buildDialogs();
    d.open(d.openTrigger);
    d.invite.fire('close');
    assert.equal(d.invite.open, true);
    assert.equal(d.env.document.body.style.overflow, 'hidden');
    assert.equal(d.openTrigger.getAttribute('aria-expanded'), 'true');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, '');
});

test('dialog: nested dialogs keep the body locked until the outermost closes', () => {
    const d = buildDialogs();
    d.env.document.body.style.overflow = 'auto';
    d.open(d.openTrigger);
    d.open(d.drawerTrigger);
    assert.equal(d.invite.open && d.drawer.open, true);
    assert.equal(d.env.document.body.style.overflow, 'hidden');
    d.drawer.close();
    assert.equal(d.env.document.body.style.overflow, 'hidden', 'inner close must not unlock scroll');
    assert.equal(d.document.activeElement, d.drawerTrigger, 'the nested launcher inside the invite dialog regains focus');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, 'auto', 'the outermost close restores the saved value');
    assert.equal(d.document.activeElement, d.openTrigger);
});

test('dialog: repeated announcement and duplicate close events stay balanced', () => {
    const d = buildDialogs();
    d.open(d.openTrigger);
    d.document.fire('app:shell-dialog-open', { detail: { dialog: d.invite } });
    assert.equal(d.env.document.body.style.overflow, 'hidden', 'a repeated announcement claims no second depth');
    d.invite.close();
    d.invite.fire('close'); d.invite.fire('close');
    assert.equal(d.env.document.body.style.overflow, '', 'repeated close events stay balanced');
    d.open(d.drawerTrigger);
    assert.equal(d.env.document.body.style.overflow, 'hidden');
    d.drawer.close();
    assert.equal(d.env.document.body.style.overflow, '');
});

test('dialog: double announcement of one opening counts once', () => {
    const d = buildDialogs();
    d.open(d.openTrigger);
    // The launcher click itself announces, and the caller announces again; a
    // dialog-local dispatch bubbles to the same document listener.
    d.document.fire('app:shell-dialog-open', { detail: { dialog: d.invite } });
    d.invite.fire('app:shell-dialog-open', { detail: { dialog: d.invite }, target: d.invite });
    assert.equal(d.counts.appOpen, 3, 'one launcher activation plus two announcements');
    d.invite.close();
    assert.equal(d.env.document.body.style.overflow, '', 'one close releases the single claimed opening');
});

test('dialog: announcement for a closed dialog is ignored', () => {
    const d = buildDialogs();
    d.document.fire('app:shell-dialog-open', { detail: { dialog: d.invite } });
    assert.equal(d.env.document.body.style.overflow, '', 'closed dialog cannot claim the lock');
});

test('dialog: missing or hidden launcher falls back to page content', () => {
    const d = buildDialogs();
    d.open(d.openTrigger);
    d.openTrigger.isConnected = false;
    d.invite.close();
    assert.equal(d.document.activeElement, d.env.pageContent);
    // The nested launcher is connected but concealed: the dialog itself takes
    // focus while it is still open, and page content takes it once it closes.
    d.open(d.openTrigger);
    const concealedLauncher = new Node_('button'); concealedLauncher.hidden = true;
    d.invite.shellLauncher = concealedLauncher;
    d.invite.close();
    assert.equal(d.document.activeElement, d.env.pageContent);
    // An open dialog that still cannot use its launcher focuses itself.
    d.open(d.openTrigger);
    d.invite.shellLauncher = concealedLauncher;
    d.invite.focus();
    assert.equal(d.document.activeElement, d.invite);
});

function buildDock({ boundary = 'cloud' } = {}) {
    const env = build();
    const { document } = env;
    const dock = new FakeDialog('chat-dock-drawer', 'shell-right-panel w-full');
    dock.dataset.chatDock = '';
    const close = new Node_('button'); close.setAttribute('data-chat-dock-close', '');
    const input = new Node_('input'); input.setAttribute('data-dock-input', '');
    const send = new Node_('button'); send.setAttribute('data-dock-send', '');
    const typeInput = new Node_('input'); typeInput.setAttribute('data-dock-input-type', '');
    const idInput = new Node_('input'); idInput.setAttribute('data-dock-input-id', '');
    const boundaryInput = new Node_('input'); boundaryInput.setAttribute('data-dock-input-boundary', '');
    const refusal = new Node_('p'); refusal.setAttribute('data-dock-local-only-refusal', ''); refusal.hidden = true;
    const chip = new Node_('div'); chip.setAttribute('data-dock-context-chip', '');
    const toggle = new Node_('button'); toggle.id = 'chat-dock-toggle';
    dock.append(close, input, send, typeInput, idInput, boundaryInput, refusal, chip);
    env.body.append(dock);
    const source = new Node_('div');
    source.dataset.objectContext = '';
    source.setAttribute('data-object-context', '');
    source.setAttribute('data-context-type', 'object'); source.setAttribute('data-context-id', '42');
    source.setAttribute('data-boundary', boundary); source.setAttribute('data-context-title', 'Объект 42');
    document.querySelector = selector => (selector.startsWith('[data-object-context]') ? source : null);
    document.getElementById = id => ({ 'chat-dock-drawer': dock, 'chat-dock-toggle': toggle })[id] || null;
    document.querySelectorAll = selector => (selector.startsWith('dialog.') ? [dock] : []);
    vm.runInContext(read('shell.js'), env.context);
    document.fire('DOMContentLoaded', {});
    vm.runInContext(read('context_panel.js'), env.context);
    return { env, document, dock, toggle, input, send, refusal, boundaryInput, close };
}

test('chat dock: shared close handler owns scroll depth and launcher focus', () => {
    const d = buildDock();
    // context_panel boots after the shell: the dock is still the same shell dialog.
    assert.equal(d.env.document.body.style.overflow, '');
    d.env.document.body.style.overflow = 'auto';
    let showEvents = 0;
    d.dock.addEventListener('app:shell-dialog-open', () => showEvents++);
    d.toggle.fire('click');
    assert.equal(d.dock.open, true);
    assert.equal(showEvents, 1, 'one open event per launcher activation');
    assert.equal(d.env.document.body.style.overflow, 'hidden');
    assert.equal(d.toggle.getAttribute('aria-expanded'), 'true');
    assert.equal(d.document.activeElement, d.input);
    d.dock.fire('keydown', key('Escape'));
    assert.equal(d.dock.open, false);
    assert.equal(d.env.document.body.style.overflow, 'auto', 'dock close decrements the shared depth');
    assert.equal(d.toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(d.document.activeElement, d.toggle);
});

test('chat dock: page trigger restores that launcher, not the fixed toggle', () => {
    const d = buildDock();
    const trigger = new Node_('button'); trigger.setAttribute('data-ask-agent-trigger', '');
    d.document.fire('click', { target: trigger });
    assert.equal(d.dock.open, true);
    d.dock.fire('keydown', key('Escape'));
    assert.equal(d.dock.open, false, 'Escape dismisses the dock once');
    assert.equal(d.document.activeElement, trigger);
    d.document.fire('click', { target: trigger });
    assert.equal(d.dock.open, true, 'a second page click reopens once, not twice');
    d.dock.fire('keydown', key('Escape'));
    assert.equal(d.dock.open, false);
    assert.equal(d.document.activeElement, trigger);
});

test('chat dock: the empty initial overflow is restored exactly after closing', () => {
    const d = buildDock();
    assert.equal(d.env.document.body.style.overflow, '');
    d.toggle.fire('click');
    assert.equal(d.env.document.body.style.overflow, 'hidden', 'an originally empty overflow is still locked');
    d.dock.fire('keydown', key('Escape'));
    assert.equal(d.env.document.body.style.overflow, '', 'and restored to the same empty value');
});

test('chat dock: local_only context keeps the refusal boundary and disables sending', () => {
    const d = buildDock({ boundary: 'local_only' });
    d.toggle.fire('click');
    assert.equal(d.boundaryInput.value, 'local_only');
    assert.equal(d.send.disabled, true);
    assert.equal(d.refusal.hidden, false);
    assert.equal(d.document.activeElement, d.close, 'blocked dock focuses its close control');
});
