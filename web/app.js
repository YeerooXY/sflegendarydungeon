"use strict";
const $ = id => document.getElementById(id);
const names = {
  monster:"Monster", mystery:"Mystery room", locked:"Locked room", double_locked:"Double lock", unlocked:"Unlocked room",
  epic:"Epic door", golden:"Golden room", shop:"Blessing shop", cursed:"Cursed door", sacrifice:"Sacrificial door",
  blessing:"Door of Blessing", destiny:"Destiny Door", trial:"Arena", exit_trial:"Collect arena reward", boss:"Boss", wall:"Wall",
  barrel:"Blessing barrel", crate:"Crate", silver:"Silver chest", bronze:"Bronze chest", skeleton:"Sleeping soldier",
  mimic:"Mimic", cursed_chest:"Cursed chest", sacrifice_chest:"Sacrificial chest", sated_chest:"Sated chest", prize:"Arena prize",
  empty:"Empty room", fountain:"Healing fountain", cleansing_fountain:"Cleansing fountain", rocks:"Falling rocks", lava:"Lava",
  narrator:"Storyteller", flooded:"Flooded room", wishing_well:"Wishing well", rps:"Rock, paper, scissors", sewers:"Sewers",
  undead:"Lantern zombie", sarcophagus:"Sarcophagus", locked_sarcophagus:"Locked sarcophagus", wood:"Wood", stone:"Stone",
  souls:"Souls", metal:"Metal", arcane:"Arcane splinters", hourglasses:"Hourglasses", wheel:"Wheel of Fortune",
  spider_legs:"Spider legs", spider_head:"Spider head", spider_full:"Full spider", curse_shop:"Curse trader", armory:"Armory",
  locker:"Locker", tube:"Tube monster", beta:"Beta beast", shakes:"Shakes", valaraukar:"Valaraukar", auction:"Auction",
  rainbow:"Rainbow", pig:"Pig", trial_monster:"Arena opponent", raider:"Raider", one_hit:"One Hit Wonder", escape:"Escape",
  disarm:"Disarm", lockpick:"Lockpick", key_moment:"Key Moment", elixir:"Elixir", recovery:"Recovery", broken_armor:"Broken armor",
  poison:"Poison", clumsy:"Clumsy", gold_hangover:"Gold hangover", hard_lock:"Hard lock", rabbit:"Soul of the Rabbit",
  moonstone:"Moonstone", spying:"Spying stone", pendant:"Key pendant", greasy:"Greasy stone", gambler:"Gambler's stone",
  greed:"Boulder of Greed", hero:"Hero's stone", time_traveler:"Time Traveler", thirsty:"Thirsty stone", pearl:"Pearl",
  blood:"Blood stone", explorer:"Explorer", misadventurer:"Misadventurer", devil:"Devil's stone", deceit:"Deceit",
  lodestone:"Lodestone", bull:"Bull's stone", hick:"Hick's stone", rusty:"Rusty healer", masochist:"Masochist", old_sacrifice:"Sacrifice stone", kidney:"Kidney stone"
};
const strategyNames = {baseline:"Original baseline", strategic:"Strategic", "key-stockpile":"Collect keys early", "preferred-stones":"Recommended strategy", learned:"Learned policy"};
const hungry = ["wood","stone","souls","metal","arcane","hourglasses"];
const stones = {
 rabbit:["Soul of the Rabbit","Escape chance +40 points.","Monster damage +25%."],
 moonstone:["Cursed Moonstone","Escape chance +20 points.","Curses last 1 additional use."],
 spying:["Spying Gem","Unlocks a chance of finding unlocked doors.","Combat key chance −15 points."],
 pendant:["Pendant of the Key Master","40% chance of a key after a successful escape.","One choice is a physical door. Special doors, including Blessing Doors, are possible."],
 greasy:["Greasy Healing Stone","After reentry: heal 10% HP per room for 3 rooms.","Epic chests no longer appear."],
 hero:["Treasure of the Hero","Monster damage −20%.","Chance of receiving items −25%."],
 thirsty:["Hope of the Thirsty One","Strong blessing chance from barrels +40 points.","More cursed doors; a cursed chest waits behind them."],
 gambler:["Boulder of the Gambler","Barrel blessing chance +50 points.","Traps inflict a curse instead of direct damage."],
 greed:["Boulder of Greed","More blessings from boxes, chests and sleeping soldiers.","More mystery rooms."],
 pearl:["Cursed Pearl","Failed escape damage −40%.","20% chance of a curse after a successful escape."],
 explorer:["Emerald of the Explorer","Fewer mystery rooms.","Strong curse chance +20 points."],
 blood:["Blood Drop of Sacrifice","Sacrificial door damage −40%.","More sacrificial doors, usually hiding a sacrificial chest."],
 rusty:["Rusty Healing Stone","Healing from effects gives 40% more HP.","After reentry: 5% poison damage per room for 5 rooms."],
 misadventurer:["Sapphire of the Misadventurer","Fewer cursed doors.","Battle curse chance +10 points."],
 time_traveler:["Diamond of the Time Traveler","Blessings last 1 additional use.","Barrel curses become more likely."],
 deceit:["Pebble of Deceit","Monster damage −20%.","Locked doors can hide monsters."],
 devil:["Crown Jewel of the Devil","Enables epic doors: 1 key for an epic chest.","Monster damage +25%."],
 lodestone:["Lodestone","Combat key chance +30 points.","Enables double-locked doors."],
 bull:["Eye of the Bull","Monster damage −20%.","Escape chance −30 points."],
 hick:["Erratic Boulder of the Hick","Fewer sacrificial doors.","Failed escape damage +30%."],
 masochist:["Masochist's Stone","Trap damage −20%.","Forces a trap when an eligible choice exists.","Legacy scenario stone; not in the current offer pool."],
 old_sacrifice:["Sacrifice Stone","Sacrificial chest damage −20%.","Locked rooms can contain sacrificial chests.","Legacy scenario stone; not in the current offer pool."],
 kidney:["Kidney Stone","Chance of a blessing after defeating a monster.","Locked rooms can contain cursed chests.","Legacy scenario stone; not in the current offer pool."]
};
for(const [id,stone] of Object.entries(stones))names[id]=stone[0];
const policyDefaults={revive_hp:.21,survival_aware:true,adaptive_gems:false,early_key_target:10,shop_allowance:5,shop_key_reserve:4,shop_mushroom_reserve:0,practical_tactics:true,patient_boss_hp:.52,neural_adviser:true,final_stone_adviser:true,stone_advice_samples:2048};
const policyFields={survival_aware:"survival-aware-input",adaptive_gems:"adaptive-gems-input",early_key_target:"early-key-target-input",shop_allowance:"shop-allowance-input",shop_key_reserve:"shop-key-reserve-input",shop_mushroom_reserve:"shop-mushroom-reserve-input"};
function randomSeed(){return crypto.getRandomValues(new Uint32Array(1))[0];}
let config={seed:randomSeed(),budget:100,run_number:1,deadline_hours:168,sleep_until_hour:0,event_start_hour:17,initial_state:"",learned_policy:"",scenario:"default",...policyDefaults};
const finalStoneExample="schema=1\nroom=76\nrun_number=2\nphase=gems\nhp_percent=1\nkeys=3\ngems=moonstone,thirsty\noffered_gems=moonstone,thirsty,hero,pearl,explorer,blood\ngem_offers=deceit,rusty,pendant\nshops_seen=2,3\ntrial_seen=true\n";
const requestedScenario = new URLSearchParams(location.search).get("scenario");
if (["default", "mushroom-school"].includes(requestedScenario)) config.scenario = requestedScenario;
if(new URLSearchParams(location.search).get("scene")==="final-stones")config={...config,run_number:2,budget:5,initial_state:finalStoneExample};
let session=null, payload=null, assets={}, busy=false, playing=false, timer=null, comparing=false, pendingAction=null, pendingActor="";
let selectedOffer=0,selectedGem=0,selectionStamp="",rerollUnlockedStamp="";
let displayedDoors=[0,1];
const esc = value => String(value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const label = value => names[value] || String(value).replaceAll("_"," ");
const asset = key => assets[key] ? `/local-assets/${encodeURIComponent(assets[key])}` : "";
const img = (key,cls="",alt="") => asset(key) ? `<img class="${cls}" src="${asset(key)}" alt="${esc(alt)}" draggable="false">` : "";
// Also covers transient reward/effect sprites created by the animation layer.
document.addEventListener('dragstart',event=>{if(event.target.closest('img'))event.preventDefault();});
const imageCache=new Map();
async function preloadScene(o){
 const stage=Math.min(4,Math.floor((o.room-1)/25)+1);
 const ids=[`background.${stage}.${o.phase==="doors"?"doors":"encounter"}`,"encounter.spider_key1",
  ...(o.doors||[]).map(d=>`door.${hungry.includes(d.kind)?"hungry":d.kind}`),
  ...(o.gems||[]).map(g=>`gem.${g}`),...(o.gem_offers||[]).map(g=>`gem.${g}`),
  ...(o.offers||[]).map(x=>`effect.${x.effect.kind}`),
  ...(o.blessings||[]).map(e=>`effect.${e.kind}`),...(o.curses||[]).map(e=>`effect.${e.kind}`),
  o.encounter==="boss"?`boss.${stage}`:`encounter.${o.encounter}`,"encounter.soldier_awake",
  `opened.${o.encounter}`,"shopkeeper","trap.beartrap","trap_right.beartrap","reward.item","reward.epic","reward.legendary",
  "cursor.interact","cursor.fight","cursor.flee","cursor.arrow","cursor.arrow_left","cursor.arrow_right","hud.stone_frame"];
 await Promise.all(ids.map(id=>{
  const src=asset(id);if(!src)return;
  if(!imageCache.has(src)){
   const image=new Image(),loaded=new Promise(resolve=>{image.onload=()=>resolve(image);image.onerror=()=>resolve(null);});
   image.src=src;
   imageCache.set(src,loaded.then(async result=>{
    // Hidden desktop views can defer decode until the next paint. A scene
    // must still become ready when the window is minimized or not yet shown.
    if(result&&!document.hidden)await Promise.race([image.decode().catch(()=>null),new Promise(resolve=>setTimeout(resolve,250))]);
    return result;
   }));
  }
  return imageCache.get(src);
 }));
}
const pct = n => `${(n*100).toFixed(1).replace(/\.0$/,"")}%`;
const num = n => n==null ? "—" : Number(n).toFixed(2);
const duration = n => {const minutes=Math.round(n*60);return `${Math.floor(minutes/60)}h ${String(minutes%60).padStart(2,"0")}m`;};
const clockTime = n => {const minutes=Math.floor((n*60+1e-7)%1440);return `${String(Math.floor(minutes/60)).padStart(2,"0")}:${String(minutes%60).padStart(2,"0")}`;};
function recoveryWaitHours(o,hours){
 const clock=((o.event_start_hour||0)+o.elapsed_hours+hours)%24;
 if(o.sleep_until_hour&&clock<o.sleep_until_hour)hours+=o.sleep_until_hour-clock;
 return Math.min(hours,o.hours_left);
}
const obs = () => payload?.observation;
const unrestrictedPolicy = () => obs()?.neural_adviser?.case === "unrestricted_budget_policy";
const suggestion = () => {
 const preferred=$("strategy").value==="preferred-stones",o=obs();
 if(preferred&&payload?.stone_advice)return payload.stone_advice.status==="ready"?payload.stone_advice.action:null;
 const neural=o?.suggestions.find(s=>s.strategy==="neural")?.action;
 if(preferred&&neural)return neural;
 const base=o?.suggestions.find(s=>s.strategy===(preferred?"key-stockpile":$("strategy").value))?.action;
 if(!preferred||base?.kind!=="choose_gem"||!o.gem_offers?.includes("pendant"))return base;
 for(const gem of ["rabbit","moonstone","greed","pendant"]){const i=o.gem_offers.indexOf(gem),a=o.legal_actions.find(a=>a.kind==="choose_gem"&&a.index===i);if(a)return a;}
 return base;
};
const same = (a,b) => !!a && !!b && a.kind===b.kind && a.index===b.index && a.value===b.value;
function legal(kind,index=0){return obs()?.legal_actions.find(a=>a.kind===kind && a.index===index);}
const decisionStamp=()=>`${session}:${payload?.steps}`;
function usefulOffers(){
 const o=obs();if(o?.phase!=="shop")return [];
 if(o.offer_analysis)return o.offer_analysis.filter(v=>v.protect_reroll).map(v=>v.index);
 const permanent=o.scenario.escape_chance+(o.gems.includes("rabbit")?.4:0)+(o.gems.includes("moonstone")?.2:0)-(o.gems.includes("bull")?.3:0);
 return o.offers.flatMap((offer,i)=>{
  if(!legal("buy",i))return [];
  const e=offer.effect,current=o.blessings.find(b=>b.kind===e.kind);
  if(current&&current.magnitude>=e.magnitude&&current.remaining>=e.remaining)return [];
  const useful=e.kind==="one_hit"||e.kind==="recovery"||
   (e.kind==="escape"&&permanent-o.curses.filter(c=>c.kind==="clumsy").reduce((n,c)=>n+c.magnitude,0)<1)||
   (e.kind==="lockpick"&&e.remaining>=4&&o.hp>=.4);
  return useful?[i]:[];
 });
}
function rerollGuarded(){return !!legal("reroll")&&usefulOffers().length>0&&rerollUnlockedStamp!==decisionStamp();}
function renderSelection(){
 const o=obs();if(!o)return;
 if(selectionStamp!==decisionStamp()){
  selectionStamp=decisionStamp();
  const a=suggestion();selectedOffer=a?.kind==="buy"?a.index:usefulOffers()[0]??o.offers?.findIndex((_,i)=>legal("buy",i))??0;
  if(selectedOffer<0)selectedOffer=0;selectedGem=a?.kind==="choose_gem"?a.index:0;
 }
 const useful=usefulOffers(),guarded=rerollGuarded();
 document.querySelectorAll('.offer').forEach(b=>{const i=Number(b.dataset.index);b.classList.toggle("selected",i===selectedOffer);b.classList.toggle("useful",useful.includes(i));b.setAttribute("aria-pressed",String(i===selectedOffer));});
 document.querySelectorAll('.gem-offer').forEach(b=>b.classList.toggle("selected",Number(b.dataset.index)===selectedGem));
 $("shop-guard").hidden=!guarded;
 $("scene").classList.toggle("shop-protected",guarded);
 $("shop-guard-message").textContent=guarded?`Useful offer: ${useful.map(i=>label(o.offers[i].effect.kind)).join(" / ")}. Manual reroll protected.`:"";
 $("unlock-reroll").disabled=busy;
 document.querySelectorAll('[data-kind="reroll"]').forEach(b=>{b.disabled=busy||guarded;b.title=guarded?"Choose a blessing, or select Keep looking to unlock rerolls.":"Space: reroll offers";});
 const buy=$("shop-buy");if(buy){
  buy.dataset.index=String(selectedOffer);buy.disabled=busy||!legal("buy",selectedOffer);
  const a=legal("buy",selectedOffer),recommended=same(a,suggestion());buy.classList.toggle("suggested",recommended);
  buy.innerHTML=`${recommended?'<span class="action-recommendation">Recommended</span>':""}<span class="action-name">${o.phase==="curse_shop"?"Accept curse":"Buy"} ${esc(label(o.offers[selectedOffer]?.effect.kind||"offer"))}</span>`;
 }
 $("keyboard-hint").textContent=o.phase==="shop"||o.phase==="curse_shop"?"← → select offer · Enter buy · Space new goods":o.phase==="gems"?"← → select stone · Enter choose":o.phase==="doors"?"← left room · → right room · Click either side to enter":"← fight / use · → escape / leave · Click either side";
 if(o.phase==="shop"||o.phase==="curse_shop")syncShopSpacing();
}
function showError(error){$("error").hidden=false;$("error").textContent=error.message||String(error);}
async function api(path,body){
 const response=await fetch(`/api/${path}`,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-SFLD-Lab":"1"},body:JSON.stringify(body)});
 const result=await response.json();if(!response.ok)throw new Error(result.error||"Request failed");return result;
}
function stop(){playing=false;clearTimeout(timer);if(payload)renderControls();}
function stoneAdviceMessage(){
 const advice=payload?.stone_advice;
 if(!advice)return "Choose one stone for the rest of this run.";
 if(advice.status==="pending")return "Comparing your three stones…";
 if(advice.status==="unavailable")return "Stone comparison unavailable. You can choose manually; autoplay is paused.";
 return advice.supported_best?"Estimated for your current run.":"Close result — these stones perform similarly.";
}
async function ensureFinalStoneAdvice(retry=false){
 if(payload?.stone_advice?.status!=="pending"&&!(retry&&payload?.stone_advice?.status==='unavailable'))return;
 const atSession=session,atSteps=payload.steps;
 if(retry){$("error").hidden=true;payload.stone_advice={...payload.stone_advice,status:'pending',samples_per_offer:config.stone_advice_samples};}
 busy=true;render();
 try{
  const next=await api("stone-advice",{session:atSession,steps:atSteps,state_key:payload.stone_advice.state_key,retry});
  if(session!==atSession||payload.steps!==atSteps)return;
  payload=next;selectionStamp="";
  if(payload.stone_advice?.status!=="ready"){stop();showError(payload.stone_advice?.message||"Stone comparison unavailable.");}
 }catch(error){
  if(session===atSession&&payload.steps===atSteps){payload.stone_advice={status:"unavailable",message:error.message};stop();showError(error);}
 }finally{if(session===atSession){busy=false;render();}}
}
function effectText(e){
 const clocks={room:"rooms",trap:"traps",door:"doors",fight:"fights"};
 const power={escape:`+${e.magnitude*100} escape points`,elixir:`${pct(e.magnitude)} health`,recovery:`${pct(e.magnitude)} health per room`,poison:`${pct(e.magnitude)} damage per room`,broken_armor:`+${pct(e.magnitude)} damage`,clumsy:`−${e.magnitude*100} escape points`,gold_hangover:`${pct(e.magnitude)} less container gold`,hard_lock:`${e.magnitude}× key costs`,key_moment:`${pct(e.magnitude)} chance of two keys`};
 const unit=e.remaining===1?e.clock:(clocks[e.clock]||e.clock);
 return `${e.remaining} ${unit}${power[e.kind]?` · ${power[e.kind]}`:""}`;
}
function actionText(a,o=obs()){
 const simple={fight:"Fight",flee:"Escape",interact:"Use / open",skip:"Leave",reroll:"New goods · 1 mushroom",reenter:"Enter dungeon",restart:"Start next run"};
 if(a.kind==="skip"&&["shop","curse_shop"].includes(o.phase))return "No thanks";
 if(a.kind==="choose_door")return `Enter ${label(o.doors?.[a.index]?.kind||"door")}`;
 if(a.kind==="buy")return `Take ${label(o.offers?.[a.index]?.effect.kind||"offer")}`;
 if(a.kind==="choose_gem")return `Choose ${label(o.gem_offers?.[a.index]||"stone")}`;
 if(a.kind==="rps")return ["Rock","Paper","Scissors"][a.index];
 if(a.kind==="interact")return o.encounter==="rps"?"Play a random round":o.encounter?.startsWith("spider_")?"Reach for the keys":o.encounter==="empty"?"Continue":"Use / open";
 if(a.kind==="fight"&&o.encounter==="mimic")return "Wake and fight";
 if(a.kind==="wait"){const hours=recoveryWaitHours(o,a.value);return o.hp==null?`Wait ${duration(hours)}`:`Wait to ${pct(Math.min(1,o.hp+hours/o.scenario.recovery_hours))} HP · ${duration(hours)}${hours>a.value+1e-9?" · includes sleep":""}`;}
 if(a.kind==="heal_step")return `+20% HP · ${o.heal_step_price} mushrooms`;
 if(a.kind==="heal_full")return `Full HP · ${o.heal_full_price} mushrooms`;
 return simple[a.kind]||label(a.kind);
}
function actionButton(a,extra=""){
 const button=document.createElement("button");button.disabled=busy;
 button.className=`${same(a,suggestion())?"suggested ":""}${extra}`;
 const detail=combatDetail(a);
 button.innerHTML=`${same(a,suggestion())?'<span class="action-recommendation">Recommended</span>':""}<span class="action-name">${esc(actionText(a))}</span>${detail?`<span class="action-detail" title="Estimated from the simulator. Hit/escape survival excludes later healing, poison and newly rolled effects.">${esc(detail)}</span>`:""}`;
 button.dataset.kind=a.kind;button.dataset.index=a.index;
 button.onclick=()=>act(a,true);return button;
}
function probabilityText(value){
 if(value<=0)return "0%";if(value>=1)return "100%";
 if(value>.999)return ">99.9%";if(value<.001)return "<0.1%";
 return `${(value*100).toFixed(1).replace(/\.0$/,"")}%`;
}
function combatDetail(a){
 if(!['fight','flee'].includes(a.kind))return "";
 const forecast=obs().combat_forecast?.[a.kind];
 if(!forecast)return a.kind==='flee'&&obs().combat_advice?`${pct(obs().combat_advice.escape_chance)} escape chance · damage estimate unavailable`:"Damage estimate unavailable";
 const maximum=`${Number((Math.ceil(forecast.max_damage*10000-1e-8)/100).toFixed(2))}% HP`;
 const lines=a.kind==='flee'?[`${probabilityText(forecast.escape_chance)} escape`,forecast.escape_chance>=1?'No hit':`Max failed hit ${maximum}`]:[`Max hit ${maximum}`];
 if(forecast.survival<1)lines.push(`Est. ${probabilityText(forecast.survival)} survive ${a.kind==='fight'?'hit':'attempt'}`);
 return lines.join(' · ');
}
function currencyIcon(kind,alt=""){
 return img(`hud.${kind}`,`currency-icon ${kind}-icon`,alt)||`<span class="currency-fallback">${kind==='mushroom'?'M':'Bag'}</span>`;
}
let recoveryStamp="",selectedRecoveryWait=null;
function renderRecovery(o,recommended){
 const panel=$("recovery-picker");panel.hidden=o.phase!=="recovery"||o.terminal;
 if(panel.hidden)return null;
 const options=[];
 const waits=DungeonPresentation.recoveryActions(o,recommended).filter(a=>a.kind==='wait');
 waits.sort((a,b)=>a.value-b.value);
 for(const action of waits){
  const hours=recoveryWaitHours(o,action.value),hp=Math.min(1,o.hp+hours/o.scenario.recovery_hours);
  const previous=options.findIndex(item=>Math.abs(item.hp-hp)<1e-8);
  if(previous<0)options.push({action,hours,hp});else if(same(action,recommended))options[previous]={action,hours,hp};
 }
 if(recoveryStamp!==decisionStamp()||!options.some(item=>item.action.value===selectedRecoveryWait)){
  recoveryStamp=decisionStamp();selectedRecoveryWait=(options.find(item=>same(item.action,recommended))||options[0])?.action.value??null;
 }
 const chosen=options.find(item=>item.action.value===selectedRecoveryWait);
 panel.innerHTML=`<div class="recovery-heading"><div><small>ROOM ${o.room} / 100</small><h2>Recover health</h2></div><span class="recovery-current">${pct(o.hp)}<small>current health</small></span></div>${chosen?`
  <div class="recovery-meter" aria-hidden="true"><span class="recovery-target-fill" style="width:${chosen.hp*100}%"></span><span class="recovery-current-fill" style="width:${o.hp*100}%"></span></div>
  <p class="recovery-target-label" id="recovery-target-label">Choose your target health</p>
  <div class="recovery-targets" role="group" aria-labelledby="recovery-target-label">${options.map((item,i)=>`<button type="button" data-recovery-index="${i}" aria-pressed="${item===chosen}" class="${same(item.action,recommended)?'suggested ':''}${item===chosen?'selected':''}" ${busy?'disabled':''}><strong>${pct(item.hp)}</strong><small>${same(item.action,recommended)?'Recommended':item===chosen?'Selected':'Health'}</small></button>`).join('')}</div>
  <div class="recovery-plan" aria-live="polite"><div><small>Free recovery</small><strong>${duration(chosen.hours)}</strong></div><div><small>Health afterward</small><strong>${pct(chosen.hp)}</strong></div>${chosen.hours>chosen.action.value+1e-8?'<span>Includes scheduled sleep</span>':''}</div>`:'<p>Health is ready. Enter when you are ready.</p>'}`;
 const choose=i=>{selectedRecoveryWait=options[i].action.value;render();$(`recovery-picker`).querySelector(`[data-recovery-index="${i}"]`)?.focus({preventScroll:true});};
 panel.querySelectorAll('[data-recovery-index]').forEach(button=>{
  const index=Number(button.dataset.recoveryIndex);button.onclick=()=>choose(index);
  button.onkeydown=event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();event.stopPropagation();choose(event.key==='Home'?0:event.key==='End'?options.length-1:Math.max(0,Math.min(options.length-1,index+(event.key==='ArrowRight'?1:-1))));};
 });
 return chosen;
}
function stoneBody(id,showRating=false){
 const s=[...(stones[id]||[label(id),"Effects unavailable.",""])];
 if(id==="time_traveler"&&obs()?.scenario?.time_traveler_elixir_full_heal)s[1]+=" Instant healing potions restore full health.";
 const rating=showRating?obs()?.stone_ratings?.entries.find(r=>r.gem===id):null;
 const comparison=showRating?payload?.stone_advice:null,simulated=comparison?.status==="ready"?comparison.rankings.find(r=>r.stone===id):null;
 const badge=simulated?`<span class="stone-simulation-rating"><b>#${simulated.rank}</b><span>${duration(simulated.mean_hours)}<small>${simulated.completion_probability<1?"Average time (deadline capped)":"Average time to finish"}</small></span></span><small class="stone-note">${simulated.mean_mushrooms.toFixed(2)} mushrooms on average${simulated.completion_probability<1?` · ${pct(simulated.completion_probability)} finish before the deadline`:""}</small>`:
  comparison?'<span class="stone-note">'+(comparison.status==="pending"?"Comparing offers…":"Comparison unavailable")+'</span>':
  rating?`<span class="stone-rating" data-tier="${esc(rating.tier)}"><b>${esc(rating.tier)}</b><span>${rating.points.toFixed(1)} / 100<small>For your current build</small></span></span>`:showRating?'<span class="stone-note">Rating unavailable</span>':"";
 return `${img(`gem.${id}`,"stone-icon",label(id))}<strong>${esc(s[0])}</strong>${badge}<small class="stone-good">+ ${esc(s[1])}</small><small class="stone-bad">− ${esc(s[2])}</small>`;
}
function offerAnalysis(index){
 const value=obs()?.offer_analysis?.[index];if(!value)return "";
 const current=value.current,lines=[];
 if(current)lines.push(`You have: ${effectText(current)}`);
 if(value.displaced&&!current)lines.push(`Replaces ${label(value.displaced.kind)} · ${value.displaced.remaining} uses left`);
 if(value.acquired.kind==="recovery"){
  const extra=value.extra_healing,sign=extra>0?"+":"";
  lines.push(`${sign}${pct(extra)} extra potential healing${current&&current.magnitude===value.acquired.magnitude?` · ${value.extra_uses>0?"+":""}${value.extra_uses} ticks`:""}`);
  lines.push(`Heals on exit · ${Math.max(0,value.acquired.remaining-1)} rooms left afterward`);
 }else if(value.acquired.kind==="elixir")lines.push(`${pct(value.extra_healing)} healing now`);
 else if(current)lines.push(`${value.extra_uses>0?"+":""}${value.extra_uses} uses after leaving${current.magnitude!==value.acquired.magnitude?" · strength changes":""}`);
 if(value.immediate_healing_lost)lines.push(`Loses this exit's ${pct(value.immediate_healing_lost)} Recovery heal`);
 if(value.status==="small_refresh")lines.push("Small refresh · save your keys");
 if(value.status==="save_last_key")lines.push("Existing tick fills HP · save your last key");
 if(value.status==="no_gain")lines.push("No improvement over your current effects");
 if(value.status==="lethal_exit")lines.push("Poison would kill you on exit");
 return `<span class="offer-analysis ${["small_refresh","save_last_key","no_gain","lethal_exit"].includes(value.status)?"low-value":""}">${lines.map(line=>`<span>${esc(line)}</span>`).join("")}</span>`;
}
function adviceReason(){
 if(payload?.stone_advice)return ""; // Its own comparison note replaces the old actor's reason.
 const o=obs(),a=suggestion();if(!a)return "";
 return o.neural_adviser?.reason||o.suggestions.find(s=>same(s.action,a)&&s.reason)?.reason||"";
}
function journalAction(j){
 if(j.choice)return `Enter ${label(j.choice)}`;
 if(j.offer)return `Take ${label(j.offer.effect.kind)} · ${j.offer.keys} keys`;
 if(j.gem)return `Choose ${label(j.gem)}`;
 return actionText(j.action,{encounter:j.encounter,heal_step_price:j.price,heal_full_price:j.price});
}
function resultLines(j){
 const lines=[];
 if(j.revealed)lines.push(`Inside: ${label(j.revealed)}.`);
 for(const e of j.outcomes||[]){
  const n=e.amount,sign=n>0?"+":"",detail=label(e.detail);
  const simple={escaped:"Escaped successfully.",escape_failed:"Escape failed.",defeated:`Defeated ${detail.toLowerCase()}.`,awakened:"The soldier woke up. Choose fight or escape.",
   disarmed:"Trap disarmed.",completed:"Dungeon completed.",item:"Received an ordinary item.",nothing:"Nothing found.",
   cleansed:`Removed ${n} curse${n===1?"":"s"}.`,trap:e.detail==="curse"?"Cursed trap triggered.":"Damage trap triggered.",
   death:e.detail==="next_choice"?`Died. Reenter at the choices for room ${j.next_room}.`:`Died. The boss must be retried.`,
   epics:`Received ${n} epic item${n===1?"":"s"}.`,legendaries:`Received ${n} legendary item${n===1?"":"s"}.`,
   spider:e.detail==="success"?"The keys are yours.":"The spider bit you.",rps:`Random round: ${e.detail}.`};
  if(e.kind in simple)lines.push(simple[e.kind]);
  else if(e.kind==="damage")lines.push(`${e.detail==="poison"?"Poison":j.action.kind==="flee"?"Failed escape hit":j.action.kind==="fight"?"Enemy hit":"Damage"}: −${pct(n)} HP.`);
  else if(e.kind==="healing")lines.push(`+${pct(n)} HP.`);
  else if(e.kind==="sleep")lines.push(`Offline for ${duration(n)} during scheduled sleep.`);
  else if(e.kind==="blessing"||e.kind==="curse")lines.push(`${e.kind==="blessing"?"Blessing":"Curse"}: ${detail}${e.duration?` · ${e.duration} uses`:""}.`);
  else if(e.kind==="gold")lines.push(`+${DungeonPresentation.goldValue(n)} gold.`);
  else if(e.kind==="resource")lines.push(`${sign}${n} ${detail.toLowerCase()} reward units.`);
  else if(e.kind==="mushrooms_spent")lines.push(`${n} mushrooms spent.`);
  else if(e.kind==="mushrooms_found")lines.push(`${n} mushrooms found.`);
  else lines.push(`${sign}${n} ${label(e.kind)}.`);
 }
 if(["fight","flee"].includes(j.action.kind)&&j.encounter!=="boss"&&j.active_curses_before?.some(e=>e.kind==="broken_armor"))
  lines.push("Broken Armor was active: +50% combat damage.");
 if(!lines.length)lines.push(j.action.kind==="skip"?"Left without interacting.":j.action.kind==="reroll"?"Both offers were replaced.":j.action.kind==="choose_gem"?"Stone active for this run.":j.action.kind==="reenter"?`Reentered room ${j.next_room}.`:j.action.kind==="restart"?"New run. Full health.":j.next_phase==="shop"?"Choose one of the two offers.":j.action.kind==="interact"?"No reward this time.":"Moved to the next choice.");
 return lines;
}
function renderOutcome(){
 const j=payload.journal.at(-1);
 $("outcome-title").textContent=j?`Run ${j.run} · room ${j.room} — ${journalAction(j)}`:"Choose a room to begin.";
 $("outcome-details").innerHTML=j?resultLines(j).map(s=>`<span>${esc(s)}</span>`).join(""):"";
 $("outcome").classList.toggle("death-result",!!j?.death);
}
function renderControls(){
 const a=suggestion(),o=obs();
 $("recommendation").textContent=a?`${strategyNames[$("strategy").value]} suggests: ${actionText(a)}.`:o.terminal?"Event finished. Save the decisions or start another event.":"No suggestion for this scene.";
 if(payload.stone_advice)$("recommendation").textContent=payload.stone_advice.status==="ready"?`Simulated choice: ${actionText(a)}. ${payload.stone_advice.supported_best?"Supported best among these offers.":"Lowest estimate; close result."}`:stoneAdviceMessage();
 const settings=[];
 if($("strategy").value==="preferred-stones")settings.push("Collect keys early, with Rabbit → Cursed Moonstone → Boulder of Greed → Pendant when Pendant is offered");
 if(config.survival_aware)settings.push("Survival probabilities");
 if(config.adaptive_gems)settings.push("Stone combinations (experimental)");
 if(config.early_key_target)settings.push(`Early key target: ${config.early_key_target}`);
 settings.push(`${pct(config.revive_hp)} recovery target; higher near the final boss`);
 if(config.shop_allowance)settings.push(`${config.shop_allowance} refresh mushrooms per paid shop; ${config.shop_key_reserve} keys before refreshing`);
 if(config.shop_mushroom_reserve)settings.push(`${config.shop_mushroom_reserve} mushrooms reserved for refreshes`);
 $("strategy-settings-summary").textContent=o.neural_adviser?`Trained policy · optimizes completions within your ${config.budget}-mushroom event budget. Refreshes, recovery and key spending follow the policy.`:o.adviser_unavailable||($("strategy").value==="learned"&&settings.length?"Optional strategy settings apply to the three named strategies, not this loaded policy.":settings.join(" · "));
 $("strategy-settings-summary").hidden=!settings.length;
 $("step-policy").disabled=busy||!a;$("autoplay").disabled=!playing&&(busy||!a);
 $("autoplay").textContent=playing?"Pause":"Watch play";$("play-state").textContent=playing?"Watching":"Manual";
 $("fullscreen-play").textContent=playing?"Pause":"Watch play";$("fullscreen-play").disabled=!playing&&(busy||!a);
 $("game-play").disabled=!playing&&(busy||!a);$("game-play").classList.toggle("is-playing",playing);
 $("game-play").querySelector(".play-label").textContent=playing?"Pause":"Watch play";
 $("game-play").querySelector(".play-icon").textContent=playing?"Ⅱ":"▶";
 $("game-step").disabled=busy||!a;$("game-undo").disabled=busy||!payload.steps;$("game-menu").disabled=busy;
 $("menu-progress").innerHTML=`Run ${o.run_number} · room ${o.room} / 100 · ${o.completed_runs} clears<br>${currencyIcon('mushroom','Mushrooms')} ${mushroomBalance(o)} balance · ${o.mushrooms_spent} / ${config.budget} budget spent<br>${duration(o.run_elapsed_hours)} this run · ${o.run_mushrooms_spent} mushrooms this run · ${duration(o.elapsed_hours)} event time${o.sleep_until_hour?`<br>${clockTime(o.local_hour)} local · sleep 00:00–${clockTime(o.sleep_until_hour)}`:""}`;
 $("menu-recommendation").textContent=a?actionText(a):o.terminal?"Event finished":stoneAdviceMessage()||"Choose a move";
 $("menu-reason").textContent=adviceReason()||o.trial_advice?.reason||"";
 $("undo").disabled=busy||!payload.steps;$("export").disabled=busy;$("new-run").disabled=busy;
 $("import-run").disabled=busy;$("learned-load").disabled=busy;$("step-count").textContent=`${payload.steps} actions`;
}
function inspectHud(html){
 const panel=$("effect-inspector");panel.innerHTML=html+'<button type="button">Close</button>';panel.hidden=false;
 panel.querySelector("button").onclick=()=>panel.hidden=true;
}
function renderHudEffects(o){
 const host=$("scene-hud").querySelector('.hud-effect-groups,.hud-effects');
 host.className="hud-effect-groups";
 host.innerHTML=["blessings","curses"].map(group=>`<div class="hud-effect-group ${group}"><small>${group==="curses"?"Curses":"Blessings"}</small><div class="hud-effects" aria-label="Three ${group} slots">${Array.from({length:3},(_,slot)=>{
  const e=o[group][slot],position=`data-effect-group="${group}" data-effect-slot="${slot}"`;
  return e?`<button class="hud-effect ${group==="curses"?"curse":""} ${e.starts_at>o.turn?"next":""}" ${position} data-effect-kind="${esc(e.kind)}" title="${esc(label(e.kind)+': '+effectText(e))}" aria-label="${esc(label(e.kind)+', '+effectText(e))}">${img(`effect.${e.kind}`)||`<span class="effect-fallback">${esc(label(e.kind))}</span>`}<b>${e.remaining}</b></button>`:`<span class="empty-slot" ${position} aria-hidden="true"></span>`;
 }).join("")}</div></div>`).join("");
 host.querySelectorAll('[data-effect-kind]').forEach(button=>button.onclick=()=>{
  const e=o[button.dataset.effectGroup][Number(button.dataset.effectSlot)];
  inspectHud(`<strong>${esc(label(e.kind))}</strong><p>${esc(effectText(e))}${e.starts_at>o.turn?" · starts next room":""}</p>`);
 });
}
function mushroomBalance(o){return (config.wallet_mushrooms??config.budget)+(o.mushrooms_found||0)-o.mushrooms_spent;}
function renderHud(o){
 const stoneButtons=o.gems.map((g,i)=>`<button class="hud-stone" data-stone="${i}" aria-label="${esc(label(g))}" title="${esc(label(g))}">${img(`gem.${g}`,"",label(g))||"◆"}</button>`).join("")+Array.from({length:Math.max(0,3-o.gems.length)},()=>'<span class="hud-stone empty" aria-hidden="true"></span>').join("");
 $("scene-hud").innerHTML=`
  <div class="hud-stone-cluster">${img('hud.stone_frame','hud-stone-frame')}<div class="hud-stones">${stoneButtons}</div><div class="hud-location" title="Run ${o.run_number} · Stage ${Math.min(4,Math.floor((o.room-1)/25)+1)}"><strong id="hud-room-counter" aria-label="Room ${Math.min(100,o.room)} of 100">${Math.min(100,o.room)}/100</strong></div></div>
 <div class="hud-bottom"><div class="hud-stone-spacer" aria-hidden="true"></div>
  <div class="hud-center"><div class="hud-effect-groups" aria-label="Active blessings and curses"></div><div class="hud-health" id="hud-health" role="progressbar" aria-label="Health percentage" aria-valuemin="0" aria-valuemax="100"><div id="hud-health-fill" class="hud-health-fill"></div><strong id="hud-health-number"></strong></div></div>
  <div class="hud-right"><div class="hud-keys" aria-label="${o.keys} keys">${img('encounter.spider_key1','hud-key-icon')}<b><span id="hud-key-count">${o.keys}</span><span aria-hidden="true">×</span></b></div></div>
 </div>`;
 DungeonPresentation.updateHealth(o.hp);
 renderHudEffects(o);
 $("scene-hud").querySelectorAll("[data-stone]").forEach(button=>button.onclick=()=>{const s=stones[o.gems[Number(button.dataset.stone)]];inspectHud(`<strong>${esc(s[0])}</strong><p>+ ${esc(s[1])}</p><p>− ${esc(s[2])}</p>`);});
 const counts=payload.loot?.counts||{ordinary:0,epic:0,legendary:0},total=Object.values(counts).reduce((a,b)=>a+b,0);
 const bag=$('hud-loot');
 bag.innerHTML=`${currencyIcon('backpack')}<b>${total}</b><small>Kept</small>`;
 bag.title=`${counts.ordinary} ordinary · ${counts.epic} epic · ${counts.legendary} legendary items kept`;
 bag.setAttribute('aria-label',`View ${total} kept items`);
 bag.onclick=()=>{$('play-menu').close();inspectHud(`<strong>Items kept this event</strong><div class="kept-items">${['ordinary','epic','legendary'].map(kind=>`<div>${img(`reward.${kind==='ordinary'?'item':kind}`)}<b>${counts[kind]}</b><span>${kind==='ordinary'?'Ordinary':kind==='epic'?'Epic':'Legendary'}</span></div>`).join('')}</div><p>All items are kept automatically.</p>`);$('effect-inspector').querySelector('button').focus();};
}
function render(){
 const o=obs(),stage=Math.min(4,Math.floor((o.room-1)/25)+1),recommended=suggestion();
 $("chapter").textContent=`RUN ${o.run_number} / STAGE ${stage}`;$("room").textContent=Math.min(100,o.room);
 $("hp").textContent=pct(o.hp);$("hp-fill").style.width=pct(o.hp);$("keys").textContent=o.keys;
 $("mushrooms").textContent=o.mushrooms_left;$("time").textContent=duration(o.elapsed_hours);$("completions").textContent=o.completed_runs;
 $("deaths").textContent=`${o.deaths} deaths`;$("room-phase").textContent=o.phase.toUpperCase();
 $("run-time").textContent=duration(o.run_elapsed_hours);$("run-mushrooms").textContent=`${o.run_mushrooms_spent} mushrooms`;
 $("run-history").innerHTML=payload.completed_runs?.map(r=>`<div>Run ${r.run}: ${duration(r.hours)} · ${r.mushrooms} mushrooms</div>`).join("")||"No completed runs yet.";
 $("scene").dataset.phase=o.phase;
 $("scene").classList.toggle("mimic-visible",o.phase==="encounter"&&o.encounter==="mimic");
 renderHud(o);$("effect-inspector").hidden=true;
 $("scene-title").textContent=o.terminal?"Event finished":({doors:"Choose your next room",shop:"Choose one blessing",curse_shop:"Trade a curse for keys",gems:"Choose a stone of fate",recovery:"Recover your health",complete:"Dungeon completed"}[o.phase]||label(o.encounter));
 const world=$("scene-world");
 // Cancel the held zoom in this render task, so it cannot snap back on screen
 // before the new room's content and background arrive together.
 world.getAnimations().forEach(animation=>animation.cancel());world.style.transformOrigin="";
 $("scene").classList.remove("entering-room");
 const bg=asset(`background.${stage}.${o.phase==="doors"?"doors":"encounter"}`);world.style.backgroundImage=bg?`url("${bg}")`:"";
 let content="",note="",custom=new Set();
 if(o.terminal){content=`<div class="recovery-card"><h2>${o.completed_runs} completions</h2><p>${duration(o.elapsed_hours)} simulated · ${o.mushrooms_spent} mushrooms spent<br>${o.deaths} deaths · ${payload.steps} decisions</p></div>`;note=o.action_limit?"Stopped at the action limit; this result is incomplete.":"The event's available time has ended.";}
 else if(o.phase==="doors"){
  displayedDoors=recommended?.kind==='choose_door'?[recommended.index,1-recommended.index]:[0,1];
  content=displayedDoors.map((i,slot)=>{
   const d=o.doors[i],forecast=o.door_forecast?.find(v=>v.index===i);
   const a=legal("choose_door",i),key=hungry.includes(d.kind)?"hungry":d.kind;
   const nativeRight=slot===1&&asset(`door_right.${key}`),art=nativeRight?`door_right.${key}`:`door.${key}`;
   const trapped=d.trap&&d.kind!=="golden",cursed=d.cursed_trap||o.gems.includes("gambler");
   const price=d.key_cost>0?`${d.key_cost} key${d.key_cost===1?"":"s"}`:hungry.includes(d.kind)?`Feed ${label(d.kind).toLowerCase()}`:"";
   const odds=forecast&&d.kind!=='wall'?`<span class="door-odds" title="Configured model estimate for a live monster on entry. Sleeping soldiers waking and optional mimic fights are separate.">Monster ${forecast.monster_chance>0&&forecast.monster_chance<1?'≈ ':''}${pct(forecast.monster_chance)}</span>`:"";
   const hazard=forecast?.entry_curse_chance?`<span class="door-hazard">${pct(forecast.entry_curse_chance)} curse risk</span>`:forecast?.entry_damage?`<span class="door-hazard">−${pct(forecast.entry_damage)} HP on entry</span>`:"";
   return `<button class="door-choice ${same(a,recommended)?"recommended":""} ${trapped?"trapped":""}" data-door-type="${esc(key)}" data-kind="choose_door" data-index="${i}" data-slot="${slot}" ${!a||busy?"disabled":""} aria-label="Enter ${esc(label(d.kind))}${trapped?", trapped":""}${forecast?`, monster chance ${pct(forecast.monster_chance)}`:""}"><span class="door-art ${slot===1&&!nativeRight?'mirrored':''}">${img(art)||`<span class="door-fallback">${d.kind==="wall"?"×":"◆"}</span>`}</span><span class="door-info">${odds}${price?`<span class="door-price">${esc(price)}</span>`:""}${hazard}</span>${trapped?`${img(slot===1&&asset("trap_right.beartrap")?"trap_right.beartrap":"trap.beartrap","trap-art")}<span class="trap-mark ${cursed?"cursed":""}">⚠ ${cursed?"CURSE TRAP":"TRAP"}</span>`:""}</button>`;
  }).join("");custom.add("choose_door");note=o.doors.some(d=>d.kind==="shop")?"Compulsory shop: the other passage is a wall.":"Choose a door to reveal its encounter.";
 }else if(o.phase==="shop"||o.phase==="curse_shop"){
  content=img(o.phase==="curse_shop"?"encounter.curse_shop":"shopkeeper","shopkeeper",o.phase==="curse_shop"?"Curse trader":"Blessing shopkeeper")+`<div class="offer-grid">${o.offers.map((offer,i)=>{
   const a=legal("buy",i);return `<button class="offer ${same(a,recommended)?"recommended":""}" data-kind="buy" data-index="${i}" ${!a||busy?"disabled":""}>${same(a,recommended)?'<span class="offer-suggestion">Recommended</span>':""}<span class="offer-selected">✓ Selected</span>${img(`effect.${offer.effect.kind}`)}<strong>${esc(label(offer.effect.kind))}</strong><small>${esc(effectText(o.offer_analysis?.[i]?.acquired||offer.effect))}</small>${offerAnalysis(i)}<span class="offer-price">${o.phase==="curse_shop"?"Receive ":offer.keys===0?"":"Cost: "}${offer.keys===0?"Free":`${offer.keys} key${offer.keys===1?"":"s"}`}</span></button>`;
  }).join("")}</div>`;custom.add("buy");note=o.phase==="curse_shop"?"Accept one curse to receive its keys and leave, or decline both offers.":o.run_number===1&&o.room===5?"Opening shop: one free blessing. No rerolls.":"One purchase ends the shop. Rerolls replace both offers.";
  if(o.phase==="shop"&&!o.neural_adviser&&config.shop_allowance&&!(o.run_number===1&&o.room===5)){
   const earned=config.shop_allowance*(o.reroll_shop_visits||0),used=o.reroll_bank_used||0;
   note+=` Strategy allowance: ${earned} earned, ${used} spent, ${Math.max(0,earned-used)} left. Needs ${config.shop_key_reserve} keys before refreshing; event budget still applies.`;
  }
 }else if(o.phase==="gems"){
  content=`<div class="stone-choice"><div class="stone-choice-heading"><h2>Choose a stone</h2><p>${o.gems.length?`Your stones: ${o.gems.map(g=>esc(label(g))).join(" · ")}`:"Your first stone this run"}</p></div><div class="gem-offers">${o.gem_offers.map((gem,i)=>`<button class="gem-offer ${same(legal("choose_gem",i),recommended)?"recommended":""}" data-kind="choose_gem" data-index="${i}" ${busy?"disabled":""}>${same(legal("choose_gem",i),recommended)?'<span class="stone-recommendation">Recommended pick</span>':""}${stoneBody(gem,true)}</button>`).join("")}</div><p class="stone-rating-note" role="status">${esc(stoneAdviceMessage())}</p></div>`;custom.add("choose_gem");note=payload.stone_advice?"Fresh final-stage simulations · no game time or mushrooms spent calculating.":"Ratings account for your stones, health, stage and event budget.";
 }else if(o.phase==="recovery"){
  content="";note=`Natural recovery: full health in ${o.scenario.recovery_hours} hours.`;
 }else if(o.phase==="complete"){
  content=`<div class="recovery-card"><h2>${o.phase==="complete"?"Room 100 cleared":pct(o.hp)+" health"}</h2><p>${o.phase==="complete"?"Your next run starts at full health.<br>The event's remaining time and budget carry over.":"You need at least 20% health to reenter.<br>Choose a recovery target below, or use a refill."}</p></div>`;note=o.phase==="complete"?"Keys, stones and effects reset with the next run.":`Natural recovery: full health in ${o.scenario.recovery_hours} hours.`;
 }else{
  let key=o.encounter==="boss"?`boss.${stage}`:`encounter.${o.encounter}`;
  const awakened=o.encounter==="monster"&&payload.journal.some(j=>j.room===o.room&&j.run===o.run_number&&j.outcomes?.some(e=>e.kind==="awakened"));
  if(awakened)key=asset("encounter.soldier_awake")?"encounter.soldier_awake":key;
  if(o.encounter==="flooded")key="encounter.sewers";
  const encounterLabel=awakened?"Awakened soldier":label(o.encounter);
  content=img(key,"encounter-art",encounterLabel)||`<div class="encounter-fallback">${o.encounter==="empty"?"·":"◆"}</div>`;
  content+=`<span class="encounter-tag">${esc(encounterLabel)}${o.encounter==="trial_monster"?` · tier ${o.trial_depth+1}`:""}</span>`;
  if(o.encounter==="mimic")content+='<div class="mimic-warning" role="status"><strong><span aria-hidden="true">⚠</span> MIMIC · FAKE CHEST</strong><span>A monster in disguise. Fight wakes it; Leave avoids the encounter.</span></div>';
  if(o.encounter.startsWith("spider_")){
   const i=["spider_legs","spider_head","spider_full"].indexOf(o.encounter),state=["Only the legs are visible","Head visible","Entire spider visible"][i],reward=[1,2,5][i];
   content=`<div class="spider-scene ${o.encounter}">${img("encounter.spider_web","spider-web")}<div class="spider-window">${img("encounter.spider_full","spider-creature")}</div>${img(`encounter.spider_key${i+1}`,"spider-keys")}</div><div class="spider-status"><strong>${state}</strong><span>${reward} key${reward===1?"":"s"} · ${pct(o.scenario.spider_success[i])} success (provisional)</span><small>Failure: poison for ${o.gems.includes("moonstone")?6:5} rooms${o.scenario.spider_bite_damage?` and ${pct(o.scenario.spider_bite_damage)} damage`:""}.</small></div>`;
  }
  note=o.encounter==="flooded"?`Water timer: ${o.flooded_seconds.toFixed(1)} seconds. It advances when you act.`:o.encounter==="empty"?"This room contains nothing. Continue to the next room.":o.encounter==="trial_monster"?"Escaping also advances the arena. Death loses its pending reward.":o.encounter==="skeleton"?"Search the soldier. It may wake up.":"Choose an action below.";
 }
 $("scene-content").innerHTML=content;$("scene-note").textContent=note;
 if(o.phase==='gems'&&payload.stone_advice?.status==='unavailable'){
  const retry=document.createElement('button');retry.id='retry-stone-advice';retry.textContent='Retry comparison';retry.disabled=busy;
  retry.onclick=()=>ensureFinalStoneAdvice(true);document.querySelector('.stone-choice').append(retry);
 }
 $("scene-content").querySelectorAll("[data-kind]").forEach(button=>button.onclick=()=>{
  if(button.dataset.kind==="buy"){selectedOffer=Number(button.dataset.index);renderSelection();return;}
  const a=legal(button.dataset.kind,Number(button.dataset.index));if(a)act(a,true);
 });
 $("actions").replaceChildren();$("refills").replaceChildren();
 const recovery=renderRecovery(o,recommended);
 const refills=document.querySelector('.recovery-tools');
 refills.hidden=o.phase!=="recovery"||o.terminal;
 if(refills.dataset.decision!==decisionStamp()){
  refills.dataset.decision=decisionStamp();refills.open=!!recommended?.kind.startsWith('heal_');
 }
 for(const a of DungeonPresentation.recoveryActions(o,recommended)){
  if(o.phase==='recovery'&&a.kind==='wait')continue;
  if(o.phase==="encounter"&&o.encounter==="empty"&&a.kind==="skip")continue;
  if(a.kind.startsWith("heal_")){if(o.phase==="recovery"&&!o.terminal)$("refills").append(actionButton(a));}
  else if(!custom.has(a.kind))$("actions").append(actionButton(a));
 }
 if(recovery){
  const button=actionButton(recovery.action);button.id='wait-selected';
  button.querySelector('.action-name').textContent=`Wait ${duration(recovery.hours)}`;
  button.setAttribute('aria-label',`Wait ${duration(recovery.hours)} to reach ${pct(recovery.hp)} health`);
  $("actions").prepend(button);
 }
 if(o.phase==="shop"||o.phase==="curse_shop"){
  if(o.phase==="shop"&&!legal("reroll")){const restock=document.createElement('button');restock.textContent='New goods';restock.disabled=true;restock.className='shop-restock';restock.title='This shop cannot be rerolled.';$("actions").append(restock);}
  const buy=document.createElement('button');buy.id='shop-buy';buy.dataset.kind='buy';buy.className='shop-buy';buy.textContent=o.phase==='curse_shop'?'Accept curse':'Buy';buy.onclick=()=>act(legal('buy',selectedOffer),true);$("actions").append(buy);
 }
 $("refill-note").textContent=`20% refill: ${o.heal_step_price} mushrooms. +5 after each purchase; deaths do not reset it.`;
 const refillPrices=o.legal_actions.filter(a=>a.kind.startsWith('heal_')).map(a=>a.kind==='heal_step'?o.heal_step_price:o.heal_full_price);
 $("refill-price").textContent=refillPrices.length?`From ${Math.min(...refillPrices)}`:'Unavailable';
 if(!refillPrices.length)$("refill-note").textContent=o.hp>=1?'Health is full.':'Not enough mushrooms remain in your event budget.';
 renderSelection();
 $("effects").innerHTML=[...o.blessings.map(e=>[e,false]),...o.curses.map(e=>[e,true])].map(([e,curse])=>`<div class="effect ${curse?"curse":""}">${img(`effect.${e.kind}`)}<div><strong>${esc(label(e.kind))}</strong><small>${esc(effectText(e))}${e.starts_at>o.turn?" · starts next room":""}</small></div></div>`).join("")||'<p class="empty-note">No active blessings or curses.</p>';
 $("gems").innerHTML=o.gems.map(g=>`<div class="gem">${stoneBody(g)}</div>`).join("")||'<p class="empty-note">Your first stone follows the stage-one boss.</p>';
 $("journal").innerHTML=payload.journal.slice().reverse().map(j=>{
  const hp=j.hp_after-j.hp_before,delta=[];
  if(Math.abs(hp)>.00001)delta.push(`<span class="${hp<0?"delta-bad":"delta-good"}">${hp>0?"+":"−"}${pct(Math.abs(hp))} HP</span>`);
  if(j.keys_delta)delta.push(`${j.keys_delta>0?"+":""}${j.keys_delta} keys`);
  if(j.mushrooms_delta)delta.push(`${j.mushrooms_delta} mushrooms spent`);
  if(j.death)delta.push('<span class="delta-bad">Died</span>');
  return `<li>R${j.run} · room ${j.room}: ${esc(journalAction(j))}<small>${resultLines(j).map(esc).join(" · ")} · ${duration(j.hours)}</small></li>`;
 }).join("")||'<li class="empty-note">Your decisions and their health, key and mushroom changes appear here.</li>';
 $("compare-scope").textContent=`${config.deadline_hours} hours · ${config.budget} mushroom cap · starts at ${config.initial_state?"the entered progress":"run "+config.run_number+", room 1"}. Compares the three reference strategies${config.learned_policy?" and the loaded policy":""}; the viewer's custom stone order is excluded. Policies share a seed set.`;
 $("model-status").textContent=`C++ ${o.engine} · ${o.scenario.id} · ${pct(o.scenario.escape_chance)} base escape · ${Object.keys(assets).length?"local game artwork":"art-free view"}`;
 renderOutcome();renderControls();
 const reason=adviceReason();$("scene-advice").textContent=reason;$("scene-advice").hidden=true;
 if(o.phase==="shop"||o.phase==="curse_shop")syncShopSpacing();
 updateSceneCursor();
 $("decision-banner").hidden=!pendingAction;
 if(pendingAction){
  $("decision-banner").textContent=`${pendingActor}: ${actionText(pendingAction,o)}`;
  document.querySelectorAll(`[data-kind="${pendingAction.kind}"][data-index="${pendingAction.index}"]`).forEach(b=>b.classList.add("chosen"));
 }
}
async function act(action,manual=false,actor=""){
 if(busy||!session||!action)return;
 if(action.kind==="reroll"&&rerollGuarded()&&(manual||!unrestrictedPolicy())){stop();renderSelection();return;}
 if(manual)stop();busy=true;pendingAction=action;pendingActor=actor||(manual?"Selected action":strategyNames[$("strategy").value]);$("error").hidden=true;render();
 try{
  if(!manual)await new Promise(resolve=>setTimeout(resolve,Math.min(160,Number($("speed").value)/4)));
  const perform=async selected=>{
   const before=obs(),next=await api("action",{session,action:selected});
   payload=next;
   await Promise.all([preloadScene(next.observation),DungeonPresentation.play({before,after:next.observation,journal:next.journal.at(-1),action:selected,assets,label,pace:Number($("speed").value),
    onEffects:effects=>renderHudEffects({...before,...effects}),
    onOutcomes:()=>renderHud({...next.observation,room:before.room,run_number:before.run_number})})]);
  };
  await perform(action);
  // There is no decision to make inside an empty room. Keep the native action
  // in the recording so recovery/poison ticks, time and replay stay exact.
  if(action.kind==="choose_door"&&!obs().terminal&&obs().phase==="encounter"&&obs().encounter==="empty"){
   pendingAction=null;render();await perform(legal("interact"));
  }
 }catch(e){stop();showError(e);}finally{busy=false;pendingAction=null;render();}
 await ensureFinalStoneAdvice();
 // Focus the recommended choice so the focus ring never singles out the other option.
 if(manual)(document.querySelector('#actions button.suggested:not(:disabled),#scene-content button.suggested:not(:disabled),#scene-content button.recommended:not(:disabled)')||
  document.querySelector('#actions button:not(:disabled),#scene-content button:not(:disabled)'))?.focus({preventScroll:true});
 if(playing&&!obs().terminal)timer=setTimeout(()=>act(suggestion()),obs().phase==="gems"?Math.max(900,Number($("speed").value)):100);else if(playing)stop();
}
async function replaceSession(next,recording=null){
 stop();busy=true;$("error").hidden=true;if(payload)render();
 try{
  const result=await api(recording?"import":"new",recording?{recording}:{settings:{...policyDefaults,...next}});
  const previous=session;session=result.session;payload=result;config={...policyDefaults,...next,...result.settings};
  await preloadScene(result.observation);
  if(previous)api("close",{session:previous}).catch(()=>{});
  const option=$("strategy").querySelector('option[value="learned"]');
  // Recorded research policies remain supported by the API; the play surface
  // always uses its one recommended adviser.
  if(!config.learned_policy&&option)option.remove();
  $("setup").close();$("comparison-results").replaceChildren();
 }catch(e){showError(e);throw e;}finally{busy=false;if(payload)render();}
 await ensureFinalStoneAdvice();
}
function download(value,name){const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:"application/json"}));const a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
$("strategy").onchange=()=>{stop();render();};
$("fullscreen").onclick=async()=>{
 const entering=!document.body.classList.contains("game-view");document.body.classList.toggle("game-view",entering);
 $("fullscreen").textContent=entering?"Exit fullscreen":"Fullscreen";
 try{if(entering)await document.documentElement.requestFullscreen();else if(document.fullscreenElement)await document.exitFullscreen();}catch(_){/* Expanded layout still works when browser fullscreen is unavailable. */}
 $("scene").focus({preventScroll:true});
};
document.addEventListener("fullscreenchange",()=>{if(!document.fullscreenElement){document.body.classList.remove("game-view");$("fullscreen").textContent="Fullscreen";}});
document.addEventListener("keydown",event=>{
 if(event.key==="Escape"&&!document.fullscreenElement&&!document.querySelector('dialog[open]')&&!document.body.classList.contains('desktop-view')){
  document.body.classList.remove('game-view');$("fullscreen").textContent="Fullscreen";
 }
});
$("all-stones").onclick=()=>{$("stone-list").innerHTML=Object.keys(stones).slice(0,20).map(g=>`<article class="gem">${stoneBody(g)}</article>`).join("");$("stone-catalog").showModal();};
$("close-stones").onclick=()=>$("stone-catalog").close();
$("step-policy").onclick=()=>{if(suggestion())act(suggestion(),true,strategyNames[$("strategy").value]);};
const togglePlayback=()=>{if(playing)stop();else if(suggestion()){playing=true;act(suggestion());}};
$("autoplay").onclick=togglePlayback;$("fullscreen-play").onclick=togglePlayback;
$("game-play").onclick=togglePlayback;
$("game-step").onclick=()=>$("step-policy").click();
$("game-undo").onclick=()=>{stop();$("undo").click();};
$("game-menu").onclick=()=>{stop();$("menu-speed").value=$("speed").value;$("play-menu").showModal();$("close-play-menu").focus();};
$("close-play-menu").onclick=()=>$("play-menu").close();
$("play-menu").addEventListener("close",()=>$("game-menu").focus({preventScroll:true}));
$("menu-speed").onchange=()=>{$("speed").value=$("menu-speed").value;};
for(const [control,target] of [["menu-save","export"],["menu-load","import-run"],["menu-new","new-run"]])$(control).onclick=()=>{$("play-menu").close();$(target).click();};
$("menu-journal").onclick=()=>{$("play-menu").close();$("play-journal-entries").innerHTML=$("journal").innerHTML||'<li>No decisions yet.</li>';$("play-journal").showModal();$("close-play-journal").focus();};
$("close-play-journal").onclick=()=>$("play-journal").close();
$("play-journal").addEventListener("close",()=>$("game-menu").focus({preventScroll:true}));
document.addEventListener("keydown",event=>{
 if(event.repeat||event.isComposing||document.body.classList.contains("watch-mode")||event.ctrlKey||event.altKey||event.metaKey||event.target.isContentEditable||event.target.closest("input,textarea,select")||document.querySelector("dialog[open]"))return;
 if(event.key.toLowerCase()==="p"){event.preventDefault();if(playing||!busy)togglePlayback();}
 if(event.key.toLowerCase()==="n"&&!busy){event.preventDefault();stop();$("step-policy").click();}
});
$("unlock-reroll").onclick=()=>{rerollUnlockedStamp=decisionStamp();renderSelection();$("scene").focus({preventScroll:true});};
function sideAction(right){
 const o=obs();if(!o||o.terminal)return null;
 if(o.phase==="doors")return legal("choose_door",displayedDoors[right?1:0]);
 if(o.phase==="encounter")return right?(legal("flee")||legal("skip")):(legal("fight")||legal("interact"));
 if(!right)return legal("reenter")||legal("restart");
 return null;
}
let scenePointer=null;
function updateSceneCursor(event){
 if(event){if(event.pointerType==="touch")return;scenePointer={x:event.clientX,y:event.clientY};}
 const scene=$("scene");let cursor="default",action=null;
 if(scenePointer&&obs()?.phase!=='recovery'&&!document.querySelector('dialog[open]')){
  const target=document.elementFromPoint(scenePointer.x,scenePointer.y);
  if(target&&scene.contains(target)){
   if(busy)cursor="wait";
   else {
    const button=target.closest('button');
    if(button){if(!button.disabled&&button.dataset.kind)action=legal(button.dataset.kind,Number(button.dataset.index||0));}
    else if(!target.closest('#encounter-result,#effect-inspector,.hud-top,.hud-bottom,.scene-controls,.game-controls')){
     const bounds=scene.getBoundingClientRect();action=sideAction(scenePointer.x>bounds.left+bounds.width/2);
    }
   }
  }
 }
 if(action){
  const key=action.kind==="fight"?"fight":["flee","skip"].includes(action.kind)?"flee":action.kind==="choose_door"?(action.index===0?"arrow_left":"arrow_right"):"interact";
  const src=asset(`cursor.${key}`);cursor=src?`url("${src}") 32 32, pointer`:"pointer";
 }
 scene.style.cursor=cursor;scene.dataset.customCursor=action?"true":"false";
}
$("scene").addEventListener("pointermove",updateSceneCursor);
$("scene").addEventListener("pointerleave",()=>{scenePointer=null;updateSceneCursor();});
function syncShopSpacing(){
 const scene=$("scene"),controls=scene.querySelector('.scene-controls');
 scene.style.setProperty('--shop-controls-space',`${scene.clientHeight-controls.offsetTop+12}px`);
}
const sceneSizeObserver=new ResizeObserver(()=>{syncShopSpacing();updateSceneCursor();});
sceneSizeObserver.observe($("scene"));sceneSizeObserver.observe($("scene").querySelector('.scene-controls'));
function chooseSide(right){
 if(busy||!obs()||obs().terminal)return;
 const o=obs();
 if(o.phase==="shop"||o.phase==="curse_shop"){selectedOffer=right?1:0;renderSelection();return;}
 else if(o.phase==="gems"){selectedGem=Math.max(0,Math.min(o.gem_offers.length-1,selectedGem+(right?1:-1)));renderSelection();document.querySelector(`.gem-offer[data-index="${selectedGem}"]`)?.scrollIntoView({block:"nearest",inline:"nearest"});return;}
 const action=sideAction(right);
 if(action)act(action,true);
}
document.addEventListener("keydown",event=>{
 if(event.ctrlKey||event.altKey||event.metaKey||event.target.isContentEditable||event.target.closest('input,textarea,select,.recovery-targets')||document.querySelector('dialog[open]'))return;
 if(!["ArrowLeft","ArrowRight"," ","Enter"].includes(event.key))return;
 if(event.key==="Enter"&&event.target.closest('#shop-guard,.play-toolbar,.topbar'))return;
 const o=obs();if(!o)return;
 if(o.phase==='recovery'&&event.target.closest('.scene-controls'))return;
 if(event.key==="Enter"&&!['shop','curse_shop','gems'].includes(o.phase))return;
 event.preventDefault();if(event.repeat||busy)return;
 if(event.key==="ArrowLeft"||event.key==="ArrowRight")chooseSide(event.key==="ArrowRight");
 else if(event.key===" "){if(legal("reroll"))act(legal("reroll"),true);}
 else {const a=legal(o.phase==="gems"?"choose_gem":"buy",o.phase==="gems"?selectedGem:selectedOffer);if(a)act(a,true);}
});
// Touch has no cursor preview, so a stray tap on the background must not fight
// or flee; touch players use the visible door and action buttons instead.
let scenePointerType="mouse";
$("scene").addEventListener("pointerdown",event=>{scenePointerType=event.pointerType||"mouse";});
$("scene").addEventListener("click",event=>{
 if(scenePointerType==="touch"&&event.detail!==0)return;
 if(event.target.closest('button,#encounter-result,#effect-inspector,.hud-top,.hud-bottom,.scene-controls,.game-controls')||document.querySelector('dialog[open]'))return;
 const bounds=$("scene").getBoundingClientRect();chooseSide(event.clientX>bounds.left+bounds.width/2);
});
$("undo").onclick=async()=>{if(busy)return;stop();busy=true;render();try{
 const last=payload.journal.at(-1),previous=payload.journal.at(-2);
 const emptyTransition=last?.action.kind==="interact"&&last.encounter==="empty"&&previous?.action.kind==="choose_door"&&last.room===previous.room&&last.run===previous.run;
 payload=await api("undo",{session});if(emptyTransition)payload=await api("undo",{session});$("error").hidden=true;
}catch(e){showError(e);}finally{busy=false;render();}await ensureFinalStoneAdvice();};
$("export").onclick=async()=>{try{download(await api("export",{session}),`sfld-run-${config.seed}.json`);}catch(e){showError(e);}};
$("import-run").onclick=()=>$("recording-file").click();
$("recording-file").onchange=async e=>{try{const file=e.target.files[0];if(file){const recording=JSON.parse(await file.text());await replaceSession(recording.settings,recording);}}catch(e){showError(e);}finally{e.target.value="";}};
$("learned-load").onclick=()=>$("policy-file").click();
$("policy-file").onchange=async e=>{
 try{const file=e.target.files[0];if(file){stop();const recording=await api("export",{session});recording.settings.learned_policy=await file.text();await replaceSession(recording.settings,recording);$("strategy").value="learned";render();}}
 catch(e){showError(e);}finally{e.target.value="";}
};
$("new-run").onclick=()=>{
 stop();$("seed-input").value="";$("budget-input").value=config.budget;$("wallet-input").value="";$("hours-input").value=168;$("run-input").value=1;
 const wake=0;
 if(!Array.from($("sleep-input").options).some(o=>Number(o.value)===wake))$("sleep-input").add(new Option(`Sleep 00:00–${clockTime(wake)}`,String(wake)));
 $("sleep-input").value=String(wake);$("start-time-input").value="17:00";
 $("state-input").value="";$("preset").value="fresh";
 $("scenario-input").value=config.scenario||"default";
 $("revive-hp-input").value=String((config.revive_hp??.21)*100);
 for(const [key,id] of Object.entries(policyFields))$(id).value=String(config[key]??policyDefaults[key]);
 const neural=!!obs()?.neural_adviser;
 for(const id of ['revive-hp-input',...Object.values(policyFields)])$(id).disabled=neural;
 $("policy-settings-help").textContent=neural?"The trained policy chooses combat, stones, keys, refreshes and recovery within your event budget. These rule settings do not change its decisions.":"The rule adviser uses the recovery and key targets below. Reserved mushrooms cannot be spent on healing. These targets do not guarantee a drop.";
 $("strategy-settings").open=false;$("advanced-settings").open=false;
 $("progress-details").open=false;$("setup-policy").textContent=config.learned_policy?"Your loaded learned policy stays available for this event.":"These settings apply to the new simulation.";$("setup-error").hidden=true;$("setup").showModal();$("budget-input").focus();$("budget-input").select();
};
$("close-setup").onclick=()=>$("setup").close();
$("setup").addEventListener("close",()=>{if(!document.querySelector('dialog[open]'))(document.body.classList.contains('game-view')?$("game-menu"):$("new-run")).focus({preventScroll:true});});
$("shop-refresh-preset").onclick=()=>{
 $("strategy").value="preferred-stones";
 $("budget-input").value="500";
 const settings={survival_aware:true,adaptive_gems:false,early_key_target:10,shop_allowance:5,shop_key_reserve:4,shop_mushroom_reserve:0};
 for(const [key,value] of Object.entries(settings))$(policyFields[key]).value=String(value);
 $("strategy-settings").open=!obs()?.neural_adviser;
};
$("preset").onchange=()=>{
 const preset=$("preset").value;
 const states={
  fresh:"",
  monster:"schema=1\nroom=12\nrun_number=1\nphase=encounter\nencounter=monster\nhp_percent=70\nkeys=2\nblessings=recovery:weak:3\n",
  soldier:"schema=1\nroom=12\nrun_number=1\nphase=encounter\nencounter=skeleton\nhp_percent=70\nkeys=2\n",
  chest:"schema=1\nroom=12\nrun_number=1\nphase=encounter\nencounter=silver\nhp_percent=70\nkeys=2\n",
  opening:"schema=1\nroom=5\nrun_number=1\nphase=doors\nhp_percent=100\nkeys=0\ndoors=shop,wall\n",
  arena:"schema=1\nroom=51\nrun_number=1\nphase=doors\nhp_percent=80\nkeys=4\ngems=rabbit,gambler\ndoors=monster,trial\ntrial_seen=false\nshops_seen=1,2\n",
  shop:"schema=1\nroom=82\nrun_number=2\nphase=shop\nhp_percent=55\nkeys=8\ngems=rabbit,gambler,greed\noffers=one_hit:strong:8:4,escape:strong:10:6\nshops_seen=1,2,3,4\n"
 };
 if(preset in states){$("state-input").value=states[preset];if(preset!=="fresh")$("run-input").value=preset==="shop"?2:1;}
 $("progress-details").open=preset!=="fresh";
};
$("setup-form").onsubmit=async e=>{e.preventDefault();if(busy)return;
 const next={seed:$("seed-input").value.trim()===""?randomSeed():Number($("seed-input").value),budget:Number($("budget-input").value),deadline_hours:Number($("hours-input").value),run_number:Number($("run-input").value),initial_state:$("state-input").value,learned_policy:config.learned_policy,scenario:$("scenario-input").value,revive_hp:Number($("revive-hp-input").value)/100};
 next.wallet_mushrooms=$("wallet-input").value===""?null:Number($("wallet-input").value);
 const [startHours,startMinutes]=$("start-time-input").value.split(":").map(Number);
 next.event_start_hour=startHours+startMinutes/60;next.sleep_until_hour=Number($("sleep-input").value);
 for(const [key,id] of Object.entries(policyFields))next[key]=typeof policyDefaults[key]==="boolean"?$(id).value==="true":Number($(id).value);
 $("setup-error").hidden=true;$("start-run").disabled=true;$("start-run").textContent="Starting…";
 try{await replaceSession(next);}catch(e){$("setup-error").textContent=e.message;$("setup-error").hidden=false;}
 finally{$("start-run").disabled=false;$("start-run").textContent="Start run";}
};
$("setup-form").addEventListener("invalid",e=>{if($("advanced-settings").contains(e.target))$("advanced-settings").open=true;},true);
$("compare").onclick=async()=>{
 if(comparing)return;comparing=true;$("compare").disabled=true;$("comparison-results").innerHTML='<div class="loading">Running the same starting setup for each policy…</div>';
 const comparisonConfig={...config},count=Number($("compare-runs").value);
 try{
  const result=await api("compare",{settings:comparisonConfig,runs:count}),best=Math.max(...result.results.map(r=>r.report.mean_completions));
  $("comparison-results").innerHTML=`<p class="subtle">${count.toLocaleString()} events each · ${comparisonConfig.budget} mushroom cap · ${comparisonConfig.deadline_hours} hours. ± values are Monte Carlo standard errors, excluding uncertainty in the game rules.</p><div class="table-wrap"><table><thead><tr><th>Policy</th><th>Completions / event</th><th>First finish, if reached</th><th>Events finishing ≥1</th><th>Mushrooms spent</th><th>Mushrooms / completion</th></tr></thead><tbody>${result.results.map(({strategy,report:r})=>`<tr class="${r.mean_completions===best?"best":""}"><td>${esc(strategyNames[strategy])}${r.valid?"":" (action limit)"}</td><td>${num(r.mean_completions)} ± ${num(r.mean_completions_mc_se)}</td><td>${r.conditional_first_completion_hours==null?"—":duration(r.conditional_first_completion_hours)}</td><td>${pct(r.probability_at_least_one_completion)}</td><td>${num(r.mean_mushrooms)}</td><td>${num(r.mushrooms_per_completion)}</td></tr>`).join("")}</tbody></table></div>`;
  const button=document.createElement("button");button.className="quiet";button.textContent="Download comparison data";button.onclick=()=>download({settings:comparisonConfig,...result},"sfld-comparison.json");$("comparison-results").append(button);
 }catch(e){$("comparison-results").textContent=e.message;}finally{comparing=false;$("compare").disabled=false;}
};
(async()=>{try{const meta=await api("config");assets=meta.assets;await Promise.all([DungeonPresentation.loadFont(assets),replaceSession(config)]);if(new URLSearchParams(location.search).get("desktop")==="1"){document.body.classList.add("game-view","desktop-view");$("fullscreen").textContent="Show details";}}catch(e){showError(e);}})();
