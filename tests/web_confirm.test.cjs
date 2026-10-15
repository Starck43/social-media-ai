// No application imports, database, browser packages or live requests required.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const code = fs.readFileSync(path.join(__dirname, '../app/static/js/ui-confirm.js'), 'utf8');

function setup({ missing = false, broken = false } = {}) {
    class Element {
        constructor(dataset = {}) { this.dataset = dataset; this.listeners = {}; this.isConnected = true; }
        hasAttribute(name) { return name === 'data-confirm' && Object.hasOwn(this.dataset, 'confirm'); }
        addEventListener(name, fn) { this.listeners[name] = fn; }
        fire(name, extra = {}) {
            const event = { target: this, preventDefault() { this.prevented = true; }, stopPropagation() {}, ...extra };
            this.listeners[name]?.(event);
            return event;
        }
        focus() { document.activeElement = this; }
    }
    class Form extends Element {
        requestSubmit(submitter) {
            this.replayed = submitter;
            if (this.invalid && !submitter?.formNoValidate) return;
            const event = submit(this, submitter);
            if (!event.prevented) { this.requests = (this.requests || 0) + 1; this.busy = true; }
        }
    }
    const cancel = new Element(), accept = new Element(), title = new Element(), message = new Element();
    const dialog = new Element();
    dialog.querySelector = selector => selector.includes('cancel') ? cancel : accept;
    dialog.showModal = () => { if (broken) throw Error('unavailable'); dialog.open = true; };
    dialog.close = () => { dialog.open = false; dialog.fire('close'); };
    dialog.getBoundingClientRect = () => ({ left: 10, right: 100, top: 10, bottom: 100 });
    const document = {
        body: { style: { overflow: 'auto' } }, activeElement: cancel,
        getElementById: id => ({ 'ui-confirm': missing ? null : dialog, 'ui-confirm-title': title, 'ui-confirm-message': message })[id],
        addEventListener(name, fn, capture) { assert.equal(capture, true); this[name] = fn; },
    };
    function submit(form, submitter) {
        const event = { target: form, submitter, preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
        document.submit(event);
        return event;
    }
    vm.runInNewContext(code, { document, HTMLFormElement: Form });
    const form = new Form({ confirm: 'Delete #42?', confirmTitle: 'Delete?', confirmLabel: 'Delete forever' });
    const button = new Element(); button.form = form;
    return { form, button, dialog, document, title, message, accept, cancel, submit, Form, Element };
}

test('opening stops submit and busy handlers; acceptance replays original submitter once', () => {
    const h = setup(); const event = h.submit(h.form, h.button);
    assert.equal(event.prevented, true); assert.equal(event.stopped, true);
    assert.equal(h.form.busy, undefined); assert.equal(h.dialog.open, true);
    assert.equal(h.document.activeElement, h.cancel); assert.equal(h.document.body.style.overflow, 'hidden');
    assert.equal(h.title.textContent, 'Delete?'); assert.equal(h.message.textContent, 'Delete #42?');
    assert.equal(h.accept.textContent, 'Delete forever');
    h.accept.fire('click'); h.accept.fire('click');
    assert.equal(h.form.requests, 1); assert.equal(h.form.replayed, h.button);
    assert.equal(h.document.activeElement, h.button); assert.equal(h.document.body.style.overflow, 'auto');
});

for (const route of ['cancel', 'escape', 'backdrop']) test(`${route} dismisses without submission`, () => {
    const h = setup(); h.submit(h.form, h.button);
    if (route === 'cancel') h.cancel.fire('click');
    if (route === 'escape') assert.equal(h.dialog.fire('cancel').prevented, true);
    if (route === 'backdrop') h.dialog.fire('click', { clientX: 0, clientY: 0 });
    assert.equal(h.dialog.open, false); assert.equal(h.form.requests, undefined);
    assert.equal(h.document.activeElement, h.button); assert.equal(h.document.body.style.overflow, 'auto');
});

test('panel whitespace is not a backdrop click', () => {
    const h = setup(); h.submit(h.form, h.button); h.dialog.fire('click', { clientX: 50, clientY: 50 });
    assert.equal(h.dialog.open, true);
});

test('submitter confirmation overrides form; unrelated save remains untouched', () => {
    const h = setup(); h.form.dataset = {}; h.button.dataset = { confirm: '<img onerror="bad()">', confirmLabel: 'Delete' };
    assert.equal(h.submit(h.form, new h.Element()).prevented, undefined);
    assert.equal(h.submit(h.form, h.button).prevented, true);
    assert.equal(h.message.textContent, '<img onerror="bad()">');
    h.button.formNoValidate = true; h.form.invalid = true;
    h.accept.fire('click'); assert.equal(h.form.requests, 1); assert.equal(h.form.replayed, h.button);
});

test('pending action cannot be replaced by another row', () => {
    const h = setup(); h.submit(h.form, h.button);
    const other = new h.Form({ confirm: 'Other row' }); h.submit(other);
    assert.equal(h.message.textContent, 'Delete #42?');
    h.accept.fire('click'); assert.equal(h.form.requests, 1); assert.equal(other.requests, undefined);
});

test('keyboard submit without submitter works; validation failure consumes approval', () => {
    const h = setup(); h.submit(h.form); h.form.invalid = true; h.accept.fire('click');
    assert.equal(h.form.requests, undefined);
    h.form.invalid = false; assert.equal(h.submit(h.form).prevented, true);
    h.accept.fire('click'); assert.equal(h.form.requests, 1); assert.equal(h.form.replayed, undefined);
});

for (const change of ['detached-form', 'detached-button', 'disabled-button', 'rebound-button']) test(`${change} fails closed`, () => {
    const h = setup(); h.submit(h.form, h.button);
    if (change === 'detached-form') h.form.isConnected = false;
    if (change === 'detached-button') h.button.isConnected = false;
    if (change === 'disabled-button') h.button.disabled = true;
    if (change === 'rebound-button') h.button.form = new h.Form();
    h.accept.fire('click'); assert.equal(h.form.requests, undefined);
});

for (const options of [{ missing: true }, { broken: true }]) test(`unavailable dialog fails closed: ${JSON.stringify(options)}`, () => {
    const h = setup(options); assert.equal(h.submit(h.form, h.button).prevented, true);
    assert.equal(h.form.requests, undefined); assert.equal(h.document.body.style.overflow, 'auto');
});

test('template consumers reuse one shell and no native confirms', () => {
    const root = path.join(__dirname, '../app/web/templates/web');
    const base = fs.readFileSync(path.join(root, 'base.html'), 'utf8');
    assert.equal(base.split('{% include "web/_confirm.html" %}').length, 2);
    assert.match(base, /defer[^\n]+ui-confirm\.js/);
    for (const name of ['jobs', 'task_detail', 'task_edit', 'source_detail', 'analytics_chains', 'analytics_chain_detail']) {
        const text = fs.readFileSync(path.join(root, `${name}.html`), 'utf8');
        assert.doesNotMatch(text, /\bconfirm\s*\(/);
        assert.match(text, /data-confirm/); assert.match(text, /name="_csrf"/);
    }
    const source = fs.readFileSync(path.join(root, 'source_detail.html'), 'utf8');
    assert.match(source, /analytics_count/); assert.match(source, /usage_count/);
});

test('queued close from an earlier use does not dismiss a newly opened dialog', () => {
    const h = setup(); h.submit(h.form, h.button); h.cancel.fire('click');
    h.submit(h.form, h.button); h.dialog.fire('close');
    assert.equal(h.dialog.open, true); h.accept.fire('click'); assert.equal(h.form.requests, 1);
});

test('document-level Escape cancels even when focus moved outside the dialog', () => {
    const h = setup(); h.submit(h.form, h.button);
    const event = { key: 'Escape', preventDefault() { this.prevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
    h.document.keydown(event);
    assert.equal(event.prevented, true); assert.equal(event.stopped, true);
    assert.equal(h.dialog.open, false); assert.equal(h.form.requests, undefined);
    assert.equal(h.document.activeElement, h.button);
    const idle = { key: 'Escape', preventDefault() { throw Error('idle intercepted'); } };
    h.document.keydown(idle);
});
