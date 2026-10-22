/* Outcomes are supplied by the simulator. This layer never rolls or awards loot. */
(function (root) {
 "use strict";
 const escape = value => String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
 const percent = n => `${(n*100).toFixed(1).replace(/\.0$/,"")}%`;
 const titleCase = s => String(s || "").replaceAll("_"," ").replace(/^./,c=>c.toUpperCase());
 const goldValue = units => Math.round(units * 10000000).toLocaleString("en-US");
 const rewardKinds = new Set(["gold","item","epics","legendaries","blessing","curse","resource","keys","mushrooms_found","mushrooms_spent","lucky_coins","nothing"]);

 function beats(before, after, outcomes) {
  let hp=before.hp;
  const result=outcomes.map(e=>{
   if(e.kind==="damage") hp=Math.max(0,hp-e.amount);
   if(e.kind==="healing") hp=Math.min(1,hp+e.amount);
   return {...e,hp};
  });
  // A restart resets HP without producing an ordinary healing event.
  if(!result.some(e=>e.kind==="healing"||e.kind==="damage") && Math.abs(after.hp-hp)>.000001)
   result.push({kind:after.hp>hp?"healing":"damage",amount:Math.abs(after.hp-hp),detail:"",hp:after.hp});
  return result;
 }
 function reward(e,label=titleCase) {
  const count=e.amount||1;
  if(e.kind==="blessing"||e.kind==="curse") return {kind:e.kind,title:label(e.detail),note:`${titleCase(e.kind)}${e.duration?` · ${e.duration} uses`:""}`,asset:`effect.${e.detail}`,symbol:e.kind==="curse"?"☠":"✦"};
  if(e.kind==="item")return {kind:"item",title:"Item",note:"Ordinary item · reference artwork",asset:"reward.item",symbol:"⚔"};
  if(e.kind==="epics")return {kind:"epic",title:count===1?"Epic item":`${count} epic items`,note:"Epic · reference artwork",asset:"reward.epic",symbol:"⚔"};
  if(e.kind==="legendaries")return {kind:"legendary",title:count===1?"Legendary item":`${count} legendary items`,note:"Legendary · reference artwork",asset:"reward.legendary",symbol:"♛"};
  if(e.kind==="gold")return {kind:"gold",title:`+${goldValue(e.amount)} gold`,note:"10 million gold per reward unit",symbol:"🪙"};
  if(e.kind==="nothing")return {kind:"empty",title:"Nothing found",note:"Empty result",symbol:"—"};
  if(e.kind==="keys")return {kind:"keys",title:`${e.amount>0?"+":""}${e.amount} key${Math.abs(e.amount)===1?"":"s"}`,note:e.amount>0?"Collected":"Spent",symbol:"⚿"};
  if(e.kind==="mushrooms_spent")return {kind:"cost",title:`${e.amount} mushrooms`,note:"Spent",asset:"hud.mushroom",symbol:"M"};
  if(e.kind==="mushrooms_found")return {kind:"mushrooms",title:`+${e.amount} mushrooms`,note:"Found",asset:"hud.mushroom",symbol:"M"};
  if(e.kind==="resource"||e.kind==="lucky_coins")return {kind:"resource",title:label(e.detail||e.kind),note:`${e.amount>0?"+":""}${e.amount}`,symbol:"◆"};
  return null;
 }
 function heading(outcomes,before,action) {
  const has=k=>outcomes.some(e=>e.kind===k);
  if(has("death"))return "You died";
  if(has("completed"))return "Dungeon completed!";
  if(has("awakened"))return "The soldier wakes up!";
  if(has("legendaries"))return "A legendary reward!";
  if(has("epics"))return "An epic reward!";
  if(has("escaped"))return "You escaped";
  if(has("defeated"))return before.encounter==="boss"?"Boss defeated":"Enemy defeated";
  if(has("escape_failed"))return "Escape failed";
  if(has("trap"))return has("disarmed")?"Trap disarmed":"A trap!";
  if(has("nothing")&&!outcomes.some(e=>rewardKinds.has(e.kind)&&e.kind!=="nothing"))return "Nothing found";
  if(has("blessing"))return "A blessing received";
  if(has("curse"))return "A curse received";
  if(has("healing"))return "Health restored";
  if(outcomes.some(e=>rewardKinds.has(e.kind)))return "Your reward";
  if(action.kind==="choose_gem")return "Stone of fate chosen";
  return "Room resolved";
 }
 function updateHealth(hp) {
  const fill=document.getElementById("hud-health-fill"),number=document.getElementById("hud-health-number"),bar=document.getElementById("hud-health");
  if(fill)fill.style.width=hp<=0?"0":`calc(${Math.max(0,Math.min(100,hp*100))}% - 6px)`;
  if(number)number.textContent=`${percent(hp)} / 100%`;
  if(bar){bar.classList.toggle("low",hp<.2);bar.setAttribute("aria-valuenow",(hp*100).toFixed(1));}
 }
 function recoveryActions(o,recommended) {
  const actions=[...o.legal_actions];
  if(recommended?.kind==="wait"&&!actions.some(a=>a.kind==="wait"&&Math.abs(a.value-recommended.value)<1e-9))actions.push(recommended);
  if(o.phase!=="recovery")return actions;
  const hours=o.scenario.recovery_hours;
  // The extra 0.1 percentage point survives a hit at the exact upper bound.
  const cap=o.room===100?Math.min(1,Math.max(.2,o.boss_damage_max+.001)):1;
  // Keep the policy's actual recommendation visible even when the compact
  // manual menu would otherwise hide a longer recovery target.
  return actions.filter(a=>a.kind!=="wait"||
   (o.neural_adviser?.case==="unrestricted_budget_policy"&&recommended?.kind==="wait"&&Math.abs(a.value-recommended.value)<1e-9)||
   (o.hp+a.value/hours<=cap+1e-9&&
    (Math.abs(a.value-o.hours_left)>1e-9||recommended?.kind==="wait"&&Math.abs(a.value-recommended.value)<1e-9)));
 }
 // Plan only the visible arrival. The engine remains responsible for clocks,
 // durations and room-exit ticks; the final HUD comes from its observation.
 function effectArrival(state,event,after) {
  const effects={blessings:(state.blessings||[]).map(e=>({...e})),curses:(state.curses||[]).map(e=>({...e}))};
  if(event.detail==="elixir")return {effects,group:null,slot:null};
  const group=event.kind==="curse"?"curses":"blessings",list=effects[group];
  const known=after[group]?.find(e=>e.kind===event.detail)||list.find(e=>e.kind===event.detail)||{};
  const incoming={...known,kind:event.detail,magnitude:event.amount,remaining:event.duration,
   clock:known.clock||({disarm:"trap",lockpick:"door",key_moment:"fight"}[event.detail]||"room"),starts_at:known.starts_at??state.turn??0};
  let slot=list.findIndex(e=>e.kind===event.detail);
  if(slot<0){if(list.length===3)list.shift();slot=list.length;list.push(incoming);}
  else list[slot]=incoming;
  return {effects,group,slot};
 }
 async function loadFont(assets) {
  if(!assets["font.reward"]||typeof FontFace==="undefined")return;
  try{
   const font=new FontFace("SF Tobi",`url("/local-assets/${encodeURIComponent(assets["font.reward"])}")`);
   document.fonts.add(await font.load());
  }catch{/* Artwork and fonts are optional in an art-free checkout. */}
 }
 async function play({before,after,journal,action,assets,label=titleCase,pace=900,onEffects=()=>{},onOutcomes=()=>{}}) {
  const $=id=>document.getElementById(id),scene=$("scene"),fx=$("scene-fx"),receipt=$("encounter-result");
  const reduced=matchMedia("(prefers-reduced-motion: reduce)").matches;
  const delay=n=>new Promise(resolve=>setTimeout(resolve,reduced?Math.min(n,80):Math.round(n*.35)));
  const events=beats(before,after,journal.outcomes||[]);
  let effectState=before;
  const motionMs=reduced?140:Math.max(150,pace);
  const url=key=>assets[key]?`/local-assets/${encodeURIComponent(assets[key])}`:"";
  const art=()=>$("scene-content").querySelector(".encounter-art");
  const animate=(node,cls)=>{if(node){node.classList.remove(cls);void node.offsetWidth;node.classList.add(cls);}};
  const runAnimation=async(node,frames,options)=>{
   const animation=node.animate(frames,options);
   try{await animation.finished;}finally{animation.cancel();}
  };
  const edgeFlash=async(kind,event)=>{
   const flash=document.createElement("div");flash.className=`fx-flash ${kind}`;
   if(event.kind==="damage"||event.kind==="healing"){
    flash.dataset.healthEvent=event.kind;flash.dataset.healthSource=event.detail||"";
   }else flash.dataset.effectEvent=event.kind;
   fx.append(flash);
   try{await runAnimation(flash,[{opacity:0},{opacity:reduced?.4:1,offset:.15},{opacity:0}],{duration:reduced?140:Math.min(650,motionMs),easing:"ease-out"});}
   finally{flash.remove();}
  };
  const rewardOrigin=()=>{
   const frame=fx.getBoundingClientRect();
   const selected=action.kind==="buy"?$("scene-content").querySelector(`.offer[data-index="${action.index||0}"]`):
    action.kind==="choose_door"?$("scene-content").querySelector(`[data-kind="choose_door"][data-index="${action.index||0}"]`):null;
   const source=(selected||art())?.getBoundingClientRect();
   return {x:source?source.x+source.width/2-frame.x:frame.width/2,y:source?source.y+source.height*.4-frame.y:frame.height*.43};
  };
  const sprite=async(key,cls,fallback)=>{
   const node=document.createElement("div");node.className=cls;
   const src=url(key);
   if(src){
    const image=new Image();image.alt="";image.src=src;
    try{
     await Promise.race([image.decode(),new Promise(resolve=>setTimeout(resolve,250))]);node.append(image);
    }catch{node.textContent=fallback;}
   }else node.textContent=fallback;
   return node;
  };
  const flyEffect=async event=>{
   const plan=effectArrival(effectState,event,after);
   const node=await sprite(`effect.${event.detail}`,`fx-effect ${event.kind}`,event.kind==="curse"?"☠":"✦");
   node.dataset.effectKind=event.detail;node.dataset.effectGroup=plan.group||"instant";
   node.dataset.effectSlot=String(plan.slot??"");
   const from=rewardOrigin(),frame=fx.getBoundingClientRect();
   const selector=plan.group?`[data-effect-group="${plan.group}"][data-effect-slot="${plan.slot}"]`:"#hud-health";
   const target=document.querySelector(selector)?.getBoundingClientRect();
   const to=target?{x:target.x+target.width/2-frame.x,y:target.y+target.height/2-frame.y}:from;
   const scale=target&&plan.group?target.width/76:.5;
   const pose=(p,s=1)=>`translate(${p.x}px,${p.y}px) translate(-50%,-50%) scale(${s})`;
   fx.append(node);
   try{
    await runAnimation(node,reduced?[
     {transform:pose(to,scale),opacity:0},{transform:pose(to,scale),opacity:1}
    ]:[
     {transform:pose(from,.7),opacity:0},
     {transform:pose({x:from.x,y:from.y-18},1.12),opacity:1,offset:.18},
     {transform:pose({x:from.x,y:from.y-24}),opacity:1,offset:.38},
     {transform:pose(to,scale),opacity:1}
    ],{duration:motionMs,easing:"cubic-bezier(.2,.65,.3,1)",fill:"forwards"});
    effectState={...effectState,...plan.effects};onEffects(plan.effects);
    const landed=document.querySelector(selector);animate(landed,"effect-arrived");
   }finally{node.remove();}
  };
  const floatReward=async event=>{
   const card=reward(event,label),gold=event.kind==="gold";
   const node=gold?document.createElement("div"):await sprite(card.asset,`fx-item ${card.kind}`,card.symbol);
   node.dataset.reward=card.kind;
   if(gold){node.className="fx-gold";node.innerHTML=`<span class="gold-emoji">🪙</span><span class="gold-number">+${goldValue(event.amount)}</span>`;}
   else if(event.amount>1){const count=document.createElement("b");count.textContent=`×${event.amount}`;node.append(count);}
   const origin=rewardOrigin();node.style.left=`${origin.x}px`;node.style.top=`${origin.y}px`;
   node.setAttribute("aria-label",gold?`${goldValue(event.amount)} gold received`:`${card.title} kept`);
   fx.append(node);
   try{await runAnimation(node,reduced?[
    {opacity:0,transform:"translate(-50%,-50%)"},{opacity:1,offset:.3,transform:"translate(-50%,-50%)"},{opacity:0,transform:"translate(-50%,-50%)"}
   ]:[
    {opacity:0,transform:"translate(-50%,-20%) scale(.85)"},
    {opacity:1,offset:.15,transform:"translate(-50%,-40%) scale(1)"},
    {opacity:1,offset:.75,transform:"translate(-50%,-100%) scale(1)"},
    {opacity:0,transform:"translate(-50%,-140%) scale(1)"}
   ],{duration:motionMs,easing:"ease-out"});}finally{node.remove();}
  };
  const moveKeys=async amount=>{
   const counter=$("hud-key-count");if(!counter)return;
   if(!reduced){
    const frame=fx.getBoundingClientRect(),hud=counter.getBoundingClientRect();
    const door=$("scene-content").querySelector(`[data-kind="choose_door"][data-index="${action.index||0}"]`);
    const destination=(door||art())?.getBoundingClientRect();
    const point=r=>({x:r.x+r.width/2-frame.x,y:r.y+r.height/2-frame.y});
    const center=destination?point(destination):{x:frame.width/2,y:frame.height*.46};
    const from=amount>0?center:point(hud),to=amount>0?point(hud):center;
    const key=document.createElement("div");key.className="fx-key";
    key.innerHTML=url("encounter.spider_key1")?`<img src="${url("encounter.spider_key1")}" alt="">`:'<span>⚿</span>';
    if(Math.abs(amount)>1){const count=document.createElement("b");count.textContent=`×${Math.abs(amount)}`;key.append(count);}
    fx.append(key);
    const keyImage=key.querySelector("img");if(keyImage)await keyImage.decode().catch(()=>{key.innerHTML='<span>⚿</span>';});
    await key.animate([
     {transform:`translate(${from.x}px,${from.y}px) translate(-50%,-50%) scale(1)`,opacity:1},
     {transform:`translate(${(from.x+to.x)/2}px,${Math.min(from.y,to.y)-35}px) translate(-50%,-50%) scale(1.15)`,opacity:1,offset:.5},
     {transform:`translate(${to.x}px,${to.y}px) translate(-50%,-50%) scale(.6)`,opacity:.7}
    ],{duration:320,easing:"ease-in-out",fill:"forwards"}).finished;
    key.remove();
   }
   counter.textContent=after.keys;animate(counter,"key-arrived");
  };
  $("decision-banner").hidden=true;$("effect-inspector").hidden=true;
  let descriptions=[];
  if(action.kind==="choose_door"){
   const spent=events.find(e=>e.kind==="keys"&&e.amount<0);if(spent)await moveKeys(spent.amount);
   if(!reduced){
    const world=$("scene-world"),door=$("scene-content").querySelector(`[data-kind="choose_door"][data-index="${action.index||0}"]`);
    const bounds=world.getBoundingClientRect(),target=door?.getBoundingClientRect();
    world.style.transformOrigin=target?`${target.x+target.width/2-bounds.x}px ${target.y+target.height/2-bounds.y}px`:"50% 50%";
    scene.classList.add("entering-room");
    // A single opaque world layer keeps door art registered to the masonry.
    // Hold its final size until render replaces the room, avoiding a reverse zoom.
    await world.animate([{transform:"scale(1)"},{transform:"scale(1.22)"}],{duration:260,easing:"ease-in",fill:"forwards"}).finished;
   }
  }
  if(action.kind==="interact"&&url(`opened.${before.encounter}`)&&!events.some(e=>e.kind==="awakened")){
   animate(art(),"container-opening");await delay(260);if(art())art().src=url(`opened.${before.encounter}`);
  }
  for(const e of events){
   if(e.kind==="damage"||e.kind==="healing"){
    const healing=e.kind==="healing",poison=e.detail==="poison";
    if(healing)scene.classList.add("recovering");
    updateHealth(e.hp);
    await edgeFlash(healing?"healing":poison?"curse":"",e);scene.classList.remove("recovering");
   }else if(e.kind==="keys"){
    if(!(action.kind==="choose_door"&&e.amount<0))await moveKeys(e.amount);
   }else if(["gold","item","epics","legendaries"].includes(e.kind)){
    await floatReward(e);
   }else if(e.kind==="blessing"||e.kind==="curse"){
    await Promise.all([flyEffect(e),e.kind==="curse"?edgeFlash("curse",e):Promise.resolve()]);
   }else if(e.kind==="awakened"){
    if(art()){art().src=url("encounter.soldier_awake")||url("encounter.monster");art().alt="Awakened soldier";animate(art(),"actor-wake");}
    descriptions.push("The soldier is alive. Choose whether to fight or escape.");await delay(800);
   }else if(e.kind==="defeated"){
    animate(art(),"actor-fall");descriptions.push(before.encounter==="boss"?"The boss is defeated.":"The enemy is defeated.");await delay(420);
   }else if(e.kind==="trap"){
    scene.classList.add("fx-trap");descriptions.push(e.detail==="curse"?"A curse trap was triggered.":"A damage trap was triggered.");await delay(250);scene.classList.remove("fx-trap");
   }else if(e.kind==="nothing"){
    if(url(`opened.${before.encounter}`)){
     animate(art(),"container-empty");await delay(550);
    }else descriptions.push("Nothing was found.");
   }else if(e.kind==="cleansed"){
    effectState={...effectState,curses:[]};onEffects({blessings:effectState.blessings,curses:[]});
    descriptions.push(`Removed ${e.amount} curse${e.amount===1?"":"s"}.`);
   }
   else if(e.kind==="escaped")descriptions.push("You escaped successfully.");
   else if(e.kind==="escape_failed")descriptions.push("Your attempt to escape failed.");
   else if(e.kind==="disarmed")descriptions.push("The trap was disarmed.");
   else if(e.kind==="spider")descriptions.push(e.detail==="success"?"You took the keys safely.":"The spider bit you.");
   else if(e.kind==="rps")descriptions.push(`Rock, paper, scissors: ${e.detail}.`);
  }
  updateHealth(after.hp);
  onOutcomes();
  if(events.some(e=>e.kind==="death"))descriptions.push(journal.outcomes.find(e=>e.kind==="death").detail==="retry_boss"?"Recover, then retry this boss.":`Recover, then choose a door for room ${after.room}.`);
  if(events.some(e=>e.kind==="completed"))descriptions.push("Room 100 cleared. The next run starts at full health.");
  const cards=events.filter(e=>!["keys","mushrooms_spent","gold","item","epics","legendaries","blessing","curse","nothing"].includes(e.kind)).map(e=>reward(e,label)).filter(Boolean);
  if(action.kind==="choose_gem")cards.push({kind:"blessing",title:label(journal.gem),note:"Active for this run",asset:`gem.${journal.gem}`,symbol:"◆"});
  // Results are brief feedback, never another gameplay decision.
  if(!cards.length&&!events.some(e=>["death","completed","cleansed","rps","spider"].includes(e.kind)))return;
  $("result-context").textContent=`Run ${journal.run} · room ${journal.room} / 100`;
  $("result-title").textContent=heading(events,before,action);
  $("reward-cards").innerHTML=cards.map(c=>`<div class="reward-card ${escape(c.kind)}" data-reward="${escape(c.kind)}">${url(c.asset)?`<img src="${url(c.asset)}" alt="">`:`<div class="reward-symbol" aria-hidden="true">${escape(c.symbol)}</div>`}<strong>${escape(c.title)}</strong><small>${escape(c.note)}</small></div>`).join("");
  $("result-message").textContent=descriptions.join(" ");
  receipt.classList.toggle("death-result",events.some(e=>e.kind==="death"));
  receipt.classList.toggle("legendary-result",events.some(e=>e.kind==="legendaries"));
  receipt.hidden=false;
  await new Promise(resolve=>setTimeout(resolve,Math.max(150,pace)));
  receipt.hidden=true;fx.replaceChildren();
 }
 const api={beats,reward,heading,updateHealth,play,goldValue,recoveryActions,effectArrival,loadFont};
 if(typeof module!=="undefined"&&module.exports)module.exports=api;
 else root.DungeonPresentation=api;
})(globalThis);
