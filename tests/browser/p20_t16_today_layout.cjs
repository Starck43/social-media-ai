// Real local app and synthetic warning data; no provider interactions.
const {chromium}=require('playwright');const assert=require('node:assert/strict');
(async()=>{const base=process.env.P20_BASE_URL;assert(/^http:\/\/127\.0\.0\.1:\d+$/.test(base||''));assert(process.env.P20_STORAGE_STATE);const b=await chromium.launch({executablePath:process.env.P20_CHROMIUM||'/usr/local/bin/chromium',args:['--no-sandbox']});try{
 for(const width of [390,1440]){const c=await b.newContext({storageState:process.env.P20_STORAGE_STATE,viewport:{width,height:900}});const p=await c.newPage();assert.equal((await p.goto(base+'/app/')).status(),200);for(const name of ['brief','needs_action','today_plan','attention','recent_results']) assert.equal(await p.locator(`[data-today-section="${name}"]`).count(),1,name);
  const ul=p.locator('[data-today-section="attention"] > ul');assert(await ul.count());const tracks=await ul.evaluate(el=>getComputedStyle(el).gridTemplateColumns.trim().split(/\s+/).length);assert.equal(tracks,width===390?1:3);
  if(width===1440){const brief=await p.locator('[data-today-section="brief"]').boundingBox();const actions=await p.locator('[data-today-section="needs_action"]').boundingBox();assert(actions.y>brief.y+brief.height-4);assert(Math.abs(brief.x-actions.x)<2);}
  await c.close();}
 console.log('PASS Today five sections, full-width brief, desktop bento and responsive attention cards');
 }finally{await b.close()}})().catch(e=>{console.error(e);process.exit(1)});
