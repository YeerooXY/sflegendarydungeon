import sys
import unittest
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.shop_reserve import ShopReserve, ReserveCase


class ReserveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with VectorEnv(ROOT/'build-v0610/Release/sfld_rl.dll',[ROOT/'profiles/synthetic.profile'],1,budgets=[100]) as env:
            cls.names=env.observation_names;cls.actions=env.action_names
        cls.profile=profile_values(ROOT/'profiles/synthetic.profile')

    def fixture(self,case,values):
        rule=ShopReserve(self.names,self.actions,case,self.profile)
        obs=np.zeros((1,len(self.names)),np.float32);masks=np.zeros((1,len(self.actions)),bool)
        for n,v in values.items():obs[0,rule.oi[n]]=v
        return rule,obs,masks

    def test_shop_approach_and_next_stage_reserve(self):
        r,o,m=self.fixture(ReserveCase(4,2),{'room_div100':.26})
        self.assertEqual(r.target(o)[0],1)
        o[0,r.oi['room_div100']]=.28;self.assertEqual(r.target(o)[0],3)
        o[0,r.oi['room_div100']]=.30;self.assertEqual(r.target(o)[0],4)
        o[0,r.oi['shops_seen.2']]=1;self.assertEqual(r.target(o)[0],2)
        o[0,r.oi['room_div100']]=.90;o[0,r.oi['shops_seen.4']]=1
        self.assertEqual(r.target(o)[0],0)

    def test_free_shop_and_unavoidable_door(self):
        r,o,m=self.fixture(ReserveCase(4,2),{'room_div100':.04,'first_event_run':1,'phase.doors':1,'keys_div20':.1,'doors.0.key_cost_div6':1/6})
        m[0,r.ai['door_0']]=True
        self.assertEqual(r.target(o)[0],0)
        o[0,r.oi['room_div100']]=.30
        self.assertEqual(r.apply(o,m,np.array([r.ai['door_0']]))[0][0],r.ai['door_0'])
        m[0,r.ai['door_1']]=True
        self.assertEqual(r.apply(o,m,np.array([r.ai['door_0']]))[0][0],r.ai['door_1'])

    def test_later_stage_one_saves_for_next_stage(self):
        r,o,m=self.fixture(ReserveCase(4,2),{'room_div100':.19,'first_event_run':0})
        self.assertEqual(r.target(o)[0],2)

    def test_time_traveler_heal_and_reroll_cost_is_legal(self):
        r,o,m=self.fixture(ReserveCase(cheap_elixir=True),{'phase.shop':1,'hp':.6,'owned_gem.time_traveler':1,'offers.0.effect.kind.elixir':1,'offers.0.keys_div6':1/6})
        for a in ('buy_0','skip'):m[0,r.ai[a]]=True
        self.assertEqual(r.apply(o,m,np.array([r.ai['skip']]))[0][0],r.ai['buy_0'])
        o[0,r.oi['offers.0.keys_div6']]=.5
        self.assertEqual(r.apply(o,m,np.array([r.ai['buy_0']]))[0][0],r.ai['skip'])
        m[0,r.ai['reroll']]=True
        self.assertEqual(r.apply(o,m,np.array([r.ai['buy_0']]))[0][0],r.ai['reroll'])
        o[0,r.oi['owned_gem.time_traveler']]=0
        self.assertEqual(r.apply(o,m,np.array([r.ai['buy_0']]))[0][0],r.ai['buy_0'])


if __name__=='__main__':unittest.main()
