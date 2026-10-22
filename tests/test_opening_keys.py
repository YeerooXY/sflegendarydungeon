"""Boundaries of the experimental early key policies, using the native engine."""
from pathlib import Path
import sys
import unittest
import importlib.util
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
AVAILABLE=importlib.util.find_spec('numpy') is not None
LIB=ROOT/'build-v0611/Release/sfld_rl.dll'
if AVAILABLE:
    import numpy as np
    from sfld_rl.env import VectorEnv
    from sfld_rl.effect_lab import EffectLab
    from sfld_rl.interaction_cases import state
    from sfld_rl.player_ideas import profile_values
    from sfld_rl.opening_keys import OpeningKeyPolicy


@unittest.skipUnless(AVAILABLE and LIB.is_file(),'Optional NumPy/current native environment absent')
class OpeningKeysTests(unittest.TestCase):
    def test_key_guard_respects_damage_stage_traps_and_free_alternatives(self):
        profile=ROOT/'profiles/synthetic.profile'
        with VectorEnv(ROOT/'build-v0611/Release/sfld_rl.dll',[profile],1,budgets=[1000],auto_reset=False) as env:
            env.reset();lab=EffectLab(env);rule=OpeningKeyPolicy(env,profile_values(profile),'double_lock')
            base=dict(room=12,hp=80,keys=6,gems='none',doors='monster,locked',curses='hard_lock:weak:4,clumsy:weak:5')
            def choice(changes,action='door_1'):
                fields={**base,**changes}
                if fields.get('phase')=='encounter':fields.pop('doors')
                with lab.fixture(state(**fields),1000) as snap:lab.restore(0,snap,17)
                obs,mask=lab.observe();actual=rule.apply(obs,mask,np.array([env.action_names.index(action)]))[0][0]
                return env.action_names[actual]
            self.assertEqual(choice({}),'door_0')
            for changes in ({'hp':10},{'room':26,'gems':'hero'},{'doors':'monster:trap,locked'},
                            {'curses':'hard_lock:weak:4'}, {'doors':'monster,wood','resources':'10,10,10,10,10,10'},
                            {'hp':16,'curses':'hard_lock:weak:4,clumsy:weak:5,poison:weak:5'}):
                self.assertEqual(choice(changes),'door_1',changes)
            self.assertEqual(choice({'hp':3,'blessings':'one_hit:strong:8'}),'door_0')
            self.assertEqual(choice({'phase':'encounter','encounter':'monster','doors':'unknown'},'flee'),'fight')


if __name__=='__main__':unittest.main()
