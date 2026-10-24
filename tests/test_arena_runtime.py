"""Arena safety is checked against real native transitions, not just icons."""
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.visual_advice import annotate,correct_action,trial_route
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values

LIB=library('build-adaptive-stones')
PROFILE=ROOT/'profiles/synthetic.profile'


def state(gems='rabbit,moonstone',hp=60,blessings='none',curses='none'):
    return f'schema=1\nroom=51\nrun_number=2\nphase=doors\nhp_percent={hp}\nkeys=4\ngems={gems}\nshops_seen=2\ndoors=trial,monster\nblessings={blessings}\ncurses={curses}\n'


@unittest.skipUnless(LIB.is_file(), MISSING)
class ArenaRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.env=VectorEnv(LIB,[PROFILE],1,budgets=[100],auto_reset=False)
        self.env.reset();self.lab=EffectLab(self.env)
        self.rule=InteractionPolicy(self.env,profile_values(PROFILE),'base')

    def tearDown(self):self.env.close()

    def restore(self,progress,seed=73):
        with self.lab.fixture(progress,100) as snapshot:self.lab.restore(0,snapshot,seed)

    def step(self,name):
        obs,mask=self.lab.observe();o=self.lab.visible()
        index=int(self.rule.apply(obs,mask,np.array([self.env.action_names.index(name)]))[0][0])
        self.assertTrue(mask[0,index])
        expected=correct_action(o,next(a for a in o['legal_actions'] if
            (a['kind']=='choose_door' and name==f"door_{a['index']}") or a['kind']==name))[0]
        native=self.env.action_names[index]
        self.assertEqual(native,f"door_{expected['index']}" if expected['kind']=='choose_door' else expected['kind'])
        self.env.step(np.array([index]))
        return native,self.lab.visible()

    def test_all_five_levels_and_reward_without_damage(self):
        for seed in (1,22,643,9841):
            with self.subTest(seed=seed):
                self.restore(state(),seed)
                for depth in range(5):
                    chosen,o=self.step('door_1')
                    self.assertEqual(chosen,'door_0');self.assertEqual(o['encounter'],'trial_monster')
                    chosen,o=self.step('fight')
                    self.assertEqual(chosen,'flee');self.assertEqual(o['trial_depth'],depth+1)
                    self.assertEqual(o['hp'],.6);self.assertEqual(o['deaths'],0)
                _,o=self.step('door_1')
                self.assertEqual(o['encounter'],'prize');self.assertEqual(o['pending_trial_reward'],5)
                chosen,o=self.step('skip')
                self.assertEqual(chosen,'interact');self.assertEqual(o['room'],57)
                self.assertEqual(o['pending_trial_reward'],0)

    def test_projected_poison_healing_and_expiry_match_engine(self):
        for progress in (state(hp=4,blessings='recovery:strong:6',curses='poison:weak:5'),
                         state(gems='hero,greed',blessings='escape:weak:5'),
                         state(hp=75,curses='poison:weak:5')):
            with self.subTest(progress=progress):
                self.restore(progress);expected=trial_route(self.lab.visible())
                self.assertTrue(expected['safe'])
                for _ in range(5):self.step('door_1');self.step('fight')
                self.step('door_1');_,final=self.step('skip')
                self.assertEqual(final['deaths'],0)
                self.assertAlmostEqual(final['hp'],expected['hp_after'])

    def test_temporary_escape_curses_and_traps_are_not_called_guaranteed(self):
        for progress in (state(gems='hero,greed',blessings='escape:weak:4'),
                         state(curses='clumsy:weak:3'),state(hp=20,curses='poison:weak:5'),
                         state(gems='rabbit,pearl',blessings='escape:weak:5'),
                         state().replace('trial,monster','trial:trap,monster')):
            self.restore(progress)
            obs,mask=self.lab.observe()
            choice=self.rule.apply(obs,mask,np.array([self.env.action_names.index('door_1')]))[0][0]
            self.assertEqual(self.env.action_names[int(choice)],'door_1')

    def test_last_key_refresh_keeps_the_same_immediate_heal(self):
        progress=state(hp=90,blessings='recovery:weak:1').replace('room=51','room=60').replace('keys=4','keys=1').replace('phase=doors','phase=shop').replace('doors=trial,monster','offers=recovery:weak:3:1,one_hit:strong:8:4')
        self.restore(progress)
        obs,mask=self.lab.observe()
        choice=self.rule.apply(obs,mask,np.array([self.env.action_names.index('buy_0')]))[0][0]
        self.assertEqual(self.env.action_names[int(choice)],'skip')
        self.env.step(np.array([choice]));skipped=self.lab.visible()
        self.assertEqual(skipped['hp'],1);self.assertEqual(skipped['keys'],1)
        self.restore(progress)
        self.env.step(np.array([self.env.action_names.index('buy_0')]));bought=self.lab.visible()
        self.assertEqual(bought['hp'],1);self.assertEqual(bought['keys'],0)
        self.assertEqual(skipped['room'],bought['room'])

    def test_one_hit_mimic_collects_epic_without_extra_damage(self):
        progress=state(blessings='one_hit:weak:1').replace('phase=doors','phase=encounter\nencounter=mimic').replace('doors=trial,monster\n','')
        self.restore(progress)
        before=self.lab.visible();self.step('skip');after=self.lab.visible()
        self.assertEqual(after['epics'],before['epics']+1)
        self.assertEqual(after['hp'],before['hp'])
        self.assertEqual(after['curses'],before['curses'])
        self.restore(progress.replace('one_hit:weak:1','none'))
        self.step('skip');self.assertEqual(self.lab.visible()['epics'],0)

    def test_escape_pair_takes_survivable_damage_trap_over_curse(self):
        progress=state(hp=21).replace('doors=trial,monster','doors=cursed,monster:trap')
        self.restore(progress)
        self.step('door_0');entered=self.lab.visible()
        self.assertEqual(entered['encounter'],'monster')
        self.assertAlmostEqual(entered['hp'],.11)
        self.assertEqual(entered['curses'],[])
        self.step('flee');self.assertAlmostEqual(self.lab.visible()['hp'],.11)

    def test_escape_pair_does_not_force_lethal_or_curse_traps(self):
        fixtures=[state(hp=10),state(hp=15,curses='poison:weak:1'),
                  state(gems='rabbit,moonstone,gambler').replace('room=51','room=80')]
        for progress in fixtures:
            self.restore(progress.replace('doors=trial,monster','doors=cursed,monster:trap'))
            obs,mask=self.lab.observe()
            choice=self.rule.apply(obs,mask,np.array([self.env.action_names.index('door_0')]))[0][0]
            self.assertEqual(self.env.action_names[int(choice)],'door_0')


if __name__=='__main__':unittest.main()
