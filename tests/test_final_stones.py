"""Verify set reuse is exact, all additions are covered, and HP1% stays alive."""
from itertools import combinations
from pathlib import Path
import sys
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from sfld_rl.final_stones import KEYS, catalog, counts, menu
from sfld_rl.stone_grid import position
from sfld_rl.player_ideas import profile_values
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab

PROFILE=ROOT/'profiles/synthetic.profile';LIB=library('build-v0612')


@unittest.skipUnless(LIB.is_file(), MISSING)
class FinalStonesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile=profile_values(PROFILE);cls.entries=catalog(cls.profile,123456)

    def test_combinatorial_coverage_and_counts(self):
        self.assertEqual(len(self.entries),1260)
        self.assertEqual(sum(c['pair_key_states'] for c in counts(self.profile)),1175)
        self.assertEqual(sum(c['candidate_states'] for c in counts(self.profile)),18900)
        for run,field in ((1,'gems_first_run'),(2,'gems_later_runs')):
            pool=self.profile[field].split(',')
            actual={(frozenset(p),g) for e in self.entries if e['run']==run
                    for p in combinations(e['stones'],2) for g in e['stones'] if g not in p}
            expected={(frozenset(p),g) for p in combinations(pool,2) for g in pool if g not in p}
            self.assertEqual(actual,expected)

    def test_legal_history_panels(self):
        for e in self.entries:
            self.assertEqual(len(e['histories']),7 if e['run']==1 else 8)
            if e['run']==1:self.assertEqual(len({frozenset(h) for h in e['histories']}),7)
            for h in range(len(e['histories'])):
                for last in e['stones']:
                    m=menu(e,h,last)
                    self.assertEqual(len(set(m['held']+m['offered']+m['rejected'])),9)
                    self.assertEqual(m['offered'][0],last)
                    self.assertEqual(set(m['held']+[last]),set(e['stones']))

    def test_every_final_set_native_valid(self):
        with VectorEnv(LIB,[PROFILE],1,budgets=[5],auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            for e in self.entries:
                with lab.fixture(position(menu(e,0),0,1),5) as snap:lab.restore(0,snap,333)
                obs,masks=lab.observe();o=lab.visible()
                self.assertEqual(o['phase'],'gems');self.assertAlmostEqual(o['hp'],.01)
                self.assertEqual(o['room'],76);self.assertEqual(o['keys'],0)
                self.assertFalse(masks[0,env.action_names.index('reenter')])

    def test_chosen_last_role_has_identical_future(self):
        # Same triple/history/keys/seed, but each stone takes its turn as the third.
        with VectorEnv(LIB,[PROFILE],3,budgets=[5],auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            for e in (self.entries[0],self.entries[119],self.entries[120],self.entries[548],self.entries[-1]):
                for h in (0,len(e['histories'])-1):
                    for k in KEYS:
                        for slot,last in enumerate(e['stones']):
                            with lab.fixture(position(menu(e,h,last),k,1),5) as snap:lab.restore(slot,snap,456789)
                        lab.observe();obs,masks,*_=env.step([env.action_names.index('gem_0')]*3)
                        for slot in (1,2):
                            np.testing.assert_array_equal(obs[0],obs[slot]);np.testing.assert_array_equal(masks[0],masks[slot])
                            self.assertEqual(lab.visible(0),lab.visible(slot))
                        self.assertEqual(set(lab.visible()['gems']),set(e['stones']))
                        self.assertEqual(lab.visible()['keys'],k);self.assertAlmostEqual(lab.visible()['hp'],.01)
                        # One subsequent choice also has exactly the same resulting transition.
                        action=int(np.flatnonzero(masks[0])[0]);obs,masks,*_=env.step([action]*3)
                        for slot in (1,2):
                            np.testing.assert_array_equal(obs[0],obs[slot]);self.assertEqual(lab.visible(0),lab.visible(slot))


if __name__=='__main__':unittest.main()
