'use strict';
const $ = id => document.getElementById(id);
function render(state) {
  document.body.classList.toggle('fullscreen', state.fullscreen);
  $('notice').textContent = state.notice;
  $('notice').parentElement.hidden = !state.notice;
  $('game').setAttribute('aria-pressed', String(state.mode === 'game'));
  $('practice').setAttribute('aria-pressed', String(state.mode === 'practice'));
  $('mode-label').textContent = state.mode === 'practice' ? 'PRACTICE · LOCAL SIMULATION' : 'ORIGINAL GAME · LIVE ADVISER';
  $('fullscreen').textContent = state.fullscreen ? 'Exit fullscreen' : 'Fullscreen';
  const auto = state.autoplay || {enabled: false};
  $('autoplay').setAttribute('aria-pressed', String(auto.enabled));
  $('autoplay').textContent = auto.enabled ? 'Autoplay on · F10 stops' : 'Autoplay';
  $('autoplay').hidden = state.mode !== 'game';
  $('profile-list').replaceChildren(...state.accounts.map(profile => {
    const row = document.createElement('button'); row.className = 'profile';
    row.setAttribute('aria-pressed', String(profile.id === state.selected));
    const title = document.createElement('strong'); title.textContent = profile.label;
    const world = document.createElement('small'); world.textContent = new URL(profile.origin).hostname;
    row.append(title, world); row.onclick = () => perform(async () => { await closeProfiles(); return dungeonApp.select(profile.id); }); return row;
  }));
}
async function perform(fn) { try { $('profile-error').textContent = ''; const state = await fn(); if (state) render(state); } catch (error) { const text = error.message.replace(/^Error invoking remote method '[^']+': Error: /, ''); $('notice').textContent = text; $('profile-error').textContent = text; } }
async function closeProfiles() { $('profile-dialog').close(); await dungeonApp.modal(false); $('accounts').focus(); }
$('game').onclick = () => perform(() => dungeonApp.mode('game'));
$('practice').onclick = () => perform(() => dungeonApp.mode('practice'));
$('fullscreen').onclick = () => perform(() => dungeonApp.fullscreen());
$('autoplay').onclick = () => perform(() => dungeonApp.autoplay($('autoplay').getAttribute('aria-pressed') !== 'true'));
$('accounts').onclick = () => perform(async () => { await dungeonApp.modal(true); $('profile-dialog').showModal(); $('close-profiles').focus(); });
$('close-profiles').onclick = () => perform(closeProfiles);
$('profile-dialog').addEventListener('cancel', event => { event.preventDefault(); perform(closeProfiles); });
$('profile-form').onsubmit = event => { event.preventDefault(); perform(async () => { const state = await dungeonApp.add({label: $('profile-label').value, world: $('profile-world').value}); $('profile-label').value = ''; return state; }); };
$('import-profiles').onclick = () => perform(() => dungeonApp.import());
$('mimic-example').onclick = () => perform(async () => { await closeProfiles(); return dungeonApp.mimicExample(); });
dungeonApp.onState(render); perform(() => dungeonApp.state());
