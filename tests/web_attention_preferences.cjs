/* Pure browser-local preferences. No application, network, DB or credentials. */
const fs = require('fs'), vm = require('vm'), assert = require('node:assert/strict');
const store = new Map(), events = [];
const sandbox = { localStorage: { getItem: k => store.get(k) || null, setItem: (k,v) => store.set(k,v) },
    document: { documentElement: { dataset: {}, style: { setProperty() {} } }, addEventListener() {} },
    window: { dispatchEvent: e => events.push(e) }, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } }, URLSearchParams };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('app/static/js/shell.js', 'utf8') + '\nthis.pref = AttentionPreferences;', sandbox);
const p = sandbox.pref, defaults = p.defaults(), checks = [];
function test(name, fn) { fn(); checks.push(name); }
test('safe defaults, no automatic popup', () => { assert.equal(defaults.popup, false); assert.equal(defaults.tasks, true); assert.equal(p.key(null), null); assert.equal(p.key(true), null); });
test('caller-isolated keys', () => { store.set(p.key(101), JSON.stringify({...defaults, tasks:false})); assert.equal(p.read(101).tasks,false); assert.equal(p.read(202).tasks,true); });
for (const value of ['bad json', JSON.stringify({...defaults,version:2}), JSON.stringify({...defaults,tasks:'false'}), JSON.stringify({...defaults,start:'24:00'}), JSON.stringify({...defaults,quiet:true,start:'08:00',end:'08:00'})])
    test('invalid values restore visible defaults', () => { store.set(p.key(101),value); assert.equal(p.read(101).tasks,true); });
test('only supported derived signals suppressible; messages mandatory', () => { const quiet={...defaults,tasks:false,connections:false}; for(const kind of ['task','connection']) assert.equal(p.visible({kind},quiet),false); assert.equal(p.visible({kind:'notification'},quiet),true); });
test('quiet boundaries including overnight and device local clock', () => { const q={...defaults,quiet:true}; for(const [h,m,expected] of [[21,59,false],[22,0,true],[7,59,true],[8,0,false]]) assert.equal(p.quietNow(q,{getHours:()=>h,getMinutes:()=>m}),expected); const day={...q,start:'09:00',end:'17:00'}; assert.equal(p.quietNow(day,{getHours:()=>17,getMinutes:()=>0}),false); });
test('saving updates caller display only, no POST/read/ack', () => { const editor=sandbox.notificationPreferences(101); editor.value={...defaults,tasks:false}; editor.save(); assert.equal(events.at(-1).detail.userId,101); assert.equal(JSON.parse(store.get(p.key(101))).tasks,false); assert.equal(p.read(202).tasks,true); assert.ok(editor.message.includes('браузере')); });
test('storage failure honest memory-only fallback', () => { sandbox.localStorage.setItem=()=>{throw Error('denied')}; const editor=sandbox.notificationPreferences(101); editor.save(); assert.ok(editor.message.includes('на этой странице')); });
test('invalid editor values fail without event', () => { const before=events.length; const editor=sandbox.notificationPreferences(101); editor.value={...defaults,quiet:true,start:'08:00',end:'08:00'}; editor.save(); assert.equal(events.length,before); });
test('count and list use same display filter; authorization state unchanged', () => { const bell=sandbox.notificationsBell('mock',7,101); bell.preferences={...defaults,tasks:false,connections:false}; bell.items=['task','connection','notification'].map(kind=>({key:kind,kind})); assert.equal(bell.visibleItems().length,1); assert.equal(bell.badge(),'1'); assert.equal(bell.items.length,3); bell.hasMore=true; assert.equal(bell.badge(),'1'); bell.error='unknown'; assert.equal(bell.badge(),'1 · ?'); bell.items=[]; assert.equal(bell.badge(),'?'); });
test('popup opt-in, no initial-load noise, no focus or read effects', () => { const bell=sandbox.notificationsBell('mock',7,101); bell.preferences={...defaults,popup:true}; bell.complete=true; bell.items=[{key:'event1',kind:'notification'}]; bell.considerPopup(); assert.equal(bell.popupVisible,false); bell.considerPopup(); assert.equal(bell.popupVisible,false); bell.items.push({key:'event2',kind:'notification'}); bell.considerPopup(); assert.equal(bell.popupVisible,true); bell.complete=false; bell.items.push({key:'event3',kind:'notification'}); bell.considerPopup(); assert.equal(bell.popupVisible,false); });
console.log(JSON.stringify({passed:checks.length,failed:0,checks},null,2));
