const {test} = require('node:test');
const assert = require('node:assert/strict');
const {inspectPacket, observeMimics, CLIENT} = require('../live-mimic.cjs');
function packet(phase=10, step=0) {
  const words=Array(50).fill(0); words[15]=phase;words[17]=12;words[19+step]=100;words[22+step]=500;
  return 'iadungeonsave:'+words.join('/');
}
test('marks each revealed mimic step, but never future or hidden choices', () => {
  for (let step=0;step<3;step++) assert.deepEqual(inspectPacket(packet(10+step,step),CLIENT),{kind:'mimic',room:12});
  for (const phase of [0,1,100,101,102]) assert.equal(inspectPacket(packet(phase),CLIENT).kind,'clear');
  assert.equal(inspectPacket(packet(10,1),CLIENT).kind,'clear');
  assert.equal(inspectPacket(packet(11,2),CLIENT).kind,'clear');
});
test('unknown versions, partial saves, duplicates and ordinary chests clear the warning', () => {
  const body=packet();
  assert.equal(inspectPacket(body,'future-client').kind,'clear');
  assert.equal(inspectPacket(body+'&iadungeonsave(0):0/1',CLIENT).kind,'clear');
  assert.equal(inspectPacket(body+'&'+body,CLIENT).kind,'clear');
  assert.equal(inspectPacket(body.replace('/500/','/100/'),CLIENT).kind,'clear');
  assert.equal(inspectPacket('iadungeonsave:1/2/3',CLIENT).kind,'clear');
  assert.equal(inspectPacket('resources:100/20',CLIENT).kind,'ignore');
});

test('clears on a new action and ignores late responses from the previous page', async () => {
  const {EventEmitter} = require('node:events');
  const contents = new EventEmitter(), debug = new EventEmitter(), bodies = new Map(), seen = [];
  contents.debugger = debug;
  contents.isDestroyed = () => false;
  contents.executeJavaScriptInIsolatedWorld = async () => {};
  debug.attach = () => {};
  debug.sendCommand = async (command, args) => command === 'Network.getResponseBody'
    ? {body: await bodies.get(args.requestId)} : {};
  observeMimics(contents, value => seen.push(value));
  const emit = (method, data) => debug.emit('message', {}, 'Network.' + method, data);
  const receive = async (id, resource, body) => {
    bodies.set(id, body);
    emit('requestWillBeSent', {requestId: id, request: {url: 'https://sfgame.net' + resource}});
    emit('responseReceived', {requestId: id, response: {url: 'https://sfgame.net' + resource}});
    emit('loadingFinished', {requestId: id});
    await new Promise(resolve => setImmediate(resolve));
  };
  await receive('build', '/js/build.json', JSON.stringify({productVersion: CLIENT}));
  await receive('mimic', '/cmd.php', packet());
  assert.equal(seen.at(-1).kind, 'mimic');
  emit('requestWillBeSent', {requestId: 'leave', request: {url: 'https://sfgame.net/cmd.php'}});
  assert.equal(seen.at(-1).kind, 'clear');
  let resolveLate;
  await receive('late', '/cmd.php', new Promise(resolve => { resolveLate = resolve; }));
  contents.emit('did-start-navigation', {}, 'https://sfgame.net', false, true);
  const count = seen.length;
  resolveLate(packet());
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(seen.length, count);
  assert.equal(seen.at(-1).kind, 'clear');
});

test('passes every cmd.php response to the recorder, including superseded ones', async () => {
  const {EventEmitter} = require('node:events');
  const contents = new EventEmitter(), debug = new EventEmitter(), bodies = new Map(), packets = [];
  contents.debugger = debug; contents.isDestroyed = () => false;
  contents.executeJavaScriptInIsolatedWorld = async () => {};
  debug.attach = () => {};
  debug.sendCommand = async (command, args) => command === 'Network.getResponseBody' ? {body: await bodies.get(args.requestId)} : {};
  observeMimics(contents, () => {}, p => packets.push(p));
  const emit = (method, data) => debug.emit('message', {}, 'Network.' + method, data);
  const headers = {'PG-Player': '7'};
  const send = (id, resource) => emit('requestWillBeSent', {requestId: id, request: {url: 'https://sfgame.net' + resource, headers}});
  const finish = async (id, resource, body) => {
    bodies.set(id, body);
    emit('responseReceived', {requestId: id, response: {url: 'https://sfgame.net' + resource}});
    emit('loadingFinished', {requestId: id});
    await new Promise(resolve => setImmediate(resolve));
  };
  send('a', '/cmd.php?req=IADungeonInteract'); send('b', '/cmd.php?req=IADungeonInteract');
  await finish('a', '/cmd.php?req=IADungeonInteract', packet());   // superseded by b for the overlay
  await finish('b', '/cmd.php?req=IADungeonInteract', packet(1));
  send('c', '/js/build.json'); await finish('c', '/js/build.json', JSON.stringify({productVersion: CLIENT}));
  assert.equal(packets.length, 2);
  assert.deepEqual(packets.map(p => p.headers['PG-Player']), ['7', '7']);
  assert.ok(packets[0].body.startsWith('iadungeonsave:'));
});
