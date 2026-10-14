/* Pure notification-controller regression. No browser, app, DB or real HTTP.
 * Local validation only; do not mistake this for ASGI/ACL acceptance. */
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app/static/js/shell.js', 'utf8');
const sandbox = {
    document: { documentElement: { dataset: {} }, addEventListener() {} },
    localStorage: { getItem() { throw Error('storage unavailable'); } },
    URLSearchParams,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
(async () => {
    const bell = sandbox.notificationsBell('csrf-fixture');
    bell.$nextTick = fn => fn();
    bell.$refs = { dialog: { focus() {} } };
    bell.items = [{ id: 1 }, { id: 2 }];
    sandbox.fetch = async () => ({ ok: false });
    await bell.readOne(bell.items[0]);
    assert.equal(bell.items.length, 2, 'failed read must retain count and list');
    assert.ok(bell.error);
    sandbox.fetch = async () => { throw Error('offline'); };
    await bell.load();
    assert.equal(bell.items.length, 2, 'failed refresh preserves known rows');
    sandbox.fetch = async (_, options) => {
        if (!options.method) return { ok: true, json: async () => ({ notifications: [{ id: 2 }] }) };
        assert.equal(options.method, 'POST');
        assert.equal(options.body.get('_csrf'), 'csrf-fixture');
        return { ok: true, json: async () => ({ ok: true }) };
    };
    await bell.readOne(bell.items[0]);
    assert.equal(bell.items.length, 1);
    assert.equal(bell.items[0].id, 2);
    sandbox.fetch = async () => ({ ok: true, json: async () => ({ notifications: [] }) });
    await bell.load();
    assert.equal(bell.items.length, 0);
    assert.equal(bell.error, '');
    sandbox.fetch = async () => ({ ok: true, redirected: true });
    await bell.load();
    assert.ok(bell.error, 'expired-session redirect is not an empty healthy feed');
    console.log('Shell notification state checks passed (mocked, no real requests).');
})().catch(error => { console.error(error); process.exit(1); });
