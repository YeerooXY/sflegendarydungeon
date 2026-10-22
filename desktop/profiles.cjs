'use strict';
const {randomUUID} = require('node:crypto');

function gameOrigin(value = 'play.sfgame.net') {
  if (typeof value !== 'string') throw new Error('Enter a game world, such as s1.sfgame.eu.');
  const url = new URL(value.includes('://') ? value : `https://${value}`);
  if (url.protocol !== 'https:' || url.port || url.username || url.password ||
      !/^(?:[a-z0-9-]+\.)?sfgame\.(?:net|eu)$/i.test(url.hostname) ||
      url.pathname !== '/' || url.search || url.hash) {
    throw new Error('Use an HTTPS S&F world address, without a path or login details.');
  }
  // The public root includes a marketing landing page over the Unity canvas.
  // The site's own script directs players to this official client host.
  return url.hostname === 'sfgame.net' ? 'https://play.sfgame.net' : url.origin;
}

function navigationAllowed(value, practiceOrigin = '') {
  try {
    const url = new URL(value);
    if (practiceOrigin && url.origin === practiceOrigin) return true;
    return url.protocol === 'https:' && !url.username && !url.password && !url.port &&
      (/^(?:[a-z0-9-]+\.)?sfgame\.(?:net|eu)$/i.test(url.hostname) ||
       url.hostname === 'sso.playa-games.com');
  } catch { return false; }
}

function importProfiles(document, existing = []) {
  const incoming = Array.isArray(document) ? document : document?.accounts;
  if (!Array.isArray(incoming) || !incoming.length || incoming.length > 200) {
    throw new Error('Import a JSON accounts list containing 1–200 login profiles.');
  }
  const keys = new Set(existing.map(p => `${p.label.toLowerCase()}\n${p.origin}`));
  const additions = [];
  for (const row of incoming) {
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        Object.keys(row).some(k => !['label', 'world'].includes(k))) {
      throw new Error('Each login profile must contain only label and world. Sign in inside the game.');
    }
    const label = typeof row.label === 'string' ? row.label.trim() : '';
    if (!label || label.length > 80 || /[\x00-\x1f\x7f]/.test(label)) throw new Error('Use a profile label of 1–80 characters.');
    const origin = gameOrigin(row.world);
    const key = `${label.toLowerCase()}\n${origin}`;
    if (keys.has(key)) continue;
    keys.add(key);
    additions.push({id: randomUUID(), label, origin});
  }
  if (existing.length + additions.length > 200) throw new Error('This app supports up to 200 saved login profiles.');
  return [...existing, ...additions];
}

function validateSaved(document) {
  if (document?.schema !== 1 || !Array.isArray(document.accounts) || document.accounts.length > 200) {
    throw new Error('The saved login profile list needs review.');
  }
  const ids = new Set();
  return document.accounts.map(row => {
    if (!/^[a-f0-9-]{36}$/.test(row.id) || ids.has(row.id)) throw new Error('Invalid or duplicate login profile.');
    ids.add(row.id);
    const checked = importProfiles([{label: row.label, world: row.origin}])[0];
    return {...checked, id: row.id};
  });
}

module.exports = {gameOrigin, navigationAllowed, importProfiles, validateSaved};
