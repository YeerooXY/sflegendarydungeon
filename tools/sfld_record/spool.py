"""Append parsed Legendary Dungeon packets to a private daily JSONL spool.

The extraction rules mirror desktop/ld-recorder.cjs exactly (see fields.json):
numeric values only, never credentials, sessions or player names. Recording
never changes or delays the game command it observes.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading

RULES = json.loads((Path(__file__).with_name('fields.json')).read_text(encoding='utf-8'))
NUMERIC = re.compile(r'-?\d+(?:/-?\d+)*')
SCHEMA = 1


def default_directory():
    root = os.environ.get('SFLD_RECORDER_DIR') or os.path.join(os.environ.get('LOCALAPPDATA', str(Path.home())), 'sfld-recorder')
    return Path(root)


def base(key):
    return re.split(r'[.(]', key)[0].lower()


def extract(req, params, values, *, source, host, player, client=None, now=None):
    """Return the spool record for one response, or None when nothing is dungeon-related."""
    dungeon_command = isinstance(req, str) and req.lower().startswith('iadungeon')
    keep = set(RULES['dungeon_fields'])
    fields = {}
    for key, value in values.items():
        name = base(key)
        if name in RULES['deny'] or not isinstance(value, str) or len(value) > RULES['max_value_length']:
            continue
        if not NUMERIC.fullmatch(value):
            continue
        if dungeon_command or name in keep or (name in RULES['context_fields'] and any(base(k) in keep for k in values)):
            fields[key] = value
    if not dungeon_command and not any(base(k) in keep for k in fields):
        return None
    now = now or datetime.now(timezone.utc)
    return {'schema': SCHEMA, 'source': source, 't': now.isoformat(timespec='milliseconds'),
            'ms': int(now.timestamp() * 1000), 'client': client, 'host': host, 'player': str(player or ''),
            'req': req if isinstance(req, str) else None,
            # Dungeon command arguments are short numeric menus (codes, ids, prices).
            'params': params if dungeon_command and isinstance(params, str) and NUMERIC.fullmatch(params or '0') else None,
            'fields': fields}


def decode_params(encoded):
    try:
        return base64.b64decode(encoded, validate=True).decode('utf-8')
    except (ValueError, UnicodeError):
        return None


class Spool:
    def __init__(self, directory=None, source='controller'):
        self.directory = Path(directory) if directory else default_directory()
        self.source = source
        self.lock = threading.Lock()

    def packet(self, req, params, values, *, host, player, client=None):
        record = extract(req, params, values, source=self.source, host=host, player=player, client=client)
        if record is None:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{record['t'][:10]}.jsonl"
        with self.lock, path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, separators=(',', ':')) + '\n')
        return record
