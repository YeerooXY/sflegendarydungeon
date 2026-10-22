// Rendered checks against a separate local simulator; never changes the user's session.
const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const out=path.resolve(__dirname,'../out/lockpick-poison-2026-10-18');
const url=process.env.SFLD_LAB_URL||'http://127.0.0.1:49409/?desktop=1';
const profiles=[
 ['windows',chromium,{viewport:{width:1440,height:1000}}],
 ['mac',webkit,{viewport:{width:1440,height:1000}}],
 ['android',chromium,devices['Pixel 7']],
 ['iphone',webkit,devices['iPhone 13']],
];
const report=[];
async function open(page){await page.locator('#game-menu').click();await page.locator('#menu-new').click();}
async function settled(page){await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);}
async function checkBounds(page){
 return page.locator('#setup').evaluate(d=>{
  const b=d.getBoundingClientRect();
  return {width:b.width,height:b.height,viewport:[innerWidth,innerHeight],overflow:d.scrollWidth>d.clientWidth+1,
          contained:b.left>=0&&b.right<=innerWidth+1&&b.top>=0&&b.bottom<=innerHeight+1};
 });
}
(async()=>{
 for(const [name,type,options] of profiles){
  const browser=await type.launch();const context=await browser.newContext({...options,reducedMotion:'reduce'});
  const page=await context.newPage();const errors=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!r.url().startsWith(new URL(url).origin))external.push(r.url())});
  try{
   await page.goto(url);await settled(page);await open(page);
   assert.equal(await page.locator('#advanced-settings').getAttribute('open'),null);
   assert.equal(await page.locator('#seed-input').inputValue(),'');
   assert.deepEqual(await page.locator('#setup input:visible').evaluateAll(xs=>xs.map(x=>x.id)),['budget-input']);
   const box=await checkBounds(page);assert.ok(box.contained&&!box.overflow);
   await page.screenshot({path:path.join(out,`setup-${name}.png`)});
   const focus=[];
   for(let i=0;i<8;i++){await page.keyboard.press('Tab');focus.push(await page.evaluate(()=>({id:document.activeElement.id,tag:document.activeElement.tagName,inside:!!document.activeElement.closest('#setup'),documentFocused:document.hasFocus()})));}
   // Native dialogs allow Tab to visit browser chrome, but not inert page controls.
   assert.ok(focus.every(f=>f.inside||!f.documentFocused));
   await page.keyboard.press('Escape');await page.waitForFunction(()=>!document.querySelector('#setup').open);
   await page.waitForFunction(()=>document.activeElement.id==='game-menu');
   assert.equal(await page.evaluate(()=>document.activeElement.id),'game-menu');
   await open(page);await page.locator('#budget-input').fill('37');await page.locator('#start-run').click();await settled(page);
   const first=await page.evaluate(()=>({seed:config.seed,budget:config.budget,hours:config.deadline_hours,run:obs().run_number,hp:obs().hp,room:obs().room}));
   assert.deepEqual({...first,seed:0},{seed:0,budget:37,hours:168,run:1,hp:1,room:1});
   await open(page);await page.locator('#budget-input').fill('0');await page.locator('#budget-input').press('Enter');await settled(page);
   const second=await page.evaluate(()=>({seed:config.seed,budget:config.budget}));assert.notEqual(first.seed,second.seed);assert.equal(second.budget,0);
   await open(page);await page.locator('#advanced-settings>summary').click();
   await page.locator('#seed-input').fill('0');await page.locator('#hours-input').fill('24');
   await page.screenshot({path:path.join(out,`setup-advanced-${name}.png`)});
   const advanced=await checkBounds(page);assert.ok(advanced.contained&&!advanced.overflow);
   await page.locator('#start-run').click();await settled(page);
   assert.deepEqual(await page.evaluate(()=>[config.seed,config.deadline_hours]),[0,24]);
   await open(page);assert.equal(await page.locator('#hours-input').inputValue(),'168');assert.equal(await page.locator('#seed-input').inputValue(),'');
   await page.locator('#budget-input').fill('-1');await page.locator('#start-run').click();
   assert.equal(await page.locator('#budget-input').evaluate(e=>e.validity.rangeUnderflow),true);
   assert.equal(await page.locator('#setup').evaluate(e=>e.open),true);
   await page.locator('#budget-input').fill('55');
   await page.route('**/api/new',r=>r.fulfill({status:400,contentType:'application/json',body:JSON.stringify({error:'Test: simulator unavailable. Try again.'})}));
   await page.locator('#start-run').click();await page.locator('#setup-error').waitFor({state:'visible'});
   assert.equal(await page.locator('#budget-input').inputValue(),'55');assert.equal(await page.locator('#start-run').isDisabled(),false);
   await page.unroute('**/api/new');await page.locator('#start-run').click();await settled(page);
   assert.equal(await page.evaluate(()=>config.budget),55);
   const storage=await page.evaluate(()=>({cookies:document.cookie,local:Object.keys(localStorage),session:Object.keys(sessionStorage)}));
   assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
   report.push({profile:name,engine:type.name(),coverage:name==='windows'?'Windows desktop Chromium':'Emulated on Windows; actual device and OS fonts unverified',box,advanced,focus,first,second,storage,errors,external,tests:'Budget-only start, randomized seed, seed zero, advanced controls, resets, keyboard/Escape, invalid input, recoverable API error, retry, no external requests'});
  }finally{await page.evaluate(()=>session&&api('close',{session})).catch(()=>{});await browser.close();}
 }
 // Small reflow and intermediate tablet widths in the same browser engine.
 const browser=await chromium.launch();
 for(const width of [320,768]){
  const page=await browser.newPage({viewport:{width,height:900}});await page.goto(url);await settled(page);await open(page);
  const box=await checkBounds(page);assert.ok(box.contained&&!box.overflow);
  await page.screenshot({path:path.join(out,`setup-width-${width}.png`)});
  report.push({profile:`width-${width}`,box});await page.evaluate(()=>api('close',{session}));await page.close();
 }
 await browser.close();
 fs.writeFileSync(path.join(out,'setup-verification.json'),JSON.stringify(report,null,2));
 console.log(JSON.stringify({passed:true,profiles:report.map(r=>r.profile)}));
})().catch(e=>{fs.writeFileSync(path.join(out,'setup-verification-failure.txt'),String(e.stack));console.error(e);process.exit(1)});
