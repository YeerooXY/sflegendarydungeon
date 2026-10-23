"""Local decision service for MFBot's Legendary Dungeon hook.

MFBot sends the raw numeric dungeon fields of its latest responses; this service
decodes them with the reviewed automatic projection, asks the learned pilot for
one action and returns the game request to send. MFBot sends it through its
own transport and confirms the outcome. Run memory is committed only after a
confirmed send, so a lost or failed command never advances the history.

Healing screens are decided here too: HP regenerates at 100% per 24 hours from
the healing start time (word 22 of "characterstatus"), and the policy chooses
between re-entering (at 20% or more) and waiting to 20/40/60/80/100%, to the
boss's maximum hit after a boss death, or to the event end, as in the simulator.
A wait is answered with {"decision": "wait", "seconds": ...}; MFBot then leaves
the character alone until the wait ends and asks again.

Anything the projection or pilot refuses (special rooms, later runs, unknown
codes, a history gap) is answered with a fallback, and MFBot's own logic plays
that screen. Mushroom budget is zero: paid heals and rerolls are never offered
to the policy.

PROVISIONAL: the healing screen's room numbering after a boss death and whether
its save shows the destination doors are unverified; doors are encoded only
when both codes are known, and every recovery decision is journaled.

PROVISIONAL: MFBot's parser reads the main save from "iadungeon" while our
decoder was reviewed against "iadungeonsave". Both use the same 50-word
layout, so a 50-word "iadungeon" is aliased when no "iadungeonsave" is present.
Every alias is journaled for the event-day packet check.

Listens on 127.0.0.1 only and requires the bearer token written at startup.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sfld_record.spool import Spool  # noqa: E402
from sfld_rl.player_ideas import profile_values  # noqa: E402
from sfld_study.automatic import DOORS, project  # noqa: E402
from sfld_study.protocol import AUTOMATIC_CLIENT_VERSION, command, numeric  # noqa: E402
from sfld_study.schema import StudyError, action  # noqa: E402

MAX_BODY = 1024 * 1024
BUDGET = 0
NUMERIC = re.compile(r'-?\d+(?:/-?\d+)*')
PROFILE = Path(__file__).resolve().parents[2]/'profiles/synthetic.profile'
BOSS_MAX = [float(row.split(':')[1]) for row in profile_values(PROFILE)['boss_damage'].split(',')]
DAY = 86400


def recovery(visible, fields):
    """Healing screen: regenerated HP, return screen and the simulator's recovery menu."""
    status = numeric(fields.get('characterstatus'))
    start = status[22] if len(status) > 22 else 0
    if start <= 0 or 'timestamp' not in fields or visible.get('theme') is None or not visible.get('hp_max'):
        raise StudyError('Healing screen without a healing start, server time or theme.')
    now = numeric(fields['timestamp'], 1)[0]
    hp = min(1.0, max(0.0, (now-start)/DAY))
    save = numeric(fields.get('iadungeonsave', fields.get('iadungeon')), 50);death_room = save[17]
    boss = death_room > 0 and death_room % 25 == 0
    v = dict(visible, screen_kind='recovery', hp_current=int(hp*visible['hp_max']),
             room=death_room if boss else death_room+1, resume_phase='encounter' if boss else 'doors')
    doors = []
    if not boss and all(t in DOORS for t in save[19:21]) and all(0 <= t < 10 for t in save[25:27]):
        for i in range(2):
            kind = DOORS[save[19+i]];cost = 2 if kind == 'double_locked' else int(kind in ('locked', 'epic'))
            doors.append({'index': i, 'type': save[19+i], 'kind': kind, 'trap': save[25+i], 'key_cost': cost,
                          'blocked': kind == 'wall' or v['keys'] < cost,
                          'trap_kind': 'none' if not save[25+i] else 'curse' if 11 in (v['gems'] or []) else 'damage'})
    v['doors'] = doors or None
    left = v.get('event_seconds_left') or 0
    menu = []
    if hp >= .2-1e-9:menu.append(('reenter', action({'kind': 'start', 'theme': v['theme']})))
    targets = [(f'wait_hp_{k}', k/100) for k in (20, 40, 60, 80, 100)]
    if boss:targets.append(('wait_boss_safe', min(1.0, max(.2, BOSS_MAX[min(3, (death_room-1)//25)]+.001))))
    for name, target in targets:
        seconds = min(left, int((target-hp)*DAY+.999))
        if seconds >= 1:menu.append((name, action({'kind': 'wait', 'seconds': min(DAY, seconds)})))
    if left >= 1:menu.append(('wait_deadline', action({'kind': 'wait', 'seconds': min(DAY, int(left))})))
    v['recovery_menu'] = menu;v['legal_actions'] = [a for _, a in menu]
    return v, hp


def default_directory():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'sfld' / 'mfbot-bridge'


class Bridge:
    def __init__(self, directory, choose=None, spool=None):
        self.directory = Path(directory);self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock();self.spool = spool
        if choose is None:
            from sfld_study.learned import choose
        self.choose = choose

    def _state_path(self, character):
        safe = re.sub(r'[^A-Za-z0-9_.-]', '_', character)[:120]
        return self.directory / f'{safe}.json'

    def _load(self, character):
        path = self._state_path(character)
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def _save(self, character, state):
        path = self._state_path(character);tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(state, indent=1), encoding='utf-8');os.replace(tmp, path)

    def journal(self, entry):
        with (self.directory / 'decisions.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': time.time(), **entry}, separators=(',', ':')) + '\n')

    def visible(self, fields, level):
        fields = {k: v for k, v in fields.items() if isinstance(k, str) and isinstance(v, str) and NUMERIC.fullmatch(v)}
        aliased = False
        if 'iadungeonsave' not in fields and 'iadungeon' in fields and len(fields['iadungeon'].split('/')) == 50:
            fields['iadungeonsave'] = fields['iadungeon'];aliased = True
        if level is not None:
            # MFBot passes the level it already parsed; only word 3 of this record is read.
            fields['ownplayersavecharacter'] = '0/0/0/' + str(int(level))
        visible, _, reason = project(fields, AUTOMATIC_CLIENT_VERSION)
        visible = dict(visible)
        if 'iadungeontime' in fields and 'timestamp' in fields:
            times = numeric(fields['iadungeontime'], 4);now = numeric(fields['timestamp'], 1)[0]
            visible['event_start'] = times[1];visible['elapsed_seconds'] = max(0, now - times[1])
        visible['budget_remaining'] = BUDGET
        return visible, reason, aliased

    def decide(self, request):
        character = str(request['character']);fields = request.get('fields') or {}
        base = {'character': character, 'mfbot_version': request.get('mfbot_version')}
        with self.lock:
            try:
                visible, reason, aliased = self.visible(fields, request.get('level'))
            except StudyError as error:
                self.journal({**base, 'decision': 'fallback', 'reason': 'decode: ' + str(error)})
                return {'decision': 'fallback', 'reason': 'decode: ' + str(error)}
            base.update(aliased=aliased, status=visible.get('status'), room=visible.get('room'),
                        encounter=visible.get('encounter_type'))
            state = self._load(character)
            event = visible.get('event_start')
            if state.get('event_start') != event:state = {'event_start': event, 'memory': None, 'completed_runs': 0}
            if state.get('pending'):
                # MFBot never confirmed the last command; its outcome is unknown.
                state['blocked'] = 'unconfirmed command ' + state['pending']['id'];state.pop('pending')
            if visible.get('status') == 102 and state.get('memory') is not None:
                state['completed_runs'] += 1;state['memory'] = None;state['blocked'] = 'later runs need the multi-run adapter'
            visible['run_number_event'] = 1 + state.get('completed_runs', 0)
            if reason == 'recovery_or_restart_requires_review' and visible.get('status') == 101 and not state.get('blocked'):
                try:
                    visible, hp = recovery(visible, fields);reason = 'automatic_menu'
                    base.update(room=visible['room'], healing_hp=round(hp, 4), resume=visible['resume_phase'],
                                doors_known=visible['doors'] is not None)
                except StudyError as error:
                    reason = 'recovery: ' + str(error)
            fallback = state.get('blocked') or (None if reason == 'automatic_menu' else reason)
            if fallback is None:
                try:
                    result = self.choose(visible, state.get('memory'))
                    if result['action']['kind'] != 'wait':req, params = command(result['action'])
                except StudyError as error:
                    fallback = 'policy: ' + str(error)
            if fallback:
                self._save(character, state)
                self.journal({**base, 'decision': 'fallback', 'reason': fallback})
                return {'decision': 'fallback', 'reason': fallback}
            if result['action']['kind'] == 'wait':
                # Nothing is sent, so the wait is part of the history at once.
                state['memory'] = result['memory'];self._save(character, state)
                seconds = result['action']['seconds'];why = result['memory'].get('reason', 'learned')
                self.journal({**base, 'decision': 'wait', 'seconds': seconds, 'why': why})
                return {'decision': 'wait', 'seconds': seconds, 'why': why}
            decision_id = uuid.uuid4().hex
            state['pending'] = {'id': decision_id, 'memory': result['memory']}
            self._save(character, state)
            reason = (result['memory'] or {}).get('reason', 'fresh_entry')
            self.journal({**base, 'decision': 'command', 'id': decision_id, 'req': req, 'params': params,
                          'action': result['action'], 'why': reason})
            return {'decision': 'command', 'id': decision_id, 'req': req, 'params': params, 'why': reason}

    def confirm(self, request):
        character = str(request['character']);decision_id = str(request['id']);ok = bool(request.get('ok'))
        with self.lock:
            state = self._load(character);pending = state.get('pending')
            if not pending or pending['id'] != decision_id:
                self.journal({'character': character, 'decision': 'confirm_mismatch', 'id': decision_id})
                return {'committed': False}
            state.pop('pending')
            if ok:state['memory'] = pending['memory']
            else:state['blocked'] = 'command rejected: ' + str(request.get('error'))[:200]
            self._save(character, state)
            self.journal({'character': character, 'decision': 'confirmed' if ok else 'rejected', 'id': decision_id,
                          'error': None if ok else str(request.get('error'))[:200]})
            return {'committed': ok}

    def record(self, request):
        if self.spool is None:return {'recorded': False}
        values = {k: v for k, v in (request.get('fields') or {}).items() if isinstance(k, str) and isinstance(v, str)}
        row = self.spool.packet(request.get('req'), request.get('params'), values, host=str(request.get('host') or ''),
                                player=str(request.get('player') or ''), client=request.get('mfbot_version'))
        return {'recorded': row is not None}


def serve(bridge, port, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            routes = {'/decide': bridge.decide, '/confirm': bridge.confirm, '/record': bridge.record}
            if self.headers.get('Authorization') != 'Bearer ' + token or self.path not in routes:
                self.send_response(403);self.end_headers();return
            size = int(self.headers.get('Content-Length') or 0)
            if not 0 < size <= MAX_BODY:
                self.send_response(413);self.end_headers();return
            try:
                body = json.dumps(routes[self.path](json.loads(self.rfile.read(size)))).encode()
                self.send_response(200)
            except Exception as error:  # A bridge failure must only make MFBot fall back.
                bridge.journal({'decision': 'error', 'path': self.path, 'error': type(error).__name__ + ': ' + str(error)[:300]})
                body = json.dumps({'decision': 'fallback', 'reason': 'bridge error'}).encode();self.send_response(500)
            self.send_header('Content-Type', 'application/json');self.send_header('Content-Length', str(len(body)))
            self.end_headers();self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--port', type=int, default=47831);p.add_argument('--directory', type=Path, default=default_directory())
    p.add_argument('--no-record', action='store_true', help='do not copy captured packets into the recorder spool')
    a = p.parse_args()
    bridge = Bridge(a.directory, spool=None if a.no_record else Spool(source='mfbot'))
    token = secrets.token_urlsafe(32);token_path = a.directory / 'token'
    token_path.write_text(token, encoding='utf-8')
    print(json.dumps({'listening': f'http://127.0.0.1:{a.port}', 'token_file': str(token_path)}), flush=True)
    serve(bridge, a.port, token)


if __name__ == '__main__':
    main()
