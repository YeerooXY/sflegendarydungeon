'use strict';
const {app, BrowserWindow, Menu, screen, ipcMain, globalShortcut} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const readline = require('node:readline');
const assert = require('node:assert/strict');

const ROOT = path.resolve(__dirname, '..');
const SMOKE = process.argv.includes('--smoke');
function integerOption(name, fallback, minimum, maximum) {
  const raw = process.argv.find(value => value.startsWith(`--${name}=`));
  const value = raw ? Number(raw.split('=')[1]) : fallback;
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error(`Invalid ${name}`);
  return value;
}
const BUDGET = integerOption('budget', 1000, 0, 1000000);
const PACE = integerOption('pace', 900, 450, 2400);
if (![450, 900, 2400].includes(PACE)) throw new Error('Pace must be 450, 900 or 2400 milliseconds.');
const SEED = integerOption('seed', Date.now() % 1000000000, 0, 2000000000);
const runId = new Date().toISOString().replace(/[:.]/g, '-');
const OUTPUT = path.join(ROOT, 'out', SMOKE ? 'watch-four-smoke' : 'watch-four', runId);
fs.mkdirSync(OUTPUT, {recursive:true});
const profile = path.join(ROOT, 'out', SMOKE ? 'watch-four-smoke-profile' : 'watch-four-profile');
fs.mkdirSync(profile, {recursive:true});
app.setPath('userData', profile);
app.setPath('sessionData', profile);
app.setName('Legendary Dungeon — Four agents');
const windows = new Map(), archived = new Map(), errors = [];
let host, origin = '', exiting = false, monitor, monitoring = false, controls = Promise.resolve();

function tile() {
  const {x,y,width,height} = screen.getPrimaryDisplay().workArea;
  const left = Math.floor(width / 2), top = Math.floor(height / 2);
  for (const [win, lane] of windows) {
    if (win.isDestroyed()) continue;
    const right = (lane - 1) % 2, bottom = lane > 2;
    win.setBounds({x:x + (right ? left : 0), y:y + (bottom ? top : 0),
      width:right ? width - left : left, height:bottom ? height - top : top});
  }
}
async function waitFor(win, expression, timeout=30000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (win.isDestroyed()) throw new Error('Watch window was closed during startup.');
    const value = await win.webContents.executeJavaScript(expression);
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${expression}`);
}
function startHost() {
  const python = path.join(ROOT, 'out/rl-env/Scripts/python.exe');
  host = spawn(python, ['-u', path.join(ROOT, 'tools/desktop_host.py')], {
    cwd:ROOT, windowsHide:true, stdio:['pipe','pipe','pipe'],
  });
  const log = fs.createWriteStream(path.join(OUTPUT, 'backend.log'));
  host.stderr.pipe(log);
  return new Promise((resolve, reject) => {
    let ready = false;
    const timeout = setTimeout(() => { host.kill(); reject(new Error('The simulator did not start in time.')); }, 30000);
    readline.createInterface({input:host.stdout}).on('line', line => {
      try {
        const value = JSON.parse(line);
        if (!ready && value.ready && /^http:\/\/127\.0\.0\.1:\d+$/.test(value.origin)) {
          ready = true; clearTimeout(timeout); origin = value.origin; resolve();
        }
      } catch { /* Non-JSON diagnostics do not change the host address. */ }
    });
    host.on('error', error => { clearTimeout(timeout); reject(error); });
    host.on('exit', () => {
      clearTimeout(timeout);
      if (!ready) reject(new Error('The simulator stopped during startup.'));
      else if (!exiting) {
        errors.push('The simulator stopped. Reopen Start-Watch-Four.ps1.');
        for (const win of windows.keys()) if (!win.isDestroyed()) {
          win.webContents.executeJavaScript('window.dungeonWatch?.pause(); window.dungeonWatch?.notice("The simulator stopped. Reopen the four-window launcher.")').catch(() => {});
        }
      }
    });
  });
}
function trusted(event) {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!windows.has(win) || event.senderFrame !== event.sender.mainFrame ||
      new URL(event.senderFrame.url).origin !== origin) throw new Error('Only a local watch window can use these controls.');
  return win;
}
async function snapshots() {
  return Promise.all([...windows.keys()].filter(win => !win.isDestroyed()).map(async win => {
    const value = await win.webContents.executeJavaScript('window.dungeonWatch?.snapshot()');
    return {...value, bounds:win.getBounds(), visible:win.isVisible(), rendererPid:win.webContents.getOSProcessId()};
  }));
}
async function setAll(running) {
  await Promise.all([...windows.keys()].filter(win => !win.isDestroyed()).map(win =>
    win.webContents.executeJavaScript(`window.dungeonWatch?.${running ? 'resume' : 'pause'}()`)));
}
async function toggleAll() {
  const states = await snapshots();
  await setAll(!states.some(value => value.running));
}
function serialize(task) {
  const result = controls.then(task);
  controls = result.catch(error => { errors.push(error.message); });
  return result;
}
async function archive(win, flagged=false) {
  const state = await win.webContents.executeJavaScript('window.dungeonWatch.snapshot()');
  const key = state.session;
  if (!flagged && archived.has(key)) return archived.get(key);
  const recording = await win.webContents.executeJavaScript('window.dungeonWatch.recording()');
  const name = `agent-${state.lane}-event-${state.event}-seed-${state.seed}-${flagged ? 'flag-' + Date.now() : 'complete'}`;
  const target = path.join(OUTPUT, name);
  fs.writeFileSync(target + '.json', JSON.stringify(recording) + '\n');
  if (flagged) {
    await new Promise(resolve => setTimeout(resolve,250));
    fs.writeFileSync(target + '.png', (await win.webContents.capturePage()).toPNG());
    fs.writeFileSync(target + '.context.json', JSON.stringify({...state, session:undefined, captured_at:new Date().toISOString()}, null, 2) + '\n');
    await win.webContents.executeJavaScript(`window.dungeonWatch.notice(${JSON.stringify('Saved ' + name + '. All agents paused; F9 resumes.')})`);
  } else archived.set(key, target + '.json');
  return target + '.json';
}
async function flag(win) {
  await setAll(false);
  await Promise.all([...windows.keys()].filter(value => !value.isDestroyed()).map(value =>
    value.webContents.executeJavaScript('window.dungeonWatch?.idle()')));
  return archive(win, true);
}
async function createWindow(lane) {
  const win = new BrowserWindow({show:false, width:1280, height:700, minWidth:440, minHeight:320,
    title:`Dungeon agent ${lane} · simulation`, backgroundColor:'#101d25', autoHideMenuBar:true,
    webPreferences:{preload:path.join(__dirname,'watch-preload.cjs'), partition:'sfld-watch',
      nodeIntegration:false, contextIsolation:true, sandbox:true, webSecurity:true, backgroundThrottling:false}});
  windows.set(win, lane); tile();
  const scale = () => {
    const [width,height] = win.getContentSize();
    win.webContents.setZoomFactor(Math.max(.55, Math.min(1, width / 1150, height / 790)));
  };
  win.on('resize', scale);
  win.on('page-title-updated', event => event.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({action:'deny'}));
  win.webContents.on('will-navigate', event => event.preventDefault());
  win.webContents.on('will-redirect', event => event.preventDefault());
  win.webContents.session.setPermissionRequestHandler((_contents,_permission,callback) => callback(false));
  win.webContents.on('render-process-gone', (_event, details) => errors.push(`Agent ${lane}: ${details.reason}`));
  win.webContents.on('console-message', (_event, level, message) => {
    if (level === 3) errors.push(`Agent ${lane}: ${message}`);
  });
  win.webContents.on('before-input-event', (event,input) => {
    if (input.type !== 'keyDown' || input.isAutoRepeat) return;
    if (input.key === 'F8') { event.preventDefault(); serialize(() => flag(win)); }
    else if (input.key === 'F9' && !globalShortcut.isRegistered('F9')) { event.preventDefault(); serialize(toggleAll); }
  });
  win.on('close', event => {
    if (exiting || win.closing) return;
    event.preventDefault(); win.closing = true;
    (async () => {
      try {
        await win.webContents.executeJavaScript('window.dungeonWatch?.pause(); window.dungeonWatch?.idle()');
        await win.webContents.executeJavaScript('window.dungeonWatch?.close()');
      } catch { /* Closing the owner also closes the backend's remaining sessions. */ }
      finally { win.destroy(); }
    })();
  });
  win.on('closed', () => { windows.delete(win); });
  const params = new URLSearchParams({desktop:'1',lane:String(lane),seed:String(SEED + lane - 1),
    budget:String(BUDGET),pace:String(PACE),autostart:'0'});
  await win.loadURL(origin + '/?' + params);
  await waitFor(win, 'typeof payload !== "undefined" && payload && !busy');
  await win.webContents.insertCSS(fs.readFileSync(path.join(__dirname,'watch.css'),'utf8'));
  await win.webContents.executeJavaScript(fs.readFileSync(path.join(__dirname,'watch-renderer.js'),'utf8'));
  scale();
  if (!SMOKE) win.show();
  return win;
}
async function status() {
  if (monitoring || exiting) return;
  monitoring = true;
  try {
    const value = {updated_at:new Date().toISOString(),pid:process.pid,origin,budget:BUDGET,pace_ms:PACE,
      display:screen.getPrimaryDisplay().workArea,windows:await snapshots(),errors};
    fs.writeFileSync(path.join(OUTPUT,'status.json'), JSON.stringify(value,null,2) + '\n');
  } finally { monitoring = false; }
}
async function smoke() {
  const first = [...windows.keys()][0];
  const before = await snapshots();
  assert.equal(new Set(before.map(value => value.seed)).size,4);
  assert.equal(new Set(before.map(value => value.session)).size,4);
  assert.ok(before.every(value => value.checkpoint === '9689128e5df57f75724b6c0384a3df040d866f283629b4bb9d4c61e630fba59e'));
  for (const win of windows.keys()) {
    const layout = await win.webContents.executeJavaScript(`(()=>{const r=$('scene').getBoundingClientRect();return {width:r.width,height:r.height,viewport:innerWidth,bottom:r.bottom,screenHeight:innerHeight};})()`);
    assert.ok(layout.width > layout.viewport * .95, 'The game must fill the watch window width.');
    assert.ok(layout.height > 400 && layout.bottom <= layout.screenHeight, `The game must fit vertically: ${JSON.stringify(layout)}`);
  }
  await setAll(true);
  await Promise.all([...windows.keys()].map(win => waitFor(win, 'window.dungeonWatch.snapshot().steps >= 4')));
  await setAll(false);
  await Promise.all([...windows.keys()].map(win => win.webContents.executeJavaScript('window.dungeonWatch.idle()')));
  const paused = await snapshots();
  await new Promise(resolve => setTimeout(resolve,1200));
  assert.deepEqual((await snapshots()).map(value => value.steps), paused.map(value => value.steps));
  await first.webContents.executeJavaScript('window.dungeonWatch.step()');
  assert.ok((await snapshots())[0].steps > paused[0].steps);
  const flagged = await flag(first);
  assert.ok(fs.existsSync(flagged));
  assert.ok(fs.existsSync(flagged.replace(/\.json$/,'.png')));
  const recorded = JSON.parse(fs.readFileSync(flagged,'utf8'));
  assert.equal(recorded.actions.length,(await snapshots())[0].steps);
  // End a real native event, then verify automatic archive/restart with a new seed.
  await first.webContents.executeJavaScript(`(async()=>{
    await replaceSession({...config,run_number:2,deadline_hours:1,budget:0,initial_state:'schema=1\\nroom=19\\nrun_number=2\\nphase=recovery\\nresume_phase=doors\\ndoors=unknown\\nhp_percent=0\\nkeys=0\\n'});
    const action=obs().legal_actions.filter(a=>a.kind==='wait').sort((a,b)=>b.value-a.value)[0];
    await act(action,false); return obs().terminal;
  })()`).then(value => assert.equal(value,true));
  await first.webContents.executeJavaScript('window.dungeonWatch.resume()');
  await waitFor(first,'window.dungeonWatch.snapshot().event === 2 && window.dungeonWatch.snapshot().steps >= 1');
  await setAll(false);
  await first.webContents.executeJavaScript('window.dungeonWatch.idle()');
  const current = (await snapshots())[0];
  assert.equal(current.seed, SEED + 4); assert.equal(current.budget,BUDGET); assert.equal(current.deadline_hours,168);
  assert.ok(archived.size > 0);
  assert.equal(await first.webContents.executeJavaScript('typeof require'),'undefined');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(OUTPUT,'result.json'),JSON.stringify({passed:true,independentSessions:4,
    latestPolicy:true,pauseAndStep:true,flagRecording:true,automaticEventRestart:true,errors},null,2));
  console.log('Four-window smoke passed: four trained agents, pause/step, saved flags, automatic event restart.');
  app.quit();
}

if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on('second-instance', () => { tile(); for (const win of windows.keys()) { win.restore(); win.show(); } });
  app.whenReady().then(async () => {
    Menu.setApplicationMenu(null);
    await startHost();
    ipcMain.handle('watch:pause-all', event => { trusted(event); return serialize(toggleAll); });
    ipcMain.handle('watch:flag', event => { const win = trusted(event); return serialize(() => flag(win)); });
    ipcMain.handle('watch:archive', event => archive(trusted(event)));
    await Promise.all([1,2,3,4].map(createWindow));
    await status();
    if (SMOKE) await smoke();
    else {
      globalShortcut.register('F9', () => serialize(toggleAll));
      await setAll(true); await status();
      monitor = setInterval(() => status().catch(error => errors.push(error.message)),2000);
      console.log(`Four agents running. Logs: ${OUTPUT}`);
    }
  }).catch(error => {
    errors.push(error.stack); fs.writeFileSync(path.join(OUTPUT,'error.log'), errors.join('\n'));
    console.error(error); process.exitCode=1; app.quit();
  });
}
app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (exiting) return;
  exiting=true; event.preventDefault(); clearInterval(monitor); globalShortcut.unregisterAll();
  (async () => {
    await setAll(false).catch(() => {});
    if (host && host.exitCode === null) {
      const done = once(host,'exit').catch(() => {});
      host.stdin.end('stop\n');
      await Promise.race([done,new Promise(resolve => setTimeout(resolve,4000))]);
      if (host.exitCode === null) host.kill();
    }
    app.exit(process.exitCode || 0);
  })();
});
