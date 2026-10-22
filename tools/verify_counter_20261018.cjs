const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const out=path.resolve(__dirname,'../out/lockpick-poison-2026-10-18');
const url=process.env.SFLD_LAB_URL||'http://127.0.0.1:49409/?desktop=1';
const profiles=[['windows',chromium,{viewport:{width:1440,height:1000}}],['mac',webkit,{viewport:{width:1440,height:1000}}],['android',chromium,devices['Pixel 7']],['iphone',webkit,devices['iPhone 13']]];
const report=[];
async function geometry(page){return page.evaluate(()=>{
 const rect=s=>{const b=document.querySelector(s).getBoundingClientRect();return {left:b.left,right:b.right,top:b.top,bottom:b.bottom,width:b.width,height:b.height}};
 return {scene:rect('#scene-hud'),frame:rect('.hud-stone-frame'),number:rect('#hud-room-counter'),health:rect('#hud-health'),font:getComputedStyle(document.querySelector('#hud-room-counter')).fontFamily,overflow:document.documentElement.scrollWidth>innerWidth+1};
});}
function verify(b){
 assert.ok(Math.abs(b.scene.left-b.frame.left)<1,'Frame is flush left');
 assert.ok(Math.abs(b.scene.bottom-b.frame.bottom)<1,'Frame is flush bottom');
 assert.ok(b.number.left>=b.frame.left&&b.number.right<=b.frame.left+b.frame.width*.74,'Whole number is on the black base: '+JSON.stringify(b));
 assert.ok(b.number.top>=b.frame.top+b.frame.height*.8&&b.number.bottom<=b.frame.bottom,'Number fits vertically inside artwork');
 assert.ok(b.frame.right<b.health.left,'Frame does not overlap health');
 assert.ok(!b.overflow,'No horizontal overflow');
}
async function load(page,room,phase='encounter'){
 let state=`schema=1\nroom=${room}\nrun_number=3\nphase=${phase}\nhp_percent=60\nkeys=12\ngems=moonstone,greed,spying\n`;
 if(phase==='encounter')state+='encounter=boss\n';
 if(phase==='shop')state+='offers=one_hit:strong:8:4,recovery:weak:3:1\n';
 if(phase==='recovery')state+='resume_phase=encounter\nencounter=boss\n';
 await page.evaluate(state=>replaceSession({...config,initial_state:state,run_number:3,neural_adviser:false,final_stone_adviser:false}),state);
 await page.evaluate(()=>document.fonts.ready);
 await page.waitForFunction(()=>Array.from(document.querySelectorAll('.hud-stone-cluster img')).every(i=>i.complete&&i.naturalWidth>0));
}
(async()=>{
 for(const [name,type,options] of profiles){
  const browser=await type.launch();const page=await browser.newPage({...options,reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  try{
   await page.goto(url);await page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy);
   const opening=await geometry(page);verify(opening);assert.equal(await page.locator('#hud-room-counter').innerText(),'1/100');
   await load(page,100);const boss=await geometry(page);await page.screenshot({path:path.join(out,`counter-${name}.png`)});verify(boss);
   assert.equal(await page.locator('#hud-room-counter').innerText(),'100/100');
   for(let slot=0;slot<3;slot++){
    await page.locator(`.hud-stone[data-stone="${slot}"]`).click();assert.ok(await page.locator('#effect-inspector').isVisible());
    await page.locator('#effect-inspector button').click();
   }
   await page.screenshot({path:path.join(out,`counter-${name}.png`)});
   const phases=[];
   for(const phase of ['shop','recovery']){
    await load(page,phase==='shop'?82:100,phase);const b=await geometry(page);verify(b);phases.push({phase,...b});
   }
   if(name==='windows'){
    for(const [width,height] of [[2048,1152],[900,650],[320,900]]){
     await page.setViewportSize({width,height});verify(await geometry(page));
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.evaluate(()=>document.body.classList.remove('game-view'));verify(await geometry(page));
   }
   assert.deepEqual(errors,[]);
   report.push({profile:name,engine:type.name(),coverage:name==='windows'?'Windows desktop Chromium':'Emulated on Windows; real OS fonts and device behavior unverified',opening,boss,phases,stoneButtons:3,errors});
  }finally{await page.evaluate(()=>api('close',{session})).catch(()=>{});await browser.close();}
 }
 fs.writeFileSync(path.join(out,'counter-verification.json'),JSON.stringify({passed:true,profiles:report},null,2));
 console.log('Corner frame, 1/100 and 100/100, all three stone buttons, shop/recovery and resizing passed in all four profiles.');
})().catch(e=>{console.error(e);process.exit(1)});
