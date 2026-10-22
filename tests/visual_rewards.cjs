// Real native outcomes -> browser animation -> retained loot. No game login.
const {chromium}=require(process.env.SFLD_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const output=path.join(root,'out/reward-ui-2026-10-15');
fs.mkdirSync(output,{recursive:true});
const settings=(extra,phase='encounter',room=60)=>({seed:1,budget:100,run_number:2,neural_adviser:false,
 initial_state:`schema=1\nroom=${room}\nrun_number=2\nphase=${phase}\nhp_percent=70\nkeys=8\ngems=rabbit,moonstone${room>75?',greed':''}\n${extra}\n`});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.SFLD_BROWSER_CHANNEL||undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],reports=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.SFLD_LAB_URL||'http://127.0.0.1:8798');
  await page.waitForSelector('#hud-health');
  await page.evaluate(()=>{document.body.classList.add('game-view');$('speed').value='900';});
  const watch=async()=>page.evaluate(()=>{
   window.rewardEvents=[];window.popupSeen=false;
   window.rewardObserver?.disconnect();window.popupObserver?.disconnect();
   window.rewardObserver=new MutationObserver(records=>{
    for(const record of records)for(const n of record.addedNodes)if(n.nodeType===1){
     rewardEvents.push({className:n.className,kind:n.dataset.effectKind,group:n.dataset.effectGroup,slot:n.dataset.effectSlot,
      reward:n.dataset.reward,health:n.dataset.healthEvent,source:n.dataset.healthSource,effect:n.dataset.effectEvent,
      hp:Number($('hud-health').getAttribute('aria-valuenow')),text:n.textContent,font:n.querySelector('.gold-number')?getComputedStyle(n.querySelector('.gold-number')).fontFamily:null,
      frames:n.getAnimations().map(a=>a.effect.getKeyframes())});
    }
   });
   rewardObserver.observe($('scene-fx'),{childList:true});
   window.popupObserver=new MutationObserver(()=>{if(!$('encounter-result').hidden)window.popupSeen=true;});
   popupObserver.observe($('encounter-result'),{attributes:true,attributeFilter:['hidden']});
  });
  const perform=async(action,capture)=>{
   await watch();
   await page.evaluate(action=>{window.actionDone=act(legal(action.kind,action.index||0),true);},action);
   if(capture){
    await page.waitForSelector(capture.selector,{state:'visible'});
    await page.evaluate(selector=>{
     const n=document.querySelector(selector),a=n?.getAnimations()[0];
     if(a){a.pause();a.currentTime=a.effect.getTiming().duration*.4;}
    },capture.selector);
    await page.screenshot({path:path.join(output,capture.name+'.png')});
    await page.evaluate(selector=>document.querySelector(selector)?.getAnimations().forEach(a=>a.play()),capture.selector);
   }
   await page.evaluate(()=>window.actionDone);
   const result=await page.evaluate(()=>({events:rewardEvents,popup:popupSeen,observation:obs(),loot:payload.loot,
    hud:Array.from(document.querySelectorAll('.hud-effect')).map(n=>({group:n.dataset.effectGroup,kind:n.dataset.effectKind,slot:Number(n.dataset.effectSlot),remaining:Number(n.querySelector('b').textContent)})),
    outcome:payload.journal.at(-1).outcomes,fxChildren:$('scene-fx').children.length,bag:Number(document.querySelector('#hud-loot b').textContent)}));
   assert.equal(result.fxChildren,0,'Transient animation nodes must be cleaned up');
   assert.equal(result.bag,Object.values(result.loot.counts).reduce((a,b)=>a+b,0));
   reports.push(result);return result;
  };
  // A fourth distinct blessing arrives in the last blessing slot. Three curses
  // remain independent, with Recovery healing before the poison tick.
  const effects='blessings=one_hit:strong:8,recovery:strong:6,lockpick:strong:6\ncurses=poison:weak:5,clumsy:weak:4,hard_lock:strong:4\n';
  await page.evaluate(s=>replaceSession(s),settings(effects+'offers=escape:strong:10:6,raider:weak:5:1','shop'));
  let r=await perform({kind:'buy'},{selector:'.fx-effect.blessing',name:'blessing-fly'});
  assert.equal(r.popup,false);assert.deepEqual(r.hud.filter(e=>e.group==='blessings').map(e=>e.kind),['recovery','lockpick','escape']);
  assert.equal(r.hud.filter(e=>e.group==='curses').length,3);
  assert.ok(r.events.some(e=>e.kind==='escape'&&e.group==='blessings'&&e.slot==='2'));
  const ticks=r.events.filter(e=>e.health);
  assert.deepEqual(ticks.map(e=>e.health),['healing','damage']);
  assert.match(ticks[0].className,/healing/);assert.match(ticks[1].className,/curse/);assert.equal(ticks[1].source,'poison');
  assert.ok(ticks[0].hp>ticks[1].hp);
  // A weaker duplicate replaces in place; no phantom fourth slot.
  await page.evaluate(s=>replaceSession(s),settings(effects+'offers=recovery:weak:3:2,raider:weak:5:1','shop'));
  r=await perform({kind:'buy'});
  assert.ok(r.events.some(e=>e.kind==='recovery'&&e.slot==='1'));
  assert.deepEqual(r.hud.filter(e=>e.group==='blessings').map(e=>e.kind),['one_hit','recovery','lockpick']);
  // Receiving a curse flashes purple even though no poison damage occurs.
  await page.evaluate(s=>replaceSession(s),settings('blessings=one_hit:strong:8,recovery:strong:6,escape:strong:10\ncurses=clumsy:weak:4,hard_lock:weak:4,gold_hangover:weak:4\noffers=broken_armor:strong:5:2,poison:weak:5:3','curse_shop'));
  r=await perform({kind:'buy'},{selector:'.fx-effect.curse',name:'curse-fly'});
  assert.equal(r.popup,false);assert.ok(r.events.some(e=>e.effect==='curse'&&/fx-flash curse/.test(e.className)));
  assert.ok(!r.events.some(e=>e.source==='poison'));
  assert.deepEqual(r.hud.filter(e=>e.group==='curses').map(e=>e.kind),['hard_lock','gold_hangover','broken_armor']);
  // Instant healing flies to the health bar and doesn't consume a slot.
  await page.evaluate(s=>replaceSession(s),settings(effects+'offers=elixir:weak:1:1,raider:weak:5:1','shop'));
  r=await perform({kind:'buy'});
  assert.ok(r.events.some(e=>e.kind==='elixir'&&e.group==='instant'));
  assert.equal(r.hud.filter(e=>e.group==='blessings').length,3);assert.ok(!r.hud.some(e=>e.kind==='elixir'));
  assert.equal(r.popup,false);
  // Use native fixture seeds to find actual gold/ordinary/legendary outcomes.
  for(const [kind,extra,rarity] of [['gold','encounter=crate',null],['item','encounter=crate','ordinary'],['epics','encounter=epic','epic'],['legendaries','encounter=prize\ntrial_seen=true\npending_trial_reward=5','legendary']]){
   const fixture=await page.evaluate(async({base,kind})=>{
    for(let seed=1;seed<=100;seed++){
     const s={...base,seed},probe=await api('new',{settings:s});
     try{
      const result=await api('action',{session:probe.session,action:{kind:'interact'}});
      if(result.journal.at(-1).outcomes.some(e=>e.kind===kind))return s;
     }finally{await api('close',{session:probe.session});}
    }throw new Error('Missing reward fixture '+kind);
   },{base:settings(extra),kind});
   await page.evaluate(s=>replaceSession(s),fixture);
   r=await perform({kind:'interact'},{selector:kind==='gold'?'.fx-gold':'.fx-item',name:kind+'-float'});
   assert.equal(r.popup,false,'Reward must not open a result panel');
   if(kind==='gold'){
    const gold=r.events.find(e=>e.reward==='gold');assert.match(gold.text,/🪙\+/);assert.match(gold.font,/SF Tobi/);
    assert.equal(await page.evaluate(()=>[...document.fonts].some(f=>f.family.replaceAll('"','')==='SF Tobi'&&f.status==='loaded')),true);
    assert.equal(r.loot.counts.ordinary,0);
   }else{
    assert.equal(r.loot.counts[rarity],1);assert.ok(r.events.some(e=>e.reward===(rarity==='ordinary'?'item':rarity)));
    const saved=await page.evaluate(()=>api('export',{session}));
    await page.evaluate(async()=>{payload=await api('undo',{session});render();});
    assert.equal(await page.evaluate(()=>Number(document.querySelector('#hud-loot b').textContent)),0);
    await page.evaluate(record=>replaceSession(record.settings,record),saved);
    assert.equal(await page.evaluate(()=>Number(document.querySelector('#hud-loot b').textContent)),1);
   }
  }
  await page.locator('#hud-loot').click();await page.screenshot({path:path.join(output,'kept-items.png')});
  assert.match(await page.locator('#effect-inspector').innerText(),/All items are kept automatically/);
  await page.locator('#effect-inspector button').click();
  await page.evaluate(s=>replaceSession(s),settings('encounter=monster\nblessings=recovery:strong:6\ncurses=poison:weak:4'));
  r=await perform({kind:'fight'},{selector:'.fx-flash.healing',name:'healing-tick'});
  assert.deepEqual(r.events.filter(e=>e.health).map(e=>e.health),['damage','healing','damage']);
  assert.equal(await page.locator('.fx-number,.fx-slash').count(),0);
  // A compact viewport and reduced motion retain feedback and HUD counts.
  await page.setViewportSize({width:390,height:844});await page.emulateMedia({reducedMotion:'reduce'});
  await page.evaluate(s=>replaceSession(s),settings('encounter=epic'));
  r=await perform({kind:'interact'});assert.equal(r.loot.counts.epic,1);assert.equal(r.popup,false);
  await page.screenshot({path:path.join(output,'compact.png')});
  await page.evaluate(s=>replaceSession(s),settings(effects+'offers=escape:strong:10:6,raider:weak:5:1','shop'));
  r=await perform({kind:'buy'});
  assert.equal(r.hud.length,6);assert.equal(r.popup,false);
  await page.evaluate(()=>api('close',{session}));
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({passed:true,scenes:reports.length,reports},null,2));
  console.log(`Passed ${reports.length} native reward scenes, item replay/undo, slot replacement, fonts and reduced motion.`);
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
