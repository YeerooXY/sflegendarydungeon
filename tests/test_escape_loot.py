import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
AVAILABLE=importlib.util.find_spec('numpy') is not None
if AVAILABLE:
    import numpy as np
    from sfld_rl.escape_loot import EscapeLoot
    from sfld_rl.player_ideas import profile_values
    from sfld_rl.env import VectorEnv


@unittest.skipUnless((ROOT/'docs/player-ideas-2026-10-11/schema.json').exists(), 'Local research archive absent (not published)')
@unittest.skipUnless(AVAILABLE,'Optional NumPy is absent')
class EscapeLootTests(unittest.TestCase):
    def state(self,case,phase,action,**values):
        schema=json.loads((ROOT/'docs/player-ideas-2026-10-11/schema.json').read_text())
        self.rule=EscapeLoot(schema['observation_names'],schema['action_names'],case,profile_values(ROOT/'profiles/synthetic.profile'))
        self.obs=np.zeros((1,len(schema['observation_names'])),np.float32)
        self.mask=np.zeros((1,len(schema['action_names'])),bool)
        self.proposed=np.array([self.rule.ai[action]])
        self.set(**{'phase.'+phase:1,'hp':.8,'room_div100':.65,'owned_gem.rabbit':1,'owned_gem.moonstone':1,**values})
        self.allow(action)
    def set(self,**values):
        for k,v in values.items(): self.obs[0,self.rule.oi[k]]=v
    def allow(self,*actions):
        for a in actions:self.mask[0,self.rule.ai[a]]=True
    def result(self):return self.rule.apply(self.obs,self.mask,self.proposed)[0][0]
    def expect(self,action):self.assertEqual(self.result(),self.rule.ai[action])
    def test_untrapped_mystery_beats_trapped_monster_only_when_escape_guaranteed(self):
        self.state('safe_escape','doors','door_0',**{'doors.0.kind.monster':1,'doors.0.trap':1,'doors.1.kind.mystery':1})
        self.allow('door_1');self.expect('door_1')
        self.set(**{'owned_gem.moonstone':0});self.expect('door_0')
    def test_key_saved_but_clumsy_revokes_guarantee(self):
        self.state('safe_escape','doors','door_1',**{'doors.0.kind.monster':1,'doors.1.kind.locked':1,'doors.1.key_cost_div6':1/6})
        self.allow('door_0');self.expect('door_0')
        self.set(**{'curses.0.active':1,'curses.0.kind.clumsy':1,'curses.0.magnitude':.2});self.expect('door_1')
    def test_one_hit_kept_but_recovery_does_not_force_fight(self):
        self.state('safe_escape','encounter','fight',**{'encounter.monster':1,'blessings.0.active':1,'blessings.0.kind.recovery':1,'blessings.0.magnitude':.2})
        self.allow('flee');self.expect('flee')
        self.set(**{'blessings.0.kind.recovery':0,'blessings.0.kind.one_hit':1,'blessings.0.magnitude':1});self.expect('fight')
    def test_search_only_leaves_other_choices_and_preserves_healing(self):
        self.state('soldier_only','encounter','skip',**{'encounter.skeleton':1})
        self.allow('interact');self.expect('interact')
        self.allow('heal_full');self.proposed[0]=self.rule.ai['heal_full'];self.expect('heal_full')
        self.state('soldier_only','encounter','fight',**{'encounter.monster':1});self.allow('flee');self.expect('fight')
    def test_lethal_poison_blocks_loot_override(self):
        self.state('safe_escape','encounter','skip',hp=.04,**{'encounter.skeleton':1,'curses.0.active':1,'curses.0.kind.poison':1,'curses.0.magnitude':.05})
        self.allow('interact');self.expect('skip')
    def test_pendant_only_replaces_two_named_choices(self):
        for picked,want in [('bull','gem_1'),('hick','gem_1'),('greed','gem_0'),('rabbit','gem_0'),('hero','gem_0')]:
            self.state('safe_escape_fallback','gems','gem_0',**{f'gem_offers.0.{picked}':1,'gem_offers.1.pendant':1,'gem_offers.2.rusty':1})
            self.allow('gem_1','gem_2');self.expect(want)
    def test_inputs_not_modified(self):
        self.state('safe_escape','encounter','skip',**{'encounter.skeleton':1});self.allow('interact')
        before=self.obs.copy();mask=self.mask.copy();self.result()
        np.testing.assert_array_equal(before,self.obs);np.testing.assert_array_equal(mask,self.mask)
    @unittest.skipUnless((ROOT/'build/Release/sfld_rl.dll').exists(),'Native Windows library absent')
    def test_gold_reporting_matches_observation_and_resets(self):
        with VectorEnv(ROOT/'build/Release/sfld_rl.dll',[ROOT/'profiles/synthetic.profile'],4,budgets=[0],seed=987) as env:
            obs,masks=env.reset();np.testing.assert_array_equal(env.reward_totals()['gold_units'],0)
            for _ in range(120):
                actions=np.argmax(masks,axis=1).astype(np.int64)
                obs,masks,_,done,truncated,_=env.step(actions)
                gold=env.reward_totals()['gold_units']
                np.testing.assert_allclose(gold,obs[:,env.observation_names.index('gold_reward_units_div100')]*100,atol=1e-5)
                self.assertTrue(np.all(gold>=0))
                if np.any(done|truncated):obs,masks=env.reset_done(np.flatnonzero(done|truncated))
            env.reset();np.testing.assert_array_equal(env.reward_totals()['gold_units'],0)

if __name__=='__main__':unittest.main()
