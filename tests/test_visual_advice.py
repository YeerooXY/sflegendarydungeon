"""Visible-state regressions for the reported door and blessing decisions."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from sfld_rl.visual_advice import annotate, correct_action, escape_chance, trial_route


def effect(kind, magnitude, remaining, clock='room', starts_at=29):
    return dict(kind=kind, magnitude=magnitude, remaining=remaining, clock=clock, starts_at=starts_at)


def action(kind, index=0):
    return dict(kind=kind, index=index, value=0)


def shop():
    return dict(phase='shop', room=30, turn=29, hp=.41, keys=2, gems=[],
                blessings=[effect('recovery', .1, 2)], curses=[],
                scenario=dict(escape_chance=.4, time_traveler_elixir_full_heal=True),
                offers=[dict(keys=1, effect=effect('recovery', .1, 3)), dict(keys=2, effect=effect('lockpick', 1, 4, 'door'))],
                legal_actions=[action('buy'), action('buy', 1), action('skip'), action('reroll')])


class VisibleAdviceTests(unittest.TestCase):
    def test_arena_pair_goes_on_and_collects_the_prize(self):
        o=shop();o.update(phase='doors',room=51,turn=50,gems=['rabbit','moonstone'],
            blessings=[],trial_depth=0,doors=[dict(kind='trial',trap=False,cursed_trap=False),dict(kind='monster',trap=False,cursed_trap=False)],
            legal_actions=[action('choose_door'),action('choose_door',1)])
        self.assertEqual(correct_action(o,action('choose_door',1))[0],action('choose_door'))
        o.update(phase='encounter',encounter='trial_monster',legal_actions=[action('fight'),action('flee')])
        self.assertEqual(correct_action(o,action('fight'))[0],action('flee'))
        o.update(encounter='prize',legal_actions=[action('interact'),action('skip')]);o.pop('trial_advice',None)
        self.assertEqual(correct_action(o,action('skip'))[0],action('interact'))

    def test_arena_checks_duration_pending_curses_and_poison_exit(self):
        o=shop();o.update(room=51,turn=50,gems=[],trial_depth=0,blessings=[effect('escape',.8,5,starts_at=50)],curses=[])
        self.assertTrue(trial_route(o)['safe'])
        o['blessings'][0]['remaining']=4
        self.assertFalse(trial_route(o)['safe'])
        o['gems']=['rabbit','moonstone'];o['blessings']=[]
        o['curses']=[effect('clumsy',.4,3,starts_at=52)]
        self.assertFalse(trial_route(o)['safe'])
        o['curses']=[effect('poison',.05,6,starts_at=50)];o['hp']=.3
        self.assertFalse(trial_route(o)['safe'])  # The sixth tick is the prize exit.
        o['blessings']=[effect('recovery',.1,6,starts_at=50)]
        self.assertTrue(trial_route(o)['safe'])
        o['gems'].append('pearl')
        self.assertFalse(trial_route(o)['safe'])

    def test_refreshing_one_weak_tick_is_twenty_percent_extra_not_zero(self):
        o=shop();o['blessings'][0]['remaining']=1;annotate(o)
        self.assertAlmostEqual(o['offer_analysis'][0]['extra_healing'],.2)
        self.assertEqual(o['offer_analysis'][0]['extra_uses'],2)

    def test_last_key_capped_refresh_is_skipped_only_in_supported_context(self):
        o=shop();o.update(room=60,hp=.95,keys=1,gems=['rabbit','moonstone'])
        o['blessings'][0]['remaining']=1
        o['offers'][1]=dict(keys=4,effect=effect('one_hit',1,8))
        o['legal_actions'].remove(action('buy',1))
        choice,reason,rule=correct_action(o,action('buy'))
        self.assertEqual(choice,action('skip'));self.assertEqual(rule,'shop_save_last_key')
        self.assertIn('last key',reason)
        for changes in (dict(hp=.6),dict(keys=4),dict(gems=['hero','greed']),dict(room=85)):
            other=copy.deepcopy(o);other.update(changes);other.pop('combat_advice',None)
            self.assertEqual(correct_action(other,action('buy'))[0],action('buy'))

    def test_already_paid_hungry_chest_is_collected_even_with_poison(self):
        o=shop();o.update(phase='encounter',encounter='sated_chest',hp=.03,keys=0,
            blessings=[],curses=[effect('poison',.05,5)],
            legal_actions=[action('interact'),action('skip')])
        chosen,reason,rule=correct_action(o,action('skip'))
        self.assertEqual(chosen,action('interact'));self.assertEqual(rule,'hungry_chest_reward')
        self.assertIn('already paid',reason)
        # A mimic, cursed chest or sacrifice chest is a different risk decision.
        for encounter in ('mimic','cursed_chest','sacrifice_chest','crate','barrel'):
            other=copy.deepcopy(o);other['encounter']=encounter
            self.assertEqual(correct_action(other,action('skip'))[0],action('skip'))
        o['legal_actions']=[action('skip')]
        self.assertEqual(correct_action(o,action('skip'))[0],action('skip'))

    def test_traveler_cheap_full_heal_replaces_reroll_only(self):
        o=shop();o['gems']=['time_traveler'];o['hp']=.5
        o['offers'][0]=dict(keys=1,effect=effect('elixir',.25,1))
        chosen,reason,rule=correct_action(o,action('reroll'))
        self.assertEqual(chosen,action('buy'));self.assertEqual(rule,'shop_traveler_elixir')
        self.assertIn('one key',reason)
        self.assertEqual(correct_action(o,action('buy',1))[0],action('buy',1))
        for hp,cost,enabled,affordable in ((.51,1,True,True),(.5,3,True,True),(.5,1,False,True),(.5,1,True,False)):
            c=copy.deepcopy(o);c['hp']=hp;c['offers'][0]['keys']=cost
            c['scenario']['time_traveler_elixir_full_heal']=enabled
            if not affordable:c['legal_actions'].remove(action('buy'))
            self.assertEqual(correct_action(c,action('reroll'))[0],action('reroll'))

    def test_recovery_values_only_extra_tick_and_does_not_guard_refresh(self):
        o=shop();annotate(o);value=o['offer_analysis'][0]
        self.assertAlmostEqual(value['extra_healing'], .1)
        self.assertEqual(value['acquired']['starts_at'], o['turn'])
        self.assertEqual(value['extra_uses'], 1)
        self.assertEqual(value['status'], 'small_refresh')
        self.assertFalse(value['protect_reroll'])
        chosen, reason, rule=correct_action(o, action('buy'))
        self.assertEqual(chosen, action('buy', 1))
        self.assertEqual(rule, 'shop_small_refresh')
        self.assertIn('10% potential healing', reason)

    def test_no_other_useful_offer_saves_keys_without_spending_mushrooms(self):
        o=shop();o['offers'][1]['effect']=effect('raider', 1, 5)
        self.assertEqual(correct_action(o, action('buy'))[0], action('skip'))

    def test_healing_precedes_poison_for_purchase_analysis(self):
        o=shop();o['hp']=.04;o['blessings']=[];o['curses']=[effect('poison', .05, 5)]
        annotate(o)
        self.assertNotEqual(o['offer_analysis'][0]['status'], 'lethal_exit')
        self.assertEqual(correct_action(o, action('buy'))[0], action('buy'))

    def test_new_blessing_reports_evicted_oldest_and_lost_heal(self):
        o=shop();o['blessings'] += [effect('escape', .8, 5), effect('one_hit', 1, 8)]
        annotate(o);value=o['offer_analysis'][1]
        self.assertEqual(value['displaced']['kind'], 'recovery')
        self.assertAlmostEqual(value['immediate_healing_lost'], .1)
        self.assertFalse(value['protect_reroll'])

    def test_traveler_and_rusty_are_included_in_incremental_healing(self):
        o=shop();o['gems']=['time_traveler','rusty'];annotate(o)
        value=o['offer_analysis'][0]
        self.assertEqual(value['acquired']['remaining'],4)
        self.assertAlmostEqual(value['extra_healing'],.28)
        self.assertTrue(value['protect_reroll'])

    def test_weaker_recovery_is_not_an_upgrade(self):
        o=shop();o['blessings']=[effect('recovery',.2,5)];annotate(o)
        self.assertEqual(o['offer_analysis'][0]['status'],'no_gain')
        self.assertLess(o['offer_analysis'][0]['extra_healing'],0)

    def test_safe_mystery_beats_resource_door_but_not_with_curse_or_trap(self):
        o=shop();o.update(phase='doors',blessings=[effect('escape',.8,2)],curses=[effect('gold_hangover',.5,3)],
            doors=[dict(kind='mystery',key_cost=0,trap=False,cursed_trap=False),dict(kind='metal',key_cost=0,trap=False,cursed_trap=False)],
            legal_actions=[action('choose_door'),action('choose_door',1)])
        chosen,reason,rule=correct_action(o,action('choose_door',1))
        self.assertEqual(chosen,action('choose_door'));self.assertEqual(rule,'safe_free_door')
        for mutation in ('clumsy','poison','trap','pending','illegal','pearl'):
            changed=copy.deepcopy(o);changed.pop('combat_advice',None)
            if mutation in ('clumsy','poison'):changed['curses']=[effect(mutation,.8 if mutation=='clumsy' else .05,3)]
            if mutation=='trap':changed['doors'][0]['trap']=True
            if mutation=='pending':changed['blessings'][0]['starts_at']=30
            if mutation=='illegal':changed['legal_actions']=changed['legal_actions'][1:]
            if mutation=='pearl':changed['gems']=['pearl']
            self.assertEqual(correct_action(changed,action('choose_door',1))[0],action('choose_door',1),mutation)

    def test_escape_clamps_and_ignores_expired_effects(self):
        o=shop();o['gems']=['rabbit','moonstone'];self.assertAlmostEqual(escape_chance(o),1)
        o['curses']=[effect('clumsy',.8,2)];self.assertAlmostEqual(escape_chance(o),.2)
        o['curses'][0]['remaining']=0;self.assertAlmostEqual(escape_chance(o),1)


if __name__=='__main__':unittest.main()
