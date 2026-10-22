// Injected only into the local practice page by watch.cjs, after it is ready.
// Use the same act()/suggestion() path as manual practice, including animations.
(async () => {
  const options = new URLSearchParams(location.search);
  const lane = Number(options.get('lane'));
  const initialSeed = Number(options.get('seed'));
  const budget = Number(options.get('budget'));
  let running = false, inFlight = false, nextTimer = null, eventNumber = 1;
  let finishedCompletions = 0, restarting = false, fault = '', message = '';
  const base = {...config, budget, seed:initialSeed, run_number:1,
    deadline_hours:168, initial_state:'', learned_policy:'', neural_adviser:true};
  const bar = document.createElement('header');
  bar.className = 'watch-bar';
  bar.innerHTML = `<div class="watch-identity"><strong>Agent ${lane} <span>SIMULATION</span></strong><small id="watch-progress"></small></div>
    <button id="watch-toggle">Pause · P</button><button id="watch-step">Step</button>
    <button id="watch-all" title="Pause or resume all four windows">All · F9</button>
    <button id="watch-flag" title="Pause all and save this position, its decisions and a screenshot">Flag · F8</button>
    <label>Pace <select id="watch-speed"><option value="450">0.45s</option><option value="900">0.9s</option><option value="2400">2.4s</option></select></label>
    <div id="watch-note" role="status"></div>`;
  document.body.prepend(bar);
  document.body.classList.add('watch-mode', 'game-view', 'desktop-view');
  $('watch-speed').value = options.get('pace');
  $('speed').value = options.get('pace');
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function update() {
    const o = obs();
    $('watch-toggle').textContent = running ? 'Pause · P' : (busy || inFlight) ? 'Pausing…' : 'Play · P';
    $('watch-step').disabled = running || busy || inFlight || restarting || !o || o.terminal;
    $('watch-progress').textContent = `Event ${eventNumber} · ${budget.toLocaleString()} mushrooms/week · ${finishedCompletions + (o?.completed_runs || 0)} clears total`;
    const chosen = pendingAction || suggestion();
    const latest = payload?.journal.at(-1);
    const last = latest ? `Last: ${journalAction(latest)} · ${resultLines(latest).join(' · ')}` : '';
    $('watch-note').textContent = fault || message || (running ? (chosen ? `Next: ${actionText(chosen)}` : 'Starting the next event…') : 'Paused. Step through a decision, or flag this position.') + (last ? ` | ${last}` : '');
    $('watch-note').title = $('watch-note').textContent;
    bar.dataset.paused = String(!running);
  }
  function pause() {
    running = false; clearTimeout(nextTimer); stop(); update();
  }
  function fail(error) {
    fault = error.message || String(error); pause(); showError(error);
  }
  function schedule(delay=150) {
    clearTimeout(nextTimer);
    if (running) nextTimer = setTimeout(tick, delay);
  }
  async function tick() {
    if (!running) return;
    if (busy || inFlight || restarting) { schedule(); return; }
    if (obs().terminal) {
      if (obs().action_limit) { fail(new Error('The simulator hit its action limit. Flag this event for review.')); return; }
      restarting = true;
      try {
        // Archive before replacing the native session. Pause also cancels the restart.
        await window.watchDesktop.archive();
        await sleep(1500);
        if (!running) return;
        const clears = obs().completed_runs;
        const nextEvent = eventNumber + 1;
        await replaceSession({...base, seed:initialSeed + (nextEvent - 1) * 4});
        eventNumber = nextEvent; finishedCompletions += clears;
      } catch (error) { fail(error); }
      finally { restarting = false; update(); schedule(); }
      return;
    }
    inFlight = true;
    try {
      if (!obs().neural_adviser) throw new Error('The trained adviser is unavailable. Autoplay is paused.');
      const action = suggestion();
      if (!action) throw new Error('No recommended action for this position.');
      await act(action, false, `Agent ${lane}`);
      if (!$('error').hidden) throw new Error($('error').textContent);
    } catch (error) { fail(error); }
    finally { inFlight = false; update(); schedule(); }
  }
  function resume() {
    if (fault) return;
    message = ''; running = true; update(); schedule(0);
  }
  async function step() {
    pause();
    if (busy || inFlight || restarting || !suggestion()) return;
    inFlight = true;
    try { await act(suggestion(), true, `Agent ${lane}`); }
    finally { inFlight = false; update(); }
  }
  function snapshot() {
    const o = obs();
    return {lane, event:eventNumber, seed:config.seed, session, running, busy:busy || inFlight || restarting,
      steps:payload?.steps, room:o?.room, run:o?.run_number, phase:o?.phase, terminal:o?.terminal,
      completions:o?.completed_runs, total_completions:finishedCompletions + (o?.completed_runs || 0),
      mushrooms_spent:o?.mushrooms_spent, budget:config.budget, deadline_hours:config.deadline_hours, elapsed_hours:o?.elapsed_hours,
      checkpoint:o?.neural_adviser?.checkpoint_sha256, error:fault || (!$('error').hidden ? $('error').textContent : '')};
  }
  window.dungeonWatch = {
    pause, resume, step, snapshot,
    idle:async () => { while (busy || inFlight || restarting) await sleep(40); },
    recording:() => api('export', {session}),
    close:() => api('close', {session}),
    notice:text => { message = text; update(); },
  };
  $('watch-toggle').onclick = () => running ? pause() : resume();
  $('watch-step').onclick = step;
  $('watch-all').onclick = () => window.watchDesktop.pauseAll().catch(fail);
  $('watch-flag').onclick = () => window.watchDesktop.flag().catch(fail);
  $('watch-speed').onchange = () => { $('speed').value = $('watch-speed').value; };
  // Any attempt to take over manually pauses this lane before the normal handler.
  $('scene').addEventListener('pointerdown', pause, true);
  document.addEventListener('keydown', event => {
    if (event.target.matches('input,textarea,select') || event.ctrlKey || event.altKey || event.metaKey) return;
    if (event.key.toLowerCase() === 'p') { event.preventDefault(); running ? pause() : resume(); }
    else if (['ArrowLeft','ArrowRight','Enter',' '].includes(event.key)) pause();
  }, true);
  await replaceSession(base);
  update();
  setInterval(update, 200);
  if (options.get('autostart') !== '0') resume();
})();
