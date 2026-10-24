'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const {readPacket, merge, createLiveAdviser, adviceScript} = require('../live-adviser.cjs');

const ROOT = path.resolve(__dirname, '..', '..');

// Mirrors tests/test_study_automatic.py packet(): a reviewed 50-word save.
function body({status = 1, room = 0, types = [1, 1001, 99999], payloads = [0, 0, 87654321]} = {}) {
  const save = new Array(50).fill(0);
  save[0] = 123456789; save[2] = save[3] = save[4] = 10000; save[15] = status;
  save[17] = room; save[18] = 100; types.forEach((t, i) => { save[19 + i] = t; }); payloads.forEach((p, i) => { save[22 + i] = p; });
  const now = Math.floor(Date.now() / 1000);
  return [`iadungeonsave:${save.join('/')}`, `iadungeonsoulstones:${new Array(36).fill(0).join('/')}`,
    'resources:500000/100', 'serverversion:31500', `timestamp:${now}`,
    `iadungeontime:8/${now - 100}/${now + 604700}/${now + 604800}`, 'iadungeon20cost:10', 'sessionid:secret'].join('&');
}
const url = (req, params) => `https://s1.sfgame.net/cmd.php?req=${req}&params=${Buffer.from(params).toString('base64')}`;

test('packets keep only numeric dungeon and context fields; partial saves are replaced', () => {
  const read = readPacket({url: url('IADungeonInteract', '2'), headers: {'PG-Player': '7'}, body: body()}, 'acct');
  assert.equal(read.character, 'acct@s1.sfgame.net#7');
  assert.equal(read.params, '2');
  assert.ok(read.values.iadungeonsave && !('sessionid' in read.values));
  const first = merge(null, {'iadungeonsave(3)': '1/2', resources: '1/2'});
  assert.ok(first.dungeon);
  const second = merge(first.fields, {iadungeonsave: '5'});
  assert.deepEqual(Object.keys(second.fields).sort(), ['iadungeonsave', 'resources']);
  assert.equal(merge(null, {timestamp: '1'}), null);
  assert.equal(readPacket({url: 'https://evil.example/cmd.php', body: body()}, 'acct'), null);
});

test('the overlay script only carries fixed strings and the label as text', () => {
  const script = adviceScript({decision: 'command', label: 'Right door (mystery)</div><script>'});
  assert.match(script, /textContent=/);
  assert.doesNotMatch(script, /innerHTML/);
  assert.match(script, /"Right"/);
});

test('live adviser end to end with the local host', {timeout: 120000}, async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'sfld-live-'));
  const python = process.env.SFLD_TEST_PYTHON || 'python';
  const host = spawn(python, ['-u', path.join(ROOT, 'tools/desktop_host.py')], {
    cwd: ROOT, env: {...process.env, SFLD_ADVISER_DIR: directory}, stdio: ['pipe', 'pipe', 'pipe']});
  try {
    const ready = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({input: host.stdout});
      lines.on('line', line => { try { const r = JSON.parse(line); if (r.ready) resolve(r); } catch { /* diagnostics */ } });
      host.on('exit', code => reject(new Error('host exited ' + code)));
    });
    const advice = [];
    const live = createLiveAdviser({origin: ready.live, token: ready.token, onAdvice: a => advice.push(a)});
    // Joined mid-run at room 10: a monster door and a mystery door.
    await live.handle({url: url('IADungeonView', ''), headers: {}, body: body({room: 9})}, 'acct');
    assert.equal(advice.at(-1).decision, 'command', JSON.stringify(advice.at(-1)));
    assert.match(advice.at(-1).label, /^(Left door \(monster\)|Right door \(mystery\))$/);
    // The client sends the advised door; its response shows a barrel.
    const door = advice.at(-1);
    await live.handle({url: url(door.req, door.params), headers: {}, body: body({status: 10, room: 10, types: [100, 0, 0], payloads: [400, 0, 0]})}, 'acct');
    assert.match(advice.at(-1).label, /^(Open it|Leave it)$/);
    const log = fs.readFileSync(path.join(directory, 'decisions.jsonl'), 'utf8');
    assert.match(log, /"outcome":"advised"/);
    // A wrong token is refused by the host.
    const refused = createLiveAdviser({origin: ready.live, token: 'wrong', onAdvice: a => advice.push(a)});
    await refused.handle({url: url('IADungeonView', ''), headers: {}, body: body({room: 9})}, 'other');
    assert.equal(advice.at(-1).decision, 'error');
  } finally {
    host.stdin.end('stop\n');
    await new Promise(resolve => host.on('exit', resolve));
  }
});
