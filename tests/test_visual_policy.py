"""Check browser encoding against the native training observation and mask."""
import ctypes as ct
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
AVAILABLE=importlib.util.find_spec('torch') is not None
BUILD=build_dir('build-v0611')
if AVAILABLE:
 import numpy as np
 import torch
 from sfld_rl.env import VectorEnv
 from sfld_rl.visual_policy import VisualPolicy,encode

@unittest.skipUnless(AVAILABLE and (BUILD/'sfld_rl.dll').exists(),'Optional RL environment absent')
class VisualPolicyTests(unittest.TestCase):
 def test_unrestricted_policy_is_not_changed_by_legacy_shop_settings(self):
  from visual_lab import Lab
  profile=ROOT/'profiles/synthetic.profile';policy=VisualPolicy()
  self.assertFalse(hasattr(policy.model,'shop_config'))
  lab=Lab(BUILD/'sfld.exe',profile)
  try:
   state='schema=1\nroom=35\nrun_number=2\nphase=shop\nhp_percent=100\nkeys=4\ngems=rabbit\noffers=raider:weak:5:1,elixir:weak:1:1\n'
   visible=lab.create({'initial_state':state,'run_number':2,'budget':100,'neural_adviser':False})['observation']
   vector,mask,mapping=encode(visible,policy.names,policy.actions)
   with torch.inference_mode():expected=mapping[int(policy.model.actions(torch.as_tensor(vector),torch.as_tensor(mask),greedy=True)[0][0])]
   for allowance,reserve in [(5,4),(0,4),(5,5),(1000,0)]:
    o=copy.deepcopy(visible)
    policy.advise(o,{'shop_allowance':allowance,'shop_key_reserve':reserve},profile)
    action=next(s['action'] for s in o['suggestions'] if s['strategy']=='neural')
    self.assertEqual(action,expected)
    self.assertIn(action,o['legal_actions'])
    self.assertEqual(o['neural_adviser']['case'],'unrestricted_budget_policy')
   self.assertTrue(all(r.variant=='learned' for r in policy.rules.values()))
  finally:lab.close()

 def test_paid_recovery_remains_the_policy_decision_at_final_boss(self):
  from visual_lab import Lab
  profile=ROOT/'profiles/synthetic.profile';policy=VisualPolicy()
  lab=Lab(BUILD/'sfld.exe',profile)
  try:
   state='schema=1\nroom=100\nrun_number=2\nphase=recovery\nresume_phase=encounter\nencounter=boss\nhp_percent=0\nkeys=0\ngems=hero,greed,moonstone\npaid_heals=2\n'
   o=lab.create({'initial_state':state,'run_number':2,'budget':100,'neural_adviser':False})['observation']
   # A legal paid refill must not become the old mandatory final-boss wait.
   index=policy.actions.index('heal_full')
   _,mask,mapping=encode(o,policy.names,policy.actions);self.assertTrue(mask[0,index])
   policy.model.actions=lambda *a,**k:(torch.tensor([index]),None,None)
   policy.advise(o,{'shop_allowance':5,'shop_key_reserve':4},profile)
   self.assertEqual(o['suggestions'][-1]['action'],mapping[index])
  finally:lab.close()

 def test_native_feature_and_mask_parity_through_real_episodes(self):
  policy=VisualPolicy();phases=set();checked=0
  from sfld_rl.practical import Practical
  from sfld_rl.player_ideas import profile_values
  profile=ROOT/'profiles/synthetic.profile'
  reference=Practical(policy.names,policy.actions,'learned',profile_values(profile))
  with VectorEnv(BUILD/'sfld_rl.dll',[profile],4,budgets=[1000],seed=1734,seed_namespace=236,deadline_hours=168,max_actions=100000,auto_reset=False) as env:
   native=env._lib.sfld_rl_visible_json;native.argtypes=[ct.c_void_p,ct.c_int32];native.restype=ct.c_char_p
   obs,masks=env.reset()
   for step in range(500):
    if step%5==0:
     for slot in range(4):
      visible=json.loads(native(env._handle,slot));phases.add(visible['phase'])
      actual,mask,mapping=encode(visible,policy.names,policy.actions)
      diff=np.flatnonzero(np.abs(actual[0]-obs[slot])>2e-7)
      self.assertEqual([policy.names[i] for i in diff],[])
      np.testing.assert_array_equal(mask[0],masks[slot]);checked+=1
      self.assertTrue(all(a in visible['legal_actions'] for a in mapping.values()))
      with torch.inference_mode():proposed=policy.model.actions(torch.as_tensor(obs[slot:slot+1]),torch.as_tensor(masks[slot:slot+1]),greedy=True)[0].numpy()
      expected,_,_=reference.apply(obs[slot:slot+1],masks[slot:slot+1],proposed)
      policy.advise(visible,{'shop_allowance':0,'shop_key_reserve':100},profile)
      self.assertEqual(visible['neural_adviser']['trained_action'],mapping[int(expected[0])])
      chosen=visible['suggestions'][-1]['action']
      self.assertIn(chosen,visible['legal_actions'])
      if chosen!=mapping[int(expected[0])]:
       self.assertIn(visible['neural_adviser']['adjustment'],('lockpick_poison_rescue','survivable_noncombat_route','late_recovery_buffer','safe_free_door','shop_small_refresh','shop_no_gain','shop_lethal_exit','shop_traveler_elixir','hungry_chest_reward','protect_combat_blessing','lockpick_last_recovery','recovery_mimic','one_hit_mimic','preserve_escape_pair','arena_prize','safe_arena'))
       self.assertTrue(visible['neural_adviser']['reason'])
    with torch.inference_mode():actions=policy.model.actions(torch.as_tensor(obs),torch.as_tensor(masks),greedy=True)[0].numpy()
    obs,masks,_,done,truncated,_=env.step(actions)
    if np.any(done|truncated):obs,masks=env.reset_done(np.flatnonzero(done|truncated))
   self.assertTrue({'doors','encounter','shop','recovery','gems'}<=phases,phases)
   self.assertEqual(checked,400)

 def test_stone_ratings_are_contextual_and_offer_order_independent(self):
  from visual_lab import Lab
  profile=ROOT/'profiles/synthetic.profile';policy=VisualPolicy();lab=Lab(BUILD/'sfld.exe',profile)
  def ratings(owned,offers):
   state=f'schema=1\nroom=51\nrun_number=2\nphase=gems\nhp_percent=60\nkeys=4\ngems={owned}\ngem_offers={offers}\n'
   o=lab.create({'initial_state':state,'run_number':2,'budget':100,'neural_adviser':False})['observation']
   policy.advise(o,{},profile)
   return {r['gem']:(r['points'],r['tier']) for r in o['stone_ratings']['entries']}
  try:
   first=ratings('rabbit','moonstone,hero,greed')
   swapped=ratings('rabbit','greed,moonstone,hero')
   different=ratings('bull','moonstone,hero,greed')
   self.assertEqual(first,swapped)
   self.assertNotEqual(first,different)
   self.assertTrue(all(0<=score<=100 and grade in 'SABCDF' for score,grade in first.values()))
  finally:lab.close()

if __name__=='__main__':unittest.main()
