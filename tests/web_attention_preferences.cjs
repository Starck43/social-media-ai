/* User-owned preferences: local storage is read only by an explicit import. */
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const store = new Map(), events = [], calls = [];
const sandbox = { localStorage: { getItem: k => store.get(k) || null, setItem: (k,v) => store.set(k,v) },
    document: { documentElement: { dataset: {}, style: { setProperty() {} } }, addEventListener() {}, removeEventListener() {} },
    window: { dispatchEvent: e => events.push(e) }, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, URLSearchParams,
    fetch: async (url, options = {}) => { calls.push({url, options}); const method = options.method || 'GET'; const body = method === 'POST' ? JSON.parse(options.body) : null; return { ok: true, redirected: false, json: async () => ({ user_id: 101, preferences: body?.preferences || defaults }) }; } };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/js/shell.js', 'utf8') + '\nthis.pref = AttentionPreferences;', sandbox);
const p = sandbox.pref, defaults = p.defaults(), checks = [];
function test(name, fn) { fn(); checks.push(name); }
async function asyncTest(name, fn) { await fn(); checks.push(name); }
(async () => {
    test('safe defaults and explicit local import key', () => { assert.equal(defaults.popup, false); assert.equal(defaults.tasks, true); assert.equal(p.key(null), null); assert.equal(p.key(true), null); });
    test('local value is available only through explicit import', () => { store.set(p.key(101), JSON.stringify({...defaults,tasks:false})); assert.equal(p.readLocal(101).tasks,false); assert.equal(p.readLocal(202).tasks,true); });
    for (const value of ['bad json', JSON.stringify({...defaults,version:2}), JSON.stringify({...defaults,tasks:'false'}), JSON.stringify({...defaults,start:'24:00'}), JSON.stringify({...defaults,quiet:true,start:'08:00',end:'08:00'})])
        test('invalid local data becomes safe defaults', () => { store.set(p.key(101),value); assert.equal(p.readLocal(101).tasks,true); });
    test('only supported derived signals suppressible; messages mandatory', () => { const quiet={...defaults,tasks:false,connections:false}; for(const kind of ['task','connection']) assert.equal(p.visible({kind},quiet),false); assert.equal(p.visible({kind:'notification'},quiet),true); });
    test('quiet boundaries use device local clock', () => { const q={...defaults,quiet:true}; for(const [h,m,expected] of [[21,59,false],[22,0,true],[7,59,true],[8,0,false]]) assert.equal(p.quietNow(q,{getHours:()=>h,getMinutes:()=>m}),expected); });
    await asyncTest('editor loads from server and saves with CSRF', async () => {
        const editor=sandbox.notificationPreferences(101,'csrf-token');
        assert.equal(editor.value.tasks,true);
        await editor.load();
        editor.value={...defaults,tasks:false};
        await editor.save();
        assert.equal(calls.at(-1).url,'/app/notifications/preferences');
        assert.equal(calls.at(-1).options.method,'POST');
        assert.equal(JSON.parse(calls.at(-1).options.body)._csrf,'csrf-token');
        assert.equal(events.at(-1).detail.value.tasks,false);
        assert.ok(editor.message.includes('сохранены'));
    });
    await asyncTest('local import requires explicit action and persists to server', async () => {
        calls.length=0;
        store.set(p.key(101),JSON.stringify({...defaults,connections:false}));
        const editor=sandbox.notificationPreferences(101,'csrf-token');
        await editor.load();
        calls.length=0;
        await editor.importLocal();
        assert.equal(calls.length,1);
        assert.equal(JSON.parse(calls[0].options.body).preferences.connections,false);
    });
    await asyncTest('failed save does not claim success', async () => {
        const oldFetch=sandbox.fetch;
        sandbox.fetch=async()=>({ok:false,redirected:false});
        const editor=sandbox.notificationPreferences(101,'csrf-token');
        editor.loading=false; editor.value={...defaults,tasks:false};
        await editor.save();
        assert.ok(editor.message.includes('Не удалось сохранить'));
        sandbox.fetch=oldFetch;
    });
    await asyncTest('foreground refresh preserves unsaved edits', async () => {
        const oldFetch=sandbox.fetch;
        sandbox.fetch=async()=>({ok:true,redirected:false,json:async()=>({user_id:101,preferences:{...defaults,connections:false}})});
        const editor=sandbox.notificationPreferences(101,'csrf-token');
        editor.loading=false; editor.serverValue={...defaults}; editor.value={...defaults,tasks:false};
        await editor.load(false);
        assert.equal(editor.value.tasks,false);
        assert.equal(editor.value.connections,true);
        sandbox.fetch=oldFetch;
    });
    await asyncTest('poll cannot supersede a pending save', async () => {
        const oldFetch=sandbox.fetch;
        let resolveSave;
        let callsBefore=0;
        sandbox.fetch=(url,options={})=>{
            callsBefore++;
            return new Promise(resolve=>{ resolveSave=()=>resolve({ok:true,redirected:false,json:async()=>({user_id:101,preferences:JSON.parse(options.body).preferences})}); });
        };
        const editor=sandbox.notificationPreferences(101,'csrf-token');
        editor.loading=false; editor.value={...defaults,tasks:false};
        const saving=editor.save();
        const count=callsBefore;
        await editor.load(false);
        assert.equal(callsBefore,count);
        resolveSave();
        await saving;
        assert.equal(editor.saving,false);
        assert.ok(editor.message.includes('сохранены'));
        sandbox.fetch=oldFetch;
    });
    await asyncTest('response for another login identity is ignored', async () => {
        const oldFetch=sandbox.fetch;
        sandbox.fetch=async()=>({ok:true,redirected:false,json:async()=>({user_id:202,preferences:{...defaults,tasks:false}})});
        const editor=sandbox.notificationPreferences(101,'csrf-token');
        editor.value={...defaults,connections:false};
        await editor.load();
        assert.equal(editor.value.connections,false);
        assert.equal(editor.value.tasks,true);
        assert.ok(editor.message.includes('Не удалось загрузить'));
        sandbox.fetch=oldFetch;
    });
    test('count and list share filters while mandatory notices remain', () => { const bell=sandbox.notificationsBell('mock',7,101); bell.preferences={...defaults,tasks:false,connections:false}; bell.items=['task','connection','notification'].map(kind=>({key:kind,kind})); assert.equal(bell.visibleItems().length,1); assert.equal(bell.badge(),'1'); bell.hasMore=true; assert.equal(bell.badge(),'1'); bell.error='unknown'; assert.equal(bell.badge(),'1 · ?'); });
    await asyncTest('reset restores server defaults', async () => { const editor=sandbox.notificationPreferences(101,'csrf-token'); editor.loading=false; await editor.restore(); assert.equal(calls.at(-1).url,'/app/notifications/preferences/reset'); assert.equal(editor.value.popup,false); });
    console.log(JSON.stringify({passed:checks.length,failed:0,checks},null,2));
})().catch(error=>{console.error(error);process.exitCode=1;});
