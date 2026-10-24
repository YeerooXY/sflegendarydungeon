'use strict';
// Live adviser for the original game. Each dungeon response from the embedded
// client updates that character's raw dungeon fields (as MFBot's bridge keeps
// them); the local adviser (tools/desktop_host.py, live server) answers with
// the recommended action, drawn over the game. Every dungeon command the client
// sends is reported first, so the adviser's history follows what was done.
const {parseBody, decodeParams} = require('./ld-recorder.cjs');

const PREFIXES = ['iadungeon', 'iamerchant', 'iapendingitems', 'ialootitem'];
const CONTEXT = ['resources', 'timestamp', 'serverversion', 'characterstatus', 'ownplayersavecharacter'];
const NUMERIC = /^-?\d+(?:\/-?\d+)*$/;
const isDungeon = key => PREFIXES.some(prefix => key.toLowerCase().startsWith(prefix));
const isContext = key => CONTEXT.includes(key.toLowerCase());

function official(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.pathname === '/cmd.php' && /^(?:[a-z0-9-]+\.)?sfgame\.(?:net|eu)$/.test(url.hostname) ? url : null;
  } catch { return null; }
}

// One network packet -> {character, req, params, values} or null.
function readPacket({url, headers, body}, account) {
  const parsed = official(url);
  if (!parsed || typeof body !== 'string' || body.length > 4 * 1024 * 1024) return null;
  const header = Object.entries(headers || {}).find(([k]) => k.toLowerCase() === 'pg-player');
  const character = `${account}@${parsed.hostname}#${header ? header[1] : ''}`;
  const values = {};
  for (const [key, value] of Object.entries(parseBody(body))) {
    if ((isDungeon(key) || isContext(key)) && typeof value === 'string' && value.length <= 100000 && NUMERIC.test(value)) values[key] = value;
  }
  return {character, req: parsed.searchParams.get('req') || '', params: decodeParams(parsed.searchParams.get('params')) || '', values};
}

// Same merge as arena-policy/LegendaryBridge.cs Capture().
function merge(fields, values) {
  const dungeon = Object.keys(values).some(isDungeon);
  if (!dungeon && !fields) return null;
  const next = {...(fields || {})};
  if (dungeon) for (const key of Object.keys(next)) if (key.includes('(')) delete next[key];
  for (const [key, value] of Object.entries(values)) if ((dungeon && isDungeon(key)) || isContext(key)) next[key] = value;
  return {fields: next, dungeon};
}

function createLiveAdviser({origin, token, fetch = globalThis.fetch, onAdvice}) {
  const characters = new Map();
  let queue = Promise.resolve();
  const post = async (route, body) => {
    const response = await fetch(origin + route, {method: 'POST', body: JSON.stringify(body),
      headers: {'Content-Type': 'application/json', Authorization: 'Bearer ' + token}});
    if (!response.ok) throw new Error('adviser ' + response.status);
    return response.json();
  };
  function handle(packet, account) {
    const read = readPacket(packet, account);
    if (!read) return queue;
    // Keep each packet's /sent before its /decide, in arrival order.
    queue = queue.then(async () => {
      const merged = merge(characters.get(read.character), read.values);
      if (!merged) return;
      characters.set(read.character, merged.fields);
      if (/iadungeon/i.test(read.req)) await post('/sent', {character: read.character, req: read.req, params: read.params});
      if (!merged.dungeon) return;
      const advice = await post('/decide', {character: read.character, level: null, fields: merged.fields});
      onAdvice({...advice, character: read.character});
    }).catch(error => onAdvice({decision: 'error', label: 'Adviser unavailable', reason: String(error.message || error)}));
    return queue;
  }
  return {handle, characters};
}

function adviceScript(advice) {
  // Only our own label text (set via textContent) and a fixed side reach the page.
  const show = advice && ['command', 'wait', 'native', 'pause', 'error'].includes(advice.decision);
  const label = show ? String(advice.label || '').slice(0, 120) : '';
  const side = advice?.decision === 'command' && /^(Left|Right) door/.exec(label)?.[1] || '';
  const muted = !advice || advice.decision !== 'command';
  return `(()=>{
    for(const id of ['sfld-adviser','sfld-adviser-arrow']){const old=document.getElementById(id);if(old)old.remove();}
    if(!${JSON.stringify(show)})return;
    const canvas=document.querySelector('#canvas,canvas');if(!canvas)return;
    const rect=canvas.getBoundingClientRect();if(rect.width<100||rect.height<100)return;
    const box=document.createElement('div');box.id='sfld-adviser';box.setAttribute('role','status');
    box.style.cssText='position:fixed;z-index:2147482990;pointer-events:none;transform:translateX(-50%);padding:8px 18px;border-radius:7px;font:700 20px Arial,sans-serif;max-width:80vw;text-align:center;box-shadow:0 2px 14px #0008;'+(${muted}?'background:#22262cee;color:#d9dde3;border:2px solid #59606b':'background:#0f3d24f2;color:#eafff1;border:2px solid #5fdc8f');
    box.style.left=(rect.left+rect.width/2)+'px';box.style.top=(rect.top+rect.height*.03)+'px';
    const small=document.createElement('div');small.textContent='Adviser';small.style.cssText='font:600 11px Arial,sans-serif;letter-spacing:.08em;text-transform:uppercase;opacity:.8';
    const text=document.createElement('div');text.textContent=${JSON.stringify(label)};
    box.append(small,text);document.body.appendChild(box);
    const side=${JSON.stringify(side)};if(!side)return;
    const arrow=document.createElement('div');arrow.id='sfld-adviser-arrow';arrow.textContent='⬇';
    arrow.style.cssText='position:fixed;z-index:2147482990;pointer-events:none;transform:translateX(-50%);font:700 64px Arial,sans-serif;color:#5fdc8f;text-shadow:0 0 12px #000';
    arrow.style.left=(rect.left+rect.width*(side==='Left'?.3:.7))+'px';arrow.style.top=(rect.top+rect.height*.12)+'px';
    document.body.appendChild(arrow);
  })()`;
}

module.exports = {readPacket, merge, createLiveAdviser, adviceScript};
