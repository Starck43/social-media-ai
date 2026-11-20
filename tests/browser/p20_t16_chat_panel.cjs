// Real local app and fixture; only the panel endpoint is stubbed for failure/race states.
const {chromium}=require('playwright'); const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.P20_BASE_URL;
 assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')); assert(process.env.P20_STORAGE_STATE);
 const browser=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});
 try {
  const context=await browser.newContext({storageState:process.env.P20_STORAGE_STATE,viewport:{width:1440,height:900}});
  await context.addInitScript(()=>sessionStorage.setItem('chat.workspace.tab','decisions'));
  const p=await context.newPage(); let mode='success', calls=0, raceSeq=0;
  await p.route('**/app/chat/workspace/panel?*',async route=>{
   calls++;
   if(mode==='denied') return route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({error:'denied'})});
   if(mode==='race') {const current=++raceSeq; await new Promise(r=>setTimeout(r,current===1?300:20)); return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({decisions:[{id:current,title:'result-'+current,authoritative_url:'/app/'}],runs:[],materials:[]})});}
   return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({decisions:[{id:1,title:'Тестовое решение',authoritative_url:'/app/'}],runs:[],materials:[]})});
  });
  assert.equal((await p.goto(base+'/app/chat',{waitUntil:'domcontentloaded'})).status(),200);
  await p.getByText('Тестовое решение',{exact:true}).waitFor(); assert(calls>=1,'restored tab must fetch data');
  mode='denied'; await p.locator('[data-workspace-tabpanel="decisions"]').getByRole('button',{name:'Обновить'}).click();
  const err=p.locator('[data-chat-panel-error]'); await err.waitFor({state:'visible'}); assert((await err.innerText()).includes('Доступ к рабочему контексту изменился'));
  assert.equal(await p.getByText('Тестовое решение',{exact:true}).count(),0); assert.equal(await p.getByText('Нет решений, ожидающих подтверждения',{exact:false}).isVisible(),false);
  mode='success'; await err.getByRole('button',{name:'Повторить'}).click(); await p.getByText('Тестовое решение',{exact:true}).waitFor();
  await p.locator('[data-workspace-tab="context"]').click();
  const picker=p.locator('[data-chat-task-picker]'); await picker.getByRole('button',{name:'Упоминания «Смены» в VK и Telegram'}).click();
  const chip=p.locator('[data-context-chip][data-context-type="task"]'); await chip.waitFor({state:'visible'}); await p.waitForFunction(() => document.querySelector('[data-context-chip][data-context-type=task]')?.innerText.includes('Упоминания')); assert((await chip.innerText()).includes('Упоминания'));
  const card=p.locator('[data-chat-inline-cards] [data-card-type="plan_preview"]'); await card.waitFor({state:'visible'}); assert((await card.innerText()).includes('Упоминания'));
  await chip.getByRole('button',{name:'Убрать задачу из контекста сообщения'}).click(); await card.waitFor({state:'hidden'});
  mode='race'; await p.locator('[data-workspace-tab="decisions"]').click();
  while(raceSeq < 1) await new Promise(r=>setTimeout(r,10));
  await p.locator('[data-workspace-tab="decisions"]').click(); await p.getByText('result-2',{exact:true}).waitFor();
  await new Promise(r=>setTimeout(r,400)); assert.equal(await p.getByText('result-1',{exact:true}).count(),0); assert(await p.getByText('result-2',{exact:true}).isVisible());
  console.log('PASS restored tab, denied/retry, stale-response fence, context chip and service-backed card');
  await context.close();
 } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
