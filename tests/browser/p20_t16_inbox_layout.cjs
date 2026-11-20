// Requires isolated local app and synthetic inbox with no uncertain messages.
const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const base=process.env.P20_BASE_URL;assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''));assert(process.env.P20_STORAGE_STATE);const b=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [390,1440]){const c=await b.newContext({storageState:process.env.P20_STORAGE_STATE,viewport:{width,height:900}});const p=await c.newPage();assert.equal((await p.goto(base+'/app/communications')).status(),200);
  assert.equal(await p.locator('[data-inbox-thread-list] nav[aria-label="Категории входящих"]').count(),1);
  await p.getByRole('link',{name:'Не уверен',exact:true}).click(); await p.waitForURL('**triage_category=uncertain');
  assert.equal(await p.locator('[data-inbox-thread-item]').count(),0); assert.equal(await p.locator('nav[aria-label="Категории входящих"]').count(),1);
  await p.getByRole('link',{name:'Все',exact:true}).click(); await p.locator('[data-inbox-thread-item]').first().waitFor();
  const d=await p.evaluate(()=>[document.documentElement.scrollWidth,document.documentElement.clientWidth]);assert(d[0]<=d[1]+1);
  await c.close();}
 console.log('PASS inbox categories in list, empty-filter recovery and mobile/desktop overflow');
 }finally{await b.close()}})().catch(e=>{console.error(e);process.exit(1)});
