const {chromium}=require(process.env.SFLD_PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const output=path.resolve(__dirname,'../out/daily-schedule-ui-2026-10-15');
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.SFLD_BROWSER_CHANNEL||undefined});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.env.SFLD_LAB_URL||'http://127.0.0.1:8798');
  await page.waitForSelector('#hud-health');
  await page.locator('#new-run').click();
  await page.locator('#sleep-input').selectOption('8');
  await page.locator('#start-time-input').fill('17:00');
  await page.locator('#setup-form button[type=submit]').click();
  await page.waitForFunction(()=>!busy&&!$('setup').open);
  let observed=await page.evaluate(()=>({settings:config,observation:obs()}));
  assert.equal(observed.settings.sleep_until_hour,8);assert.equal(observed.settings.event_start_hour,17);
  assert.equal(observed.observation.local_hour,17);
  assert.match(await page.locator('.hud-location').innerText(),/17:00 local/);
  await page.evaluate(async()=>{
   await replaceSession({...config,neural_adviser:false,initial_state:'schema=1\nroom=10\nrun_number=1\nphase=recovery\nresume_phase=doors\nhp_percent=0\nkeys=0\ndoors=monster,mystery\n'});
   document.body.classList.add('game-view');$('speed').value='900';
  });
  assert.match(await page.evaluate(()=>actionText({kind:'wait',index:0,value:9.6})),/15h 00m.*includes sleep/);
  await page.evaluate(async()=>{await act(obs().legal_actions.find(a=>a.kind==='wait'&&Math.abs(a.value-9.6)<1e-9),true);});
  observed=await page.evaluate(()=>({settings:config,observation:obs(),journal:payload.journal}));
  assert.equal(observed.observation.elapsed_hours,15);assert.equal(observed.observation.local_hour,8);
  assert.equal(observed.observation.hp,.625);assert.equal(observed.observation.phase,'recovery');
  assert.match(await page.locator('.hud-location').innerText(),/08:00 local/);
  assert.ok(observed.journal.at(-1).outcomes.some(e=>e.kind==='sleep'));
  await page.screenshot({path:path.join(output,'morning-recovery.png')});
  await page.evaluate(async()=>{const r=await api('export',{session});await replaceSession(r.settings,r);});
  assert.equal(await page.evaluate(()=>obs().elapsed_hours),15);
  await page.evaluate(()=>document.body.classList.remove('game-view'));
  await page.locator('#undo').click();await page.waitForFunction(()=>!busy&&obs().elapsed_hours===0);
  assert.equal(await page.evaluate(()=>obs().hp),0);
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({passed:true,errors,observed},null,2));
  console.log('PASS daily schedule: setup, local clock, overnight recovery, wait label, replay and undo');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
