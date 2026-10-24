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
 from sfld_rl.practical import Practical
 from sfld_rl.player_ideas import profile_values

@unittest.skipUnless((ROOT/'docs/player-ideas-2026-10-11/schema.json').exists(), 'Local research archive absent (not published)')
@unittest.skipUnless(AVAILABLE,'Optional NumPy is absent')
class PracticalTests(unittest.TestCase):
 def state(self,case,phase,action,**values):
  schema=json.loads((ROOT/'docs/player-ideas-2026-10-11/schema.json').read_text())
  self.rule=Practical(schema['observation_names'],schema['action_names'],case,profile_values(ROOT/'profiles/synthetic.profile'))
  self.obs=np.zeros((1,len(schema['observation_names'])),np.float32);self.mask=np.zeros((1,len(schema['action_names'])),bool)
  self.proposed=np.array([self.rule.ai[action]])
  self.set(**{'phase.'+phase:1,'hp':.8,'room_div100':.35,'hours_left_div168':1,**values});self.allow(action)
 def set(self,**values):
  for k,v in values.items():self.obs[0,self.rule.oi[k]]=v
 def allow(self,*names):
  for name in names:self.mask[0,self.rule.ai[name]]=True
 def expect(self,name):self.assertEqual(int(self.rule.apply(self.obs,self.mask,self.proposed)[0][0]),self.rule.ai[name])
 def test_five_credits_stop_for_useful_offer_and_key_gate(self):
  self.state('shop5','shop','skip',**{'keys_div20':.2,'reroll_shop_visits_div20':.05})
  self.allow('reroll','buy_0');self.expect('reroll')
  self.set(**{'offers.0.effect.kind.one_hit':1,'offers.0.effect.remaining_div10':.8,'offers.0.effect.magnitude':1});self.expect('buy_0')
  self.set(**{'offers.0.effect.kind.one_hit':0,'keys_div20':.15});self.proposed[0]=self.rule.ai['reroll'];self.expect('skip')
  self.set(**{'keys_div20':.2,'reroll_bank_used_div100':.05});self.expect('skip')
 def test_paid_door_can_still_avoid_unsafe_trap(self):
  self.state('keys','doors','door_1',**{'keys_div20':.2,'doors.0.kind.monster':1,'doors.1.kind.locked':1,'doors.1.key_cost_div6':1/6})
  self.allow('door_0');self.expect('door_0')
  self.set(**{'doors.0.trap':1});self.expect('door_1')
  self.set(**{'doors.0.trap':0,'hp':.01});self.expect('door_1')
 def test_trial_checks_effective_escape_and_survival(self):
  self.state('trials','doors','door_0',**{'hp':.03,'doors.0.kind.exit_trial':1,'doors.1.kind.trial':1,'owned_gem.rabbit':1,'owned_gem.moonstone':1})
  self.allow('door_1');self.expect('door_1')
  self.set(**{'curses.0.active':1,'curses.0.kind.clumsy':1,'curses.0.magnitude':.8});self.expect('door_0')
 def test_patient_only_at_final_boss_after_two_steps_and_with_time(self):
  self.state('patient52','recovery','heal_full',**{'hp':0,'room_div100':1,'paid_heals_div20':.1})
  self.allow('wait_deadline','reenter');actual,targets,_=self.rule.apply(self.obs,self.mask,self.proposed)
  self.assertAlmostEqual(targets[0],.52)
  self.set(**{'room_div100':.75});self.expect('heal_full')
  self.set(**{'room_div100':1,'hours_left_div168':1/168});self.expect('heal_full')
  self.set(**{'hours_left_div168':1,'paid_heals_div20':0});self.expect('heal_full')
 def test_one_hit_cover_must_last_into_new_curse(self):
  self.state('trades','curse_shop','skip',**{'room_div100':.6,'keys_div20':.5,'offers.0.keys_div6':1/6,
   'offers.0.effect.kind.broken_armor':1,'offers.0.effect.magnitude':.5,'offers.0.effect.remaining_div10':.3,
   'blessings.0.active':1,'blessings.0.kind.one_hit':1,'blessings.0.magnitude':1,'blessings.0.remaining_div10':.8})
  self.allow('buy_0');self.expect('buy_0')
  self.set(**{'blessings.0.remaining_div10':.1});self.expect('skip')
 def test_one_hit_downgrade_guard_preserves_slots_and_duration_boundary(self):
  self.state('learned','shop','buy_0',**{'room_div100':.05,'first_event_run':1,
   'offers.0.effect.kind.one_hit':1,'offers.0.effect.remaining_div10':.4,
   'offers.1.effect.kind.escape':1,'offers.1.effect.remaining_div10':.5,
   'blessings.0.present':1,'blessings.0.active':1,'blessings.0.kind.one_hit':1,
   'blessings.0.magnitude':1,'blessings.0.remaining_div10':.8})
  self.allow('skip','buy_1');self.expect('buy_1')
  self.set(**{'blessings.1.present':1,'blessings.2.present':1});self.expect('skip')
  self.set(**{'blessings.0.remaining_div10':.5});self.expect('buy_0')
  self.set(**{'blessings.0.remaining_div10':.6,'owned_gem.time_traveler':1});self.expect('buy_0')
  self.set(**{'blessings.0.remaining_div10':.7});self.expect('skip')

if __name__=='__main__':unittest.main()
