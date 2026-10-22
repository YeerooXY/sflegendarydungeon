const {chromium}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const output='out/arena-desktop-2026-10-16';
const base='schema=1\nroom=51\nrun_number=2\nphase=doors\nhp_percent=60\nkeys=4\ngems=rabbit,moonstone\nshops_seen=2\ndoors=trial,monster\n';
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:850}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(process.env.SFLD_LAB_URL||'http://127.0.0.1:8795/?desktop=1');
  await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
  await page.evaluate(()=>{$('speed').value='450';});
  assert.equal(await page.locator('.run-summary').isVisible(),false);
  const scene=await page.locator('#scene').boundingBox();assert.equal(scene.height,850);
  await page.locator('#game-step').click();await page.waitForFunction(()=>payload.steps===1&&!busy);
  await page.locator('#game-undo').click();await page.waitForFunction(()=>payload.steps===0&&!busy);
  await page.keyboard.press('n');await page.waitForFunction(()=>payload.steps===1&&!busy);
  await page.locator('#game-menu').click();assert.equal(await page.evaluate(()=>playing),false);
  await page.locator('#menu-speed').selectOption('2400');assert.equal(await page.locator('#speed').inputValue(),'2400');
  await page.locator('#menu-journal').click();assert.equal(await page.locator('#play-journal').isVisible(),true);
  assert.ok(await page.locator('#play-journal-entries li').count()>0);
  await page.keyboard.press('Escape');assert.equal(await page.locator('#play-journal').isVisible(),false);
  await page.evaluate(()=>{$('speed').value='450';});
  await page.evaluate(state=>replaceSession({...config,initial_state:state,run_number:2,budget:100}),base);
  assert.equal(await page.evaluate(()=>suggestion().index),0);
  await page.screenshot({path:output+'/arena-recommendation.png'});
  await page.locator('#game-play').click();
  await page.waitForFunction(()=>obs().trial_depth===5,{},{timeout:30000});
  await page.evaluate(()=>stop());await page.waitForFunction(()=>!busy);
  const trail=await page.evaluate(()=>payload.journal);
  assert.equal(trail.filter(j=>j.action.kind==='flee').length,5);
  assert.equal(trail.filter(j=>j.death).length,0);
  assert.equal(await page.evaluate(()=>obs().hp),.6);
  for(let i=0;i<3&&await page.evaluate(()=>obs().room<57);i++){
   await page.locator('#game-step').click();await page.waitForFunction(()=>!busy);
  }
  assert.equal(await page.evaluate(()=>obs().room),57);
  assert.equal(await page.evaluate(()=>payload.journal.at(-1).action.kind),'interact');
  await page.locator('#game-menu').click();
  const downloadPromise=page.waitForEvent('download');await page.locator('#menu-save').click();
  const download=await downloadPromise;await download.saveAs(output+'/arena-replay.json');
  const record=JSON.parse(fs.readFileSync(output+'/arena-replay.json','utf8'));
  await page.evaluate(record=>replaceSession(record.settings,record),record);
  assert.equal(await page.evaluate(()=>obs().room),57);
  const shop=base.replace('room=51','room=60').replace('phase=doors','phase=shop').replace('doors=trial,monster','blessings=recovery:weak:1\noffers=recovery:weak:3:1,one_hit:strong:8:4');
  await page.evaluate(state=>replaceSession({...config,initial_state:state,budget:100}),shop.replace('hp_percent=60','hp_percent=95').replace('keys=4','keys=1'));
  assert.equal(await page.evaluate(()=>suggestion().kind),'skip');
  assert.match(await page.locator('.offer-analysis').first().innerText(),/save your last key/);
  await page.screenshot({path:output+'/last-key-shop.png'});
  const gems='schema=1\nroom=26\nrun_number=2\nphase=gems\nhp_percent=70\nkeys=4\ngem_offers=rabbit,greed,greasy\n';
  for(const [width,height] of [[1440,850],[820,620],[390,844]]){
   await page.setViewportSize({width,height});
   for(const [name,state] of [['doors',base],['shop',shop],['stones',gems]]){
    await page.evaluate(state=>replaceSession({...config,initial_state:state,run_number:2,budget:100}),state);
    assert.equal(await page.locator('#game-menu').isVisible(),true);
    const boxes=await page.locator('.game-controls,.hud-location,.hud-currency').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom}}));
    const a=boxes[0];for(const b of boxes.slice(1))assert.ok(a.right<=b.left||a.left>=b.right||a.bottom<=b.top||a.top>=b.bottom,`HUD controls overlap: ${width} ${name} ${JSON.stringify(boxes)}`);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Horizontal overflow');
    if(width<581){const hp=await page.locator('#hud-health').boundingBox();assert.ok(hp.width>=width*.8&&hp.x>=0&&hp.x+hp.width<=width,'Mobile health bar clipped');}
    await page.screenshot({path:`${output}/${name}-${width}.png`});
    await page.locator('#game-menu').click();await page.keyboard.press('Escape');
   }
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.keyboard.press('p');await page.waitForFunction(()=>payload.steps>0);
  await page.keyboard.press('p');await page.waitForFunction(()=>!playing&&!busy);
  assert.deepEqual(errors,[]);
  fs.writeFileSync(output+'/browser-check.json',JSON.stringify({passed:true,arenaFights:5,damage:0,prizeCollected:true,replay:true,menu:true,keyboard:true,responsiveWidths:[1440,820,390],pageErrors:errors},null,2));
  await page.evaluate(()=>api('close',{session}));
  console.log('Play shell: arena autoplay, prize, undo, keyboard, menu, replay and responsive checks passed.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1)});
