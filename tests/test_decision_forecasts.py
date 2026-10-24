"""Native public forecasts and the reported practical decision corrections."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from visual_lab import Lab, default_engine
from sfld_rl.visual_advice import correct_action


def action(kind, index=0):
    return dict(kind=kind, index=index, value=0)


from engine_build import MISSING  # noqa: E402


@unittest.skipUnless(default_engine().is_file(), MISSING)
class DecisionForecastTests(unittest.TestCase):
    def setUp(self):
        self.lab = Lab(default_engine(), ROOT / 'profiles/synthetic.profile')

    def tearDown(self):
        self.lab.close()

    def observation(self, *, room=35, hp=100, seed=42, **extra):
        fields = dict(schema=1, room=room, run_number=2, phase='doors', hp_percent=hp,
                      keys=5, doors='monster,mystery', gems='greed')
        fields.update(extra)
        if fields.get('gems') == 'none': fields['gems'] = 'greed'
        if fields['phase'] != 'doors': fields.pop('doors', None)
        state = ''.join(f'{k}={v}\n' for k, v in fields.items())
        result = self.lab.create(dict(initial_state=state, seed=seed, run_number=2,
                                     budget=0, neural_adviser=False))
        self.lab.sessions.pop(result['session']).close()
        return result['observation']

    def test_monster_odds_follow_pair_context_and_deceit(self):
        for doors, gems, expected in [('monster,mystery', 'none', 0),
                ('locked,mystery', 'none', .8), ('mystery,mystery', 'none', .468),
                ('monster,locked', 'none', 0), ('monster,locked', 'deceit', .2),
                ('monster,blessing', 'none', 1), ('monster,destiny', 'none', .4)]:
            with self.subTest(doors=doors, gems=gems):
                o = self.observation(doors=doors, gems=gems)
                self.assertAlmostEqual(o['door_forecast'][1]['monster_chance'], expected)
        o = self.observation(doors='monster,locked')
        self.assertAlmostEqual(o['door_forecast'][1]['mimic_chance'], .045)

    def test_forecasts_are_seed_independent_and_show_entry_hazards(self):
        a = self.observation(seed=101, doors='destiny,monster:trap')['door_forecast']
        b = self.observation(seed=999, doors='destiny,monster:trap')['door_forecast']
        self.assertEqual(a, b)
        self.assertAlmostEqual(a[0]['entry_curse_chance'], 1/6)
        self.assertAlmostEqual(a[1]['entry_damage'], .1)
        o = self.observation(doors='cursed,monster:trap', gems='gambler')
        self.assertEqual(o['door_forecast'][0]['entry_curse_chance'], 1)
        self.assertEqual(o['door_forecast'][1]['entry_curse_chance'], 1)
        self.assertEqual(o['door_forecast'][1]['entry_damage'], 0)
        o = self.observation(doors='sacrifice,monster:trap', blessings='disarm:weak:4')
        self.assertAlmostEqual(o['door_forecast'][0]['entry_damage'], .12)
        self.assertEqual(o['door_forecast'][1]['entry_damage'], 0)

    def test_full_health_mimic_uses_recovery_but_respects_lethal_hit(self):
        o = self.observation(phase='encounter', encounter='mimic', doors='none',
                             blessings='recovery:weak:2,lockpick:weak:2,escape:weak:5')
        chosen, _, rule = correct_action(o, action('skip'))
        self.assertEqual(chosen, action('fight'))
        self.assertEqual(rule, 'recovery_mimic')
        for change in (dict(hp=.1), dict(blessings=[]), dict(room=60),
                       dict(curses=[dict(kind='poison',magnitude=.05,remaining=3,clock='room',starts_at=0)])):
            c = copy.deepcopy(o); c.update(change)
            self.assertEqual(correct_action(c, action('skip'))[0], action('skip'))

    def test_last_heal_tick_and_lockpick_take_the_lower_monster_route(self):
        o = self.observation(hp=10, doors='monster,locked',
                             blessings='recovery:strong:1,lockpick:weak:2', keys=0)
        chosen, _, rule = correct_action(o, action('choose_door'))
        self.assertEqual(chosen, action('choose_door', 1))
        self.assertEqual(rule, 'lockpick_last_recovery')
        for changes in (dict(hp=1), dict(blessings=[])):
            c = copy.deepcopy(o); c.update(changes)
            self.assertEqual(correct_action(c, action('choose_door'))[0], action('choose_door'))
        c = self.observation(hp=10, doors='monster,locked:trap',
                             blessings='recovery:strong:1,lockpick:weak:2', keys=0)
        self.assertEqual(correct_action(c, action('choose_door'))[0], action('choose_door'))

    def test_guaranteed_escape_prefers_plain_route_over_curse_roll(self):
        for risk in ('cursed', 'destiny'):
            o = self.observation(doors=f'{risk},monster', blessings='escape:weak:5')
            self.assertEqual(correct_action(o, action('choose_door'))[0], action('choose_door',1))
            for changes in (dict(blessings=[]), dict(gems=['pearl'])):
                c = copy.deepcopy(o);c.update(changes)
                self.assertEqual(correct_action(c, action('choose_door'))[0], action('choose_door'))
        o = self.observation(hp=5, doors='destiny,monster:trap', blessings='escape:weak:5')
        self.assertEqual(correct_action(o, action('choose_door'))[0], action('choose_door'))

    def test_fatal_poison_uses_free_lockpick_for_healing_loot(self):
        for doors, chosen, proposed in [('locked,mystery', 0, 1), ('monster,double_locked', 1, 0)]:
            o = self.observation(room=55, hp=4.327293841, keys=0, doors=doors,
                                 gems='moonstone,greed', blessings='lockpick:weak:2', curses='poison:weak:5')
            actual, _, rule = correct_action(o, action('choose_door', proposed))
            self.assertEqual(actual, action('choose_door', chosen))
            self.assertEqual(rule, 'lockpick_poison_rescue')

    def test_survivable_trapped_mystery_beats_risky_combat(self):
        for doors, chosen in [('monster,mystery:trap', 1), ('mystery:trap,monster', 0)]:
            o=self.observation(room=27,hp=11.994528179,keys=9,gems='moonstone',doors=doors,
                               blessings='recovery:weak:2')
            actual,_,rule=correct_action(o,action('choose_door',1-chosen))
            self.assertEqual(actual,action('choose_door',chosen))
            self.assertEqual(rule,'survivable_noncombat_route')
        for fields in (dict(hp=10),dict(hp=35),dict(curses='poison:strong:5',blessings='none'),
                       dict(gems='gambler'),dict(blessings='escape:weak:5'),dict(blessings='one_hit:weak:4')):
            args=dict(room=27,hp=12,doors='monster,mystery:trap',gems='moonstone',blessings='recovery:weak:2')
            args.update(fields)
            with self.subTest(fields=fields):
                self.assertEqual(correct_action(self.observation(**args),action('choose_door'))[0],action('choose_door'))

    def test_poison_rescue_respects_hazards_effects_and_other_options(self):
        for fields in (
                dict(doors='locked:trap,mystery'), dict(doors='cursed,mystery'),
                dict(doors='epic,mystery'), dict(doors='locked,golden'),
                dict(doors='locked,blessing'), dict(doors='locked,destiny'),
                dict(blessings='none'), dict(blessings='lockpick:weak:2:1'),
                dict(curses='none'), dict(curses='poison:weak:5:1'),
                dict(hp=25), dict(blessings='lockpick:weak:2,recovery:weak:3'),
                dict(blessings='lockpick:weak:2,one_hit:strong:4')):
            args=dict(room=55,hp=4.327293841,keys=0,doors='locked,mystery',
                      gems='moonstone,greed',blessings='lockpick:weak:2',curses='poison:weak:5')
            args.update(fields)
            with self.subTest(fields=fields):
                o=self.observation(**args)
                self.assertEqual(correct_action(o,action('choose_door',1))[0],action('choose_door',1))
        o=self.observation(room=55,hp=4.327293841,keys=1,doors='locked,mystery',
                           gems='moonstone,greed',blessings='lockpick:weak:2',curses='poison:weak:5')
        # Do not assume an observed nonzero door price has been waived.
        o['doors'][0]['key_cost']=1
        self.assertEqual(correct_action(o,action('choose_door',1))[0],action('choose_door',1))


if __name__ == '__main__':
    unittest.main()
