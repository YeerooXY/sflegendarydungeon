// Uses the real loopback API and engine. Apple/mobile profiles are emulated.
const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs');
const output='out/action-feedback-2026-10-17';fs.mkdirSync(output,{recursive:true});
const origin=process.env.SFLD_LAB_URL||'http://127.0.0.1:8797';
const combat='schema=1\nroom=60\nrun_number=2\nphase=encounter\nencounter=monster\nhp_percent=15\nkeys=7\ngems=hero,gambler\n';
const recovery='schema=1\nroom=100\nrun_number=2\nphase=recovery\nresume_phase=encounter\nencounter=boss\nhp_percent=0\nkeys=2\ngems=rabbit,moonstone,greed\n';
const profiles=[
 ['windows-chromium',chromium,{viewport:{width:1440,height:900}},{channel:'msedge'}],
 ['mac-safari-emulated',webkit,{...devices['Desktop Safari'],viewport:{width:1440,height:900}},{}],
 ['android-chrome-emulated',chromium,{...devices['Pixel 7']},{channel:'msedge'}],
 ['iphone-safari-emulated',webkit,{...devices['iPhone 13']},{}],
];
const luminance=hex=>hex.match(/\w\w/g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4).reduce((n,x,i)=>n+x*[.2126,.7152,.0722][i],0);
const contrast=(fg,bg)=>(Math.max(luminance(fg),luminance(bg))+.05)/(Math.min(luminance(fg),luminance(bg))+.05);
(async()=>{
 const results=[];
 for(const [name,engine,device,launch] of profiles){
  const browser=await engine.launch({headless:true,...launch});
  try{
   const context=await browser.newContext({...device,locale:'en-GB',timezoneId:'Europe/Budapest'});
   const page=await context.newPage(),errors=[],external=new Set();
   page.on('pageerror',e=>errors.push(e.message));
   page.on('request',r=>{if(!r.url().startsWith(origin)&&!r.url().startsWith('data:'))external.add(new URL(r.url()).origin)});
   await page.goto(origin+'/?desktop=1');await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
   const load=async(state,extra={})=>{
    await page.evaluate(async({state,extra})=>{await replaceSession({...config,seed:4137,run_number:2,budget:100,wallet_mushrooms:600,sleep_until_hour:0,initial_state:state,...extra});$('speed').value='450';}, {state,extra});
    await page.locator('#scene img').evaluateAll(nodes=>Promise.all(nodes.map(n=>n.decode())));await page.evaluate(()=>document.fonts.ready);
   };
   const shot=label=>page.screenshot({path:`${output}/${name}-${label}.png`,scale:'css'});
   const hit=locator=>locator.evaluate(n=>{const r=n.getBoundingClientRect();return n.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2));});
   await load(combat);
   assert.equal(await page.locator('.hud-currency .mushroom-icon').count(),3);
   assert.match(await page.locator('.hud-currency .mushroom-icon').first().getAttribute('src'),/resources-resource_mushroom_icon/);
   assert.match(await page.locator('#hud-loot .backpack-icon').getAttribute('src'),/resources-backpack_icon/);
   const forecast=await page.evaluate(()=>obs().combat_forecast);
   assert.equal(forecast.scope,'hit_only');assert.ok(forecast.fight.survival>0&&forecast.fight.survival<1);
   assert.match(await page.locator('#actions [data-kind=fight]').innerText(),/Max hit .*Est\. .* survive hit/);
   assert.match(await page.locator('#actions [data-kind=flee]').innerText(),/Max failed hit .*survive attempt/);
   assert.match(await page.locator('#actions .suggested').evaluate(n=>getComputedStyle(n).backgroundImage),/36, 99, 62/);
   assert.match(await page.locator('#actions button:not(.suggested)').evaluate(n=>getComputedStyle(n).backgroundImage),/121, 61, 53/);
   assert.ok(await hit(page.locator('#actions [data-kind=fight]')));assert.ok(await hit(page.locator('#actions [data-kind=flee]')));
   await shot('combat');
   await load(combat.replace('hero,gambler','rabbit,moonstone')+'blessings=one_hit:strong:8\n');
   assert.match(await page.locator('#actions [data-kind=fight]').innerText(),/Max hit 0% HP/);
   assert.match(await page.locator('#actions [data-kind=flee]').innerText(),/100% escape · No hit/);
   await load(recovery);
   const stateBefore=await page.evaluate(()=>({steps:payload.steps,hours:obs().elapsed_hours}));
   const targets=page.locator('.recovery-targets button');
   assert.ok(await targets.count()>=2);assert.equal(await page.locator('#actions [data-kind=wait]').count(),1);
   await shot('recovery');
   assert.ok(await hit(page.locator('#wait-selected')));assert.ok(await hit(page.locator('.recovery-tools summary')));
   const summaryBox=await page.locator('.recovery-plan').boundingBox(),waitBox=await page.locator('#wait-selected').boundingBox();
   assert.ok(summaryBox.y+summaryBox.height<=waitBox.y,'Wait must not cover recovery summary');
   await targets.first().click();
   assert.deepEqual(await page.evaluate(()=>({steps:payload.steps,hours:obs().elapsed_hours})),stateBefore,'Selecting a target must not spend time');
   await page.keyboard.press('End');
   assert.equal(await targets.last().getAttribute('aria-pressed'),'true');
   assert.deepEqual(await page.evaluate(()=>({steps:payload.steps,hours:obs().elapsed_hours})),stateBefore,'Arrow/Home/End must not reenter');
   await targets.last().hover();assert.equal(await targets.last().evaluate(n=>getComputedStyle(n).cursor),'default');
   assert.doesNotMatch(await page.locator('#scene').evaluate(n=>n.style.cursor),/url/);
   const expected=await page.evaluate(()=>{const hours=recoveryWaitHours(obs(),selectedRecoveryWait);return {hours,hp:Math.min(1,obs().hp+hours/obs().scenario.recovery_hours)};});
   await page.locator('#wait-selected').focus();await page.keyboard.press('Space');
   await page.waitForFunction(steps=>!busy&&payload.steps>steps,stateBefore.steps);
   assert.ok(Math.abs(await page.evaluate(()=>obs().elapsed_hours)-stateBefore.hours-expected.hours)<1e-8);
   assert.ok(Math.abs(await page.evaluate(()=>obs().hp)-expected.hp)<1e-8);
   assert.equal(await page.evaluate(()=>obs().mushrooms_spent),0);
   assert.ok(await page.locator('#actions [data-kind=reenter]').isVisible());
   await shot('ready');
   await load(recovery);
   await page.locator('.recovery-tools summary').focus();await page.keyboard.press('Space');
   assert.equal(await page.locator('.recovery-tools').evaluate(n=>n.open),true);
   await page.locator('#refills [data-kind=heal_step]').scrollIntoViewIfNeeded();
   assert.ok(await hit(page.locator('#refills [data-kind=heal_step]')));
   await shot('refills');
   await page.locator('#refills [data-kind=heal_step]').click();await page.waitForFunction(()=>!busy&&obs().mushrooms_spent===10);
   assert.equal(await page.evaluate(()=>obs().hp),.2);assert.equal(await page.evaluate(()=>obs().heal_step_price),15);
   assert.match(await page.locator('#hud-wallet').innerText(),/590/);
   await load(recovery,{budget:0});
   if(!await page.locator('.recovery-tools').evaluate(n=>n.open))await page.locator('.recovery-tools summary').click();
   assert.match(await page.locator('#refill-note').innerText(),/Not enough mushrooms/);
   assert.equal(await page.locator('#refills button').count(),0);
   const ordinary=recovery.replace('room=100','room=60').replace('resume_phase=encounter\nencounter=boss','resume_phase=doors\ndoors=monster,mystery').replace('rabbit,moonstone,greed','greasy,rusty');
   await load(ordinary);await shot('ordinary-recovery');
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   await load(recovery,{sleep_until_hour:8});
   const options=await targets.allTextContents();assert.equal(new Set(options.map(t=>t.split('\n')[0])).size,options.length,'Sleep must not duplicate equivalent health targets');
   await shot('sleep-recovery');
   await load(recovery.replace('hp_percent=0','hp_percent=100'));
   assert.equal(await page.locator('#wait-selected').count(),0);assert.ok(await page.locator('#actions [data-kind=reenter]').isVisible());
   const store=await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length}));
   assert.deepEqual(store,{local:0,session:0});assert.deepEqual(await context.cookies(),[]);
   await page.evaluate(()=>api('close',{session}));assert.deepEqual(errors,[]);assert.deepEqual([...external],[]);
   results.push({profile:name,engine:engine.name(),version:browser.version(),emulated:name!=='windows-chromium',forecast,icons:true,recoverySelection:true,keyboard:true,waitExact:true,paidRefill:true,overflow:false,errors,external:[...external],store});
   console.log(`${name}: action feedback and recovery passed`);
  }finally{await browser.close()}
 }
 const colors=[['green','f0fff5','24633e'],['green-hover','f0fff5','2b7147'],['red','fff8ef','793d35'],['red-hover','fff8ef','87453a'],['recovery-secondary','c6d2d7','172e39']].map(([name,fg,bg])=>({name,ratio:contrast(fg,bg)}));
 assert.ok(colors.every(c=>c.ratio>=4.5));
 fs.writeFileSync(`${output}/browser-check.json`,JSON.stringify({passed:true,profiles:results,contrast:colors,limitation:'Mac Safari is WebKit on Windows; Android/iPhone are device-profile emulations. Physical-device behavior and Apple font rendering remain unchecked.'},null,2));
})().catch(e=>{console.error(e);process.exit(1)});
