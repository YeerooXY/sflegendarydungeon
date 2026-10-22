// Checks the painted doorway bounds and camera transform, including the held
// final frame. Run against the local viewer; no live game connection is used.
const {chromium}=require(process.env.SFLD_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.SFLD_LAB_URL||'http://127.0.0.1:8791');
  await page.waitForSelector('#hud-health-number');
  for(const mode of ['desktop','fullscreen','mobile']){
   await page.setViewportSize(mode==='mobile'?{width:390,height:844}:{width:1440,height:1000});
   await page.evaluate(mode=>document.body.classList.toggle('game-view',mode==='fullscreen'),mode);
   for(const pair of ['golden,locked','locked,golden','golden,golden']){
    await page.evaluate(pair=>replaceSession({seed:5,budget:0,initial_state:`schema=1\nroom=10\nrun_number=1\nphase=doors\nhp_percent=90\nkeys=3\ndoors=${pair}\n`}),pair);
    await page.locator('.door-art img').evaluateAll(nodes=>Promise.all(nodes.map(n=>n.decode())));
    const doors=await page.locator('.door-choice').evaluateAll(nodes=>nodes.map(n=>{
     const b=n.getBoundingClientRect(),frame=n.querySelector('.door-art').getBoundingClientRect(),im=n.querySelector('.door-art img').getBoundingClientRect();
     return {kind:n.dataset.doorType,width:b.width,height:b.height,frameWidth:frame.width,
             portal:n.dataset.doorType==='golden'?{left:im.left+im.width*70/1413-frame.left,
               top:im.top+im.height*565/1938-frame.top,width:im.width*417/1413,height:im.height*870/1938}:null};
    }));
    assert.ok(Math.abs(doors[0].width-doors[1].width)<1,`${mode}: unequal door widths`);
    for(const d of doors.filter(d=>d.kind==='golden')){
     assert.ok(Math.abs(d.portal.width-d.width)<1&&Math.abs(d.portal.height-d.height)<1,`${mode}: golden portal shrank into its halo`);
     assert.ok(Math.abs(d.portal.left)<1&&Math.abs(d.portal.top)<1,`${mode}: golden portal is offset`);
    }
   }
   await page.screenshot({path:`out/visual-doors-${mode}-2026-10-12.png`});
   const zoom=await page.evaluate(async()=>{
    const world=document.getElementById('scene-world'),content=document.getElementById('scene-content');
    const b=world.getBoundingClientRect(),hud=document.getElementById('hud-health').getBoundingClientRect();
    const task=DungeonPresentation.play({before:obs(),after:obs(),journal:{outcomes:[]},action:{kind:'choose_door',index:1},assets});
    const animation=world.getAnimations()[0];animation.pause();
    const samples=[];
    for(const t of [0,65,130,195,260]){
     animation.currentTime=t;
     const w=world.getBoundingClientRect(),d=content.querySelector('.door-choice').getBoundingClientRect(),h=document.getElementById('hud-health').getBoundingClientRect();
     samples.push({scale:w.width/b.width,opacity:getComputedStyle(world).opacity,contentOpacity:getComputedStyle(content).opacity,
       covers:w.left<=b.left+.01&&w.right>=b.right-.01&&w.top<=b.top+.01&&w.bottom>=b.bottom-.01,
       doorWidth:d.width,hudWidth:h.width});
    }
    animation.finish();await task;
    const held=world.getBoundingClientRect().width/b.width;
    render();return {samples,held,reset:world.getBoundingClientRect().width/b.width,hudWidth:hud.width};
   });
   for(let i=0;i<zoom.samples.length;i++){
    const s=zoom.samples[i];assert.ok(s.covers,'Camera exposes an edge behind the scene');
    assert.equal(s.opacity,'1');assert.equal(s.contentOpacity,'1');assert.equal(s.hudWidth,zoom.hudWidth);
    if(i)assert.ok(s.doorWidth>=zoom.samples[i-1].doorWidth,'Door shrinks during forward zoom');
   }
   assert.ok(Math.abs(zoom.held-1.22)<.001,'Zoom snaps back before the next render');assert.equal(zoom.reset,1);
  }
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.evaluate(async()=>{await DungeonPresentation.play({before:obs(),after:obs(),journal:{outcomes:[]},action:{kind:'choose_door',index:0},assets});return document.getElementById('scene-world').getAnimations().length;}),0);
  assert.deepEqual(errors,[]);console.log('Passed full-width golden portals and opaque, monotonic, held camera zoom on desktop, fullscreen, mobile and reduced motion.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
