// Offline Chromium smoke check: no application server or live requests.
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..') + path.sep;
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'web-confirm-'));
const fixture = path.join(temp, 'smoke.html');let partial=fs.readFileSync(root+'app/web/templates/web/_confirm.html','utf8').replace(/\{#[\s\S]*?#\}/g,'');let js=fs.readFileSync(root+'app/static/js/ui-confirm.js','utf8');
const checks=`
const results=[]; function check(ok,label){if(!ok)throw Error(label);results.push(label);}
const form=document.querySelector('form');const button=document.getElementById('delete');const dialog=document.getElementById('ui-confirm');let posts=0;
form.addEventListener('submit',e=>{e.preventDefault();posts++;check(e.submitter===button,'original submitter');check(e.submitter.formAction.endsWith('/delete'),'delete formaction');let data=new FormData(form,e.submitter);check(data.get('_csrf')==='csrf-marker'&&data.get('tenant_id')==='7','CSRF and tenant preserved');});
button.focus();button.click();check(dialog.open&&posts===0,'waits for acceptance');check(document.activeElement.hasAttribute('data-confirm-cancel'),'cancel gets focus');check(document.getElementById('ui-confirm-message').textContent.includes('<img'),'message is text');check(!dialog.querySelector('img'),'no injected markup');
dialog.querySelector('[data-confirm-cancel]').click();check(!dialog.open&&posts===0&&document.activeElement===button,'cancel and focus restored');
button.click();dialog.dispatchEvent(new Event('cancel',{cancelable:true}));check(!dialog.open&&posts===0,'escape cancellation');
button.click();dialog.querySelector('[data-confirm-accept]').click();check(!dialog.open&&posts===1,'one accepted POST');check(document.body.style.overflow==='','scroll restored');
document.getElementById('results').textContent='PASS '+results.length+' checks: '+results.join('; ');
`;
fs.writeFileSync(fixture,`<!doctype html><html><body><form method="post" action="/save"><input name="name" required value=""><input name="_csrf" value="csrf-marker"><input name="tenant_id" value="7"><button id="delete" type="submit" formnovalidate formaction="/delete" data-confirm="Delete &lt;img&gt; &quot;name&quot;?">Delete</button></form>${partial}<pre id="results">NOT RUN</pre><script>${js}</script><script>try{${checks}}catch(e){document.getElementById('results').textContent='FAIL '+e.stack}</script></body></html>`);

try {
    const browser = process.env.CHROMIUM_BIN || 'chromium';
    const run = spawnSync(browser, ['--headless', '--no-sandbox', '--disable-gpu', '--dump-dom', pathToFileURL(fixture).href], { encoding: 'utf8', timeout: 30000 });
    if (run.error) throw run.error;
    const result = run.stdout.match(/<pre id="results">([^<]+)/)?.[1];
    if (run.status !== 0 || !result?.startsWith('PASS 11 checks:')) throw Error(result || 'Chromium did not produce smoke evidence');
    console.log(result);
} finally {
    fs.rmSync(temp, { recursive: true, force: true });
}
