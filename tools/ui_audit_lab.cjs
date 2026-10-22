// Rendered UI audit of the local visual lab in four device profiles plus two widths.
// Uses a separate lab instance; never touches a user's session or the live game.
const {chromium,webkit,devices}=require('../out/vod-review-browser/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const out=path.resolve(process.argv[2]||path.join(__dirname,'../out/ui-audit'));
const url=process.env.SFLD_LAB_URL||'http://127.0.0.1:49431/?desktop=1';
fs.mkdirSync(out,{recursive:true});
const profiles=[
 ['windows',chromium,{viewport:{width:1440,height:1000}}],
 ['mac',webkit,{viewport:{width:1440,height:1000}}],
 ['android',chromium,devices['Pixel 7']],
 ['iphone',webkit,devices['iPhone 13']],
 ['width-320',chromium,{viewport:{width:320,height:700}}],
 ['width-768',chromium,{viewport:{width:768,height:1000}}],
];
const settled=page=>page.waitForFunction(()=>typeof payload!=='undefined'&&payload&&!busy,null,{timeout:60000});

function inspect(){
 const visible=e=>{const r=e.getBoundingClientRect();const s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&s.opacity!=='0'};
 const label=e=>(e.getAttribute('aria-label')||e.getAttribute('title')||e.textContent||e.value||'').trim()||
   (e.getAttribute('aria-labelledby')&&document.getElementById(e.getAttribute('aria-labelledby'))?.textContent?.trim())||
   (e.id&&document.querySelector(`label[for="${e.id}"]`)?.textContent?.trim())||e.closest('label')?.textContent?.trim()||
   e.querySelector('img[alt]:not([alt=""])')?.alt||'';
 const name=e=>e.id?'#'+e.id:e.tagName.toLowerCase()+(e.className&&typeof e.className==='string'?'.'+e.className.trim().split(/\s+/).slice(0,2).join('.'):'');
 const rgb=c=>{const m=c.match(/[\d.]+/g);return m?m.map(Number):[0,0,0,0]};
 const lum=([r,g,b])=>{const f=v=>{v/=255;return v<=.03928?v/12.92:((v+.055)/1.055)**2.4};return .2126*f(r)+.7152*f(g)+.0722*f(b)};
 const background=e=>{for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);const c=rgb(s.backgroundColor);if(s.backgroundImage!=='none')return null;if((c[3]??1)>.9)return c}return [255,255,255,1]};
 const controls=[...document.querySelectorAll('button,a[href],input,select,textarea,[role=button],summary')].filter(visible);
 const unnamed=controls.filter(e=>!label(e)).map(name);
 const small=controls.filter(e=>{const r=e.getBoundingClientRect();return (r.width<24||r.height<24)&&e.tagName!=='A'}).map(e=>{const r=e.getBoundingClientRect();return `${name(e)} ${Math.round(r.width)}x${Math.round(r.height)}`});
 const images=[...document.images].filter(i=>visible(i)&&!i.hasAttribute('alt')).map(name);
 const wide=[...document.querySelectorAll('body *')].filter(e=>{if(!visible(e))return false;const r=e.getBoundingClientRect();return r.right>innerWidth+1||r.left<-1}).filter(e=>!e.closest('[style*="overflow"],.scroll-x')).map(name).slice(0,12);
 const contrast=[];
 for(const e of document.querySelectorAll('body *')){
  if(!visible(e)||![...e.childNodes].some(n=>n.nodeType===3&&n.textContent.trim()))continue;
  const s=getComputedStyle(e),bg=background(e);if(!bg)continue;
  const fg=rgb(s.color);const a=fg[3]??1;const mix=fg.slice(0,3).map((v,i)=>v*a+bg[i]*(1-a));
  const [l1,l2]=[lum(mix),lum(bg)].sort((x,y)=>y-x);const ratio=(l1+.05)/(l2+.05);
  const large=parseFloat(s.fontSize)>=24||(parseFloat(s.fontSize)>=18.66&&+s.fontWeight>=700);
  if(ratio<(large?3:4.5))contrast.push(`${name(e)} "${e.textContent.trim().slice(0,30)}" ${ratio.toFixed(2)}`);
 }
 return {overflowX:document.documentElement.scrollWidth>innerWidth+1,scrollWidth:document.documentElement.scrollWidth,viewport:[innerWidth,innerHeight],
   unnamed,small,images,wide,contrast:contrast.slice(0,15),contrastCount:contrast.length};
}

(async()=>{
 const report=[];
 for(const [profile,type,options] of profiles){
  const browser=await type.launch();const context=await browser.newContext({...options,reducedMotion:'reduce'});
  const page=await context.newPage();const errors=[],failed=[],external=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push('console: '+m.text())});
  page.on('requestfailed',r=>failed.push(r.url()));
  page.on('request',r=>{if(!r.url().startsWith(new URL(url).origin)&&!r.url().startsWith('data:'))external.push(r.url())});
  const entry={profile,engine:type.name(),states:{}};
  try{
   await page.goto(url);await settled(page);
   entry.states.start=await page.evaluate(inspect);await page.screenshot({path:path.join(out,`${profile}-start.png`)});
   const menu=page.locator('#game-menu');
   if(await menu.isVisible()){await menu.click();await page.waitForTimeout(300);
    entry.states.menu=await page.evaluate(inspect);await page.screenshot({path:path.join(out,`${profile}-menu.png`)});
    await page.keyboard.press('Escape');await page.waitForTimeout(200);}
   // Play a few recommended steps to reach other screens.
   for(let i=0;i<6;i++){await page.evaluate(()=>typeof act==='function'&&typeof legal==='function'&&obs()&&obs().suggestions?.[0]&&act(obs().suggestions[0].action,true)).catch(()=>{});await settled(page).catch(()=>{});}
   entry.states.played=await page.evaluate(inspect);await page.screenshot({path:path.join(out,`${profile}-played.png`)});
   const focus=[];
   await page.locator('body').click({position:{x:2,y:2}}).catch(()=>{});
   for(let i=0;i<12;i++){await page.keyboard.press('Tab');focus.push(await page.evaluate(()=>{const e=document.activeElement;if(!e||e===document.body)return null;const s=getComputedStyle(e);
     return {id:e.id||e.tagName,outline:s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>0,shadow:s.boxShadow!=='none'}}));}
   entry.focus=focus.filter(Boolean);entry.focusInvisible=entry.focus.filter(f=>!f.outline&&!f.shadow).map(f=>f.id);
  }catch(e){entry.failure=String(e.message||e)}
  finally{entry.errors=errors;entry.failedRequests=failed;entry.external=external;
   await page.evaluate(()=>typeof session!=='undefined'&&session&&api('close',{session})).catch(()=>{});await browser.close();}
  report.push(entry);console.log(profile,'done');
 }
 fs.writeFileSync(path.join(out,'ui-audit.json'),JSON.stringify(report,null,1));
})().catch(e=>{console.error(e);process.exit(1)});
