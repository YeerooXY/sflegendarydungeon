"""Decision boundaries for the corrected-engine player-idea experiment."""
import json
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
AVAILABLE = all(importlib.util.find_spec(name) for name in ('numpy', 'torch'))
if AVAILABLE:
    import numpy as np
    import train_rl
    from benchmark_player_ideas import paired, transfer_changes, validate
    from sfld_rl.player_ideas import CASES, PlayerIdeas, profile_values


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch dependencies are absent')
class PlayerIdeaTests(unittest.TestCase):
    def state(self, case, phase, action, **values):
        schema = json.loads((ROOT / 'docs/shop-arena-2026-10-11/schema.json').read_text())
        self.names, self.actions = schema['observation_names'], schema['action_names']
        self.rules = PlayerIdeas(self.names, self.actions, CASES[case], profile_values(ROOT / 'profiles/synthetic.profile'))
        self.obs = np.zeros((1, len(self.names)), np.float32)
        self.mask = np.zeros((1, len(self.actions)), bool)
        self.proposed = np.array([self.rules.ai[action]])
        self.set(**{'phase.'+phase: 1, 'hp': .8, 'room_div100': .65, **values})
        self.allow(action)

    def set(self, **values):
        for k, v in values.items(): self.obs[0, self.rules.oi[k]] = v

    def allow(self, *actions):
        for a in actions: self.mask[0, self.rules.ai[a]] = True

    def result(self):
        actions, targets, reasons = self.rules.apply(self.obs, self.mask, self.proposed)
        return self.actions[actions[0]], targets[0], reasons[0]

    def test_control_identity(self):
        self.state('control', 'recovery', 'wait_hp_20', hp=0)
        before = self.obs.copy()
        action, target, reasons = self.result()
        self.assertEqual((action, target), ('wait_hp_20', -1))
        self.assertFalse(reasons.any())
        np.testing.assert_array_equal(before, self.obs)

    def test_exact21_preserves_larger_wait_and_paid_heal(self):
        self.state('reentry21', 'recovery', 'wait_hp_20', hp=0)
        self.assertEqual(self.result()[:2], ('wait_deadline', .21))
        for action in ('wait_hp_40', 'heal_step'):
            self.allow(action); self.proposed[0] = self.rules.ai[action]
            self.assertEqual(self.result()[:2], (action, -1))
        self.set(room_div100=.75)
        self.proposed[0] = self.rules.ai['wait_hp_20']
        self.assertEqual(self.result()[:2], ('wait_hp_20', -1))

    def test_boss_preparation_two_hits_then_one(self):
        self.state('boss_buffer', 'recovery', 'wait_hp_20', hp=0, room_div100=.98)
        self.assertEqual(self.result()[:2], ('wait_deadline', 1.))
        self.set(room_div100=.99, **{'owned_gem.hero': 1})
        self.assertAlmostEqual(self.result()[1], .525+.2571*.8+.001)
        self.set(room_div100=.96)
        self.assertEqual(self.result()[:2], ('wait_hp_20', -1))

    def test_recovery_cannot_save_a_lethal_hit(self):
        self.state('recovery_fights', 'encounter', 'flee', hp=.22, **{
            'encounter.monster': 1, 'blessings.0.kind.recovery': 1,
            'blessings.0.magnitude': .25, 'blessings.0.active': 1})
        self.allow('fight')
        self.assertEqual(self.result()[0], 'flee')
        self.set(hp=.4)
        self.assertEqual(self.result()[0], 'fight')

    def test_guaranteed_escape_respects_clumsy_and_onehit(self):
        self.state('free_escape', 'encounter', 'fight', **{
            'encounter.monster': 1, 'owned_gem.rabbit': 1, 'owned_gem.moonstone': 1})
        self.allow('flee')
        self.assertEqual(self.result()[0], 'flee')
        self.set(**{'curses.0.kind.clumsy': 1, 'curses.0.active': 1, 'curses.0.magnitude': .2})
        self.assertEqual(self.result()[0], 'fight')
        self.set(**{'curses.0.active': 0, 'blessings.0.kind.one_hit': 1,
                    'blessings.0.active': 1, 'blessings.0.magnitude': 1})
        self.assertEqual(self.result()[0], 'fight')

    def test_soldier_search_and_trapped_monster_guard(self):
        self.state('free_escape', 'encounter', 'skip', **{
            'encounter.skeleton': 1, 'blessings.0.kind.escape': 1,
            'blessings.0.magnitude': .8, 'blessings.0.active': 1})
        self.allow('interact')
        self.assertEqual(self.result()[0], 'interact')
        self.state('free_escape', 'doors', 'door_1', **{
            'doors.0.kind.monster': 1, 'doors.0.trap': 1, 'doors.1.key_cost_div6': 1/6,
            'owned_gem.rabbit': 1, 'owned_gem.moonstone': 1})
        self.allow('door_0')
        self.assertEqual(self.result()[0], 'door_1')
        self.set(**{'doors.0.trap': 0})
        self.assertEqual(self.result()[0], 'door_0')

    def test_shop_reserve_only_before_shop_and_safe_fight(self):
        self.state('keys10_shop4', 'doors', 'door_1', keys_div20=.2, room_div100=.6, **{
            'doors.0.kind.monster': 1, 'doors.1.kind.locked': 1, 'doors.1.key_cost_div6': 1/6})
        self.allow('door_0')
        self.assertEqual(self.result()[0], 'door_0')
        self.set(**{'shops_seen.3': 1})
        self.assertEqual(self.result()[0], 'door_1')
        self.set(hp=.1, **{'shops_seen.3': 0})
        self.assertEqual(self.result()[0], 'door_1')

    def test_stone_priority_and_no_pendant_no_override(self):
        for gem in ('rabbit', 'moonstone', 'greed'):
            self.state('stone_priority', 'gems', 'gem_0', **{
                'gem_offers.0.pendant': 1, f'gem_offers.1.{gem}': 1, 'gem_offers.2.bull': 1})
            self.allow('gem_1', 'gem_2')
            self.assertEqual(self.result()[0], 'gem_1')
        self.set(**{'gem_offers.0.pendant': 0, 'gem_offers.0.hero': 1})
        self.assertEqual(self.result()[0], 'gem_0')


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch dependencies are absent')
class StudyAccountingTests(unittest.TestCase):
    def test_transfer_requires_opt_in_and_identical_features(self):
        current = {'schema': {'engine': '0.6.9', 'observation_names': ['hp'], 'action_names': ['fight']},
                   'schema_sha256': 'new-schema', 'library_sha256': 'new-library',
                   'profiles': [{'sha256': 'same-profile'}], 'deadline_hours': 168, 'max_actions': 100000}
        old = train_rl.compact_metadata(current)
        old['schema'] = {**old['schema'], 'engine': '0.6.7'}
        old['library_sha256'] = 'old-library'; old['schema_sha256'] = 'old-schema'
        with self.assertRaisesRegex(RuntimeError, 'Explicit'): transfer_changes(old,current,False)
        self.assertEqual(set(transfer_changes(old,current,True)), {'schema','schema_sha256','library_sha256'})
        current['schema']['observation_names'] = ['hidden_rng']
        with self.assertRaisesRegex(RuntimeError, 'schema'): transfer_changes(old,current,True)

    def test_pairing_rejects_reordered_or_missing_events(self):
        with self.assertRaisesRegex(RuntimeError, 'Mismatched'):
            paired([{'event_index': 0}, {'event_index': 1}], [{'event_index': 1}, {'event_index': 0}])

    def test_accounting_rejects_overspend_and_truncation(self):
        row = {'event_index': 0, 'case': 'control', 'budget': 20, 'terminated': True,
               'truncated': False, 'invalid_actions': 0, 'actions': 100, 'decisions': 100,
               'mushrooms': 20, 'recovery_mushrooms': 15, 'reroll_mushrooms': 5, 'elapsed_hours': 168}
        validate([row],1,20,'control')
        for change in ({'mushrooms': 21}, {'truncated': True}, {'event_index': 1}, {'decisions': 99}):
            with self.assertRaises(RuntimeError): validate([{**row,**change}],1,20,'control')


if __name__ == '__main__': unittest.main()
