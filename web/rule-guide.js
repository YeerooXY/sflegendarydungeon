"use strict";
(() => {
 const stoneNames={rabbit:"Soul of the Rabbit",moonstone:"Cursed Moonstone",spying:"Spying Gem",pendant:"Pendant of the Key Master",greasy:"Greasy Healing Stone",gambler:"Boulder of the Gambler",greed:"Boulder of Greed",hero:"Treasure of the Hero",time_traveler:"Diamond of the Time Traveler",thirsty:"Hope of the Thirsty One",pearl:"Cursed Pearl",blood:"Blood Drop of Sacrifice",explorer:"Emerald of the Explorer",misadventurer:"Sapphire of the Misadventurer",devil:"Crown Jewel of the Devil",deceit:"Pebble of Deceit",lodestone:"Lodestone",bull:"Eye of the Bull",hick:"Erratic Boulder of the Hick",rusty:"Rusty Healing Stone"};
 const stoneIds=Object.keys(stoneNames);
 const originalPriority=["rabbit","greasy","moonstone","pendant","spying","gambler","greed","hero","time_traveler","thirsty","pearl","blood","explorer","misadventurer","devil","deceit","lodestone","bull","hick","old_sacrifice","kidney","masochist","rusty"];
 const conditions={
  escape_pair_bonus:"Complete the Rabbit + Moonstone pair",
  greasy_traveler_bonus:"Complete the Greasy Healing Stone + Time Traveler pair",
  gambler_traveler_adjustment:"Complete the Gambler + Time Traveler pair",
  greasy_with_perfect_escape_adjustment:"Choose Greasy Healing Stone with 100% permanent escape",
  greasy_second_pick_adjustment:"Choose Greasy Healing Stone after room 50",
  greasy_third_pick_adjustment:"Choose Greasy Healing Stone after room 75",
  spying_pendant_adjustment:"Complete the Spying Gem + Key Master pendant pair",
  gambler_moonstone_adjustment:"Complete the Gambler + Moonstone pair",
  pearl_with_perfect_escape_adjustment:"Choose Cursed Pearl with 100% permanent escape"
 };
 function parseRules(text){
  if(typeof text!=="string")throw new Error("This chart is missing its rule settings.");
  const scores=Object.fromEntries(originalPriority.map((id,i)=>[id,originalPriority.length-i]));
  const adjustments=Object.fromEntries(Object.keys(conditions).map(id=>[id,0])),seen=new Set();
  for(const raw of text.split(/\r?\n/)){
   const line=raw.split("#",1)[0].trim();if(!line)continue;
   const split=line.indexOf("=");if(split<0)throw new Error("A rule setting is missing its value.");
   const key=line.slice(0,split).trim(),value=line.slice(split+1).trim();
   if(key==="schema"&&value!=="1")throw new Error("This rule format is not supported by the guide.");
   if(!key.startsWith("gem."))continue;
   if(seen.has(key))throw new Error(`Duplicate stone setting: ${key}.`);seen.add(key);
   const name=key.slice(4),target=Object.hasOwn(scores,name)?scores:Object.hasOwn(adjustments,name)?adjustments:null;
   if(!target)throw new Error(`Unknown stone setting: ${key}.`);
   if(!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(value))throw new Error(`Invalid points for ${key}.`);
   const number=Number(value);if(!Number.isFinite(number)||Math.abs(number)>1000000)throw new Error(`Invalid points for ${key}.`);
   target[name]=number;
  }
  return {scores,adjustments};
 }
 function validateContext(room,owned,offers=null){
  if(![26,51,76].includes(room))throw new Error("Choose the stone offer after room 25, 50 or 75.");
  if(!Array.isArray(owned)||owned.length>2||owned.some(id=>!Object.hasOwn(stoneNames,id)))throw new Error("Choose up to two current stones you already own.");
  if(new Set(owned).size!==owned.length)throw new Error("The two owned stones must be different.");
  if(offers!==null){
   if(!Array.isArray(offers)||offers.length!==3||offers.some(id=>!Object.hasOwn(stoneNames,id)))throw new Error("Choose all three offered stones.");
   if(new Set(offers).size!==3)throw new Error("The three offered stones must be different.");
   if(offers.some(id=>owned.includes(id)))throw new Error("An offered stone cannot be one you already own.");
  }
 }
 function scoreGem(rules,gem,owned,room){
  validateContext(room,owned);
  if(!Object.hasOwn(stoneNames,gem))throw new Error("Choose a current stone.");
  const terms=[{key:"base",label:"Base chart",value:rules.scores[gem]}];
  const add=key=>{const value=rules.adjustments[key];if(value!==0)terms.push({key,label:conditions[key],value});};
  const pair=(first,second)=>gem===first&&owned.includes(second)||gem===second&&owned.includes(first);
  if(pair("rabbit","moonstone"))add("escape_pair_bonus");
  if(pair("greasy","time_traveler"))add("greasy_traveler_bonus");
  if(pair("gambler","time_traveler"))add("gambler_traveler_adjustment");
  if(pair("spying","pendant"))add("spying_pendant_adjustment");
  if(pair("gambler","moonstone"))add("gambler_moonstone_adjustment");
  if(gem==="greasy"||gem==="pearl"){
   const escape=.4+(owned.includes("rabbit")?.4:0)+(owned.includes("moonstone")?.2:0)-(owned.includes("bull")?.3:0);
   if(escape>=1-1e-12)add(gem==="greasy"?"greasy_with_perfect_escape_adjustment":"pearl_with_perfect_escape_adjustment");
  }
  if(gem==="greasy"){
   if(room===51)add("greasy_second_pick_adjustment");
   if(room===76)add("greasy_third_pick_adjustment");
  }
  const total=terms.reduce((sum,term)=>sum+term.value,0);
  return {gem,total,base:rules.scores[gem],adjustment:total-rules.scores[gem],terms};
 }
 function chooseOffer(rules,offers,owned,room){
  validateContext(room,owned,offers);
  const scores=offers.map(id=>scoreGem(rules,id,owned,room));let index=0;
  for(let i=1;i<scores.length;i++)if(scores[i].total>scores[index].total)index=i;
  return {index,gem:offers[index],scores};
 }
 const exported={parseRules,validateContext,scoreGem,chooseOffer,stoneNames,stoneIds,conditions};
 if(typeof module!=="undefined"&&module.exports)module.exports=exported;
 if(typeof document==="undefined")return;

 const $=id=>document.getElementById(id);
 const make=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
 const point=value=>Number.isInteger(value)?String(value):String(Number(value.toFixed(4)));
 const signed=value=>(value>0?"+":"")+point(value);
 let data=null,assets={},current=null,rules=null;
 function fallbackIcon(){const node=make("span","◇","stone-icon stone-fallback");node.setAttribute("aria-hidden","true");return node;}
 function icon(id){
  const path=assets[`gem.${id}`];
  if(typeof path!=="string"||!path)return fallbackIcon();
  const node=make("img",undefined,"stone-icon");node.src=`/local-assets/${encodeURIComponent(path)}`;node.alt="";node.loading="lazy";
  node.onerror=()=>node.replaceWith(fallbackIcon());return node;
 }
 function option(id,title){const node=make("option",title);node.value=id;return node;}
 function fillStones(){
  for(const id of ["owned-0","owned-1","offer-0","offer-1","offer-2"]){
   const select=$(id);if(id.startsWith("owned"))select.append(option("","None"));
   for(const stone of stoneIds)select.append(option(stone,stoneNames[stone]));
   select.onchange=renderChoice;
  }
  $("offer-0").value="rabbit";$("offer-1").value="greasy";$("offer-2").value="gambler";
  $("guide-room").onchange=renderChoice;
 }
 function selectedContext(){return {room:Number($("guide-room").value),owned:[$("owned-0").value,$("owned-1").value].filter(Boolean),offers:[$("offer-0").value,$("offer-1").value,$("offer-2").value]};}
 function renderRanking(context){
  const rows=stoneIds.map(id=>({...scoreGem(rules,id,context.owned,context.room),owned:context.owned.includes(id)}));
  rows.sort((a,b)=>Number(a.owned)-Number(b.owned)||b.total-a.total||stoneIds.indexOf(a.gem)-stoneIds.indexOf(b.gem));
  $("rank-rows").replaceChildren();let rank=0;
  for(const entry of rows){
   const row=make("tr");row.className=entry.owned?"owned":context.offers.includes(entry.gem)?"offered":"";
   row.append(make("td",entry.owned?"—":String(++rank),"rank-number"));
   const title=make("td"),name=make("div",undefined,"rank-name"),text=make("div",stoneNames[entry.gem]);
   if(entry.owned)text.append(make("small","Already owned"));
   else if(entry.terms.length>1)text.append(make("small",entry.terms.slice(1).map(term=>`${signed(term.value)}: ${term.label}`).join(" · ")));
   name.append(icon(entry.gem),text);title.append(name);row.append(title,make("td",point(entry.base)));
   row.append(make("td",entry.owned?"—":signed(entry.adjustment),entry.adjustment>0?"positive":entry.adjustment<0?"negative":""));
   row.append(make("td",entry.owned?"—":point(entry.total)));$("rank-rows").append(row);
  }
 }
 function renderChoice(){
  if(!rules)return;
  const context=selectedContext();$("choice-error").hidden=true;$("recommendation").replaceChildren();$("offer-breakdowns").replaceChildren();
  try{validateContext(context.room,context.owned);renderRanking(context);}catch(error){$("rank-rows").replaceChildren();$("choice-error").textContent=error.message;$("choice-error").hidden=false;return;}
  try{
   const choice=chooseOffer(rules,context.offers,context.owned,context.room);
   $("recommendation").append(make("strong",`Take ${stoneNames[choice.gem]}`));
   const ties=choice.scores.filter(item=>item.total===choice.scores[choice.index].total).length;
   $("recommendation").append(make("p",`${point(choice.scores[choice.index].total)} preference points under this chart.${ties>1?" The highest score is tied; choose the leftmost of those offers.":""}`));
   for(let i=0;i<choice.scores.length;i++){
    const entry=choice.scores[i],card=make("article",undefined,`offer-card${i===choice.index?" selected":""}`);
    card.append(icon(entry.gem),make("h3",stoneNames[entry.gem]),make("div",point(entry.total),"total"));
    const list=make("ul");for(const term of entry.terms)list.append(make("li",`${term.key==="base"?point(term.value):signed(term.value)} · ${term.label}`));
    if(entry.terms.length===1)list.append(make("li","No conditional adjustment."));
    card.append(list);$("offer-breakdowns").append(card);
   }
  }catch(error){$("choice-error").textContent=error.message;$("choice-error").hidden=false;}
 }
 function selectProfile(){
  try{
   current=data.profiles.find(profile=>profile.id===$("guide-profile").value);if(!current)throw new Error("Choose an available rule chart.");
   rules=parseRules(current.rules_text);$("guide-error").hidden=true;
   $("profile-description").textContent=current.description||"";$("profile-results").replaceChildren();
   for(const result of Array.isArray(current.results)?current.results:[]){
    if(!Number.isFinite(result.budget)||!Number.isFinite(result.mean_completions))continue;
    const metric=make("div",undefined,"metric");metric.append(make("span",result.budget===0?"No mushrooms":`${result.budget} mushrooms available`),make("strong",result.mean_completions.toFixed(2)),make("span","Average completions per 7-day event"));$("profile-results").append(metric);
   }
   $("guide-actions").replaceChildren();
   const actions=Array.isArray(current.actions)?current.actions:[];
   for(const action of actions)if(typeof action==="string")$("guide-actions").append(make("li",action));
   if(!actions.length)$("guide-actions").append(make("li","This chart does not include additional action rules."));
   $("condition-list").replaceChildren();
   for(const [key,text] of Object.entries(conditions))if(rules.adjustments[key]!==0)$("condition-list").append(make("li",`${signed(rules.adjustments[key])} points: ${text}.`));
   if(!$("condition-list").children.length)$("condition-list").append(make("li","This chart uses base points only. Owned stones and stage do not add points."));
   renderChoice();
  }catch(error){rules=null;$("guide-error").textContent=error.message;$("guide-error").hidden=false;$("profile-description").textContent="";for(const id of ["profile-results","guide-actions","condition-list","recommendation","offer-breakdowns","rank-rows"])$(id).replaceChildren();}
 }
 async function loadData(){
  try{
   const response=await fetch("/rule-guide-data.json");if(!response.ok)throw new Error("The rule charts are not available yet. Return to the simulator or reload this page after they have been saved.");
   data=await response.json();if(data.schema!==1||!Array.isArray(data.profiles)||!data.profiles.length)throw new Error("No supported rule charts were found.");
   const seen=new Set();
   for(const profile of data.profiles){
    if(typeof profile.id!=="string"||!profile.id||seen.has(profile.id)||typeof profile.title!=="string")throw new Error("A rule chart has an invalid name.");seen.add(profile.id);
    $("guide-profile").append(option(profile.id,profile.title));
   }
   $("guide-status").textContent=typeof data.notice==="string"&&data.notice?data.notice:"These charts were tested under the simulator’s working assumptions.";
   fillStones();$("guide-profile").onchange=selectProfile;
   const requested=new URLSearchParams(location.search).get("profile");if(seen.has(requested))$("guide-profile").value=requested;
   $("guide-content").hidden=false;selectProfile();
  }catch(error){$("guide-status").hidden=true;$("guide-error").textContent=error.message;$("guide-error").hidden=false;}
 }
 loadData();
 fetch("/api/config").then(response=>response.ok?response.json():null).then(config=>{if(config&&config.assets&&typeof config.assets==="object"){assets=config.assets;if(rules)renderChoice();}}).catch(()=>{});
})();
