'use strict';
const {app, BrowserWindow, WebContentsView, ipcMain, dialog, Menu, screen, globalShortcut} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const readline = require('node:readline');
const {navigationAllowed, importProfiles, validateSaved} = require('./profiles.cjs');
const {observeMimics} = require('./live-mimic.cjs');
const {createRecorder} = require('./ld-recorder.cjs');
const {createLiveAdviser, adviceScript} = require('./live-adviser.cjs');
const {createAutoplay} = require('./autoplay.cjs');
// Every embedded-client dungeon packet goes to the private spool for measurement.
const recorder = createRecorder();

// Installed: the bundled runtime (tools/build_desktop_runtime.py). Development: the repository.
const ROOT = app.isPackaged ? path.join(process.resourcesPath, 'runtime') : path.resolve(__dirname, '..');
const SHELL = pathToFileURL(path.join(__dirname, 'index.html')).href;
const SMOKE = process.argv.includes('--smoke');
const VERIFY_ORIGINAL = process.argv.includes('--verify-original');
const PRACTICE_PREVIEW = process.argv.includes('--practice-preview');
const START_PRACTICE = PRACTICE_PREVIEW || process.argv.includes('--practice');
if (SMOKE || VERIFY_ORIGINAL || PRACTICE_PREVIEW) {
  const profile = path.join(ROOT, 'out', PRACTICE_PREVIEW ? `desktop-practice-preview-profile/${Date.now()}` : VERIFY_ORIGINAL ? 'desktop-original-check-profile' : 'desktop-smoke-profile');
  fs.mkdirSync(profile, {recursive: true});
  app.setPath('userData', profile);
  app.setPath('sessionData', profile);
}
app.setName('Legendary Dungeon Companion');
let win, practice, game, host, hostOrigin = '', mode = 'game', modalOpen = false;
// Live adviser for the original game: created once the local host is ready.
let liveHost = null, adviser = null, lastAdvice = null, autoplay = null;
const earlyPackets = [];
let fullscreen = false;
let accounts = [], selected = '', notice = 'Sign in inside the original game to see your characters.';
let exiting = false, savedPath;
let operations = Promise.resolve();

function state() {
  return {accounts, selected, mode, notice, fullscreen,
    capabilities: {originalGame: true, practiceRecommendations: true, practiceAutoplay: true,
                   liveRecommendations: true, liveAutoplay: true, liveMimicWarning: true},
    autoplay: autoplay ? autoplay.state() : {enabled: false, status: '', learned: 0},
    advice: lastAdvice && {decision: lastAdvice.decision, label: lastAdvice.label || ''}};
}
function publish() { if (win && !win.isDestroyed()) win.webContents.send('desktop:update', state()); }
function saveProfiles() {
  const tmp = savedPath + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({schema: 1, accounts}, null, 2) + '\n', {mode: 0o600});
  fs.renameSync(tmp, savedPath);
}
function layout() {
  if (!win || win.isDestroyed()) return;
  const [width, height] = win.getContentSize();
  const viewHeight = Math.max(1, height - (fullscreen ? 0 : 60 + (notice ? 24 : 0)));
  for (const [view, kind] of [[game, 'game'], [practice, 'practice']]) if (view) {
    view.setBounds({x: 0, y: fullscreen ? 0 : 60, width, height: viewHeight});
    view.setVisible(mode === kind && !modalOpen);
  }
  // Page zoom keeps the HUD, dialogs, hit targets and reward animations in the
  // same coordinate system. Only scale Practice; the live game owns its layout.
  const practiceAlive = practice?.webContents && !practice.webContents.isDestroyed();
  if (practiceAlive) {
    const zoom = Math.max(1, Math.min(2.5, width / 1440, viewHeight / 850));
    if (Math.abs(practice.webContents.getZoomFactor() - zoom) > .001) practice.webContents.setZoomFactor(zoom);
  }
  if (practiceAlive && !practice.webContents.isLoadingMainFrame() && practice.fullscreenLayout !== fullscreen) {
    practice.fullscreenLayout = fullscreen;
    practice.webContents.executeJavaScript(`document.body.classList.toggle('native-fullscreen', ${fullscreen})`).catch(() => {
      practice.fullscreenLayout = undefined;
    });
  }
  if (mode === 'game' && !modalOpen) game?.mimics?.redraw();
}
function setFullscreen(enabled) {
  fullscreen = enabled;
  win.setFullScreen(enabled);
  // Apply the layout immediately; native transitions may finish asynchronously.
  layout(); publish();
}
function fullscreenKey(event, input) {
  if (input.type !== 'keyDown') return;
  if (input.key === 'F11' || (input.key === 'Escape' && fullscreen)) {
    event.preventDefault(); setFullscreen(input.key === 'F11' && !fullscreen);
  }
}
function protect(view, origin = '') {
  const contents = view.webContents;
  contents.on('will-navigate', (event, url) => {
    if (!navigationAllowed(url, origin)) { event.preventDefault(); notice = 'That link opens outside the supported game view.'; publish(); }
  });
  contents.on('will-redirect', (event, url) => { if (!navigationAllowed(url, origin)) event.preventDefault(); });
  contents.setWindowOpenHandler(({url}) => {
    // Keep sign-in in the same isolated account session. No arbitrary external launches.
    if (navigationAllowed(url, origin)) contents.loadURL(url).catch(() => {});
    return {action: 'deny'};
  });
  contents.session.setPermissionRequestHandler((_contents, permission, callback) => callback(permission === 'fullscreen'));
  contents.session.setPermissionCheckHandler((_contents, permission) => permission === 'fullscreen');
  contents.session.on('will-download', event => { if (!origin) event.preventDefault(); });
  contents.on('did-fail-load', (_event, code, _description, _url, main) => {
    if (main && code !== -3) { notice = 'The game could not load. Check the connection and reopen this profile.'; publish(); }
  });
  contents.on('before-input-event', fullscreenKey);
}
async function openGame() {
  const account = accounts.find(a => a.id === selected);
  if (!account) throw new Error('Choose a saved login profile.');
  if (game?.accountId === selected) return;
  if (game) { win.contentView.removeChildView(game); game.webContents.close(); }
  game = new WebContentsView({webPreferences: {
    partition: `persist:sfld-account-${account.id}`, nodeIntegration: false,
    contextIsolation: true, sandbox: true, webSecurity: true,
    offscreen: VERIFY_ORIGINAL, backgroundThrottling: !VERIFY_ORIGINAL,
  }});
  game.accountId = selected;
  protect(game);
  game.mimics = observeMimics(game.webContents, warning => {
    if (warning.kind === 'mimic') notice = `Room ${warning.room}: MIMIC — fake chest. The warning uses the currently revealed encounter.`;
    publish();
  }, packet => { recorder.record(packet); livePacket(packet, account.id); });
  win.contentView.addChildView(game); layout();
  startLiveAdviser();
  game.webContents.on('input-event', (_event, input) => {
    if (input.type !== 'mouseDown' || !autoplay) return;
    const bounds = game.getBounds();
    autoplay.userClick(input.x / bounds.width, input.y / bounds.height);
  });
  game.webContents.on('did-finish-load', () => { if (lastAdvice) showAdvice(lastAdvice); });
  // Smoke tests do not visit the game or access a user's account cookies.
  if (!SMOKE) await game.webContents.loadURL(account.origin);
}
function livePacket(packet, accountId) {
  if (adviser) adviser.handle(packet, accountId);
  else if (earlyPackets.length < 200) earlyPackets.push([packet, accountId]);
}
// Autoplay presses the learned position of a control inside the game view.
function clickGame(x, y) {
  if (!game || game.webContents.isDestroyed() || mode !== 'game' || modalOpen) { autoplay.stop('Autoplay paused: the game is not in view.'); return; }
  const bounds = game.getBounds();
  const px = Math.round(x * bounds.width), py = Math.round(y * bounds.height);
  for (const type of ['mouseMove', 'mouseDown', 'mouseUp']) {
    game.webContents.sendInputEvent({type, x: px, y: py, button: 'left', clickCount: 1});
  }
}
function autoplayState(next) {
  if (mode === 'game' && (next.enabled || next.status.startsWith('Autoplay') || next.status.startsWith('Learned'))) notice = next.status;
  publish();
}
async function setAutoplay(on) {
  if (typeof on !== 'boolean') throw new Error('Invalid autoplay state.');
  if (on) {
    const consent = path.join(app.getPath('userData'), 'autoplay-consent.json');
    if (!fs.existsSync(consent)) {
      const choice = await dialog.showMessageBox(win, {type: 'warning', buttons: ['Turn on autoplay', 'Cancel'], defaultId: 1, cancelId: 1,
        title: 'Autoplay', message: 'Automated play breaks the game\'s rules.',
        detail: 'Shakes & Fidget can suspend accounts that use automation. Autoplay only clicks what the adviser recommends, stops on anything unexpected and pauses when you click in the game. It never spends mushrooms. Use it at your own risk.'});
      if (choice.response !== 0) return state();
      fs.writeFileSync(consent, JSON.stringify({accepted: new Date().toISOString()}) + '\n');
    }
    if (mode !== 'game') await setMode('game');
  }
  autoplay.setEnabled(on);
  if (on && lastAdvice) autoplay.advice(lastAdvice);
  return state();
}
function showAdvice(advice) {
  lastAdvice = advice;
  autoplay?.advice(advice);
  if (game && !game.webContents.isDestroyed()) {
    game.webContents.executeJavaScriptInIsolatedWorld(143, [{code: adviceScript(advice)}]).catch(() => {});
  }
  if (mode === 'game') notice = advice.label ? `Adviser: ${advice.label}` : notice;
  publish();
}
async function startLiveAdviser() {
  if (adviser) return;
  try {
    await startHost();
    adviser = createLiveAdviser({origin: liveHost.origin, token: liveHost.token, onAdvice: showAdvice,
      onSent: result => autoplay?.sent(result)});
    for (const [packet, accountId] of earlyPackets.splice(0)) adviser.handle(packet, accountId);
  } catch {
    notice = 'The adviser could not start; the game still works without it.'; publish();
  }
}
async function startHost() {
  if (hostOrigin) return hostOrigin;
  const python = [path.join(ROOT, 'python/python.exe'), path.join(ROOT, 'out/rl-env/Scripts/python.exe')].find(p => fs.existsSync(p));
  if (!python) throw new Error('The local simulator environment is missing. Reinstall the app.');
  host = spawn(python, ['-u', path.join(ROOT, 'tools/desktop_host.py')], {
    cwd: ROOT, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'],
  });
  return new Promise((resolve, reject) => {
    let ready = false;
    const timeout = setTimeout(() => { host.kill(); reject(new Error('The practice engine did not start.')); }, 20000);
    const lines = readline.createInterface({input: host.stdout});
    lines.on('line', line => {
      try {
        const record = JSON.parse(line);
        if (!ready && record.ready && /^http:\/\/127\.0\.0\.1:\d+$/.test(record.origin)) {
          ready = true; clearTimeout(timeout); hostOrigin = record.origin;
          if (/^http:\/\/127\.0\.0\.1:\d+$/.test(record.live || '') && typeof record.token === 'string') liveHost = {origin: record.live, token: record.token};
          resolve(hostOrigin);
        }
      } catch { /* Engine diagnostics are never displayed as account data. */ }
    });
    host.stderr.on('data', () => {});
    host.on('error', () => { clearTimeout(timeout); reject(new Error('The practice engine could not start.')); });
    host.on('exit', () => {
      clearTimeout(timeout); hostOrigin = '';
      if (!ready) reject(new Error('The practice engine stopped during startup.'));
      else if (!exiting) { notice = 'The practice engine stopped. Close and reopen the companion.'; publish(); }
    });
  });
}
async function openPractice() {
  if (practice) return;
  const origin = await startHost();
  practice = new WebContentsView({webPreferences: {
    partition: 'sfld-practice', nodeIntegration: false, contextIsolation: true,
    sandbox: true, webSecurity: true, backgroundThrottling: false, offscreen: SMOKE,
  }});
  protect(practice, origin); win.contentView.addChildView(practice); layout();
  practice.webContents.on('did-finish-load', () => { practice.fullscreenLayout = undefined; layout(); });
  await practice.webContents.loadURL(origin + '/?desktop=1');
}
async function setMode(next) {
  if (!['game', 'practice'].includes(next)) throw new Error('Unknown play mode.');
  if (next !== 'game' && autoplay?.state().enabled) autoplay.stop('Autoplay is off: Practice is open.');
  mode = next;
  notice = next === 'game'
    ? (lastAdvice?.label ? `Adviser: ${lastAdvice.label}` : 'Original game · the adviser shows its recommendation once you enter the Legendary Dungeon.')
    : '';
  layout(); publish();
  if (next === 'game') await openGame(); else await openPractice();
  layout(); publish(); return state();
}
async function mimicExample() {
  await setMode('practice');
  const value = 'schema=1\nroom=12\nrun_number=2\nphase=encounter\nencounter=mimic\nhp_percent=80\nkeys=3\n';
  // This is the trusted local simulator, never a script injected into the game.
  await practice.webContents.executeJavaScript(`replaceSession({...config, run_number:2, initial_state:${JSON.stringify(value)}})`);
  return state();
}
function trusted(event) {
  if (event.sender !== win.webContents || event.senderFrame !== win.webContents.mainFrame || event.senderFrame.url !== SHELL) {
    throw new Error('Only the local companion controls can use this operation.');
  }
}
function handler(name, fn) {
  ipcMain.handle('desktop:' + name, async (event, ...args) => {
    trusted(event);
    try {
      if (['state', 'modal', 'fullscreen'].includes(name)) return await fn(...args);
      const result = operations.then(() => fn(...args));
      operations = result.catch(() => {});
      return await result;
    }
    catch (error) { notice = error.message; publish(); throw new Error(error.message); }
  });
}
function registerHandlers() {
  handler('state', state);
  handler('mode', setMode);
  handler('autoplay', setAutoplay);
  handler('modal', open => { if (typeof open !== 'boolean') throw new Error('Invalid dialog state.'); modalOpen = open; layout(); return state(); });
  handler('select', async id => {
    if (!accounts.some(a => a.id === id)) throw new Error('Unknown login profile.');
    selected = id; await setMode('game'); return state();
  });
  handler('add', profile => { accounts = importProfiles([profile], accounts); saveProfiles(); publish(); return state(); });
  handler('import', async () => {
    const result = await dialog.showOpenDialog(win, {title: 'Import login profiles', properties: ['openFile'], filters: [{name: 'Login profile JSON', extensions: ['json']}]});
    if (result.canceled) return state();
    const file = result.filePaths[0];
    if (fs.statSync(file).size > 1024 * 1024) throw new Error('The login profile file is too large.');
    accounts = importProfiles(JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '')), accounts);
    saveProfiles(); notice = 'Login profiles imported. Sign in separately inside each game view.'; publish(); return state();
  });
  handler('fullscreen', () => { setFullscreen(!fullscreen); return state(); });
  handler('mimic-example', mimicExample);
}
async function smoke() {
  const assert = require('node:assert/strict');
  const output = path.join(ROOT, 'out/desktop-smoke'); fs.mkdirSync(output, {recursive: true});
  await setMode('practice');
  // Readiness is tied to an actual observation, not a fixed render delay.
  for (let i = 0; i < 300; i++) {
    if (await practice.webContents.executeJavaScript('typeof obs === "function" && !!obs()?.neural_adviser && !busy && document.body.classList.contains("game-view")')) break;
    const failure = await practice.webContents.executeJavaScript('document.querySelector("#error:not([hidden])")?.textContent || ""');
    if (failure) throw new Error('Practice initialization: ' + failure);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(await practice.webContents.executeJavaScript('!!obs()?.neural_adviser'),
    await practice.webContents.executeJavaScript('JSON.stringify({phase:obs()?.phase, advice:obs()?.suggestions, error:document.querySelector("#error")?.textContent})'));
  assert.ok(await practice.webContents.executeJavaScript('document.body.classList.contains("game-view")'));
  const waitForView = async (contents, expression) => {
    for (let i = 0; i < 60; i++) {
      if (await contents.executeJavaScript(expression)) return;
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    throw new Error('Desktop layout did not update: ' + expression);
  };
  const capturePractice = () => new Promise((resolve, reject) => {
    const contents = practice.webContents;
    // Electron's offscreen WebContentsView paints on its parent window canvas.
    const [width,height]=win.getContentSize(),bounds={width,height},scale=screen.getDisplayMatching(win.getBounds()).scaleFactor;
    let lastSize;
    const painted = (_event, _rect, bitmap) => {
      const size=bitmap.getSize(); lastSize=size;
      if (size.width!==Math.round(bounds.width*scale)||size.height!==Math.round(bounds.height*scale)) return;
      clearTimeout(timeout); contents.removeListener('paint',painted); resolve(bitmap.toPNG());
    };
    const timeout = setTimeout(() => { contents.removeListener('paint', painted); reject(new Error(`Practice did not paint after resize: ${JSON.stringify({bounds,scale,lastSize})}`)); }, 5000);
    contents.on('paint', painted); contents.invalidate();
  });
  const originalBounds = practice.getBounds();
  const originalSize = win.getContentSize();
  assert.equal(originalBounds.y, 60);
  win.setContentSize(2554, 1339); layout();
  const largeBounds = practice.getBounds(), largeZoom = practice.webContents.getZoomFactor();
  assert.ok(largeZoom > 1.4, 'Large desktop should scale the Practice HUD');
  await waitForView(practice.webContents, `Math.abs(innerWidth - ${largeBounds.width / largeZoom}) < 2`);
  await waitForView(practice.webContents, 'Array.from(document.images).every(i => i.complete)');
  const alignment = await practice.webContents.executeJavaScript(`(() => {
    const b=document.querySelector('#scene-world').getBoundingClientRect();
    return [...document.querySelectorAll('.door-choice')].map(d=>{
      const r=d.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
      return {left:(r.x-b.x)/b.width,width:r.width/b.width,clickable:!!hit?.closest('.door-choice')};
    });
  })()`);
  assert.equal(alignment.length, 2);
  assert.ok(alignment.every(d => d.clickable && d.width < .21), 'Door hit targets must stay inside the artwork openings');
  const largeHud = await practice.webContents.executeJavaScript(`(() => {
    const r=document.querySelector('#hud-room-counter').getBoundingClientRect();
    return {inside:r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight,
      text:document.querySelector('#hud-room-counter').textContent,
      clean:!document.querySelector('#scene-hud .hud-currency,#scene-hud .hud-run')};
  })()`);
  assert.ok(largeHud.inside && largeHud.clean, JSON.stringify(largeHud));
  assert.equal(await win.webContents.executeJavaScript('document.querySelector("footer").hidden'),true);
  fs.writeFileSync(path.join(output, 'large-screen-doors.png'), await capturePractice());
  fs.writeFileSync(path.join(output, 'header.png'), (await win.webContents.capturePage({x: 0, y: 0, width: win.getContentSize()[0], height: 60})).toPNG());
  win.setContentSize(...originalSize); layout();
  assert.equal(practice.webContents.getZoomFactor(), 1);
  await win.webContents.executeJavaScript('window.dungeonApp.fullscreen()');
  await waitForView(win.webContents, 'document.body.classList.contains("fullscreen")');
  await waitForView(practice.webContents, 'document.body.classList.contains("native-fullscreen")');
  assert.deepEqual(practice.getBounds(), {x: 0, y: 0, width: win.getContentSize()[0], height: win.getContentSize()[1]});
  assert.equal(await win.webContents.executeJavaScript('getComputedStyle(document.querySelector("header")).display'), 'none');
  assert.equal(await practice.webContents.executeJavaScript('getComputedStyle(document.querySelector(".run-summary")).display'), 'none');
  await waitForView(practice.webContents, `Math.abs(innerWidth - ${practice.getBounds().width / practice.webContents.getZoomFactor()}) < 2`);
  fs.writeFileSync(path.join(output, 'fullscreen-doors.png'), await capturePractice());
  let escaped = false;
  practice.webContents.emit('before-input-event', {preventDefault() { escaped = true; }}, {type: 'keyDown', key: 'Escape'});
  await waitForView(win.webContents, '!document.body.classList.contains("fullscreen")');
  await waitForView(practice.webContents, '!document.body.classList.contains("native-fullscreen")');
  assert.ok(escaped); assert.equal(practice.getBounds().y, 60);
  assert.notEqual(await win.webContents.executeJavaScript('getComputedStyle(document.querySelector("header")).display'), 'none');
  await mimicExample();
  const before = await practice.webContents.executeJavaScript('({phase:obs().phase, encounter:obs().encounter, steps:payload.steps, warning:document.querySelector(".mimic-warning")?.textContent, actions:obs().legal_actions.map(a=>a.kind)})');
  assert.equal(before.encounter, 'mimic'); assert.equal(before.steps, 0);
  assert.match(before.warning, /MIMIC/); assert.ok(before.actions.includes('skip'));
  assert.ok(!before.actions.some(kind => kind.startsWith('heal_')));
  assert.equal(await practice.webContents.executeJavaScript('document.querySelector(".recovery-tools").hidden'), true);
  // Offscreen test rendering also works while the Windows desktop is locked.
  await new Promise(resolve => setTimeout(resolve, 150));
  fs.writeFileSync(path.join(output, 'mimic-before-action.png'), (await practice.webContents.capturePage()).toPNG());
  // UI-origin IPC succeeds, while the remote/practice view has no privileged bridge.
  assert.equal(await practice.webContents.executeJavaScript('typeof window.dungeonApp'), 'undefined');
  await win.webContents.executeJavaScript('window.dungeonApp.modal(true)');
  assert.equal(practice.getVisible(), false);
  await win.webContents.executeJavaScript('window.dungeonApp.modal(false)');
  await practice.webContents.executeJavaScript('act(legal("skip"),true)');
  assert.notEqual(await practice.webContents.executeJavaScript('obs().encounter'), 'mimic');
  const recoveryState = 'schema=1\nroom=100\nrun_number=2\nphase=recovery\nresume_phase=encounter\nencounter=boss\nhp_percent=0\nkeys=2\ngems=rabbit,moonstone,greed\n';
  await practice.webContents.executeJavaScript(`replaceSession({...config,budget:100,run_number:2,initial_state:${JSON.stringify(recoveryState)}})`);
  const recovery = await practice.webContents.executeJavaScript(`(() => {
    const wait=document.querySelector('#wait-selected'),r=wait.getBoundingClientRect();
    return {target:document.querySelector('.recovery-targets .selected')?.textContent,
      wait:wait.textContent,clickable:wait.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)),
      cursor:getComputedStyle(wait).cursor,menuMushroom:!!document.querySelector('#menu-progress .currency-icon'),
      backpack:!!document.querySelector('#hud-loot .backpack-icon')};
  })()`);
  assert.ok(recovery.clickable);assert.equal(recovery.cursor,'default');
  // Icons come from locally extracted game art, which installed copies do not include.
  if (fs.existsSync(path.join(ROOT, 'web/local-assets/manifest.json'))) assert.ok(recovery.menuMushroom&&recovery.backpack);
  await new Promise(resolve => setTimeout(resolve, 150));
  fs.writeFileSync(path.join(output, 'recovery.png'), await capturePractice());
  const stoneState=fs.readFileSync(path.join(ROOT,'examples/final-stone-moonstone-thirsty.state'),'utf8');
  await practice.webContents.executeJavaScript(`replaceSession({...config,budget:5,run_number:2,initial_state:${JSON.stringify(stoneState)}})`);
  const stones=await practice.webContents.executeJavaScript('({advice:payload.stone_advice,steps:payload.steps,imagesDraggable:[...document.querySelectorAll("#scene img")].some(i=>i.draggable)})');
  assert.equal(stones.advice.status,'ready',JSON.stringify(stones.advice));assert.equal(stones.advice.trajectories,6144);
  assert.equal(stones.steps,0);assert.equal(stones.imagesDraggable,false);
  await waitForView(practice.webContents,'document.querySelectorAll(".stone-simulation-rating").length===3');
  await new Promise(resolve => setTimeout(resolve, 150));
  fs.writeFileSync(path.join(output,'final-stones.png'),await capturePractice());
  await practice.webContents.executeJavaScript('document.querySelector("#scene").focus()');
  practice.webContents.sendInputEvent({type:'keyDown',keyCode:'N'});
  practice.webContents.sendInputEvent({type:'keyUp',keyCode:'N'});
  await waitForView(practice.webContents,'!busy && payload.steps===1 && obs().phase!=="gems"');
  assert.equal(await practice.webContents.executeJavaScript('payload.journal[0].gem'),stones.advice.estimated_best);
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify({passed: true, engineAdvice: true, isolatedGame: true, mimicWarningBeforeAction: before.steps === 0, fullscreenLayout: true, recoveryOnlyRefills: true, recovery, finalStones:{trajectories:stones.advice.trajectories,chosen:stones.advice.estimated_best},recommendedHotkey:true,imagesDraggable:false,largeScreen: {bounds:largeBounds,zoom:largeZoom,alignment,hud:largeHud},zoomRestored:true}, null, 2));
  console.log('Desktop window smoke passed.'); app.quit();
}

async function verifyOriginal() {
  await setMode('game');
  const output = path.join(ROOT, 'out/desktop-original-check'); fs.mkdirSync(output, {recursive: true});
  const facts = await game.webContents.executeJavaScript('({url:location.origin,title:document.title,node:typeof require,bridge:typeof window.dungeonApp,canvas:!!document.querySelector("canvas")})');
  if (!navigationAllowed(facts.url) || facts.node !== 'undefined' || facts.bridge !== 'undefined') throw new Error('Original game isolation failed.');
  for (let attempt=0; attempt<120; attempt++) {
    facts.unityReady = await game.webContents.executeJavaScript('!!window.game && typeof window.game.SendMessage === "function"');
    if (facts.unityReady) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  if (!facts.unityReady) throw new Error('The original game did not finish booting.');
  facts.landingPageRemoved = await game.webContents.executeJavaScript('!document.getElementById("lp-content")');
  if (!facts.landingPageRemoved) throw new Error('The marketing page still covers the original game.');
  // Capture only the fresh anonymous profile, without entering a character.
  await new Promise(resolve => setTimeout(resolve, 2000));
  fs.writeFileSync(path.join(output, 'original-game.png'), (await game.webContents.capturePage()).toPNG());
  fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(facts, null, 2));
  console.log(JSON.stringify(facts)); app.quit();
}

app.whenReady().then(async () => {
  fs.mkdirSync(app.getPath('userData'), {recursive: true});
  savedPath = path.join(app.getPath('userData'), 'login-profiles.json');
  accounts = fs.existsSync(savedPath) ? validateSaved(JSON.parse(fs.readFileSync(savedPath, 'utf8'))) : importProfiles([{label: 'My S&F login'}]);
  selected = accounts[0]?.id; saveProfiles();
  autoplay = createAutoplay({file: path.join(app.getPath('userData'), 'autoplay-controls.json'), click: clickGame, onState: autoplayState});
  // F10 stops autoplay from anywhere, even when another window has focus.
  globalShortcut.register('F10', () => autoplay.stop('Autoplay stopped (F10).'));
  app.on('will-quit', () => globalShortcut.unregisterAll());
  win = new BrowserWindow({width: 1440, height: 1000, minWidth: 820, minHeight: 620,
    titleBarStyle: 'hidden', titleBarOverlay: {color: '#10171f', symbolColor: '#d7dfe3', height: 60},
    show: !(SMOKE || VERIFY_ORIGINAL), backgroundColor: '#101d25', autoHideMenuBar: true,
    webPreferences: {preload: path.join(__dirname, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true}});
  Menu.setApplicationMenu(null); registerHandlers();
  win.on('resize', layout); win.on('enter-full-screen', () => { fullscreen = true; layout(); publish(); });
  win.on('leave-full-screen', () => { fullscreen = false; layout(); publish(); });
  win.webContents.on('will-navigate', event => event.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({action: 'deny'}));
  win.webContents.on('before-input-event', fullscreenKey);
  await win.loadURL(SHELL);
  if (SMOKE) await smoke(); else if (VERIFY_ORIGINAL) await verifyOriginal(); else await setMode(START_PRACTICE ? 'practice' : 'game');
}).catch(error => { console.error(error.message); process.exitCode = 1; app.quit(); });

app.on('window-all-closed', () => app.quit());
app.on('before-quit', event => {
  if (exiting) return;
  exiting = true; event.preventDefault();
  for (const view of [game, practice]) if (view && !view.webContents.isDestroyed()) view.webContents.close();
  (async () => {
    if (host && host.exitCode === null) {
      const done = once(host, 'exit').catch(() => {});
      host.stdin.end('stop\n');
      await Promise.race([done, new Promise(resolve => setTimeout(resolve, 4000))]);
      if (host.exitCode === null) host.kill();
    }
    app.exit(process.exitCode || 0);
  })();
});
