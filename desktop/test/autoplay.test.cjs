'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {createAutoplay, RESPONSE_MS} = require('../autoplay.cjs');

function harness() {
  let t = 1000;
  const timers = [];
  const clicks = [];
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'sfld-auto-')), 'controls.json');
  const auto = createAutoplay({file, now: () => t, random: () => 0, click: (x, y) => clicks.push([x, y]),
    setTimer: (fn, ms) => { const h = {fn, at: t + ms}; timers.push(h); return h; },
    clearTimer: h => { const i = timers.indexOf(h); if (i >= 0) timers.splice(i, 1); }});
  const advance = ms => {
    t += ms;
    for (;;) {
      const due = timers.filter(h => h.at <= t).sort((a, b) => a.at - b.at)[0];
      if (!due) break;
      timers.splice(timers.indexOf(due), 1); due.fn();
    }
  };
  return {auto, clicks, advance, file, tick: ms => { t += ms; }};
}
const door = {decision: 'command', target: 'door:1', label: 'Right door (mystery)'};

test('a player click followed by its command teaches that control', () => {
  const h = harness();
  h.auto.userClick(.7, .4); h.tick(300);
  h.auto.sent({outcome: 'own_choice', target: 'door:1'});
  assert.deepEqual(h.auto.controls()['door:1'], {x: .7, y: .4, n: 1});
  assert.ok(JSON.parse(fs.readFileSync(h.file, 'utf8')).controls['door:1']);
  // A click long before the command teaches nothing.
  h.auto.userClick(.2, .2); h.tick(5000);
  h.auto.sent({outcome: 'advised', target: 'door:0'});
  assert.equal(h.auto.controls()['door:0'], undefined);
});

test('autoplay clicks the learned control and checks the command', () => {
  const h = harness();
  h.auto.userClick(.7, .4); h.auto.sent({outcome: 'advised', target: 'door:1'});
  h.auto.setEnabled(true);
  h.auto.advice(door);
  assert.equal(h.clicks.length, 0);
  h.advance(1200);
  assert.deepEqual(h.clicks, [[.7, .4]]);
  h.auto.userClick(.7, .4);               // our own click echo is ignored
  h.auto.sent({outcome: 'advised', target: 'door:1'});
  h.advance(RESPONSE_MS + 10);
  assert.equal(h.auto.state().enabled, true);
});

test('a wrong command, no response or a player click stops autoplay', () => {
  const h = harness();
  h.auto.userClick(.7, .4); h.auto.sent({outcome: 'advised', target: 'door:1'});
  h.auto.setEnabled(true); h.auto.advice(door); h.advance(1200);
  h.auto.sent({outcome: 'own_choice', target: 'door:0'});
  assert.equal(h.auto.state().enabled, false);
  assert.match(h.auto.state().status, /something other/);

  h.auto.setEnabled(true); h.auto.advice(door); h.advance(1200); h.advance(RESPONSE_MS + 1);
  assert.match(h.auto.state().status, /did not respond/);

  h.auto.setEnabled(true); h.tick(1000); h.auto.userClick(.1, .9);
  assert.match(h.auto.state().status, /you clicked/);
});

test('unknown controls wait for one teaching click; waits and unknown screens pause', () => {
  const h = harness();
  h.auto.setEnabled(true);
  h.auto.advice({decision: 'command', target: 'encounter:20', label: 'Fight'});
  assert.match(h.auto.state().status, /needs to learn “Fight”/);
  h.auto.userClick(.5, .8);
  assert.equal(h.auto.state().enabled, true);
  h.auto.sent({outcome: 'advised', target: 'encounter:20'});
  assert.match(h.auto.state().status, /Learned/);
  h.auto.advice({decision: 'wait', label: 'Wait 2 h 00 min for HP to regenerate'});
  assert.equal(h.auto.state().enabled, false);
  assert.match(h.auto.state().status, /Wait 2 h/);
  h.auto.setEnabled(true); h.auto.advice({decision: 'pause', label: 'No advice for this screen'});
  assert.match(h.auto.state().status, /Continue by hand/);
  h.auto.setEnabled(true); h.auto.sent({outcome: 'not an action'});
  assert.equal(h.auto.state().enabled, true);
});
