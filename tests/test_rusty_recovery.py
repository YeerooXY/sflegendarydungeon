"""Native healing totals, visible rejected offers and conditional HP targets."""
from pathlib import Path
import sys
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.player_ideas import profile_values
from sfld_rl.rusty_recovery import CASES,progress,RustyRecovery
from sfld_rl.visual_policy import encode

LIB=library('build-v0612');PROFILE=ROOT/'profiles/synthetic.profile'


@unittest.skipUnless(LIB.is_file(), MISSING)
class RustyTests(unittest.TestCase):
    def env(self):return VectorEnv(LIB,[PROFILE],1,budgets=[0],auto_reset=False)

    def restore(self,env,text,seed=1):
        lab=EffectLab(env)
        with lab.fixture(text,0,seed=seed) as snapshot:lab.restore(0,snapshot,seed+1000)
        return lab,lab.observe()

    def test_native_full_unclipped_healing_and_poison_sequences(self):
        cases=[('greasy,explorer,hick',.30,0),('greasy,time_traveler,explorer',.40,0),
               ('greasy,rusty,explorer',.42,.25),('greasy,rusty,time_traveler',.56,.25),
               ('greasy,rusty,moonstone',.42,.30)]
        with self.env() as env:
            env.reset()
            for gems,healing,poison in cases:
                text=f'schema=1\nroom=85\nrun_number=2\nphase=recovery\nresume_phase=encounter\nencounter=empty\nhp_percent=20\nkeys=0\ngems={gems}\n'
                lab,_=self.restore(env,text);env.step([env.action_names.index('reenter')]);o=lab.visible()
                totals={'healing':0.,'damage':0.}
                for tick in range(6):
                    # Substitute an empty encounter without changing HP or the
                    # remaining effects, isolating six normal room-exit ticks.
                    if tick:
                        text=(f'schema=1\nroom={o["room"]}\nrun_number=2\nphase=encounter\nencounter=empty\n'
                              f'hp_fraction={o["hp"]!r}\nkeys=0\ngems={gems}\n')
                        for group in ('blessings','curses'):
                            if o[group]:text+=group+'='+','.join(f'{e["kind"]}:weak:{e["remaining"]}' for e in o[group])+'\n'
                        lab,_=self.restore(env,text)
                    env.step([env.action_names.index('skip')]);o=lab.visible()
                    for result in o['outcomes']:
                        if result['kind'] in totals:totals[result['kind']]+=result['amount']
                self.assertAlmostEqual(totals['healing'],healing,msg=gems)
                self.assertAlmostEqual(totals['damage'],poison,msg=gems)
                self.assertAlmostEqual(o['hp'],.2+healing-poison,msg=gems)

    def test_fixtures_record_complete_offer_history(self):
        with self.env() as env:
            env.reset()
            for case in CASES:
                lab,_=self.restore(env,progress(case,6))
                o=lab.visible();self.assertEqual(len(o['offered_gems']),3*len(case['gems']))
                self.assertEqual(set(o['gems']),set(case['gems']))

    def test_history_reaches_native_and_browser_policy_inputs(self):
        with self.env() as env:
            env.reset()
            text=('schema=1\nroom=51\nrun_number=2\nphase=gems\nhp_percent=60\nkeys=6\ngems=rusty\n'
                  'offered_gems=rusty,greasy,explorer\ngem_offers=greed,rabbit,time_traveler\n')
            lab,(obs,masks)=self.restore(env,text)
            public,public_mask,_=encode(lab.visible(),env.observation_names,env.action_names)
            np.testing.assert_array_equal(obs,public);np.testing.assert_array_equal(masks,public_mask)
            names={n:i for i,n in enumerate(env.observation_names)}
            self.assertEqual(obs[0,names['offered_gem.greasy']],1)
            self.assertEqual(obs[0,names['owned_gem.greasy']],0)
            self.assertFalse(any(lab.visible()['gem_offers'][i]=='greasy' for i in range(3)))

    def test_rejected_partner_never_reoffered(self):
        with self.env() as env:
            env.reset()
            for seed in range(64):
                text=('schema=1\nroom=51\nrun_number=2\nphase=gems\nhp_percent=60\nkeys=6\ngems=rusty\n'
                      'offered_gems=rusty,greasy,explorer\ngem_offers=unknown\n')
                lab,_=self.restore(env,text,seed)
                self.assertNotIn('greasy',lab.visible()['gem_offers'])

    def test_high_target_waits_then_enters_and_leaves_greasy_group_alone(self):
        with self.env() as env:
            env.reset();lab,state=self.restore(env,progress(CASES[0],0))
            rule=RustyRecovery(env,profile_values(PROFILE),'hp80',False)
            actions,targets,_=rule.apply(*state,np.array([rule.ai['wait_hp_20']]))
            self.assertAlmostEqual(targets[0],.8)
            state=env.step(actions,wait_targets=targets)[:2]
            actions,targets,_=rule.apply(*state,np.array([rule.ai['wait_hp_100']]))
            self.assertEqual(actions[0],rule.ai['reenter']);self.assertEqual(targets[0],-1)
            lab,state=self.restore(env,progress(CASES[3],0))
            actions,targets,_=rule.apply(*state,np.array([rule.ai['wait_hp_20']]))
            self.assertEqual(targets[0],-1)


if __name__=='__main__':unittest.main()
