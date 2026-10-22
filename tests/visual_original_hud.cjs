const {chromium}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const output='out/hud-layout-2026-10-16';fs.mkdirSync(output,{recursive:true});
const doors='schema=1\nroom=60\nrun_number=2\nphase=doors\nhp_percent=60\nkeys=4\ngems=rabbit,moonstone\nshops_seen=2\ndoors=monster,mystery\n';
const shop=doors.replace('phase=doors','phase=shop').replace('doors=monster,mystery','offers=one_hit:strong:8:4,recovery:weak:3:1');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:850}}),errors=[],external=new Set();
  page.on('pageerror',e=>errors.push(e.message));
  const origin=process.env.SFLD_LAB_URL||'http://127.0.0.1:8796';
  page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('data:'))external.add(new URL(r.url()).origin)});
  await page.goto(origin+'/?desktop=1');await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
  const load=async(state,extra={})=>{await page.evaluate(async({state,extra})=>{await replaceSession({...config,initial_state:state,run_number:2,budget:100,wallet_mushrooms:600,...extra});$('speed').value='450';}, {state,extra});};
  await load(doors);
  for(const [i,file] of [[0,'cursor-arrow.png'],[1,'cursor-arrow-left.png']]){
   await page.locator(`.door-choice[data-index="${i}"]`).hover();
   assert.match(await page.locator('#scene').evaluate(n=>n.style.cursor),new RegExp(file.replaceAll('.','\\.')));
  }
  assert.equal(await page.locator('.door-label').count(),0);
  assert.equal(await page.locator('#hud-room-counter').innerText(),'60/100');
  await load(shop);
  await page.locator('#game-menu').click();await page.locator('#menu-new').click();
  await page.locator('#wallet-input').fill('50');await page.locator('#setup-form button[type=submit]').click();
  await page.waitForFunction(()=>$('setup-policy').textContent.includes('balance must be at least'));
  assert.match(await page.locator('#setup-policy').innerText(),/balance must be at least/);
  assert.equal(await page.locator('#wallet-input').inputValue(),'50');
  await page.locator('#close-setup').click();
  await page.evaluate(()=>act(legal('reroll'),false));await page.waitForFunction(()=>!busy);
  assert.match(await page.locator('#hud-wallet').innerText(),/599/);
  assert.match(await page.locator('#hud-budget').innerText(),/100/);
  assert.match(await page.locator('#hud-spent').innerText(),/1/);
  const record=await page.evaluate(()=>api('export',{session}));
  await page.evaluate(record=>replaceSession(record.settings,record),record);
  assert.equal(await page.evaluate(()=>config.wallet_mushrooms),600);
  assert.match(await page.locator('#hud-wallet').innerText(),/599/);
  const mimic=doors.replace('phase=doors','phase=encounter\nencounter=mimic').replace('doors=monster,mystery','blessings=one_hit:weak:1');
  await load(mimic);assert.equal(await page.evaluate(()=>suggestion().kind),'fight');
  assert.equal(await page.locator('#scene-advice').isVisible(),false);
  assert.doesNotMatch(await page.locator('#actions').innerText(),/combat curse|no combat damage/);
  await page.locator('#game-step').click();await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>obs().epics),1);
  assert.equal(await page.evaluate(()=>obs().hp),.6);
  await load(doors.replace('doors=monster,mystery','doors=cursed,monster:trap'));
  assert.equal(await page.evaluate(()=>suggestion().index),1);
  const samples=[['doors',doors],['shop',shop],['mimic',mimic]];
  for(const [width,height] of [[1440,850],[820,620],[390,844]]){
   await page.setViewportSize({width,height});
   for(const [name,state] of samples){
    await load(state);await page.waitForTimeout(60);
    const room=await page.locator('#hud-room-counter').boundingBox(),hp=await page.locator('#hud-health').boundingBox(),keys=await page.locator('.hud-keys').boundingBox();
    assert.ok(room.x<width*.2&&room.y>height*.65,'Room counter belongs at bottom left');
    assert.ok(keys.x>width*.65&&keys.y>height*.6,'Keys belong at bottom right');
    assert.ok(Math.abs(hp.x+hp.width/2-width/2)<3,'Health bar centered');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    if(name!=='doors'){
     const controls=await page.locator('.scene-controls').boundingBox(),stones=await page.locator('.hud-stone-cluster').boundingBox();
     if(!(controls.y+controls.height<=stones.y||controls.x>=stones.x+stones.width))await page.screenshot({path:`${output}/overlap-${name}-${width}.png`});
     assert.ok(controls.y+controls.height<=stones.y||controls.x>=stones.x+stones.width,`Controls overlap the stone HUD: ${name} ${width}, ${JSON.stringify({controls,stones})}`);
    }
    await page.screenshot({path:`${output}/${name}-${width}.png`});
   }
  }
  assert.deepEqual(errors,[]);assert.deepEqual([...external],[]);
  fs.writeFileSync(output+'/browser-check.json',JSON.stringify({passed:true,originalHudPositions:true,cursorDirections:true,walletAndBudgetSeparate:true,replay:true,formErrorPreservesInput:true,mimicOneHit:true,cursedDoorAvoidance:true,widths:[1440,820,390],pageErrors:errors,externalRequests:[...external]},null,2));
  await page.evaluate(()=>api('close',{session}));
  console.log('Original HUD, cursors, balance/replay, mimic and cursed-door decisions passed.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
