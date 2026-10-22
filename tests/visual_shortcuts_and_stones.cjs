const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const origin=process.env.SFLD_LAB_URL||'http://127.0.0.1:8798',output='out/stone-runtime-fix-2026-10-17';
fs.mkdirSync(output,{recursive:true});
const monster='schema=1\nroom=60\nrun_number=2\nphase=encounter\nencounter=monster\nhp_percent=80\nkeys=7\ngems=rabbit,moonstone\n';
const stones=fs.readFileSync('examples/final-stone-moonstone-thirsty.state','utf8');
(async()=>{
 const results=[];
 for(const [name,engine,profile,launch] of [
  ['windows-chromium',chromium,{viewport:{width:1440,height:900}},{channel:'msedge'}],
  ['mac-safari-emulated',webkit,{...devices['Desktop Safari'],viewport:{width:1440,height:900}},{}],
  ['android-chrome-emulated',chromium,devices['Pixel 7'],{channel:'msedge'}],
  ['iphone-safari-emulated',webkit,devices['iPhone 13'],{}],
 ]){
  const browser=await engine.launch({headless:true,...launch});
  try{
   const page=await browser.newPage(profile),errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.goto(origin+'/?desktop=1');await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
   const load=state=>page.evaluate(async state=>{await replaceSession({...config,initial_state:state,run_number:2,budget:5,stone_advice_samples:512});$('speed').value='450';},state);
   await load(monster);
   assert.equal(await page.locator('#game-step').getAttribute('aria-keyshortcuts'),'N');
   assert.equal(await page.locator('#game-step kbd').innerText(),'N');
   assert.equal(await page.locator('#scene img').evaluateAll(imgs=>imgs.some(i=>i.draggable)),false);
   assert.equal(await page.locator('#scene img').first().evaluate(img=>img.dispatchEvent(new Event('dragstart',{bubbles:true,cancelable:true}))),false);
   await page.locator('#scene img').evaluateAll(imgs=>Promise.all(imgs.map(i=>i.decode())));
   await page.screenshot({path:`${output}/${name}-shortcut.png`,scale:'css'});
   const expected=await page.evaluate(()=>suggestion());
   await page.locator('#scene').focus();await page.keyboard.press('n');
   await page.waitForFunction(()=>!busy&&payload.steps===1);
   assert.deepEqual(await page.evaluate(()=>payload.journal[0].action),expected);
   await page.evaluate(()=>$('scene').dispatchEvent(new KeyboardEvent('keydown',{key:'n',repeat:true,bubbles:true})));
   assert.equal(await page.evaluate(()=>payload.steps),1,'Held N must not execute more actions');
   await page.locator('#game-menu').click();await page.keyboard.press('n');assert.equal(await page.evaluate(()=>payload.steps),1);
   await page.locator('#menu-new').click();await page.locator('#state-input').fill(monster+'# note: ');await page.locator('#state-input').press('n');
   assert.equal(await page.evaluate(()=>payload.steps),1,'Typing in settings must not play');
   await page.locator('#close-setup').click();
   await page.route('**/api/stone-advice',async route=>{
    const current=await page.evaluate(()=>payload);
    current.stone_advice={...current.stone_advice,status:'unavailable',message:'Test: temporary comparison failure'};
    await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(current)});
   });
   await load(stones);
   assert.ok(await page.locator('#retry-stone-advice').isVisible());
   assert.ok(await page.locator('#retry-stone-advice').evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));}));
   await page.screenshot({path:`${output}/${name}-retry.png`,scale:'css'});
   await page.unroute('**/api/stone-advice');await page.locator('#retry-stone-advice').click();
   await page.waitForFunction(()=>!busy&&payload.stone_advice?.status==='ready',null,{timeout:60000});
   assert.equal(await page.evaluate(()=>payload.steps),0);assert.equal(await page.evaluate(()=>obs().hp),.01);
   assert.equal(await page.locator('.stone-simulation-rating').count(),3);
   assert.ok(await page.locator('#error').isHidden());
   assert.equal(await page.locator('.gem-offer img').evaluateAll(imgs=>imgs.some(i=>i.draggable)),false);
   await page.screenshot({path:`${output}/${name}-stones.png`,scale:'css'});
   const chosen=await page.evaluate(()=>payload.stone_advice.estimated_best);
   await page.locator('#scene').focus();await page.keyboard.press('N');await page.waitForFunction(()=>!busy&&payload.steps===1);
   assert.equal(await page.evaluate(()=>payload.journal[0].gem),chosen);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert.deepEqual(errors,[]);
   await page.evaluate(()=>api('close',{session}));
   results.push({name,engine:engine.name(),emulated:name!=='windows-chromium',hotkey:true,noRepeat:true,noTypingConflict:true,noImageDrag:true,stoneComparison:true,retryWithoutStateLoss:true,errors});
   console.log(`${name}: N, image dragging and stone comparison passed`);
  }finally{await browser.close()}
 }
 fs.writeFileSync(`${output}/browser-check.json`,JSON.stringify({passed:true,profiles:results,limitation:'Mac/iPhone Safari are WebKit on Windows; Android/iPhone use emulated profiles. Actual device/font behavior remains unchecked.'},null,2));
})().catch(e=>{console.error(e);process.exit(1)});
