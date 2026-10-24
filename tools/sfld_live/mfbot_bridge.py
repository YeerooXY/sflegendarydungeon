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

Special rooms (client DungeonStep 301-329) are mapped to simulator rooms and
decided by the policy with the codes MFBot sends for them: 50 to use or fight,
51 to ignore or leave, 90-92 for rock-paper-scissors. Rooms whose meaning is not
verified get a fixed conservative action (spider webs and the Empire/Mariachi
rooms are left; the hole and flooded rooms continue). Every run of the event is
played: a completed run starts the next one.

MFBot's own dungeon logic never decides. Anything still unhandled (unknown codes,
a history gap, an unconfirmed command) answers {"decision": "pause"} and the
character's dungeon waits for review. The one exception is picking up a looted
item, which is bag management: {"decision": "native"} lets MFBot move it into a
free slot. Mushroom budget is zero: paid heals and rerolls are never offered.

PROVISIONAL: the healing screen's room numbering after a boss death and whether
its save shows the destination doors are unverified; doors are encoded only
when both codes are known, and every recovery decision is journaled.

PROVISIONAL: MFBot's parser reads the main save from "iadungeon" while our
decoder was reviewed against "iadungeonsave". Both use the same 50-word
layout, so a 50-word "iadungeon" is aliased when no "iadungeonsave" is present.
Every alias is journaled for the event-day packet check.

Every confirmed command and every wait is logged with the state it was chosen in
and the next state seen: choices.jsonl rows {before, action, after}, for event
odds such as what is behind each door. Each side keeps the whole decoded state
(room, stage, theme, run of the event, doors and traps, encounter, HP, keys,
stones, blessings and curses with their durations, menu, shop offers) and every
raw numeric field MFBot passed (save words, soul stones, resources, loot), so
values not decoded today can be read later. Rows also carry the event start, the
level, the MFBot version and the policy's reason.

The desktop app uses the same service as a live adviser (join_mid_run): it
reports each dungeon command the game client actually sent (/sent), so the
history follows the player's own choices, and a run already in progress is
joined with an approximate history. Replies carry a plain-language label.

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
# Client DungeonStep -> (simulator room, menu kind). Kinds: choice (use 50 / ignore 51),
# fight (fight 50 / flee 51), must (use 50 only), pig (fight 50 / leave 51), rps,
# leave (51 only) and continue (50 only); the last two are fixed, not policy, choices.
SPECIAL = {301: ('fountain', 'choice'), 302: (None, 'continue'), 303: ('rocks', 'must'), 304: ('lava', 'must'),
           305: ('narrator', 'choice'), 306: (None, 'continue'), 307: ('wishing_well', 'choice'), 308: ('rps', 'rps'),
           309: ('sewers', 'choice'), 310: ('undead', 'fight'), 311: ('locker', 'choice'), 312: ('sarcophagus', 'choice'),
           313: ('valaraukar', 'fight'), 314: ('wood', 'must'), 316: ('wheel', 'choice'), 317: (None, 'leave'),
           319: ('beta', 'fight'), 320: ('tube', 'fight'), 321: ('souls', 'choice'), 322: ('arcane', 'choice'),
           324: ('rainbow', 'choice'), 325: ('pig', 'pig'), 326: ('auction', 'choice'), 327: (None, 'leave'),
           328: (None, 'leave'), 329: ('armory', 'choice')}


def special(visible):
    """Menu for a special room, naming each live action's simulator counterpart."""
    code = visible.get('encounter_type')
    if code not in SPECIAL:
        raise StudyError(f'Unmapped special room {code}.')
    room, kind = SPECIAL[code]
    use, ignore = action({'kind': 'interact', 'code': 50}), action({'kind': 'interact', 'code': 51})
    menu = {'choice': [('interact', use), ('skip', ignore)], 'fight': [('fight', use), ('flee', ignore)],
            # The simulator lets the pig room be fled or skipped; leaving it live is free either way.
            'must': [('interact', use)], 'pig': [('fight', use), ('flee', ignore), ('skip', ignore)],
            'rps': [('interact', action({'kind': 'interact', 'code': secrets.choice((90, 91, 92))})), ('skip', ignore)],
            'leave': [('skip', ignore)], 'continue': [('interact', use)]}[kind]
    return dict(visible, screen_kind='encounter', sim_encounter=room, policy_menu=menu,
                legal_actions=list({a['code']: a for _, a in menu}.values()), bookkeeping=kind in ('leave', 'continue'))


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


def snapshot(visible, fields):
    return {'visible': visible, 'fields': fields}


ACTIONS = ('iadungeoninteract', 'iadungeonstart', 'iadungeonend', 'iadungeonselectsoulstone',
           'iadungeonmerchantbuy', 'iadungeondebuffmerchantbuy', 'iadungeongetlife')
EFFECT_NAMES = {1: 'Raider', 2: 'One Hit', 3: 'Escape', 4: 'Disarm', 5: 'Lockpick', 6: 'Key Moment', 7: 'Elixir',
                8: 'Recovery', 101: 'Broken Armor', 102: 'Poison', 103: 'Clumsy', 104: 'Gold Hangover', 105: 'Hard Lock'}
STONE_NAMES = {2: 'Rabbit', 18: 'Moonstone', 9: 'Spying', 6: 'Pendant', 8: 'Greasy', 11: 'Gambler', 3: 'Greed',
               20: 'Hero', 19: 'Time Traveler', 15: 'Thirsty', 22: 'Pearl', 13: 'Blood', 4: 'Explorer',
               17: 'Misadventurer', 21: 'Devil', 7: 'Deceit', 10: 'Lodestone', 1: 'Bull', 16: 'Hick', 23: 'Rusty'}


SCREEN = ('status', 'room', 'encounter_type', 'encounter_variant', 'doors', 'hp_current', 'keys', 'gems',
          'blessings', 'curses', 'legal_actions')


def same_screen(before, visible):
    return all(before.get(k) == visible.get(k) for k in SCREEN)


def duration(seconds):
    minutes = max(1, round(seconds/60))
    return f'{minutes} min' if minutes < 60 else f'{minutes//60} h {minutes%60:02d} min'


def target(chosen, visible):
    """The on-screen control an action uses, stable across rooms: 'door:1', 'stone:0',
    'offer:1', 'shop:51', 'special316:50', 'encounter:20', 'start', 'heal:2'."""
    kind = chosen.get('kind')
    if kind == 'start':return 'start'
    if kind == 'end':return 'end'
    if kind == 'choose_gem':return f"stone:{(visible.get('gem_offers') or []).index(chosen['gem_id'])}"
    if kind in ('buy_blessing', 'buy_curse'):
        ids = [o['effect_id'] for o in visible.get('offers') or []]
        return f"offer:{ids.index(chosen['effect_id'])}"
    if kind in ('heal_partial', 'heal_full'):return 'heal:' + ('1' if kind == 'heal_partial' else '2')
    code = chosen['code']
    if visible.get('screen_kind') == 'doors':return f"door:{(code & 3)-1}" + (':disarm' if code & 8 else '')
    if visible.get('screen_kind') == 'shop':return f'shop:{code}'
    if visible.get('policy_menu') or 300 < (visible.get('encounter_type') or 0) < 400:
        return f"special{visible.get('encounter_type')}:{code}"
    return f'encounter:{code}'


def describe(chosen, visible):
    """A short instruction for the player, e.g. 'Right door (mystery, trapped)'."""
    kind = chosen.get('kind')
    if kind == 'start':return 'Enter the dungeon'
    if kind == 'end':return 'End the run'
    if kind == 'choose_gem':return f"Take the {STONE_NAMES.get(chosen['gem_id'], 'offered')} stone"
    if kind == 'buy_blessing':return f"Buy {EFFECT_NAMES.get(chosen['effect_id'], 'the blessing')} ({chosen['key_price']} keys)"
    if kind == 'buy_curse':return f"Take {EFFECT_NAMES.get(chosen['effect_id'], 'the curse')} (+{chosen['keys_received']} keys)"
    if kind in ('heal_partial', 'heal_full'):return 'Heal ' + ('partly' if kind == 'heal_partial' else 'fully')
    code = chosen.get('code')
    if visible.get('screen_kind') == 'doors' and code is not None and (code & 3) in (1, 2):
        door = next((d for d in visible.get('doors') or [] if d['index'] == (code & 3)-1), None)
        detail = []
        if door:detail.append(door['kind'].replace('_', ' '))
        if door and door['trap']:detail.append('trapped')
        if code & 8:detail.append('disarm')
        return ('Left' if code & 3 == 1 else 'Right') + ' door' + (f" ({', '.join(detail)})" if detail else '')
    if visible.get('policy_menu'):
        room = (visible.get('sim_encounter') or 'room').replace('_', ' ')
        return {50: f'Use the {room}', 51: 'Leave', 90: 'Rock', 91: 'Paper', 92: 'Scissors'}.get(code, 'Continue')
    return {20: 'Fight', 21: 'Flee', 30: 'Continue', 31: 'Continue', 40: 'Open it', 42: 'Leave it', 51: 'Leave the shop',
            60: 'Collect', 70: 'Continue', 80: 'Reroll the shop (1 mushroom)'}.get(code, 'Continue')


def default_directory():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'sfld' / 'mfbot-bridge'


class Bridge:
    def __init__(self, directory, choose=None, spool=None, join_mid_run=False):
        self.directory = Path(directory);self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock();self.spool = spool;self.join_mid_run = join_mid_run
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

    def choice(self, entry):
        with (self.directory / 'choices.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'at': time.time(), **entry}, separators=(',', ':')) + '\n')

    @staticmethod
    def _choice_start(request, state, visible, fields, chosen, why):
        return {'event_start': state.get('event_start'), 'run_number_event': visible.get('run_number_event'),
                'level': request.get('level'), 'mfbot_version': request.get('mfbot_version'), 'why': why,
                'before': snapshot(visible, fields), 'action': chosen}

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
                self.journal({**base, 'decision': 'pause', 'reason': 'decode: ' + str(error)})
                return {'decision': 'pause', 'reason': 'decode: ' + str(error)}
            base.update(aliased=aliased, status=visible.get('status'), room=visible.get('room'),
                        encounter=visible.get('encounter_type'))
            state = self._load(character)
            event = visible.get('event_start')
            if state.get('event_start') != event:state = {'event_start': event, 'memory': None, 'completed_runs': 0}
            if state.get('open_choice'):
                # The first state seen after a confirmed command is that choice's outcome.
                self.choice({'character': character, **state.pop('open_choice'), 'after': snapshot(visible, fields)})
            if state.get('pending') and self.join_mid_run:
                pending = state['pending']
                if same_screen(pending['choice']['before']['visible'], visible):
                    # The same screen again (a repeated response): repeat the advice.
                    chosen = pending['choice']['action']
                    return {'decision': 'command', 'id': pending['id'], 'req': pending['choice']['req'],
                            'params': pending['choice']['params'], 'why': pending['choice']['why'],
                            'action': chosen, 'label': describe(chosen, pending['choice']['before']['visible']),
                            'target': target(chosen, pending['choice']['before']['visible'])}
                # The screen moved on without a reported command: rejoin from here.
                state.pop('pending');state['memory'] = None
            if state.get('pending'):
                # MFBot never confirmed the last command; its outcome is unknown.
                state['blocked'] = 'unconfirmed command ' + state['pending']['id'];state.pop('pending')
            if visible.get('status') in (0, 102) and state.get('memory') is not None:
                # The run is complete; the next one starts from a fresh history.
                state['completed_runs'] += 1;state['memory'] = None;state.pop('blocked', None)
            visible['run_number_event'] = 1 + state.get('completed_runs', 0)
            if self.join_mid_run:visible['join_mid_run'] = True
            if visible.get('status') == 102 and not state.get('blocked'):
                # PROVISIONAL: the completed-run screen is answered by starting the next run.
                reason = 'run_complete' if visible.get('theme') else 'run_complete_without_theme'
                if visible.get('theme'):visible = dict(visible, legal_actions=[action({'kind': 'start', 'theme': visible['theme']})])
            if reason == 'special_room_requires_review' and not state.get('blocked'):
                try:
                    visible = special(visible);reason = 'automatic_menu'
                    base.update(special_room=visible['sim_encounter'] or visible['encounter_type'])
                except StudyError as error:
                    reason = 'special: ' + str(error)
            if reason == 'recovery_or_restart_requires_review' and visible.get('status') == 101 and not state.get('blocked'):
                try:
                    visible, hp = recovery(visible, fields);reason = 'automatic_menu'
                    base.update(room=visible['room'], healing_hp=round(hp, 4), resume=visible['resume_phase'],
                                doors_known=visible['doors'] is not None)
                except StudyError as error:
                    reason = 'recovery: ' + str(error)
            fallback = state.get('blocked') or (None if reason in ('automatic_menu', 'run_complete') else reason)
            if fallback == 'item_inventory_requires_review':
                # Moving loot into a bag slot is MFBot's inventory code, not a dungeon decision.
                self._save(character, state)
                self.journal({**base, 'decision': 'native', 'reason': 'item pickup'})
                return {'decision': 'native', 'reason': 'item pickup', 'label': 'Pick up the item'}
            if fallback is None and reason == 'run_complete':
                result = {'action': visible['legal_actions'][0], 'memory': None}
                req, params = command(result['action'])
            elif fallback is None:
                try:
                    result = self.choose(visible, state.get('memory'))
                    if result['action']['kind'] != 'wait':req, params = command(result['action'])
                except StudyError as error:
                    fallback = 'policy: ' + str(error)
            if fallback:
                self._save(character, state)
                self.journal({**base, 'decision': 'pause', 'reason': fallback})
                return {'decision': 'pause', 'reason': fallback, 'label': 'No advice for this screen'}
            if result['action']['kind'] == 'wait':
                # Nothing is sent, so the wait is part of the history at once.
                seconds = result['action']['seconds'];why = result['memory'].get('reason', 'learned')
                state['memory'] = result['memory']
                state['open_choice'] = self._choice_start(request, state, visible, fields, result['action'], why)
                self._save(character, state)
                self.journal({**base, 'decision': 'wait', 'seconds': seconds, 'why': why})
                return {'decision': 'wait', 'seconds': seconds, 'why': why,
                        'label': f'Wait {duration(seconds)} for HP to regenerate'}
            decision_id = uuid.uuid4().hex
            state['pending'] = {'id': decision_id, 'memory': result['memory'],
                                'choice': {**self._choice_start(request, state, visible, fields, result['action'],
                                                                (result['memory'] or {}).get('reason', 'fresh_entry')),
                                           'req': req, 'params': params, 'id': decision_id}}
            self._save(character, state)
            reason = (result['memory'] or {}).get('reason', 'fresh_entry')
            self.journal({**base, 'decision': 'command', 'id': decision_id, 'req': req, 'params': params,
                          'action': result['action'], 'why': reason})
            return {'decision': 'command', 'id': decision_id, 'req': req, 'params': params, 'why': reason,
                    'action': result['action'], 'label': describe(result['action'], visible),
                    'target': target(result['action'], visible)}

    def confirm(self, request):
        character = str(request['character']);decision_id = str(request['id']);ok = bool(request.get('ok'))
        with self.lock:
            state = self._load(character);pending = state.get('pending')
            if not pending or pending['id'] != decision_id:
                self.journal({'character': character, 'decision': 'confirm_mismatch', 'id': decision_id})
                return {'committed': False}
            state.pop('pending')
            if ok:state['memory'] = pending['memory'];state['open_choice'] = pending.get('choice')
            else:state['blocked'] = 'command rejected: ' + str(request.get('error'))[:200]
            self._save(character, state)
            self.journal({'character': character, 'decision': 'confirmed' if ok else 'rejected', 'id': decision_id,
                          'error': None if ok else str(request.get('error'))[:200]})
            return {'committed': ok}

    def sent(self, request):
        """The game client sent a dungeon command (desktop): commit what was actually done.

        The advised command confirms its decision. Another legal command replaces
        the advised choice in the history. Anything else drops the run history; the
        next screen rejoins with an approximate one (join_mid_run).
        """
        character = str(request['character']);req = str(request.get('req') or '');params = str(request.get('params') or '')
        name = next((a for a in ACTIONS if req.lower().endswith(a)), None)
        if name is None:return {'committed': False, 'outcome': 'not an action'}
        with self.lock:
            state = self._load(character);pending = state.pop('pending', None);matched = None
            for a in (pending['choice']['before']['visible'].get('legal_actions') or []) if pending else []:
                try:
                    r, p = command(a)
                except StudyError:
                    continue
                if r.lower() == name and p == params:matched = a;break
            if matched is not None:
                memory = pending['memory'];choice = dict(pending['choice'])
                if matched != choice['action']:
                    choice.update(advised=choice['action'], action=matched)
                    if memory and memory.get('last'):memory = dict(memory, last=dict(memory['last'], choice=matched))
                state['memory'] = memory;state['open_choice'] = choice;state.pop('blocked', None)
                outcome = 'own_choice' if 'advised' in choice else 'advised'
            else:
                # An unadvised or unrecognised command: rejoin from the next screen.
                state['memory'] = None;state.pop('blocked', None);outcome = 'history_reset'
            self._save(character, state)
            self.journal({'character': character, 'decision': 'sent', 'req': req, 'params': params, 'outcome': outcome})
            seen = target(matched, pending['choice']['before']['visible']) if matched is not None else None
            return {'committed': outcome != 'history_reset', 'outcome': outcome, 'target': seen}

    def record(self, request):
        if self.spool is None:return {'recorded': False}
        values = {k: v for k, v in (request.get('fields') or {}).items() if isinstance(k, str) and isinstance(v, str)}
        row = self.spool.packet(request.get('req'), request.get('params'), values, host=str(request.get('host') or ''),
                                player=str(request.get('player') or ''), client=request.get('mfbot_version'))
        return {'recorded': row is not None}


def make_server(bridge, port, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            routes = {'/decide': bridge.decide, '/confirm': bridge.confirm, '/record': bridge.record, '/sent': bridge.sent}
            if self.headers.get('Authorization') != 'Bearer ' + token or self.path not in routes:
                self.send_response(403);self.end_headers();return
            size = int(self.headers.get('Content-Length') or 0)
            if not 0 < size <= MAX_BODY:
                self.send_response(413);self.end_headers();return
            try:
                body = json.dumps(routes[self.path](json.loads(self.rfile.read(size)))).encode()
                self.send_response(200)
            except Exception as error:  # A bridge failure pauses the character; never MFBot's logic.
                bridge.journal({'decision': 'error', 'path': self.path, 'error': type(error).__name__ + ': ' + str(error)[:300]})
                body = json.dumps({'decision': 'pause', 'reason': 'bridge error'}).encode();self.send_response(500)
            self.send_header('Content-Type', 'application/json');self.send_header('Content-Length', str(len(body)))
            self.end_headers();self.wfile.write(body)
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(bridge, port, token):
    make_server(bridge, port, token).serve_forever()


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
