"""Policy boundaries that can materially change the spending/reward comparison."""
import sys
import unittest
from pathlib import Path
import importlib.util
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
AVAILABLE = all(importlib.util.find_spec(n) for n in ('numpy', 'torch'))
if AVAILABLE:
    import numpy as np
    from sfld_rl.shop_experiments import Policy, Rules
    from benchmark_rl_shop_arena import paired


@unittest.skipUnless(AVAILABLE, 'Optional training dependencies are absent')
class ShopArenaRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema = json.loads((ROOT / 'docs/shop-arena-2026-10-11/schema.json').read_text())
        cls.names, cls.actions = schema['observation_names'], schema['action_names']

    def setup_state(self, policy, phase='shop', action='skip', **features):
        self.rules = Rules(self.names, self.actions, policy, [.1962, .2805, .2805, .525])
        self.obs = np.zeros((1, len(self.names)), np.float32)
        self.masks = np.zeros((1, len(self.actions)), bool)
        self.actions_in = np.array([self.rules.ai[action]])
        self.set(**{'phase.' + phase: 1, 'hp': .8, 'keys_div20': .2, 'room_div100': .65, **features})
        self.allow(action)

    def set(self, **fields):
        for key, value in fields.items():
            self.obs[0, self.rules.oi[key]] = value

    def allow(self, *actions):
        for action in actions:
            self.masks[0, self.rules.ai[action]] = True

    def offer(self, side, kind, *, rooms=4, magnitude=1):
        self.set(**{f'offers.{side}.effect.kind.{kind}': 1,
                    f'offers.{side}.effect.remaining_div10': rooms / 10,
                    f'offers.{side}.effect.magnitude': magnitude})
        self.allow(f'buy_{side}')

    def result(self):
        result, reasons = self.rules.apply(self.obs, self.masks, self.actions_in)
        return self.actions[int(result[0])], int(reasons[0])

    def test_baseline_is_identity_and_inputs_unchanged(self):
        self.setup_state(Policy('baseline'), action='buy_1')
        self.offer(0, 'one_hit', rooms=8); self.offer(1, 'raider')
        obs, masks = self.obs.copy(), self.masks.copy()
        self.assertEqual(self.result(), ('buy_1', 0))
        np.testing.assert_array_equal(obs, self.obs)
        np.testing.assert_array_equal(masks, self.masks)

    def test_opening_shop_is_free_one_hit_even_for_strong_only(self):
        self.setup_state(Policy('strong', shop='strong_pair'), action='buy_1', first_event_run=1, room_div100=.05, keys_div20=0)
        self.offer(0, 'one_hit', rooms=4); self.offer(1, 'escape', rooms=5)
        self.assertEqual(self.result()[0], 'buy_0')

    def test_elixir_fallback_and_gold_rejection_without_infinite_search(self):
        self.setup_state(Policy('first', shop='first_useful'), action='buy_0')
        self.offer(0, 'elixir', magnitude=.5); self.offer(1, 'raider')
        self.allow('reroll', 'skip')
        self.assertEqual(self.result()[0], 'reroll')
        self.set(keys_div20=.15)
        self.assertEqual(self.result()[0], 'buy_0')
        self.actions_in[0] = self.rules.ai['buy_1']
        self.assertEqual(self.result()[0], 'skip')

    def test_health_gate_changes_one_hit_target(self):
        self.setup_state(Policy('eight', shop='healthy_one_hit8'), hp=.6)
        self.offer(0, 'one_hit', rooms=4); self.offer(1, 'recovery', magnitude=.1, rooms=3)
        self.allow('reroll')
        self.assertEqual(self.result()[0], 'reroll')
        self.set(hp=.4)
        self.assertEqual(self.result()[0], 'buy_1')

    def test_greed_admits_strong_lockpick_and_preserves_preparatory_heal(self):
        self.setup_state(Policy('greed', shop='strong_pair_greed'), hp=.4)
        self.offer(0, 'lockpick', rooms=4); self.offer(1, 'raider')
        self.allow('reroll')
        self.assertEqual(self.result()[0], 'reroll')
        self.set(**{'owned_gem.greed': 1})
        self.assertEqual(self.result()[0], 'buy_0')
        self.allow('heal_full'); self.actions_in[0] = self.rules.ai['heal_full']
        self.assertEqual(self.result()[0], 'heal_full')

    def test_greasy_uses_price_through_deaths_but_never_boss_override(self):
        self.setup_state(Policy('greasy', greasy_price_cap=15), phase='recovery', action='wait_hp_20', hp=0,
                         **{'owned_gem.greasy': 1, 'heal_step_price_div100': .15, 'room_div100': .45})
        self.allow('heal_step')
        self.assertEqual(self.result()[0], 'heal_step')
        self.set(heal_step_price_div100=.20)
        self.assertEqual(self.result()[0], 'wait_hp_20')
        self.set(heal_step_price_div100=.10, room_div100=.50)
        self.assertEqual(self.result()[0], 'wait_hp_20')
        self.set(room_div100=.45, hp=.2); self.allow('reenter')
        self.assertEqual(self.result()[0], 'reenter')

    def test_arena_protection_is_separate_from_always_flee(self):
        for mode, expected in [('flee', 'flee'), ('protected', 'fight')]:
            self.setup_state(Policy(mode, arena=mode), phase='encounter', action='flee',
                             **{'encounter.trial_monster': 1, 'blessings.0.kind.one_hit': 1, 'blessings.0.active': 1})
            self.allow('fight')
            self.assertEqual(self.result()[0], expected)

    def test_preparation_respects_traps_and_visible_golden_alternative(self):
        self.setup_state(Policy('prep', prepare_arena=True), phase='doors', action='door_1', hp=.3,
                         **{'room_div100': .47, 'doors.0.kind.monster': 1, 'doors.0.trap': 1,
                            'doors.1.kind.locked': 1, 'blessings.0.kind.one_hit': 1, 'blessings.0.active': 1})
        self.allow('door_0')
        self.assertEqual(self.result()[0], 'door_1')
        self.set(hp=.8)
        self.assertEqual(self.result()[0], 'door_0')
        self.set(**{'doors.1.kind.locked': 0, 'doors.1.kind.golden': 1})
        self.assertEqual(self.result()[0], 'door_1')

    def test_pairing_uses_event_identity_not_finish_order(self):
        a = [dict(event_index=i, completions=i, legendaries=i, expected_legendaries=i, expected_arena_legendaries=0) for i in range(3)]
        b = [dict(r, completions=r['completions']+1) for r in reversed(a)]
        self.assertEqual(paired(a, b)['completions']['mean'], 1)
        with self.assertRaises(RuntimeError):
            paired(a, [b[0], b[0], b[1]])

    def test_pendant_priority_protects_escape_stones_and_other_menus(self):
        self.setup_state(Policy('pendant', gems='pendant_priority'), phase='gems', action='gem_1',
                         **{'gem_offers.0.pendant': 1, 'gem_offers.1.greasy': 1,
                            'gem_offers.2.hero': 1})
        self.allow('gem_0', 'gem_2')
        self.assertEqual(self.result(), ('gem_0', 5))
        self.set(**{'gem_offers.2.hero': 0, 'gem_offers.2.moonstone': 1})
        self.assertEqual(self.result(), ('gem_2', 5))
        self.set(**{'gem_offers.1.greasy': 0, 'gem_offers.1.rabbit': 1})
        self.assertEqual(self.result(), ('gem_1', 0))
        self.set(**{'gem_offers.0.pendant': 0, 'gem_offers.0.gambler': 1})
        self.actions_in[0] = self.rules.ai['gem_0']
        self.assertEqual(self.result(), ('gem_0', 0))

    def test_pendant_gate_masks_and_preparatory_healing(self):
        self.setup_state(Policy('pendant', gems='pendant_escape'), phase='gems', action='gem_1',
                         **{'gem_offers.0.pendant': 1, 'gem_offers.1.greasy': 1,
                            'gem_offers.2.hero': 1})
        self.allow('gem_0', 'gem_2', 'heal_full')
        self.assertEqual(self.result(), ('gem_1', 0))
        self.set(**{'owned_gem.rabbit': 1})
        self.assertEqual(self.result(), ('gem_0', 5))
        self.masks[0, self.rules.ai['gem_0']] = False
        self.assertEqual(self.result(), ('gem_1', 0))
        self.masks[0, self.rules.ai['gem_0']] = True
        self.actions_in[0] = self.rules.ai['heal_full']
        self.assertEqual(self.result(), ('heal_full', 0))

    def test_revised_priority_puts_greed_above_pendant(self):
        self.setup_state(Policy('revised', gems='pendant_greed_priority'), phase='gems', action='gem_0',
                         **{'gem_offers.0.pendant': 1, 'gem_offers.1.greed': 1,
                            'gem_offers.2.hero': 1})
        self.allow('gem_1', 'gem_2')
        self.assertEqual(self.result(), ('gem_1', 5))
        self.set(**{'gem_offers.2.hero': 0, 'gem_offers.2.moonstone': 1})
        self.assertEqual(self.result(), ('gem_2', 5))
        self.set(**{'gem_offers.1.greed': 0, 'gem_offers.1.rabbit': 1})
        self.assertEqual(self.result(), ('gem_1', 5))


if __name__ == '__main__':
    unittest.main()
