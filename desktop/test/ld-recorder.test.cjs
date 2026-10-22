'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {fromNetwork, createRecorder} = require('../ld-recorder.cjs');

const save = Array(50).fill(0).join('/');
const url = (req, params = '') => `https://s7.sfgame.net/cmd.php?req=${req}&params=${Buffer.from(params).toString('base64')}&sid=abc`;
const headers = {'PG-Player': '424242', 'PG-Session': 'secret-session'};

test('dungeon command keeps every numeric field except denied ones', () => {
  const body = `iadungeonsave:${save}&sessionid:123456&cryptoid:777&ownplayername:Hero&newroomfield:5/6/7&text:hello&resources:1/99/3`;
  const r = fromNetwork({url: url('IADungeonInteract', '40'), headers, body, client: '31.500.260929.1'});
  assert.equal(r.req, 'IADungeonInteract'); assert.equal(r.params, '40'); assert.equal(r.player, '424242');
  assert.equal(r.host, 's7.sfgame.net'); assert.equal(r.source, 'desktop');
  assert.deepEqual(Object.keys(r.fields).sort(), ['iadungeonsave', 'newroomfield', 'resources']);
  assert.ok(!JSON.stringify(r).includes('secret-session'));
});

test('other responses keep only dungeon records plus context', () => {
  const body = `ownplayersavecharacter:1/2/3&iadungeontime:8/1/2/3&timestamp:1790000000&sessionid:99&resources:1/2`;
  const r = fromNetwork({url: url('Poll'), headers, body, client: null});
  assert.deepEqual(Object.keys(r.fields).sort(), ['iadungeontime', 'resources', 'timestamp']);
  assert.equal(r.params, null);
  assert.equal(fromNetwork({url: url('Poll'), headers, body: 'ownplayersavecharacter:1/2&timestamp:5'}), null);
});

test('unofficial hosts and other paths are ignored', () => {
  assert.equal(fromNetwork({url: 'https://evil.example/cmd.php?req=IADungeonInteract', headers, body: `iadungeonsave:${save}`}), null);
  assert.equal(fromNetwork({url: 'https://s7.sfgame.net/other.php?req=IADungeonInteract', headers, body: `iadungeonsave:${save}`}), null);
});

test('recorder appends daily JSONL without throwing on bad input', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ldrec-'));
  const recorder = createRecorder(dir);
  recorder.record({url: url('IADungeonInteract', '1'), headers, body: `iadungeonsave:${save}`, client: 'x'});
  recorder.record({url: 'not a url', headers, body: ''});
  const files = fs.readdirSync(dir);
  assert.equal(files.length, 1);
  const lines = fs.readFileSync(path.join(dir, files[0]), 'utf8').trim().split('\n');
  assert.equal(lines.length, 1); assert.equal(JSON.parse(lines[0]).params, '1');
  assert.equal(recorder.failures(), 0);
});
