// Check the actual offline guide, including menus without the global winner.
const {chromium}=require(process.env.SFLD_PLAYWRIGHT_MODULE||'../out/vod-review-browser/node_modules/playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const url=pathToFileURL(path.resolve(__dirname,'../docs/final-stone-guide-2026-10-15/index.html')).href;
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.SFLD_BROWSER_CHANNEL||'msedge'});
 try{
  const page=await browser.newPage({viewport:{width:1300,height:1050}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.goto(url);
  assert.equal(await page.locator('#ranking tr').count(),3);
  assert.match(await page.locator('#ranking tr').first().innerText(),/Pebble of Deceit/);
  assert.match(await page.locator('#recommendation').innerText(),/Supported best/);
  await page.locator('#clear').click();assert.equal(await page.locator('#ranking tr').count(),18);
  await page.reload();assert.equal(await page.locator('#ranking tr').count(),18);
  await page.locator('#example').click();
  for(const keys of [0,1,3,5,10]){
   await page.selectOption('#keys',String(keys));
   const names=await page.locator('#ranking tr td:nth-child(2)').allTextContents();
   assert.deepEqual(names,['Pebble of Deceit','Pendant of the Key Master','Rusty Healing Stone']);
   assert.match(await page.locator('#recommendation').innerText(),/Supported best/);
  }
  await page.locator('#offers input[value="rabbit"]').click();
  assert.equal(await page.locator('#offers input:checked').count(),3);
  assert.match(await page.locator('#message').innerText(),/at most three/);
  await page.selectOption('#run','1');assert.equal(await page.locator('#ranking tr').count(),8);
  assert.equal(await page.locator('#offers input[value="rabbit"]').count(),0);
  await page.goto(url+'#run=1');assert.equal(await page.locator('#ranking tr').count(),8);
  await page.locator('#example').click();await page.selectOption('#keys','3');
  await page.screenshot({path:path.resolve(__dirname,'../out/final-stone-guide-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.resolve(__dirname,'../out/final-stone-guide-mobile.png'),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);console.log('Guide checks passed: real-offer ranking, all key levels, first-run pool, reload and mobile.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
