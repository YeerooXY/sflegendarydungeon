// Run after tools/prepare_visual_cases.py with the loopback visual lab running.
const {chromium}=require(process.env.SFLD_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const cases=JSON.parse(fs.readFileSync(path.join(root,'out/visual-cases-2026-10-11.json'),'utf8'));
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.SFLD_BROWSER_CHANNEL||undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.SFLD_LAB_URL||'http://127.0.0.1:8787');
  await page.waitForSelector('#hud-health-number');
  assert.equal(await page.evaluate(()=>config.revive_hp),.21);
  assert.equal(await page.evaluate(()=>config.budget),100);
  assert.equal(await page.evaluate(()=>config.shop_allowance),5);
  assert.ok(await page.evaluate(()=>obs().neural_adviser),'The default server must load the trained adviser');
  assert.equal(await page.evaluate(()=>obs().neural_adviser.case),'unrestricted_budget_policy');
  await page.locator('#new-run').click();
  assert.equal(await page.locator('#shop-allowance-input').isDisabled(),true);
  assert.equal(await page.locator('#shop-key-reserve-input').isDisabled(),true);
  assert.equal(await page.locator('#budget-input').isDisabled(),false);
  await page.locator('#close-setup').click();
  assert.equal(await page.locator('#speed').inputValue(),'900');
  await page.selectOption('#speed','900');
  const capture=async name=>{await page.evaluate(()=>scrollTo(0,0));await page.screenshot({path:path.join(root,`out/visual-${name}-2026-10-11.png`)});};
  for(const c of cases){
   await page.evaluate(settings=>replaceSession(settings),c.settings);
   await page.evaluate(()=>{
    window.healthBeats=[];window.keyBeats=[];window.goldBeats=[];window.rewardBeats=[];window.receiptSeen=false;
    window.receiptObserver=new MutationObserver(()=>{if(!document.getElementById('encounter-result').hidden)window.receiptSeen=true;});
    window.receiptObserver.observe(document.getElementById('encounter-result'),{attributes:true,attributeFilter:['hidden']});
    window.healthObserver=new MutationObserver(records=>{
     for(const r of records)for(const node of r.addedNodes)if(node.nodeType===1){
      if(node.matches('[data-health-event]'))window.healthBeats.push({kind:node.dataset.healthEvent,source:node.dataset.healthSource,hp:Number(document.getElementById('hud-health').getAttribute('aria-valuenow'))});
      if(node.matches('.fx-key'))window.keyBeats.push(node.className);
      if(node.matches('.fx-gold'))window.goldBeats.push(node.textContent);
      if(node.matches('.fx-effect'))window.rewardBeats.push(node.classList.contains('curse')?'curse':'blessing');
      if(node.matches('.fx-item'))window.rewardBeats.push(node.dataset.reward);
     }
    });
    window.healthObserver.observe(document.getElementById('scene-fx'),{childList:true});
   });
   await page.locator(`[data-kind="${c.action.kind}"][data-index="${c.action.index||0}"]`).first().click();
   if(c.name==='fight-and-heal'){
    await page.locator('.fx-flash[data-health-event="damage"]').first().waitFor({state:'visible'});
    assert.equal(await page.locator('.fx-number,.fx-slash,.scene.hit,.actor-lunge').count(),0);
    await capture('hit');
   }
   if(c.name==='fight-keys'){
    await page.waitForFunction(()=>window.keyBeats.length>0);await capture('moving-key');
   }
   const kinds=c.journal.outcomes.map(o=>o.kind);
   const hasReceipt=kinds.some(k=>['death','completed','cleansed','rps','spider','resource','lucky_coins','mushrooms_found'].includes(k));
   if(kinds.includes('gold')){await page.waitForFunction(()=>window.goldBeats.length>0);await capture('gold-float');}
   if(hasReceipt){
    await page.locator('#encounter-result').waitFor({state:'visible',timeout:15000});
    assert.match(await page.locator('.hud-location strong').innerText(),new RegExp(`Room ${c.before.room} / 100`));
    assert.equal(Number(await page.locator('#hud-health').getAttribute('aria-valuenow')),Number((c.after.hp*100).toFixed(1)));
   }
   if(c.name==='soldier'){await page.waitForFunction(()=>!busy);assert.match(await page.locator('.encounter-art').getAttribute('src'),/monster5000/);assert.equal(await page.evaluate(()=>window.receiptSeen),false);}
   if(c.name==='crate-nothing')assert.equal(c.before.hp,c.after.hp);
   if(['legendary','soldier','crate-blessing'].includes(c.name))await capture(c.name);
   // Feedback finishes on its own: there is no confirmation button.
   if(hasReceipt)assert.equal(await page.evaluate(()=>busy),true);
   assert.equal(await page.locator('#continue-result').count(),0);
   await page.waitForFunction(()=>!busy);
   for(const [kind,card] of [['item','item'],['epics','epic'],['legendaries','legendary'],['blessing','blessing'],['curse','curse']]){
    if(kinds.includes(kind))assert.ok(await page.evaluate(card=>window.rewardBeats.includes(card),card),c.name+' missing '+card);
   }
   if(!hasReceipt)assert.equal(await page.evaluate(()=>window.receiptSeen),false);
   await page.evaluate(()=>{window.healthObserver.disconnect();window.receiptObserver.disconnect();});
   if(kinds.includes('gold'))assert.deepEqual(await page.evaluate(()=>window.goldBeats),c.journal.outcomes.filter(e=>e.kind==='gold').map(e=>'🪙+'+Math.round(e.amount*10000000).toLocaleString('en-US')));
   if(c.name==='fight-and-heal'){
    assert.deepEqual(await page.evaluate(()=>window.healthBeats.map(e=>[e.kind,e.source])),[['damage',''],['healing',''],['damage','poison']]);
   }
   assert.equal(Number(await page.locator('#hud-health').getAttribute('aria-valuenow')),Number((c.after.hp*100).toFixed(1)));
   assert.equal(Number(await page.locator('#hud-key-count').innerText()),c.after.keys);
   assert.equal(await page.evaluate(()=>obs().room),c.after.room);
   assert.equal(await page.evaluate(()=>obs().phase),c.after.phase);
   if(c.name==='soldier')assert.match(await page.locator('.encounter-art').getAttribute('src'),/monster5000/);
   if(c.name==='trap-death'){
    await page.locator('[data-kind="heal_step"]').click();
    await page.waitForFunction(()=>!busy);
    await page.locator('[data-kind="reenter"]').click();await page.waitForFunction(()=>!busy);
    assert.equal(await page.evaluate(()=>obs().phase),'doors');assert.equal(await page.evaluate(()=>obs().room),31);
   }
   await page.evaluate(()=>window.healthBeats=[]);console.log('Passed '+c.name);
  }
  // Original free shop, its icons and the earned effect in the bottom HUD.
  await page.evaluate(()=>replaceSession({seed:1,budget:100,initial_state:'schema=1\nroom=5\nrun_number=1\nphase=doors\nhp_percent=70\nkeys=0\ndoors=shop,wall\n'}));
  await page.locator('[data-kind="choose_door"][data-index="0"]').click();await page.waitForFunction(()=>!busy);
  assert.equal(await page.locator('.offer').count(),2);assert.equal(await page.locator('[data-kind="reroll"]').count(),0);
  await capture('shop');
  await page.locator('.offer[data-index="0"]').click();
  assert.equal(await page.evaluate(()=>payload.steps),1,'Selecting an offer must not buy it');
  await page.locator('#shop-buy').click();await page.locator('.fx-effect.blessing').waitFor({state:'visible'});
  await page.waitForFunction(()=>!busy);
  assert.equal(await page.locator('.hud-effect').count(),1);
  await page.locator('.hud-effect').click();assert.match(await page.locator('#effect-inspector').innerText(),/One Hit Wonder/);await page.locator('#effect-inspector button').click();
  // Isolate pause timing from the neural completion policy's loot preferences.
  // The rule fixture opens this chest and produces a floating item.
  await page.evaluate(settings=>replaceSession({...settings,neural_adviser:false}),cases.find(c=>c.name==='epic').settings);
  assert.equal(await page.evaluate(()=>suggestion().kind),'interact');
  await page.locator('#autoplay').click();await page.locator('.fx-item.epic').waitFor({state:'visible'});
  await page.locator('#autoplay').click();await page.waitForFunction(()=>!busy);await page.waitForTimeout(300);
  assert.equal(await page.evaluate(()=>playing),false);assert.equal(await page.evaluate(()=>payload.steps),1);
  assert.equal(await page.locator('#encounter-result').isVisible(),false);
  await page.locator('#undo').click();await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>obs().encounter),'epic');assert.equal(await page.evaluate(()=>payload.steps),0);
  // Reduced motion keeps rewards visible; real choices work from the keyboard.
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(settings=>replaceSession(settings),cases.find(c=>c.name==='epic').settings);
  await page.locator('[data-kind="interact"]').focus();await page.keyboard.press('Enter');
  await page.waitForFunction(()=>!busy);assert.equal(await page.evaluate(()=>payload.loot.counts.epic),1);
  await page.selectOption('#speed','450');
  await page.evaluate(settings=>replaceSession(settings),cases.find(c=>c.name==='crate-gold').settings);
  const start=Date.now();await page.locator('[data-kind="interact"]').click();await page.waitForFunction(()=>!busy);
  assert.ok(Date.now()-start<1800,'Fast feedback should finish promptly without another click');
  // Find an actual empty opening room, then verify one click passes through it.
  const emptySetup=await page.evaluate(async()=>{
   for(let seed=1;seed<65;seed++){
    const settings={seed,budget:0},probe=await api('new',{settings});
    const index=probe.observation.doors.findIndex(d=>d.kind==='mystery');
    const next=await api('action',{session:probe.session,action:{kind:'choose_door',index}});
    await api('close',{session:probe.session});
    if(next.observation.phase==='encounter'&&next.observation.encounter==='empty')return {settings,index};
   }
   throw new Error('No empty opening fixture found');
  });
  await page.evaluate(settings=>replaceSession(settings),emptySetup.settings);
  await page.locator(`[data-kind="choose_door"][data-index="${emptySetup.index}"]`).click();await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>obs().room),2);assert.equal(await page.evaluate(()=>obs().phase),'doors');
  assert.equal(await page.evaluate(()=>payload.steps),2);
  await page.locator('#undo').click();await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>obs().room),1);assert.equal(await page.evaluate(()=>payload.steps),0);
  // The new prepared scenes load through the real setup form.
  for(const [preset,encounter] of [['monster','monster'],['soldier','skeleton'],['chest','silver']]){
   await page.locator('#new-run').click();await page.selectOption('#preset',preset);
   await page.locator('#setup-form [type="submit"]').click();await page.locator('#setup').waitFor({state:'hidden'});
   assert.equal(await page.evaluate(()=>obs().encounter),encounter);
   if(preset==='monster')assert.equal(await page.locator('.hud-effect').count(),1);
  }
  const gemState=offers=>({seed:1,budget:100,run_number:2,initial_state:`schema=1\nroom=26\nrun_number=2\nphase=gems\nhp_percent=80\nkeys=6\ngem_offers=${offers}\n`});
  for(const [offers,expected] of [['pendant,greed,hero','greed'],['pendant,greed,moonstone','moonstone'],['pendant,moonstone,rabbit','rabbit']]){
   await page.evaluate(settings=>replaceSession(settings),gemState(offers));
   assert.deepEqual(await page.evaluate(()=>suggestion()),await page.evaluate(()=>obs().suggestions.find(s=>s.strategy==='neural').action));
   assert.ok(offers.split(',').includes(await page.evaluate(()=>obs().gem_offers[suggestion().index])));
  }
  // Arrow controls select shop offers; Enter buys exactly the selected one.
  const paidShop={seed:7,budget:100,run_number:2,initial_state:'schema=1\nroom=35\nrun_number=2\nphase=shop\nhp_percent=70\nkeys=8\ngems=hero\noffers=one_hit:strong:8:4,raider:weak:5:1\n'};
  await page.evaluate(settings=>replaceSession(settings),paidShop);
  assert.equal(await page.locator('#shop-guard').isVisible(),true);
  const shopRecommendation=await page.evaluate(()=>suggestion());
  if(shopRecommendation.kind==='buy')assert.match(await page.locator('.offer-suggestion').innerText(),/recommended|suggested/i);
  else assert.ok(await page.locator(`[data-kind="${shopRecommendation.kind}"].suggested`).count(),'Highlight the actual policy action when it does not recommend buying');
  const shopScene=await page.locator('#scene').boundingBox(),shopChoices=await page.locator('.offer-grid').boundingBox();
  assert.ok(shopChoices.x>=shopScene.x&&shopChoices.x+shopChoices.width<=shopScene.x+shopScene.width,'Shop choices stay inside the scene beside the shopkeeper');
  await capture('paid-shop');
  assert.equal(await page.locator('[data-kind="reroll"]').isDisabled(),true);
  await page.locator('#scene').focus();await page.keyboard.press('Space');
  assert.equal(await page.evaluate(()=>payload.steps),0);
  await page.keyboard.press('ArrowRight');assert.equal(await page.evaluate(()=>selectedOffer),1);
  assert.equal(await page.evaluate(()=>payload.steps),0);
  await page.keyboard.press('ArrowLeft');assert.equal(await page.locator('.offer.selected').getAttribute('data-index'),'0');
  await page.keyboard.press('Enter');await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>payload.journal.at(-1).offer.effect.kind),'one_hit');
  assert.equal(await page.evaluate(()=>obs().keys),4);
  await page.evaluate(settings=>replaceSession(settings),paidShop);
  await page.locator('#unlock-reroll').click();await page.keyboard.down('Space');
  await page.waitForFunction(()=>!busy);await page.keyboard.down('Space');await page.keyboard.up('Space');
  assert.equal(await page.evaluate(()=>payload.steps),1,'Holding Space must not roll past a useful offer');
  assert.equal(await page.evaluate(()=>obs().mushrooms_spent),1);
  // The manual warning must not silently stop a trained-policy reroll.
  // Force a proposed legal reroll here to test execution, not its learned value.
  await page.evaluate(settings=>replaceSession(settings),paidShop);
  assert.equal(await page.evaluate(()=>rerollGuarded()),true);
  await page.evaluate(()=>act(legal('reroll'),false,'Policy execution fixture'));
  assert.equal(await page.evaluate(()=>payload.steps),1);
  assert.equal(await page.evaluate(()=>obs().mushrooms_spent),1);
  const unaffordable={...paidShop,initial_state:paidShop.initial_state.replace('keys=8','keys=1')};
  await page.evaluate(settings=>replaceSession(settings),unaffordable);
  assert.equal(await page.locator('#shop-guard').isVisible(),false,'An unaffordable offer must not block rerolls');
  // Arrow input and clicks on the scene use the same native actions.
  await page.evaluate(settings=>replaceSession(settings),cases.find(c=>c.name==='fight-keys').settings);
  await page.locator('#scene').focus();await page.keyboard.press('ArrowRight');await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>payload.journal.at(-1).action.kind),'flee');
  await page.evaluate(settings=>replaceSession(settings),cases.find(c=>c.name==='fight-keys').settings);
  const clickScene=await page.locator('#scene').boundingBox();
  await page.mouse.click(clickScene.x+clickScene.width*.35,clickScene.y+clickScene.height*.43);await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>payload.journal.at(-1).action.kind),'fight');
  const trappedChoice={seed:5,budget:0,initial_state:'schema=1\nroom=10\nrun_number=1\nphase=doors\nhp_percent=90\nkeys=3\ndoors=mystery:trap,monster\n'};
  await page.evaluate(settings=>replaceSession(settings),trappedChoice);
  assert.deepEqual(await page.evaluate(()=>suggestion()),await page.evaluate(()=>obs().suggestions.find(s=>s.strategy==='neural').action));
  assert.equal(await page.locator('.trap-art').count(),1);
  assert.equal(await page.locator('.trap-floor').count(),0);
  await capture('trap-art');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.locator('#scene').focus();await page.keyboard.press('ArrowRight');
  await page.locator('.entering-room').waitFor({state:'visible'});await capture('door-zoom');await page.waitForFunction(()=>!busy);
  assert.equal(await page.evaluate(()=>payload.journal[0].action.index),1);
  assert.equal(await page.locator('#strategy').isVisible(),false);
  assert.equal(await page.locator('#strategy option').count(),1);
  assert.equal(await page.locator('.comparison').isVisible(),false);
  // Three independent blessing slots and three independent curse slots stay visible.
  const sixEffects={seed:5,budget:0,run_number:2,initial_state:'schema=1\nroom=60\nrun_number=2\nphase=encounter\nencounter=skeleton\nhp_percent=70\nkeys=8\ngems=rabbit,moonstone\nblessings=one_hit:strong:8,recovery:strong:6,escape:strong:10\ncurses=poison:weak:4,broken_armor:weak:4,hard_lock:weak:4\n'};
  await page.evaluate(settings=>replaceSession(settings),sixEffects);
  assert.equal(await page.locator('.hud-effect-group.blessings .hud-effect').count(),3);
  assert.equal(await page.locator('.hud-effect-group.curses .hud-effect').count(),3);
  assert.equal(await page.locator('.hud-effect-group .empty-slot').count(),0);
  assert.equal(await page.evaluate(()=>suggestion().kind),'interact');
  await capture('six-effects');
  // Narrow viewport and fullscreen preserve the scene HUD and clickable controls.
  await page.setViewportSize({width:390,height:844});
  await page.evaluate(()=>replaceSession({seed:1,budget:100}));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await capture('mobile');
  await page.evaluate(settings=>replaceSession(settings),gemState('pendant,greed,moonstone'));await capture('mobile-stones');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.locator('#new-run').click();await page.selectOption('#preset','opening');
  await page.locator('#setup-form [type="submit"]').click();await page.locator('#setup').waitFor({state:'hidden'});
  await page.locator('[data-kind="choose_door"][data-index="0"]').click();await page.waitForFunction(()=>!busy);
  await capture('mobile-shop');
  await page.evaluate(settings=>replaceSession(settings),paidShop);await capture('mobile-paid-shop');
  assert.equal(await page.locator('#shop-buy').isVisible(),true);
  const mobileBuy=await page.locator('#shop-buy').boundingBox(),mobileTotals=await page.locator('.hud-run').boundingBox();
  assert.ok(mobileBuy.y+mobileBuy.height<mobileTotals.y,'Shop controls must not cover run totals');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.setViewportSize({width:1440,height:1000});
  await page.evaluate(()=>replaceSession({seed:1,budget:100}));
  await page.locator('#fullscreen').click();
  await capture('fullscreen');
  assert.ok(await page.locator('#hud-health').isVisible());
  assert.equal(await page.locator('.workspace aside').isVisible(),false);
  assert.equal(await page.locator('#fullscreen-play').isVisible(),false,'Native fullscreen hides the outside toolbar');
  assert.equal(await page.locator('.hud-effect-group .empty-slot').count(),6);
  assert.ok(await page.locator('.hud-effect-group .empty-slot').evaluateAll(slots=>slots.every(slot=>{
   const style=getComputedStyle(slot),box=slot.getBoundingClientRect();return parseFloat(style.borderTopWidth)>0&&box.width>=28&&box.width<=50;
  })),'All six empty slots keep equal icon-sized borders in fullscreen');
  const scene=await page.locator('#scene').boundingBox(),health=await page.locator('#hud-health').boundingBox();
  assert.ok(health.width>scene.width*.4,'Fullscreen health bar should span the center');
  assert.ok(health.y+health.height<scene.y+scene.height,'Health stays inside the frame');
  await page.evaluate(settings=>replaceSession(settings),paidShop);await capture('fullscreen-shop');
  const offers=await page.locator('.offer-grid').boundingBox(),goods=await page.locator('[data-kind="reroll"]').boundingBox(),buy=await page.locator('#shop-buy').boundingBox();
  assert.ok(offers.y+offers.height<goods.y,'Offers must sit above New goods');
  assert.ok(goods.y+goods.height<=buy.y,'Buy must sit below New goods');
  assert.deepEqual(errors,[]);
  assert.equal(await page.locator('.run-summary').isVisible(),false);
  if(await page.evaluate(()=>!!document.fullscreenElement))await page.evaluate(()=>document.exitFullscreen());
  else await page.keyboard.press('Escape');
  await page.waitForFunction(()=>!document.body.classList.contains('game-view'));
  assert.equal(await page.locator('.workspace aside').isVisible(),true);
  await page.evaluate(()=>replaceSession({budget:100,run_number:2,initial_state:'schema=1\nroom=100\nrun_number=2\nphase=recovery\nresume_phase=encounter\nencounter=boss\nhp_percent=0\nkeys=0\ngems=hero,greed,moonstone\npaid_heals=2\n'}));
  assert.deepEqual(await page.evaluate(()=>suggestion()),await page.evaluate(()=>obs().suggestions.find(s=>s.strategy==='neural').action));
  assert.ok(await page.locator('#actions .suggested,#refills .suggested').count(),'Show the policy recovery action');
  await capture('boss-wait');
  await page.evaluate(()=>replaceSession({budget:100,run_number:2,initial_state:'schema=1\nroom=60\nrun_number=2\nphase=encounter\nencounter=mimic\nhp_percent=70\nkeys=0\ngems=hero,greed\n'}));
  assert.match(await page.locator('.mimic-warning strong').innerText(),/MIMIC.*FAKE CHEST/);await capture('mimic-tag');
  await page.evaluate(()=>api('close',{session}));
  console.log('Passed shop protection, arrows, Enter, Space, pointer controls, trap art, zoom, frame-only damage, moving keys, effects, pause, undo, presets, stone priority, reduced motion, mobile, fullscreen; no page errors.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
