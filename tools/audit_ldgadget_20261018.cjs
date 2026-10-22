// Read-only verification of the public calculator through its normal controls.
const {chromium}=require('../out/vod-review-browser/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'..');
const out=path.join(root,'out/ldgadget-audit-2026-10-18');
fs.mkdirSync(out,{recursive:true});

(async()=>{
 const browser=await chromium.launch({headless:true});
 const report={url:'https://ldgadget.12hp.de/',checked_at:new Date().toISOString(),
  scope:'Public calculator behaviour, not live-game mechanics verification',cases:[],errors:[]};
 try {
  const page=await browser.newPage({viewport:{width:1500,height:1500},locale:'en-GB'});
  page.on('pageerror',e=>report.errors.push(e.message));
  await page.goto(report.url+'#damage',{waitUntil:'networkidle',timeout:25000});
  const read=section=>page.locator(`[data-monster-section="${section}"] [data-damage-meter]`).evaluateAll(nodes=>nodes.map(el=>({
   kind:'isBoss' in el.dataset?'boss':('isEscapeFail' in el.dataset?'failed_escape':'fight'),
   low:parseFloat(el.querySelector('.min-value').textContent),
   average:parseFloat(el.querySelector('.avg-value').textContent),
   high:parseFloat(el.querySelector('.max-value').textContent),
  })));
  report.baseline={};
  for(const stage of [25,50,75,100])report.baseline[stage]=await read(stage);
  const base=report.baseline[50];
  for(const [id,name,fight,escape] of [
   ['20','Treasure of the Hero',.8,1],['07','Pebble of Deceit',.8,1],
   ['01','Eye of the Bull',.8,1],['02','Soul of the Rabbit',1.25,1],
   ['21','Crown Jewel of the Devil',1.25,1],['22','Cursed Pearl',1,.6],
   ['16','Erratic Boulder of the Hick',1,1.3]
  ]){
   await page.locator('#BtnGemOfFateDropDown_BD_50_1').click();
   await page.locator(`#GemOfFateDropDownList li[data-gof="${id}"]`).click();
   const rows=await read(50);
   for(const row of rows){
    const baseline=base.find(b=>b.kind===row.kind);
    const multiplier=row.kind==='boss'?1:row.kind==='fight'?fight:escape;
    for(const key of ['low','average','high'])assert.ok(Math.abs(row[key]-baseline[key]*multiplier)<.011,`${name} ${row.kind} ${key}`);
   }
   report.cases.push({name,expected_multipliers:{fight,failed_escape:escape,boss:1},observed:rows,passed:true});
   if(['20','02'].includes(id)){
    await page.mouse.move(2,2);
    // Let the site's menu/tooltip exit animation finish before capturing it.
    await page.waitForTimeout(600);
    await page.locator('#SectionBattleDamage').screenshot({path:path.join(out,`calculator-${id==='20'?'hero':'rabbit'}.png`)});
   }
  }
  report.source_hashes={};
  for(const name of ['index.html','main.js','main.min.js'])
   report.source_hashes[name]=crypto.createHash('sha256').update(fs.readFileSync(path.join(out,name))).digest('hex');
  report.passed=true;
 }finally{
  fs.writeFileSync(path.join(out,'calculator-verification.json'),JSON.stringify(report,null,2)+'\n');
  await browser.close();
 }
 console.log(JSON.stringify({passed:report.passed,cases:report.cases.length,errors:report.errors,baseline:report.baseline}));
})().catch(e=>{console.error(e);process.exitCode=1;});
