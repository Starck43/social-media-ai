/* Pure context-bound notification-controller contracts. No app/DB/HTTP. */
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const path = require('node:path');
const properties = {};
const sandbox = {
    document: { documentElement: { dataset: {}, style: { setProperty(k, v) { properties[k] = v; } } }, addEventListener() {} },
    localStorage: { getItem() { throw Error('storage unavailable'); } }, URLSearchParams,
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/shell.js'), 'utf8'), sandbox);
const context = { workspace_id: 7, user_id: 101 };
const item = (id, kind = 'notification', read_id = id) => ({
    key: kind === 'notification' ? `notification:user:101:${id}` : `connection:7:101:vk:reauth`,
    id, user_id: 101, kind, scope: 'personal', workspace_id: kind === 'notification' ? 8 : 7,
    title: 'Safe title', message: 'Safe summary', state: 'unread', created_at: '',
    destination: kind === 'connection' ? '/app/settings?tab=connections' : `/app/notifications/open/${id}`,
    action: kind === 'connection' ? 'open' : 'read', read_id,
});
const snapshot = (items, extra = {}) => ({ context, notifications: items, count: items.length, has_more: false, complete: true, task_events_authorized: true, ...extra });
const response = data => ({ ok: true, status: 200, json: async () => data });
const bell = () => {
    const value = sandbox.notificationsBell('csrf-fixture', 7, 101);
    value.$nextTick = fn => fn(); value.$refs = { dialog: { focus() {} } };
    return value;
};
(async () => {
    let checks = 0;
    const controller = bell(); controller.items = [item(1), item(2)];
    sandbox.fetch = async () => ({ ok: false, status: 500 });
    await controller.readOne(controller.items[0]);
    assert.equal(controller.items.length, 2); assert.ok(controller.error); checks++;
    sandbox.fetch = async () => { throw Error('offline'); };
    await controller.load();
    assert.equal(controller.items.length, 2); assert.equal(controller.complete, false); assert.ok(controller.badge().includes('?')); checks++;
    sandbox.fetch = async (_, options) => {
        assert.equal(options.headers['X-App-Workspace'], '7'); assert.equal(options.headers['X-App-User'], '101');
        if (!options.method) return response(snapshot([item(2)]));
        assert.equal(options.method, 'POST'); assert.equal(options.body.get('_csrf'), 'csrf-fixture');
        assert.equal(options.body.get('workspace_id'), '7');
        return response({ ok: true, context });
    };
    await controller.readOne(controller.items[0]);
    assert.equal(controller.items.length, 1); assert.equal(controller.items[0].read_id, 2); checks++;
    const connection = item(9, 'connection', null);
    controller.items = [connection];
    sandbox.fetch = () => { throw Error('connection signal must not POST'); };
    await controller.readOne(connection); checks++;
    sandbox.fetch = async () => response(snapshot([]));
    await controller.load(); assert.equal(controller.items.length, 0); assert.equal(controller.error, ''); checks++;
    sandbox.fetch = async () => response(snapshot([item(2)], { has_more: true }));
    await controller.load(); assert.equal(controller.badge(), '1+'); checks++;
    // Partial connection outages do not retain revoked personal receipts.
    for (const [source, kind] of [['messages', 'notification'], ['connections', 'connection']]) {
        const partial = bell(); partial.items = [item(1), item(3, 'connection', null)];
        sandbox.fetch = async () => response(snapshot([], { complete: false, unavailable: [source] }));
        await partial.load();
        assert.equal(partial.items.length, 1); assert.equal(partial.items[0].kind, kind);
        assert.equal(partial.items[0].read_id, null); assert.equal(partial.items[0].action, 'none');
        assert.equal(partial.complete, false); checks++;
        sandbox.fetch = () => { throw Error('stale rows must not POST'); };
        await partial.readOne(partial.items[0]); checks++;
        sandbox.fetch = async () => response(snapshot([])); await partial.load();
        assert.equal(partial.items.length, 0); assert.equal(partial.complete, true); assert.equal(partial.error, ''); checks++;
    }
    const bounded = bell(); bounded.items = Array.from({ length: 100 }, (_, id) => item(id + 1));
    sandbox.fetch = async () => response(snapshot([item(202, 'connection', null)], { complete: false, unavailable: ['messages'] }));
    await bounded.load(); assert.equal(bounded.items.length, 100); assert.equal(bounded.visibleItems().length, 100);
    assert.equal(bounded.items[0].kind, 'connection'); assert.equal(bounded.hasMore, true); checks++;
    // Successful new authorization replaces, rather than retains, revoked rows.
    sandbox.fetch = async () => response(snapshot([]));
    await controller.load(); assert.equal(controller.items.length, 0); checks++;
    for (const data of [snapshot([item(1), item(1)]), snapshot([{ ...item(1), destination: 'https://evil.test/' }]), snapshot([item(1)], { count: 99 }),
        snapshot([item(1)], { complete: false, unavailable: ['unknown'] }),
        snapshot([{ ...item(1), kind: 'task', scope: 'workspace' }]),
        snapshot([{ ...item(1), user_id: 202 }])]) {
        const invalid = bell(); invalid.items = [item(2)];
        sandbox.fetch = async () => response(data); await invalid.load();
        assert.equal(invalid.items[0].read_id, 2); assert.ok(invalid.error); checks++;
    }
    for (const data of [snapshot([item(1)], { context: { workspace_id: 8, user_id: 101 } }), snapshot([item(1)], { context: { workspace_id: 7, user_id: 202 } })]) {
        const changed = bell(); changed.items = [item(2)];
        sandbox.fetch = async () => response(data); await changed.load();
        assert.equal(changed.items.length, 0); assert.ok(changed.contextChanged); checks++;
    }
    for (const status of [401, 403, 409]) {
        const changed = bell(); changed.items = [item(2)];
        sandbox.fetch = async () => ({ ok: false, status }); await changed.load();
        assert.equal(changed.items.length, 0); assert.ok(changed.contextChanged); checks++;
    }
    const redirected = bell(); redirected.items = [item(1)];
    sandbox.fetch = async () => ({ ok: true, redirected: true }); await redirected.load();
    assert.equal(redirected.items.length, 0); assert.ok(redirected.error); checks++;
    const race = bell(); let resolve;
    sandbox.fetch = () => new Promise(r => { resolve = r; });
    const pending = race.load(); race.reset('workspace changed'); resolve(response(snapshot([item(1)]))); await pending;
    assert.equal(race.items.length, 0); assert.equal(race.error, 'workspace changed'); checks++;
    const denied = bell(); denied.items = [item(1)];
    sandbox.fetch = async () => response({ ok: false, context }); await denied.readOne(denied.items[0]);
    assert.equal(denied.items.length, 0); assert.ok(denied.contextChanged); checks++;
    sandbox.fetch = () => { throw Error('changed context must not submit again'); };
    await denied.load(); await denied.readOne(item(1)); checks++;
    console.log(`${checks} mocked notification state/error/CSRF/context/personal-receipt checks passed.`);
})().catch(error => { console.error(error); process.exit(1); });
