"""Coverage, legal final menus, living low HP and paired branch independence."""
from itertools import combinations
from pathlib import Path
import sys
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from sfld_rl.effect_lab import EffectLab
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.stone_grid import menus,position,crossover_selection,METRICS

PROFILE=ROOT/'profiles/synthetic.profile';LIB=library('build-v0612')


@unittest.skipUnless(LIB.is_file(), MISSING)
class StoneGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=profile_values(PROFILE);cls.menus=menus(cls.profile)

    def test_every_held_pair_and_candidate_in_both_pools(self):
        self.assertEqual(len(self.menus),1275)
        for run,key in ((1,'gems_first_run'),(2,'gems_later_runs')):
            pool=self.profile[key].split(',')
            actual={(tuple(m['held']),g) for m in self.menus if m['run']==run for g in m['offered']}
            expected={(p,g) for p in combinations(pool,2) for g in pool if g not in p}
            self.assertEqual(actual,expected)

    def test_every_menu_has_complete_disjoint_history(self):
        for m in self.menus:
            self.assertEqual(len(set(m['held']+m['rejected']+m['offered'])),9)
            self.assertEqual(len(m['held']),2);self.assertEqual(len(m['rejected']),4)
            self.assertEqual(len(m['offered']),3)
            pool=self.profile['gems_first_run' if m['run']==1 else 'gems_later_runs'].split(',')
            self.assertTrue(set(m['held']+m['rejected']+m['offered']).issubset(pool))

    def test_all_fixtures_native_valid_and_low_hp_stays_alive(self):
        with VectorEnv(LIB,[PROFILE],1,budgets=[0],auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            for m in self.menus:
                with lab.fixture(position(m,0,1),0) as snap:lab.restore(0,snap,100)
                obs,masks=lab.observe();o=lab.visible()
                self.assertEqual(o['phase'],'gems');self.assertEqual(o['room'],76)
                self.assertEqual(o['keys'],0);self.assertAlmostEqual(o['hp'],.01)
                self.assertEqual(len(o['offered_gems']),9)
                self.assertFalse(masks[0,env.action_names.index('reenter')])
                self.assertTrue(all(masks[0,env.action_names.index('gem_'+str(i))] for i in range(3)))

    def test_branching_preserves_keys_health_and_held_stones(self):
        m=self.menus[900]
        with VectorEnv(LIB,[PROFILE],3,budgets=[0],auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            with lab.fixture(position(m,7,1),0) as snap:
                for i in range(3):lab.restore(i,snap,300)
                lab.observe();env.step([env.action_names.index('gem_'+str(i)) for i in range(3)])
                for i in range(3):
                    o=lab.visible(i);self.assertEqual(o['phase'],'doors');self.assertEqual(o['room'],76)
                    self.assertEqual(o['keys'],7);self.assertAlmostEqual(o['hp'],.01)
                    self.assertEqual(set(o['gems']),set(m['held']+[m['offered'][i]]))
                    self.assertEqual(o['blessings'],[]);self.assertEqual(o['curses'],[])

    def test_same_seed_and_same_choice_replay_exactly(self):
        with VectorEnv(LIB,[PROFILE],2,budgets=[0],auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            with lab.fixture(position(self.menus[135],4,50),0) as snap:
                for i in range(2):lab.restore(i,snap,400)
                lab.observe();env.step([env.action_names.index('gem_0')]*2)
                self.assertEqual(lab.visible(0),lab.visible(1))

    def test_selection_requires_real_reversal_and_omits_100_percent(self):
        plan=dict(health=[1,75,100],keys=[0,10])
        data=np.zeros((3,2,4,3,len(METRICS)))
        data[...,0]=[10,20,30]
        data[0,1,:,:,0]=[30,10,40]
        # A stronger apparent crossover at the unreachable HP100 must be omitted.
        data[2,0,:,:,0]=[1,100,200];data[2,1,:,:,0]=[100,1,200]
        selected=crossover_selection(plan,[((0,5),data)],12)
        self.assertTrue(selected)
        self.assertTrue(all(s['hp']!=100 for r in selected for s in r['states']))
        keys=next(r for r in selected if r['family']=='keys')
        self.assertEqual(keys['arms'],[0,1]);self.assertEqual(keys['states'],[dict(hp=1,keys=0),dict(hp=1,keys=10)])

    def test_each_budget_receives_its_own_selection_allowance(self):
        plan=dict(health=[1,75],keys=[0,10]);data=np.zeros((2,2,4,3,len(METRICS)))
        data[...,0]=[10,20,30];data[:,1,:,:,0]=[30,10,40]
        selected=crossover_selection(plan,[((b,m),data) for b in (0,20,50,100) for m in (4,5)],1)
        self.assertEqual(len(selected),4)
        self.assertEqual({r['budget'] for r in selected},{0,20,50,100})


if __name__=='__main__':unittest.main()
