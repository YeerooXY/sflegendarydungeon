// Verifies the 2026-10-22 touch-tap and post-action focus fixes in four profiles.
const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const out=path.resolve(__dirname,'../out/ui-audit-2026-10-22/after');
const url=process.env.SFLD_LAB_URL||'http://127.0.0.1:49431/?desktop=1';
fs.mkdirSync(out,{recursive:true});
const profiles=[
 ['windows',chromium,{viewport:{width:1440,height:1000}}],
 ['mac',webkit,{viewport:{width:1440,height:1000}}],
 ['android',chromium,devices['Pixel 7']],
 ['iphone',webkit,devices['iPhone 13']],
];
const settled=page=>page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
const steps=page=>page.evaluate(()=>payload.journal.length);
async function toEncounter(page){
 for(let i=0;i<40;i++){
  const phase=await page.evaluate(()=>obs().phase);
  if(phase==='encounter'&&await page.evaluate(()=>!!(legal('interact')||legal('fight'))&&!!(legal('skip')||legal('flee'))))return true;
  await page.evaluate(()=>act(obs().suggestions[0].action,true));await settled(page);
 }
 return false;
}
(async()=>{
 const report=[];
 for(const [name,type,options] of profiles){
  const browser=await type.launch();const context=await browser.newContext({...options,reducedMotion:'reduce'});
  const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const touch=!!options.hasTouch;
  try{
   await page.goto(url);await settled(page);
   assert.ok(await toEncounter(page),'reach a two-choice encounter');
   const box=await page.locator('#scene').boundingBox();
   const empty={x:box.x+box.width*.08,y:box.y+box.height*.45};
   const before=await steps(page);
   if(touch)await page.touchscreen.tap(empty.x,empty.y);else await page.mouse.click(empty.x,empty.y);
   await page.waitForTimeout(400);await settled(page);
   const after=await steps(page);
   if(touch)assert.equal(after,before,'a background tap must not act on touch');
   else assert.equal(after,before+1,'a background click still acts with a mouse');
   // A visible action button still works on every device.
   await toEncounter(page);
   const choice=page.locator('#actions button:not(:disabled)').first();
   const count=await steps(page);
   if(touch)await choice.tap();else await choice.click();
   await settled(page);assert.equal(await steps(page),count+1,'action button acts');
   // After a manual move, focus sits on the recommended choice when one is shown.
   const focus=await page.evaluate(()=>{const e=document.activeElement;const rec=document.querySelector('#actions button.suggested:not(:disabled),#scene-content button.suggested:not(:disabled),#scene-content button.recommended:not(:disabled)');
    return {focused:e?.className||e?.id,recommendedExists:!!rec,onRecommended:!!rec&&e===rec}});
   if(focus.recommendedExists)assert.ok(focus.onRecommended,'focus on the recommended choice: '+JSON.stringify(focus));
   await page.screenshot({path:path.join(out,`${name}-after-action.png`)});
   assert.deepEqual(errors,[]);
   report.push({profile:name,engine:type.name(),input:touch?'touch':'mouse',backgroundTapActs:after!==before,focus,
     coverage:name==='windows'?'Windows desktop Chromium':'Emulated on Windows; actual device and OS fonts unverified'});
  }finally{await page.evaluate(()=>typeof session!=='undefined'&&session&&api('close',{session})).catch(()=>{});await browser.close();}
  console.log(name,'passed');
 }
 fs.writeFileSync(path.join(out,'touch-focus-verification.json'),JSON.stringify(report,null,1));
})().catch(e=>{console.error(e);process.exit(1)});
