'use strict';
// Matches the inspected client in tools/sfld_study/automatic.py. Only the
// currently revealed step is read; no hidden door payload is classified.
const CLIENT = '31.500.260929.1';
function inspectPacket(body, client) {
  if (client !== CLIENT || typeof body !== 'string' || body.length > 4 * 1024 * 1024) return {kind: 'clear'};
  const records = body.trim().split('&');
  if (records.some(row => /^iadungeonsave[.(]/.test(row))) return {kind: 'clear'};
  const saves = records.filter(row => row.startsWith('iadungeonsave:'));
  if (!saves.length) return {kind: 'ignore'};
  if (saves.length !== 1) return {kind: 'clear'};
  const value = saves[0].slice('iadungeonsave:'.length);
  if (!/^-?\d+(?:\/-?\d+)*$/.test(value)) return {kind: 'clear'};
  const words = value.split('/');
  if (words.length !== 50) return {kind: 'clear'};
  const phase = Number(words[15]), room = Number(words[17]);
  if (![10, 11, 12].includes(phase) || room < 1 || room > 100 || words[49] !== '0') return {kind: 'clear'};
  const type = words[19 + phase - 10], variant = words[22 + phase - 10];
  return type === '100' && variant === '500' ? {kind: 'mimic', room} : {kind: 'clear'};
}

function overlayScript(warning) {
  // Only our own fixed strings and a bounded numeric room reach the page.
  const show = warning.kind === 'mimic';
  return `(()=>{
    const old=document.getElementById('sfld-visible-mimic-warning');if(old)old.remove();
    if(!${JSON.stringify(show)})return;
    const canvas=document.querySelector('#canvas,canvas');if(!canvas)return;
    const rect=canvas.getBoundingClientRect();if(rect.width<100||rect.height<100)return;
    const box=document.createElement('div');box.id='sfld-visible-mimic-warning';box.setAttribute('role','status');
    box.style.cssText='position:fixed;z-index:2147483000;pointer-events:none;transform:translateX(-50%);padding:12px 22px;border:3px solid #ffd180;border-radius:7px;background:#481810f5;color:#fff5dc;text-align:center;box-shadow:0 0 25px #b62b1c;font:700 24px Arial,sans-serif;max-width:85vw';
    box.style.left=(rect.left+rect.width/2)+'px';box.style.top=(rect.top+rect.height*.14)+'px';
    const title=document.createElement('strong');title.textContent='⚠ MIMIC · FAKE CHEST';
    const note=document.createElement('div');note.textContent='A monster in disguise. Check Fight or Leave before interacting.';note.style.cssText='font:14px Arial,sans-serif;margin-top:6px';
    box.append(title,note);document.body.appendChild(box);
  })()`;
}

function observeMimics(contents, notify, onPacket = null) {
  const pending = new Map();
  const requests = new Map();
  const sent = new Map();
  let client = null, stopped = false, epoch = 0, revision = 0, warning = {kind: 'clear'};
  const draw = (next, announce = true) => {
    if (stopped || contents.isDestroyed() || next.kind === 'ignore') return;
    warning = next;
    contents.executeJavaScriptInIsolatedWorld(142, [{code: overlayScript(next)}]).catch(() => {});
    if (announce) notify(next);
  };
  const official = value => {
    try { const url = new URL(value); return url.protocol === 'https:' && /^(?:[a-z0-9-]+\.)?sfgame\.(?:net|eu)$/.test(url.hostname) ? url : null; }
    catch { return null; }
  };
  const message = async (_event, method, parameters) => {
    if (stopped) return;
    if (method === 'Network.requestWillBeSent') {
      const url = official(parameters.request.url);
      if (url?.pathname === '/cmd.php') {
        revision++;
        if (requests.size >= 32) requests.delete(requests.keys().next().value);
        requests.set(parameters.requestId, revision);
        if (onPacket) {
          if (sent.size >= 64) sent.delete(sent.keys().next().value);
          sent.set(parameters.requestId, {url: parameters.request.url, headers: parameters.request.headers});
        }
        // A new game action can leave this encounter. Never retain its warning
        // while awaiting a response that might contain no dungeon state at all.
        draw({kind: 'clear'});
      }
    } else if (method === 'Network.responseReceived') {
      const url = official(parameters.response.url);
      if (url && ['/js/build.json', '/cmd.php'].includes(url.pathname) && pending.size < 32) {
        pending.set(parameters.requestId, {kind: url.pathname, epoch,
          revision: requests.get(parameters.requestId) ?? revision});
      }
    } else if (method === 'Network.loadingFailed') {
      pending.delete(parameters.requestId);
      requests.delete(parameters.requestId);
      sent.delete(parameters.requestId);
    } else if (method === 'Network.loadingFinished' && pending.has(parameters.requestId)) {
      const entry = pending.get(parameters.requestId); pending.delete(parameters.requestId);
      requests.delete(parameters.requestId);
      const current = () => !stopped && entry.epoch === epoch &&
        (entry.kind !== '/cmd.php' || entry.revision === revision);
      const request = sent.get(parameters.requestId); sent.delete(parameters.requestId);
      try {
        const response = await contents.debugger.sendCommand('Network.getResponseBody', {requestId: parameters.requestId});
        const body = response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body;
        // Record every dungeon response, including ones a newer action superseded.
        if (onPacket && request && entry.kind === '/cmd.php') onPacket({...request, body, client});
        if (!current()) return;
        if (body.length > 4 * 1024 * 1024) { draw({kind: 'clear'}); return; }
        if (entry.kind === '/js/build.json') {
          client = JSON.parse(body).productVersion;
          if (client !== CLIENT) draw({kind: 'clear'});
        } else draw(inspectPacket(body, client));
      } catch { if (current()) draw({kind: 'clear'}); }
    }
  };
  try {
    contents.debugger.attach('1.3');
    contents.debugger.on('message', message);
    contents.debugger.sendCommand('Network.enable', {maxTotalBufferSize: 8 * 1024 * 1024, maxResourceBufferSize: 4 * 1024 * 1024, maxPostDataSize: 0}).catch(() => draw({kind: 'clear'}));
    contents.on('did-start-navigation', (_event, _url, _inPlace, main) => {
      if (main) { epoch++; client = null; pending.clear(); requests.clear(); draw({kind: 'clear'}); }
    });
    contents.on('destroyed', () => { stopped = true; pending.clear(); requests.clear(); });
    contents.debugger.on('detach', () => { epoch++; if (!stopped) draw({kind: 'clear'}); });
  } catch { notify({kind: 'unavailable'}); }
  return {redraw: () => { if (warning.kind === 'mimic') draw(warning, false); }};
}
module.exports = {CLIENT, inspectPacket, overlayScript, observeMimics};
