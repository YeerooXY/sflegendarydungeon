"""Check deployment parity and the boundaries of experimental action rules."""
import copy
import os
from pathlib import Path
import sys
import unittest
import importlib.util
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
AVAILABLE=all(importlib.util.find_spec(n) is not None for n in ('numpy','torch'))
from visual_lab import default_engine
LIB=ROOT/os.environ['SFLD_TEST_BUILD']/'Release/sfld_rl.dll' if os.environ.get('SFLD_TEST_BUILD') else default_engine().with_name('sfld_rl.dll')
if AVAILABLE:
    import numpy as np
    import torch
    from sfld_rl.env import VectorEnv
    from sfld_rl.effect_lab import EffectLab
    from sfld_rl.interaction_cases import cases,state
    from sfld_rl.interaction_policy import InteractionPolicy
    from sfld_rl.player_ideas import profile_values
    from sfld_rl.visual_policy import VisualPolicy,encode


@unittest.skipUnless(AVAILABLE and LIB.is_file(),'Optional Torch/current native environment absent')
class InteractionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.profile=ROOT/'profiles/synthetic.profile'
        self.env=VectorEnv(LIB,[self.profile],1,budgets=[1000],auto_reset=False)
        self.env.reset();self.lab=EffectLab(self.env)
    def tearDown(self):self.env.close()
    def choice(self,progress,variant,proposed):
        with self.lab.fixture(progress,1000) as snap:self.lab.restore(0,snap,0)
        obs,mask=self.lab.observe();rule=InteractionPolicy(self.env,profile_values(self.profile),variant)
        action=rule.apply(obs,mask,np.array([self.env.action_names.index(proposed)]))[0][0]
        return self.env.action_names[action]
    def test_desktop_parity_on_every_declared_interaction(self):
        policy=VisualPolicy()
        rule=InteractionPolicy(self.env,profile_values(self.profile),'base')
        for case in cases():
            with self.subTest(case=case['name']):
                with self.lab.fixture(case['progress'],1000) as snap:self.lab.restore(0,snap,0)
                obs,mask=self.lab.observe();o=self.lab.visible()
                with torch.inference_mode():proposed=policy.model.actions(torch.as_tensor(obs),torch.as_tensor(mask),greedy=True)[0].numpy()
                actual=rule.apply(obs,mask,proposed)[0][0]
                policy.advise(o,{},self.profile)
                _,_,mapping=encode(o,policy.names,policy.actions)
                self.assertEqual(mapping[int(actual)],o['suggestions'][-1]['action'])
    def test_protects_recovery_but_does_not_route_through_a_trap(self):
        progress=state(blessings='recovery:strong:5,one_hit:strong:7,raider:strong:8',doors='blessing,monster')
        self.assertEqual(self.choice(progress,'protect_recovery','door_0'),'door_1')
        self.assertEqual(self.choice(progress.replace('blessing,monster','blessing,monster:trap'),'protect_recovery','door_0'),'door_0')
        self.assertEqual(self.choice(progress.replace('one_hit:strong:7','one_hit:weak:1'),'protect_recovery','door_0'),'door_0')
    def test_current_public_forecast_corrections_reach_batched_runtime(self):
        progress=state(room=35,hp=100,gems='greed',phase='encounter',encounter='mimic',
                       blessings='recovery:weak:2,lockpick:weak:2,escape:weak:5')
        self.assertEqual(self.choice(progress,'base','skip'),'fight')
        for door in ('cursed','destiny'):
            progress=state(room=35,gems='greed',doors=f'{door},monster',blessings='escape:weak:5')
            self.assertEqual(self.choice(progress,'base','door_0'),'door_1')
        progress=state(room=35,hp=10,keys=0,gems='greed',doors='monster,locked',
                       blessings='recovery:strong:1,lockpick:weak:2')
        self.assertEqual(self.choice(progress,'base','door_0'),'door_1')
    def test_trial_requires_escape_to_last_and_accounts_for_clumsy(self):
        progress=state(room=51,gems='hero,greed',blessings='escape:weak:5',doors='trial,monster')
        self.assertEqual(self.choice(progress,'safe_trial','door_1'),'door_0')
        self.assertEqual(self.choice(progress.replace('escape:weak:5','escape:weak:4'),'safe_trial','door_1'),'door_1')
        self.assertEqual(self.choice(progress+'curses=clumsy:strong:10\n','safe_trial','door_1'),'door_1')

    def test_poison_lockpick_correction_matches_desktop_and_batch(self):
        progress=state(room=55,hp=4.327293841,keys=0,gems='moonstone,greed',
                       doors='locked,mystery',blessings='lockpick:weak:2',curses='poison:weak:5')
        self.assertEqual(self.choice(progress,'base','door_1'),'door_0')
        self.assertEqual(self.choice(progress.replace('locked,mystery','locked:trap,mystery'),'base','door_1'),'door_1')
        with self.lab.fixture(progress,1000) as snap:self.lab.restore(0,snap,0)
        obs,masks=self.lab.observe();o=self.lab.visible()
        policy=VisualPolicy();policy.advise(o,{},self.profile)
        rule=InteractionPolicy(self.env,profile_values(self.profile),'base')
        with torch.inference_mode():
            proposed=policy.model.actions(torch.as_tensor(obs),torch.as_tensor(masks),greedy=True)[0].numpy()
        actual=rule.apply(obs,masks,proposed)[0][0]
        _,_,mapping=encode(o,policy.names,policy.actions)
        self.assertEqual(mapping[int(actual)],o['suggestions'][-1]['action'])
        self.assertEqual(o['suggestions'][-1]['action']['index'],0)

    def test_trapped_mystery_survival_reaches_batched_runtime(self):
        progress=state(room=27,hp=12,keys=9,gems='moonstone',doors='monster,mystery:trap',
                       blessings='recovery:weak:2')
        self.assertEqual(self.choice(progress,'base','door_0'),'door_1')
        self.assertEqual(self.choice(progress.replace('hp_percent=12','hp_percent=10'),'base','door_0'),'door_0')
    def test_strong_one_hit_keeps_room_and_slot_boundaries(self):
        progress=state(hp=85,phase='shop',offers='elixir:weak:1:1,one_hit:strong:8:4')
        self.assertEqual(self.choice(progress,'strong_one_hit','reroll'),'buy_1')
        self.assertEqual(self.choice(progress.replace('hp_percent=85','hp_percent=21'),'strong_one_hit','reroll'),'reroll')
        self.assertEqual(self.choice(progress+'blessings=recovery:strong:5,escape:strong:10,raider:strong:10\n','strong_one_hit','reroll'),'reroll')

    def test_paid_hungry_chest_has_same_exit_risk_and_extra_resources(self):
        rule=InteractionPolicy(self.env,profile_values(self.profile),'base')
        for donated in range(6):
            for hp,blessing,curse in ((80,'none','none'),(3,'none','poison:weak:5'),
                                       (3,'recovery:weak:3','poison:weak:5')):
                progress=state(hp=hp,keys=0,phase='encounter',encounter='sated_chest',
                    donated_resource=donated,resources='0,0,0,0,0,0',blessings=blessing,curses=curse)
                with self.lab.fixture(progress,1000) as snap:
                    outcomes={}
                    for name in ('skip','interact'):
                        self.lab.restore(0,snap,7483);obs,mask=self.lab.observe()
                        if name=='skip':
                            chosen=rule.apply(obs,mask,np.array([self.env.action_names.index(name)]))[0][0]
                            self.assertEqual(self.env.action_names[chosen],'interact')
                        self.env.step(np.array([self.env.action_names.index(name)]))
                        outcomes[name]=self.lab.visible()
                    a,b=outcomes['skip'],outcomes['interact']
                    for field in ('hp','phase','room','deaths','keys','blessings','curses','elapsed_hours'):
                        self.assertEqual(a[field],b[field],(donated,hp,field))
                    self.assertEqual(sum(b['resources'])-sum(a['resources']),2)
                    self.assertEqual(b['resources'][donated],0)


if __name__=='__main__':unittest.main()
