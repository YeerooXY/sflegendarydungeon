const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const output='out/decision-surface-2026-10-17',origin=process.env.SFLD_LAB_URL||'http://127.0.0.1:8799';
fs.mkdirSync(output,{recursive:true});
function state(room,extras={}){
 const defaults={schema:1,room,run_number:2,phase:'doors',hp_percent:80,keys:8,
  gems:['greed','rabbit','moonstone'].slice(0,Math.floor((room-1)/25)).join(',')||'none',
  doors:'monster,mystery'};
 Object.assign(defaults,extras);
 if(defaults.phase!=='doors')delete defaults.doors;
 return Object.entries(defaults).map(([k,v])=>`${k}=${v}\n`).join('');
}
(async()=>{
 const results=[];
 for(const [name,engine,profile,launch] of [
  ['windows-chromium',chromium,{viewport:{width:2048,height:1118}},{channel:'msedge'}],
  ['mac-safari-emulated',webkit,{...devices['Desktop Safari'],viewport:{width:1440,height:900}},{}],
  ['android-chrome-emulated',chromium,devices['Pixel 7'],{channel:'msedge'}],
  ['iphone-safari-emulated',webkit,devices['iPhone 13'],{}],
 ]){
  const browser=await engine.launch({headless:true,...launch});
  try{
   const page=await browser.newPage(profile),errors=[],external=new Set();
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('data:'))external.add(r.url())});
   await page.goto(origin+'/?desktop=1');await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
   const load=async (value,budget=100)=>{
    await page.evaluate(async ({value,budget})=>{await replaceSession({...config,initial_state:value,run_number:2,budget,stone_advice_samples:512});$('speed').value='450';},{value,budget});
    await page.locator('#scene img').evaluateAll(imgs=>Promise.all(imgs.map(i=>i.decode())));await page.evaluate(()=>document.fonts.ready);
   };
   const capture=label=>page.screenshot({path:`${output}/${name}-${label}.png`,scale:'css'});
   const checkHud=async()=>{
    assert.equal(await page.locator('#scene-hud .hud-currency,#scene-hud .hud-run').count(),0);
    const data=await page.evaluate(()=>{
     const s=$('scene').getBoundingClientRect();
     return ['.hud-stone-frame','#hud-room-counter','.hud-health','.hud-keys'].map(selector=>{
      const n=document.querySelector(selector),r=n.getBoundingClientRect();
      return {selector,inside:r.left>=s.left&&r.right<=s.right+.5&&r.top>=s.top&&r.bottom<=s.bottom+.5,rect:{x:r.x,y:r.y,width:r.width,height:r.height}};
     });
    });
    assert.ok(data.every(d=>d.inside),JSON.stringify({name,data}));
    assert.match(await page.locator('.hud-stone-frame').getAttribute('src'),/dot_gemoffate_info/);
    assert.ok(await page.evaluate(()=>{
     const counter=$('hud-room-counter').getBoundingClientRect(),effects=document.querySelector('.hud-effect-groups').getBoundingClientRect();
     return counter.right<=effects.left||counter.left>=effects.right||counter.bottom<=effects.top||counter.top>=effects.bottom;
    }), 'Room counter must not overlap effect slots or their labels');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   };
   for(const [room,pair] of [[10,'locked,monster'],[35,'cursed,mystery'],[60,'golden,destiny'],[85,'blessing,mystery']]){
    await load(state(room,{doors:pair}));await checkHud();
    const geometry=await page.locator('.door-choice').evaluateAll(nodes=>nodes.map(n=>{
     const r=n.getBoundingClientRect(),world=document.querySelector('#scene-world').getBoundingClientRect();
     return {slot:n.dataset.slot,index:+n.dataset.index,x:(r.x-world.x)/world.width,w:r.width/world.width,
      hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.door-choice')===n};
    }));
    assert.ok(geometry.every(r=>r.hit&&r.w>.19&&r.w<.21));
    assert.ok(geometry[0].x>.19&&geometry[0].x<.20&&geometry[1].x>.61&&geometry[1].x<.62);
    assert.equal(await page.locator('.door-choice.recommended').getAttribute('data-slot'),'0');
    assert.equal(await page.locator('.door-odds').count(),2);
    const right=page.locator('.door-choice[data-slot="1"] .door-art');
    const isNativeRight=(await right.locator('img').getAttribute('src')).includes('dot_right');
    assert.equal(await right.evaluate(n=>n.classList.contains('mirrored')),!isNativeRight);
    await capture('stage-'+Math.ceil(room/25));
   }
   await load(state(35,{phase:'encounter',encounter:'mimic',hp_percent:100,blessings:'recovery:weak:2,lockpick:weak:2,escape:weak:5'}));
   assert.ok(await page.locator('[data-kind="fight"]').evaluate(n=>n.classList.contains('suggested')));
   assert.match(await page.locator('[data-kind="fight"]').innerText(),/Max hit 20.57%/);
   await page.locator('[data-kind="fight"]').hover();
   assert.equal(await page.locator('[data-kind="fight"]').evaluate(n=>getComputedStyle(n).outlineStyle),'none');
   assert.ok(await page.locator('[data-kind="fight"]').evaluate(n=>getComputedStyle(n).backgroundColor.startsWith('rgb(')), 'Recommended action must remain opaque throughout hover');
   await checkHud();await capture('mimic');
   await page.locator('#scene').focus();await page.keyboard.press('n');await page.waitForFunction(()=>!busy&&payload.steps===1);
   assert.equal(await page.evaluate(()=>payload.journal[0].action.kind),'fight');
   assert.equal(await page.evaluate(()=>obs().epics),1);
   await page.locator('#game-menu').click();assert.match(await page.locator('#menu-progress').innerText(),/budget spent/);
   await capture('menu');await page.locator('#hud-loot').click();assert.ok(await page.locator('#effect-inspector').isVisible());
   await page.locator('#effect-inspector button').click();
   await load(state(35,{hp_percent:10,keys:0,doors:'monster,locked',blessings:'recovery:strong:1,lockpick:weak:2'}),0);
   assert.equal(await page.locator('.door-choice[data-slot="0"]').getAttribute('data-index'),'1');
   await capture('last-heal');await page.locator('#scene').focus();await page.keyboard.press('ArrowLeft');
   await page.waitForFunction(()=>!busy&&payload.steps===1);
   assert.equal(await page.evaluate(()=>payload.journal[0].action.index),1);assert.equal(await page.evaluate(()=>payload.journal[0].choice),'locked');
   await load(state(35,{hp_percent:10,keys:0,doors:'monster,locked',blessings:'recovery:strong:1,lockpick:weak:2'}),0);
   await page.locator('.door-choice[data-slot="1"]').click();await page.waitForFunction(()=>!busy&&payload.steps===1);
   assert.equal(await page.evaluate(()=>payload.journal[0].action.index),0);
   await load(state(82,{phase:'shop',gems:'rabbit,moonstone,greed',offers:'one_hit:strong:8:4,recovery:strong:6:3'}));
   assert.ok(await page.evaluate(()=>document.querySelector('.game-controls').getBoundingClientRect().bottom<document.querySelector('.offer-grid').getBoundingClientRect().top),'Toolbar must not cover shop offers');
   await checkHud();await capture('shop');
   await load(state(100,{phase:'recovery',resume_phase:'encounter',encounter:'boss',hp_percent:0,gems:'rabbit,moonstone,greed'}));
   await checkHud();await capture('recovery');
   assert.ok(await page.locator('#wait-selected').evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}));
   await load(state(26,{phase:'gems',gems:'none',gem_offers:'greed,gambler,time_traveler'}));
   assert.doesNotMatch(await page.locator('.stone-choice').innerText(),/Working model|Player-reported|simulations per offer|combined effect.*unverified/);
   await checkHud();await capture('stones');
   await page.evaluate(()=>api('close',{session}));assert.deepEqual(errors,[]);assert.equal(external.size,0);
   results.push({name,passed:true,stages:4,nativeRoomFrame:true,hudInsideViewport:true,recommendedLeft:true,keyboardMapping:true,
    rightArtOrientation:true,modelOdds:true,mimicFight:true,lockpickHeal:true,menuInventory:true,errors,emulated:name!=='windows-chromium'});
   console.log(name+': passed');
  }finally{await browser.close()}
 }
 fs.writeFileSync(output+'/browser-check.json',JSON.stringify({passed:true,profiles:results,
  limitation:'Safari is WebKit on Windows. Mac/Android/iPhone profiles are emulated; real device and Apple font rendering remain unchecked.'},null,2));
})().catch(e=>{console.error(e);process.exit(1)});
