const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { pathToFileURL } = require('node:url');
// Uses Chromium's local debugging pipe; no npm packages, server or network.
const root = path.resolve(__dirname, '../..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'confirm-cdp-'));
const fixture = path.join(temp, 'fixture.html');
const shell = fs.readFileSync(path.join(root, 'app/web/templates/web/_confirm.html'), 'utf8').replace(/\{#[\s\S]*?#\}/g, '');
const code = fs.readFileSync(path.join(root, 'app/static/js/ui-confirm.js'), 'utf8');
fs.writeFileSync(fixture, `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><form method="post" action="/save"><input name="name" required value=""><input name="_csrf" value="csrf"><input name="tenant_id" value="7"><button id="delete" formnovalidate formaction="/delete" data-confirm="Delete &quot;quoted&quot; &lt;img&gt;?">Delete</button><button id="save">Save</button></form>${shell}<script>${code}</script><script>window.posts=0; window.editorOpen=true; document.querySelector('form').addEventListener('submit',e=>{e.preventDefault();window.posts++;window.actualAction=e.submitter.formAction;});window.addEventListener('keydown',e=>{if(e.key==='Escape')window.editorOpen=false;});</script></body></html>`);
const child = spawn(process.env.CHROMIUM_BIN || 'chromium', ['--headless', '--no-sandbox', '--disable-gpu', '--remote-debugging-pipe', `--user-data-dir=${path.join(temp,'profile')}`], { stdio: ['ignore','ignore','ignore','pipe','pipe'] });
let id=0, buffer=Buffer.alloc(0), session; const waiting = new Map();
child.stdio[4].on('data', chunk => {
    buffer=Buffer.concat([buffer,chunk]); let zero;
    while((zero=buffer.indexOf(0))>=0){const raw=buffer.subarray(0,zero).toString();buffer=buffer.subarray(zero+1);if(!raw)continue;const msg=JSON.parse(raw);if(msg.id){const cb=waiting.get(msg.id);waiting.delete(msg.id);if(msg.error)cb.reject(Error(JSON.stringify(msg.error)));else cb.resolve(msg.result);}}
});
function send(method, params={}, sessionId=session){return new Promise((resolve,reject)=>{const n=++id;waiting.set(n,{resolve,reject});child.stdio[3].write(JSON.stringify({id:n,method,params,...(sessionId?{sessionId}:{})})+'\0');});}
async function evaluate(expression){const r=await send('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text);return r.result.value;}
async function key(key,code,virtual,shift=false){await send('Input.dispatchKeyEvent',{type:key==='Enter'?'keyDown':'rawKeyDown',key,code,windowsVirtualKeyCode:virtual,modifiers:shift?8:0,...(key==='Enter'?{text:'\r',unmodifiedText:'\r'}:{})});await send('Input.dispatchKeyEvent',{type:'keyUp',key,code,windowsVirtualKeyCode:virtual,modifiers:shift?8:0});}
async function click(x,y){await send('Input.dispatchMouseEvent',{type:'mousePressed',x,y,button:'left',clickCount:1});await send('Input.dispatchMouseEvent',{type:'mouseReleased',x,y,button:'left',clickCount:1});}
async function state(){return evaluate(`({open:document.getElementById('ui-confirm').open,focus:document.activeElement.id||document.activeElement.getAttribute('data-confirm-accept')!==null&&'accept'||document.activeElement.getAttribute('data-confirm-cancel')!==null&&'cancel'||document.activeElement.tagName,posts:window.posts,editorOpen:window.editorOpen,overflow:document.body.style.overflow})`);}
const timer=setTimeout(()=>{console.error('CDP timed out');child.kill('SIGKILL');process.exitCode=1;},30000);
(async()=>{
    let checked=0; function check(ok,label){assert.ok(ok,label);checked++;}
    const t=await send('Target.createTarget',{url:'about:blank'},null);session=(await send('Target.attachToTarget',{targetId:t.targetId,flatten:true},null)).sessionId;
    await send('Page.enable');await send('Runtime.enable');await send('Page.navigate',{url:pathToFileURL(fixture).href});
    for(let i=0;i<40;i++){if(await evaluate('typeof window.posts === "number"'))break;await new Promise(r=>setTimeout(r,25));}
    await evaluate(`document.getElementById('delete').focus();document.getElementById('delete').click()`);
    let s=await state();check(s.open&&s.posts===0&&s.focus==='cancel','cancel focus / no submit');
    await key('Tab','Tab',9);s=await state();check(s.focus==='accept','Tab moves to accept');
    await key('Tab','Tab',9,true);s=await state();check(s.focus==='cancel','Shift+Tab moves to cancel');
    // Native Chromium may move focus to browser chrome at the wrap boundary;
    // it must never reach another element of the inert background document.
    await key('Tab','Tab',9,true);s=await state();check(!['delete','save','INPUT'].includes(s.focus),'background cannot receive keyboard focus');
    await key('Escape','Escape',27);s=await state();check(!s.open&&s.posts===0&&s.focus==='delete','real Escape cancels and restores focus');check(s.editorOpen,'Escape does not close editor behind confirmation');check(s.overflow==='','Escape restores scroll');
    await evaluate(`document.getElementById('delete').click()`);s=await state();check(s.open,'reopens after Escape');
    const r=await evaluate(`(()=>{let r=document.getElementById('ui-confirm').getBoundingClientRect();return {left:r.left,top:r.top,right:r.right,bottom:r.bottom}})()`);
    await click(Math.max(1,r.left-8),Math.max(1,r.top-8));s=await state();check(!s.open&&s.posts===0,'real backdrop click cancels');
    await evaluate(`document.getElementById('delete').click()`);await key('Tab','Tab',9);await key('Enter','Enter',13);s=await state();check(!s.open&&s.posts===1,'Enter on accept resumes one submit');check((await evaluate('window.actualAction')).endsWith('/delete'),'accept preserves delete override');
    await send('Emulation.setDeviceMetricsOverride',{width:375,height:667,deviceScaleFactor:1,mobile:true});await evaluate(`document.getElementById('delete').click()`);
    check(await evaluate(`(()=>{const r=document.getElementById('ui-confirm').getBoundingClientRect();return r.left>=0&&r.right<=375&&r.top>=0&&r.bottom<=667})()`),'mobile dialog stays within viewport');
    console.log(`PASS ${checked} native Chromium keyboard/backdrop/mobile checks`);
})().catch(e=>{console.error(e);process.exitCode=1;}).finally(()=>{clearTimeout(timer);child.kill('SIGKILL');setTimeout(()=>fs.rmSync(temp,{recursive:true,force:true}),200);});
