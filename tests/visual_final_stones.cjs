// End-to-end final-stone advice in the real browser and native simulator.
const {chromium}=require(process.env.SFLD_PLAYWRIGHT_MODULE||'../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const state=fs.readFileSync(path.join(root,'examples/final-stone-moonstone-thirsty.state'),'utf8');
const configuration={initial_state:state,budget:5,run_number:2,event_start_hour:17,deadline_hours:168,stone_advice_samples:512};
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.SFLD_BROWSER_CHANNEL||'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  let comparisons=0;
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(r.url().endsWith('/api/stone-advice'))comparisons++;});
  await page.goto(process.env.SFLD_LAB_URL||'http://127.0.0.1:8787');
  await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
  await page.evaluate(config=>replaceSession(config),configuration);
  const before=await page.evaluate(()=>({o:obs(),advice:payload.stone_advice,steps:payload.steps}));
  assert.equal(before.advice.status,'ready');assert.equal(before.advice.estimated_best,'deceit');
  assert.equal(before.o.hp,.01);assert.equal(before.steps,0);assert.equal(before.o.elapsed_hours,0);
  assert.equal(await page.locator('.stone-simulation-rating').count(),3);
  assert.equal(await page.locator('.gem-offer.recommended').getAttribute('data-index'),String(before.advice.action.index));
  assert.equal(await page.locator('.gem-offer.selected').getAttribute('data-index'),String(before.advice.action.index));
  await page.evaluate(()=>document.body.classList.add('game-view','native-fullscreen'));
  await page.screenshot({path:path.join(root,'out/connected-final-stones.png'),fullPage:true});
  await page.evaluate(()=>document.body.classList.remove('game-view','native-fullscreen'));
  await page.locator('#step-policy').click();
  await page.waitForFunction(()=>!busy&&obs().phase!=='gems');
  assert.ok(await page.evaluate(()=>obs().gems.includes('deceit')));
  const journalAdvice=await page.evaluate(()=>payload.journal.at(-1).stone_advice);
  assert.equal(journalAdvice.action.index,before.advice.action.index);
  await page.evaluate(()=>document.body.classList.remove('game-view','native-fullscreen'));
  const used=comparisons;await page.locator('#undo').click();await page.waitForFunction(()=>!busy&&obs().phase==='gems');
  assert.equal(comparisons,used,'Undo should reuse the unchanged-state result');
  const record=await page.evaluate(()=>api('export',{session}));
  await page.evaluate(record=>replaceSession(record.settings,record),record);
  assert.equal(comparisons,used+1,'Imported state receives a fresh trusted calculation');
  assert.deepEqual(await page.evaluate(()=>payload.stone_advice.rankings),before.advice.rankings);
  // Manual choice can override the suggested index, using the existing keys.
  await page.locator('#scene').focus();await page.keyboard.press('ArrowRight');await page.keyboard.press('Enter');
  await page.waitForFunction(()=>!busy&&obs().phase!=='gems');
  assert.ok(await page.evaluate(()=>obs().gems.includes('rusty')));

  // Autoplay must stop at the final choice until its request resolves.
  const boss=state.replace('room=76','room=75').replace('phase=gems','phase=encounter')
    .replace('hp_percent=1','hp_percent=100').replace('gem_offers=deceit,rusty,pendant','encounter=boss');
  await page.evaluate(config=>replaceSession(config),{...configuration,initial_state:boss});
  let release;const gate=new Promise(resolve=>release=resolve);let waiting=false;
  await page.route('**/api/stone-advice',async route=>{waiting=true;await gate;await route.continue();});
  await page.evaluate(()=>{playing=true;void act(legal('fight'));});
  for(let i=0;i<100&&!waiting;i++)await page.waitForTimeout(50);
  assert.equal(waiting,true);assert.equal(await page.evaluate(()=>obs().phase),'gems');
  assert.equal(await page.evaluate(()=>payload.steps),1);
  assert.equal(await page.evaluate(()=>busy),true);
  assert.match(await page.locator('.stone-rating-note').innerText(),/Comparing your three stones/);
  assert.equal(await page.locator('#fullscreen-play').isDisabled(),false,'Pause remains available while calculating');
  assert.equal(await page.locator('.gem-offer:disabled').count(),3);
  release();await page.waitForFunction(()=>payload.journal.some(j=>j.action.kind==='choose_gem'),null,{timeout:60000});
  await page.evaluate(()=>stop());await page.waitForFunction(()=>!busy);
  const chosen=await page.evaluate(()=>payload.journal.find(j=>j.action.kind==='choose_gem'));
  assert.equal(chosen.gem,chosen.stone_advice.estimated_best);
  await page.unroute('**/api/stone-advice');

  // A comparison failure must not silently resume using the old heuristic.
  await page.route('**/api/stone-advice',async route=>{
   const current=await page.evaluate(()=>payload);
   current.stone_advice={status:'unavailable',message:'Test: comparison unavailable'};
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(current)});
  });
  await page.evaluate(config=>replaceSession(config),configuration);
  assert.equal(await page.evaluate(()=>playing),false);
  assert.equal(await page.locator('#autoplay').isDisabled(),true);
  assert.equal(await page.locator('.gem-offer:not(:disabled)').count(),3);
  assert.equal(await page.locator('#retry-stone-advice').isVisible(),true);
  await page.unroute('**/api/stone-advice');
  await page.locator('#retry-stone-advice').click();
  await page.waitForFunction(()=>!busy&&payload.stone_advice.status==='ready');
  assert.equal(await page.evaluate(()=>payload.steps),0,'Retry must preserve the current run');
  await page.locator('.gem-offer[data-index="2"]').click();
  await page.waitForFunction(()=>!busy&&obs().phase!=='gems');
  assert.ok(await page.evaluate(()=>obs().gems.includes('pendant')));
  await page.evaluate(()=>api('close',{session}));
  await page.goto((process.env.SFLD_LAB_URL||'http://127.0.0.1:8787')+'/?scene=final-stones');
  await page.waitForFunction(()=>typeof payload!=='undefined'&&payload?.stone_advice?.status==='ready'&&!busy,null,{timeout:60000});
  const defaultResult=await page.evaluate(()=>({advice:payload.stone_advice,hp:obs().hp,keys:obs().keys,elapsed:obs().elapsed_hours,spent:obs().mushrooms_spent,steps:payload.steps}));
  assert.equal(defaultResult.advice.samples_per_offer,2048);
  assert.equal(defaultResult.advice.trajectories,6144);
  assert.equal(defaultResult.advice.estimated_best,'deceit');
  assert.deepEqual([defaultResult.hp,defaultResult.keys,defaultResult.elapsed,defaultResult.spent,defaultResult.steps],[.01,3,0,0,0]);
  await page.evaluate(()=>document.body.classList.add('game-view','native-fullscreen'));
  await page.screenshot({path:path.join(root,'out/connected-final-stones-default.png'),fullPage:true});
  fs.writeFileSync(path.join(root,'out/connected-final-stones-check.json'),JSON.stringify(defaultResult,null,2)+'\n');
  assert.deepEqual(errors,[]);
  await page.evaluate(()=>api('close',{session}));
  console.log('Connected adviser passed: exact state, visible rankings, manual override, cache/undo, trusted replay, autoplay waits and uses result, failure stops autoplay.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
