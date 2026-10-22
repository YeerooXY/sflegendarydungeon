// Local rendering checks; Safari and mobile profiles are emulated on Windows.
const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const output='out/scene-alignment-2026-10-17';fs.mkdirSync(output,{recursive:true});
const origin=process.env.SFLD_LAB_URL||'http://127.0.0.1:8796';
const profiles=[
 ['windows-chromium',chromium,{viewport:{width:1440,height:900}}, {channel:'msedge'}],
 ['mac-safari-emulated',webkit,{...devices['Desktop Safari'],viewport:{width:1440,height:900}}, {}],
 ['android-chrome-emulated',chromium,{...devices['Pixel 7']}, {channel:'msedge'}],
 ['iphone-safari-emulated',webkit,{...devices['iPhone 13']}, {}],
];
const state=(room,pair='monster,mystery')=>{
 const gems=['rabbit','moonstone','greed'].slice(0,Math.floor((room-1)/25)).join(',');
 return `schema=1\nroom=${room}\nrun_number=2\nphase=doors\nhp_percent=60\nkeys=7\n${gems?`gems=${gems}\n`:''}doors=${pair}\nblessings=recovery:strong:6\ncurses=hard_lock:weak:4\n`;
};
(async()=>{
 const results=[];
 for(const [name,engine,device,launch] of profiles){
  const browser=await engine.launch({headless:true,...launch});
  try{
   const context=await browser.newContext({...device,locale:'en-GB',timezoneId:'Europe/Budapest'});
   const page=await context.newPage(),errors=[],external=new Set();
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('data:'))external.add(new URL(r.url()).origin)});
   await page.goto(origin+'/?desktop=1');
   await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
   const load=async(value)=>{
    await page.evaluate(value=>replaceSession({...config,seed:4137,run_number:2,budget:100,wallet_mushrooms:600,initial_state:value}),value);
    await page.locator('#scene img').evaluateAll(nodes=>Promise.all(nodes.map(n=>n.decode())));
    await page.evaluate(()=>document.fonts.ready);
   };
   const screenshot=label=>page.screenshot({path:`${output}/${name}-${label}.png`,scale:'css'});
   for(const room of [10,35,60,85]){
    await load(state(room));
    const geometry=await page.evaluate(()=>{
     const w=$('scene-world').getBoundingClientRect();
     return [...document.querySelectorAll('.door-choice')].map(d=>{
      const r=d.getBoundingClientRect();
      return {x:(r.x-w.x)/w.width,y:(r.y-w.y)/w.height,width:r.width/w.width,height:r.height/w.height,
       hit:document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.door-choice')===d};
     });
    });
    assert.equal(geometry.length,2);
    assert.ok(geometry.every(d=>d.hit&&d.width>=.19&&d.width<=.21&&d.y>.26&&d.y<.29&&d.height>.40&&d.height<.43),`${name}: doors cover the frames or cannot be clicked`);
    assert.ok(geometry[0].x>.18&&geometry[0].x<.21&&geometry[1].x>.60&&geometry[1].x<.63);
    assert.match(await page.locator('.door-choice[data-index="1"] .door-art img').getAttribute('src'),/right_unknown/);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`${name}: horizontal overflow`);
    await screenshot(`stage-${Math.ceil(room/25)}`);
   }
   await load(state(85,'monster:trap,golden'));await screenshot('trap-and-gold');
   assert.equal(await page.locator('.trap-mark').count(),1);
   const widths=await page.locator('.door-art').evaluateAll(nodes=>nodes.map(n=>n.getBoundingClientRect().width));
   assert.ok(Math.abs(widths[0]-widths[1])<1,'Gold portal must fill the same opening');
   await page.locator('#game-menu').click();
   assert.ok(await page.locator('#play-menu').isVisible());
   await screenshot('menu');await page.keyboard.press('Escape');
   await page.waitForFunction(()=>!$('play-menu').open&&document.activeElement.id==='game-menu');
   assert.equal(await page.evaluate(()=>document.activeElement.id),'game-menu');
   const shop=state(85).replace('phase=doors','phase=shop').replace('doors=monster,mystery','offers=one_hit:strong:8:4,recovery:weak:3:1');
   await load(shop);await page.keyboard.press('ArrowLeft');
   await page.waitForFunction(()=>{
    const controls=document.querySelector('.scene-controls').getBoundingClientRect();
    return [...document.querySelectorAll('.offer')].every(n=>n.getBoundingClientRect().bottom<=controls.top-5);
   });
   await screenshot('shop');
   const controls=await page.locator('.scene-controls').boundingBox(),stones=await page.locator('.hud-stone-cluster').boundingBox();
   assert.ok(controls.y+controls.height<=stones.y||controls.x>=stones.x+stones.width,`${name}: action controls overlap stones`);
   for(const offer of await page.locator('.offer').all()){
    const box=await offer.boundingBox();
    assert.ok(box.y+box.height<=controls.y-5,`${name}: shop controls cover an offer`);
    assert.ok(await offer.locator('.offer-price').evaluate(n=>{
     const r=n.getBoundingClientRect();return document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)?.closest('.offer')===n.closest('.offer');
    }),`${name}: shop price is covered`);
   }
   assert.ok(await page.locator('.offer.selected').isVisible());
   await page.keyboard.press('Enter');await page.waitForFunction(()=>!busy&&obs().phase!=='shop');
   await load(state(85));
   await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>!busy&&obs().phase!=='doors');
   await screenshot('entered-room');
   await page.evaluate(()=>api('close',{session}));
   assert.deepEqual(errors,[]);assert.deepEqual([...external],[]);
   results.push({profile:name,engine:engine.name(),browserVersion:browser.version(),viewport:device.viewport,
    emulated:name!=='windows-chromium',stages:4,doorHitTargets:true,goldenWidth:true,rightFacingArt:true,
    keyboardAndMenu:true,shopControls:true,pageErrors:errors,externalRequests:[...external]});
   console.log(`${name}: alignment and controls passed`);
  }finally{await browser.close()}
 }
 fs.writeFileSync(output+'/browser-check.json',JSON.stringify({passed:true,profiles:results,
  limitation:'Safari uses Playwright WebKit on Windows. Android/iPhone use device profiles; actual Apple fonts and physical device behavior remain unchecked.'},null,2));
})().catch(e=>{console.error(e);process.exit(1)});
