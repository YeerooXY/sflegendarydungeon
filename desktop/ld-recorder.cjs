'use strict';
// Records parsed Legendary Dungeon packets from the embedded client to a private
// daily JSONL spool. Mirrors tools/sfld_record/spool.py: numeric values only,
// never credentials, sessions or names. Recording never alters game traffic.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// Development: the repository's tools; installed: the bundled runtime's copy.
const RULES_FILE = [path.join(__dirname, '..', 'tools', 'sfld_record', 'fields.json'),
  path.join(process.resourcesPath || '', 'runtime', 'tools', 'sfld_record', 'fields.json')].find(p => fs.existsSync(p));
const RULES = JSON.parse(fs.readFileSync(RULES_FILE, 'utf8'));
const NUMERIC = /^-?\d+(?:\/-?\d+)*$/;
const base = key => key.split(/[.(]/)[0].toLowerCase();

function parseBody(body) {
  const values = {};
  for (const field of String(body).trim().split('&')) {
    if (!field) continue;
    const at = field.indexOf(':');
    if (at > 0) values[field.slice(0, at)] = field.slice(at + 1);
  }
  return values;
}

function decodeParams(encoded) {
  try { return encoded == null ? null : Buffer.from(encoded, 'base64').toString('utf8'); } catch { return null; }
}

function extract({req, params, values, source, host, player, client = null, now = new Date()}) {
  const dungeonCommand = typeof req === 'string' && req.toLowerCase().startsWith('iadungeon');
  const keep = new Set(RULES.dungeon_fields);
  const hasDungeon = Object.keys(values).some(k => keep.has(base(k)));
  const fields = {};
  for (const [key, value] of Object.entries(values)) {
    const name = base(key);
    if (RULES.deny.includes(name) || typeof value !== 'string' || value.length > RULES.max_value_length || !NUMERIC.test(value)) continue;
    if (dungeonCommand || keep.has(name) || (RULES.context_fields.includes(name) && hasDungeon)) fields[key] = value;
  }
  if (!dungeonCommand && !Object.keys(fields).some(k => keep.has(base(k)))) return null;
  const iso = now.toISOString();
  return {schema: 1, source, t: iso, ms: now.getTime(), client, host, player: String(player ?? ''),
    req: typeof req === 'string' ? req : null,
    params: dungeonCommand && typeof params === 'string' && NUMERIC.test(params || '0') ? params : null, fields};
}

function fromNetwork({url, headers, body, client}) {
  let parsed;
  try { parsed = new URL(url); } catch { return null; }
  if (parsed.protocol !== 'https:' || parsed.pathname !== '/cmd.php' || !/^(?:[a-z0-9-]+\.)?sfgame\.(?:net|eu)$/.test(parsed.hostname)) return null;
  const header = Object.entries(headers || {}).find(([k]) => k.toLowerCase() === 'pg-player');
  return extract({req: parsed.searchParams.get('req'), params: decodeParams(parsed.searchParams.get('params')),
    values: parseBody(body), source: 'desktop', host: parsed.hostname, player: header ? header[1] : '', client});
}

function spoolDirectory() {
  return process.env.SFLD_RECORDER_DIR || path.join(process.env.LOCALAPPDATA || os.homedir(), 'sfld-recorder');
}

function createRecorder(directory = spoolDirectory()) {
  let failures = 0;
  return {
    directory,
    record(packet) {
      try {
        const record = fromNetwork(packet);
        if (!record) return null;
        fs.mkdirSync(directory, {recursive: true});
        fs.appendFileSync(path.join(directory, record.t.slice(0, 10) + '.jsonl'), JSON.stringify(record) + '\n');
        return record;
      } catch { failures++; return null; }
    },
    failures: () => failures,
  };
}

module.exports = {RULES, parseBody, decodeParams, extract, fromNetwork, createRecorder, spoolDirectory};
