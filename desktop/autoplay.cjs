'use strict';
// Autoplay for the original game: clicks the control the adviser recommends.
//
// Controls are learned, never guessed: when the player clicks and the game then
// sends a command, that click position is stored (as a fraction of the game
// view) under the adviser's control name (door:1, stone:0, encounter:20, ...).
// Every autoplay click must produce exactly the advised command within a few
// seconds; anything else stops autoplay. A click by the player pauses it.
const fs = require('node:fs');
const path = require('node:path');

const TEACH_WINDOW_MS = 2500;   // a player click this recent explains the next command
const RESPONSE_MS = 5000;       // an autoplay click must produce its command by then
const OWN_CLICK_MS = 400;       // input events this soon after our click are our own

function createAutoplay({file, click, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout,
                         random = Math.random, onState = () => {}}) {
  let controls = {};
  try { controls = JSON.parse(fs.readFileSync(file, 'utf8')).controls || {}; } catch { controls = {}; }
  let enabled = false, status = 'Autoplay is off.', lastUserClick = null, ownClickAt = -Infinity;
  let awaiting = null, scheduled = null, timeout = null;

  const save = () => {
    fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file + '.tmp', JSON.stringify({schema: 1, controls}, null, 1));
    fs.renameSync(file + '.tmp', file);
  };
  const cancel = () => {
    if (scheduled) clearTimer(scheduled);
    if (timeout) clearTimer(timeout);
    scheduled = timeout = null; awaiting = null;
  };
  const report = text => { status = text; onState(state()); };
  const stop = reason => { enabled = false; cancel(); report(reason); };
  const state = () => ({enabled, status, learned: Object.keys(controls).length});

  function setEnabled(on) {
    if (on && !enabled) { enabled = true; report('Autoplay is on. Click anywhere in the game to pause it.'); }
    else if (!on && enabled) stop('Autoplay is off.');
    return state();
  }

  // A mouse press in the game view, as fractions of the view size.
  function userClick(x, y) {
    const t = now();
    if (t - ownClickAt < OWN_CLICK_MS) return;
    lastUserClick = {x, y, t};
    if (enabled && !status.startsWith('Autoplay needs')) stop('Autoplay paused: you clicked in the game.');
  }

  // The adviser's reading of a command the client sent: {outcome, target}.
  function sent(result) {
    const t = now();
    if (!result || result.outcome === 'not an action') return;
    if (awaiting) {
      const ok = result.outcome === 'advised' && result.target === awaiting.target;
      cancel();
      if (!ok) { stop('Autoplay stopped: the game did something other than the advised action.'); return; }
      return;
    }
    if (result.target && lastUserClick && t - lastUserClick.t <= TEACH_WINDOW_MS) {
      const old = controls[result.target];
      const n = old ? Math.min(old.n, 9) + 1 : 1;
      controls[result.target] = old
        ? {x: old.x + (lastUserClick.x - old.x) / n, y: old.y + (lastUserClick.y - old.y) / n, n}
        : {x: lastUserClick.x, y: lastUserClick.y, n};
      lastUserClick = null;
      try { save(); } catch { /* learning is retried on the next click */ }
      if (enabled && status.startsWith('Autoplay needs')) report('Learned. Autoplay continues.');
      onState(state());
    }
  }

  // The adviser's recommendation for the current screen.
  function advice(advice) {
    if (!enabled) return;
    cancel();
    if (!advice || advice.decision !== 'command') {
      const label = advice && advice.label ? advice.label : 'No advice for this screen';
      stop(advice && advice.decision === 'wait' ? `Autoplay paused: ${label}.` : `Autoplay paused: ${label}. Continue by hand.`);
      return;
    }
    const control = controls[advice.target];
    if (!control) { report(`Autoplay needs to learn “${advice.label}”: click it once yourself.`); return; }
    report(`Autoplay: ${advice.label}`);
    const delay = 1100 + Math.floor(random() * 900);
    scheduled = setTimer(() => {
      scheduled = null;
      if (!enabled) return;
      awaiting = {target: advice.target};
      ownClickAt = now();
      click(control.x, control.y);
      timeout = setTimer(() => { timeout = null; if (awaiting) stop('Autoplay stopped: the game did not respond to the click.'); }, RESPONSE_MS);
    }, delay);
  }

  return {setEnabled, userClick, sent, advice, state, stop, controls: () => ({...controls})};
}

module.exports = {createAutoplay, TEACH_WINDOW_MS, RESPONSE_MS};
