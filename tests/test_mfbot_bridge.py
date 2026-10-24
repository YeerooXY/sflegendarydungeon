"""MFBot bridge: synthetic packets only, no network or game commands."""
from pathlib import Path
import sys
import json
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'));sys.path.insert(0, str(ROOT/'tests'))
from sfld_live.mfbot_bridge import Bridge  # noqa: E402
from sfld_study.schema import StudyError  # noqa: E402
from test_study_automatic import packet  # noqa: E402


def mfbot_fields(**extra):
    """MFBot's view: main save under "iadungeon", level from its own parser."""
    p = packet(**extra);p['iadungeon'] = p.pop('iadungeonsave');p.pop('ownplayersavecharacter');return p


class FakePolicy:
    def __init__(self, pick=None):
        self.calls = [];self.pick = pick

    def __call__(self, visible, memory):
        self.calls.append((visible, memory))
        if visible['status'] == 101 and self.pick is None:raise StudyError('recovery')
        pick = visible['legal_actions'][0] if self.pick is None else dict(visible['recovery_menu'])[self.pick]
        return {'action': pick, 'memory': {'room': visible['room'], 'reason': 'learned'}}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory();self.policy = FakePolicy()
        self.bridge = Bridge(self.dir.name, choose=self.policy)

    def tearDown(self):
        self.dir.cleanup()

    def decide(self, **extra):
        return self.bridge.decide({'character': 'Hero@s1', 'level': 120, 'fields': mfbot_fields(**extra)})

    def test_alias_budget_and_confirm_commits_memory(self):
        reply = self.decide()
        self.assertEqual(reply['decision'], 'command');self.assertEqual(reply['req'], 'IADungeonInteract')
        visible, memory = self.policy.calls[0]
        self.assertEqual(visible['budget_remaining'], 0);self.assertEqual(visible['level'], 120)
        self.assertEqual(visible['run_number_event'], 1);self.assertIsNone(memory)
        self.assertGreaterEqual(visible['elapsed_seconds'], 100)
        self.assertTrue(self.bridge.confirm({'character': 'Hero@s1', 'id': reply['id'], 'ok': True})['committed'])
        self.decide(status=10, room=1, types=(100, 0, 0), payloads=(0, 0, 0))
        self.assertEqual(self.policy.calls[1][1], {'room': 1, 'reason': 'learned'})

    def test_confirmed_choice_is_logged_with_the_room_behind_it(self):
        reply = self.decide()
        self.bridge.confirm({'character': 'Hero@s1', 'id': reply['id'], 'ok': True})
        self.decide(status=10, room=1, types=(100, 0, 0), payloads=(400, 0, 0))
        rows = [json.loads(l) for l in (Path(self.dir.name)/'choices.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 1);row = rows[0]
        before, after = row['before']['visible'], row['after']['visible']
        self.assertEqual([d['kind'] for d in before['doors']], ['monster', 'mystery'])
        self.assertEqual(row['action'], before['legal_actions'][0])
        self.assertEqual((after['status'], after['encounter_type'], after['encounter_variant']), (10, 100, 400))
        self.assertEqual((before['stage'], before['gems'], row['run_number_event'], row['level']), (1, [], 1, 120))
        self.assertIsNotNone(row['event_start']);self.assertEqual(row['id'], reply['id'])
        self.assertIn('iadungeon', row['before']['fields']);self.assertIn('iadungeonsoulstones', row['after']['fields'])
        # An unconfirmed command logs no choice.
        self.decide()
        self.assertEqual(len((Path(self.dir.name)/'choices.jsonl').read_text(encoding='utf-8').splitlines()), 1)

    def test_refused_screens_pause_without_touching_memory(self):
        self.assertEqual(self.decide(status=101)['decision'], 'pause')
        self.assertEqual(self.decide(status=10, room=1, types=(318, 0, 0))['decision'], 'pause')

    def test_unconfirmed_or_rejected_commands_block_the_run(self):
        first = self.decide()
        second = self.decide()
        self.assertEqual(second['decision'], 'pause');self.assertIn('unconfirmed', second['reason'])
        self.assertFalse(self.bridge.confirm({'character': 'Hero@s1', 'id': first['id'], 'ok': True})['committed'])
        other = Bridge(Path(self.dir.name)/'b', choose=FakePolicy())
        reply = other.decide({'character': 'X@s1', 'level': 120, 'fields': mfbot_fields()})
        other.confirm({'character': 'X@s1', 'id': reply['id'], 'ok': False, 'error': 'ERR'})
        self.assertIn('rejected', other.decide({'character': 'X@s1', 'level': 120, 'fields': mfbot_fields()})['reason'])

    def test_low_level_entry_falls_back(self):
        reply = self.bridge.decide({'character': 'Low@s1', 'level': 49, 'fields': mfbot_fields(status=0)})
        self.assertEqual(reply['decision'], 'pause')


def healing(room, hours, **extra):
    """A death in `room` (save word 17), healing for `hours`."""
    fields = mfbot_fields(status=101, room=room, **extra)
    now = int(fields['timestamp']);status = ['0']*23;status[22] = str(now-int(hours*3600))
    fields['characterstatus'] = '/'.join(status);return fields


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def decide(self, policy, fields):
        return Bridge(self.dir.name, choose=policy).decide({'character': 'H@s', 'level': 200, 'fields': fields})

    def test_menu_follows_regenerated_hp(self):
        policy = FakePolicy('wait_hp_40');reply = self.decide(policy, healing(48, 2.4))
        v = policy.calls[0][0];menu = dict(v['recovery_menu'])
        self.assertEqual(v['screen_kind'], 'recovery');self.assertEqual(v['room'], 49);self.assertEqual(v['resume_phase'], 'doors')
        self.assertAlmostEqual(v['hp_current']/v['hp_max'], .1, places=3)
        self.assertNotIn('reenter', menu);self.assertNotIn('wait_boss_safe', menu)
        self.assertEqual(menu['wait_hp_20']['seconds'], 8640);self.assertEqual(menu['wait_hp_100']['seconds'], 77760)
        self.assertEqual(reply, {'decision': 'wait', 'seconds': 25920, 'why': 'learned',
                                 'label': 'Wait 7 h 12 min for HP to regenerate'})
        self.assertEqual(json.loads((Path(self.dir.name)/'H_s.json').read_text())['memory']['room'], 49)

    def test_reentry_and_boss_retry(self):
        policy = FakePolicy('reenter');reply = self.decide(policy, healing(50, 5))
        v = policy.calls[0][0];menu = dict(v['recovery_menu'])
        self.assertEqual(v['room'], 50);self.assertEqual(v['resume_phase'], 'encounter')
        # Boss 2 hits at most 28.05%: wait from 5/24 HP to 28.15%.
        self.assertEqual(menu['wait_boss_safe']['seconds'], int((.2815-5/24)*86400+.999))
        self.assertEqual(reply['req'], 'IADungeonStart');self.assertEqual(reply['params'], '8/0')

    def test_missing_healing_start_falls_back(self):
        fields = healing(48, 1);fields.pop('characterstatus')
        self.assertEqual(self.decide(FakePolicy('reenter'), fields)['decision'], 'pause')


class SpecialRoomAndRunTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory();self.policy = FakePolicy()
        self.bridge = Bridge(self.dir.name, choose=self.policy)

    def tearDown(self):
        self.dir.cleanup()

    def decide(self, **extra):
        return self.bridge.decide({'character': 'R@s', 'level': 200, 'fields': mfbot_fields(**extra)})

    def test_special_rooms_name_their_simulator_actions(self):
        first = self.decide(status=10, room=7, types=(310, 0, 0))
        self.bridge.confirm({'character': 'R@s', 'id': first['id'], 'ok': True})
        v = self.policy.calls[-1][0]
        self.assertEqual(v['sim_encounter'], 'undead');self.assertFalse(v['bookkeeping'])
        self.assertEqual([(n, a['code']) for n, a in v['policy_menu']], [('fight', 50), ('flee', 51)])
        self.decide(status=10, room=7, types=(308, 0, 0))
        codes = [a['code'] for _, a in self.policy.calls[-1][0]['policy_menu']]
        self.assertIn(codes[0], (90, 91, 92));self.assertEqual(codes[1], 51)

    def test_unverified_rooms_take_a_fixed_action(self):
        reply = self.decide(status=10, room=7, types=(317, 0, 0))
        v = self.policy.calls[-1][0]
        self.assertTrue(v['bookkeeping']);self.assertEqual(reply['params'], '51')

    def test_item_pickup_is_left_to_mfbot(self):
        self.assertEqual(self.decide(status=10, room=7, types=(900, 0, 0))['decision'], 'native')

    def test_completed_run_starts_the_next(self):
        reply = self.decide();self.bridge.confirm({'character': 'R@s', 'id': reply['id'], 'ok': True})
        done = self.decide(status=102, room=100)
        self.assertEqual(done['req'], 'IADungeonStart');self.bridge.confirm({'character': 'R@s', 'id': done['id'], 'ok': True})
        self.decide()
        visible, memory = self.policy.calls[-1]
        self.assertEqual(visible['run_number_event'], 2);self.assertIsNone(memory)


if __name__ == '__main__':
    unittest.main()


class LiveAdviserTests(unittest.TestCase):
    """Desktop mode: the real adviser, joined mid-run, follows commands the client sent."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.bridge = Bridge(self.dir.name, join_mid_run=True)

    def tearDown(self):
        self.dir.cleanup()

    def decide(self, **extra):
        return self.bridge.decide({'character': 'acct-1', 'level': 120, 'fields': mfbot_fields(**extra)})

    def sent(self, req, params):
        return self.bridge.sent({'character': 'acct-1', 'req': req, 'params': params})

    def test_joins_a_run_in_progress_and_names_the_door(self):
        reply = self.decide(room=9)
        self.assertEqual(reply['decision'], 'command')
        self.assertRegex(reply['label'], r'^(Left door \(monster\)|Right door \(mystery\))$')

    def test_advised_and_own_choices_update_the_history(self):
        reply = self.decide(room=9)
        self.assertEqual(self.sent(reply['req'], reply['params'])['outcome'], 'advised')
        opened = self.decide(status=10, room=10, types=(100, 0, 0), payloads=(400, 0, 0))
        self.assertEqual(self.sent(opened['req'], opened['params'])['outcome'], 'advised')
        rows = [json.loads(l) for l in (Path(self.dir.name)/'choices.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 1);self.assertNotIn('advised', rows[0])
        # The other door: still a legal command, recorded as the player's own choice.
        reply = self.decide(room=10)
        other = '2' if reply['params'] == '1' else '1'
        self.assertEqual(self.sent('IADungeonInteract', other)['outcome'], 'own_choice')
        self.decide(status=10, room=11, types=(1, 0, 0), payloads=(0, 0, 0))
        row = json.loads((Path(self.dir.name)/'choices.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(row['action']['code'], int(other));self.assertEqual(row['advised']['code'], int(reply['params']))

    def test_repeated_screen_repeats_advice_and_an_unreported_change_rejoins(self):
        first = self.decide(room=9)
        self.assertEqual(self.decide(room=9)['id'], first['id'])
        later = self.decide(status=10, room=10, types=(1, 0, 0), payloads=(0, 0, 0))
        self.assertEqual(later['decision'], 'command');self.assertNotEqual(later['id'], first['id'])

    def test_other_requests_are_ignored_and_unknown_commands_reset_history(self):
        self.decide(room=9)
        self.assertEqual(self.sent('IADungeonView', '')['outcome'], 'not an action')
        self.assertEqual(self.sent('IADungeonInteract', '77')['outcome'], 'history_reset')
        self.assertEqual(self.decide(room=10)['decision'], 'command')

    def test_labels(self):
        from sfld_live.mfbot_bridge import describe, duration
        doors = {'screen_kind': 'doors', 'doors': [{'index': 0, 'kind': 'locked', 'trap': 0}, {'index': 1, 'kind': 'mystery', 'trap': 3}]}
        self.assertEqual(describe({'kind': 'interact', 'code': 5}, doors), 'Left door (locked)')
        self.assertEqual(describe({'kind': 'interact', 'code': 10}, doors), 'Right door (mystery, trapped, disarm)')
        self.assertEqual(describe({'kind': 'interact', 'code': 21}, {'screen_kind': 'encounter'}), 'Flee')
        self.assertEqual(describe({'kind': 'choose_gem', 'gem_id': 6}, {}), 'Take the Pendant stone')
        self.assertEqual(describe({'kind': 'buy_blessing', 'effect_id': 3, 'key_price': 2}, {}), 'Buy Escape (2 keys)')
        self.assertEqual(duration(90), '2 min');self.assertEqual(duration(25920), '7 h 12 min')

    def test_targets_name_the_control(self):
        from sfld_live.mfbot_bridge import target
        self.assertEqual(target({'kind': 'interact', 'code': 6}, {'screen_kind': 'doors'}), 'door:1')
        self.assertEqual(target({'kind': 'interact', 'code': 9}, {'screen_kind': 'doors'}), 'door:0:disarm')
        self.assertEqual(target({'kind': 'choose_gem', 'gem_id': 9}, {'gem_offers': [6, 9, 3]}), 'stone:1')
        self.assertEqual(target({'kind': 'buy_blessing', 'effect_id': 3, 'key_price': 1},
                                {'screen_kind': 'shop', 'offers': [{'effect_id': 1}, {'effect_id': 3}]}), 'offer:1')
        self.assertEqual(target({'kind': 'interact', 'code': 51}, {'screen_kind': 'shop'}), 'shop:51')
        self.assertEqual(target({'kind': 'interact', 'code': 50}, {'screen_kind': 'encounter', 'encounter_type': 316,
                                                                    'policy_menu': [1]}), 'special316:50')
        self.assertEqual(target({'kind': 'interact', 'code': 21}, {'screen_kind': 'encounter', 'encounter_type': 1}), 'encounter:21')
        reply = self.decide(room=9)
        self.assertEqual(reply['target'], 'door:' + str(int(reply['params'])-1))
        self.assertEqual(self.sent(reply['req'], reply['params'])['target'], reply['target'])
