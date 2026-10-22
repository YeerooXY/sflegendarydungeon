"""Interactive-process bridge, exact current-state advice and replay safety."""
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import numpy as np
import torch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from visual_lab import Lab,Session,default_engine,settings
from sfld_rl.effect_lab import EffectLab
from sfld_rl.env import VectorEnv
from sfld_rl.visual_policy import encode

EXE=Path(os.environ.get('SFLD_EXE',default_engine())).resolve()
LIB=EXE.with_name('sfld_rl.dll' if os.name=='nt' else 'libsfld_rl.so');PROFILE=ROOT/'profiles/synthetic.profile'
STATE=(ROOT/'examples/final-stone-moonstone-thirsty.state').read_text(encoding='utf-8')
CONFIG=dict(initial_state=STATE,budget=5,event_start_hour=17,run_number=2,stone_advice_samples=512)


class RuntimeStoneAdviceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):torch.set_num_threads(1)

    def setUp(self):self.lab=Lab(EXE,PROFILE)
    def tearDown(self):self.lab.close()

    def create(self,config=None):
        payload=self.lab.create(config or CONFIG)
        return self.lab.session(payload['session']),payload

    def wire(self,session):
        session.process.stdin.write('planning_snapshot\n');session.process.stdin.flush()
        return session._read_wire()['planning_state']

    def test_default_enabled_and_settings_bounded(self):
        self.assertTrue(EXE.is_file())
        self.assertTrue(LIB.is_file(),'The deployed executable needs its matching stone-adviser library')
        self.assertTrue(settings({})['final_stone_adviser'])
        self.assertEqual(settings({})['stone_advice_samples'],2048)
        for value in (0,1,True,None,'2048',2048.0,100000):
            with self.assertRaises(ValueError):settings({'stone_advice_samples':value})
        for value in ('true',1,None):
            with self.assertRaises(ValueError):settings({'final_stone_adviser':value})

    def test_current_three_offers_cache_undo_replay_and_rng(self):
        session,payload=self.create();control,_=self.create()
        before=copy.deepcopy(session.observation)
        self.assertEqual(payload['stone_advice']['status'],'pending')
        key=payload['stone_advice']['state_key']
        with self.assertRaises(ValueError):session.final_stone_advice(0,'stale-state')
        result=session.final_stone_advice(0,key);advice=result['stone_advice']
        self.assertEqual(advice['status'],'ready',advice)
        self.assertEqual({r['stone'] for r in advice['rankings']},{'deceit','rusty','pendant'})
        self.assertEqual(advice['estimated_best'],'deceit')
        self.assertEqual(advice['trajectories'],1536)
        self.assertIsInstance(advice['planning_seed'],str)
        self.assertEqual(before,session.observation)
        self.assertEqual(len(session.history),0)
        with patch('sfld_rl.runtime_stone_advice.compare',side_effect=AssertionError('Cache missed')):
            self.assertEqual(session.final_stone_advice(0,key)['stone_advice'],advice)
        session.step(advice['action']);control.step(advice['action'])
        self.assertEqual(session.observation,control.observation,'Advice must not consume source RNG')
        self.assertEqual(session.journal[-1]['stone_advice'],advice)
        recording=session.export();replay=self.lab.create(recording['settings'],recording)
        self.assertEqual(replay['observation'],session.observation)
        self.assertEqual(replay['journal'],session.journal)
        again=self.lab.session(replay['session']).export()
        self.assertEqual(again['stone_advice_records'],recording['stone_advice_records'])
        restored=session.undo()
        self.assertEqual(restored['observation'],before)
        self.assertEqual(restored['stone_advice'],advice)

    def test_wire_preserves_elapsed_spending_effect_clocks_and_source_stream(self):
        state='''schema=1
room=75
run_number=2
phase=recovery
resume_phase=encounter
encounter=boss
hp_percent=0
keys=3
gems=greasy,rusty
offered_gems=greasy,rusty,hero,pearl,explorer,blood
paid_heals=2
resources=1,2,3,4,5,6
shops_seen=2,3
trial_seen=true
'''
        session,_=self.create({**CONFIG,'initial_state':state,'budget':100})
        session.step({'kind':'heal_step'})
        session.step(next(a for a in session.observation['legal_actions'] if a['kind']=='wait' and 12<a['value']<24))
        for action in ({'kind':'reenter'},{'kind':'fight'}):session.step(action)
        o=session.observation
        self.assertEqual(o['phase'],'gems');self.assertEqual(o['room'],76)
        self.assertGreater(o['elapsed_hours'],12);self.assertGreater(o['mushrooms_spent'],0)
        self.assertEqual(o['paid_heals'],3)
        self.assertLess(o['blessings'][0]['starts_at'],o['turn'])
        text=self.wire(session)
        with VectorEnv(LIB,[PROFILE],1,budgets=[100],auto_reset=False,deadline_hours=168,event_start_hour=17) as env:
            env.reset();lab=EffectLab(env)
            with lab.planning_fixture(text) as snapshot:lab.restore(0,snapshot,17)
            obs,masks=lab.observe();expected,expected_masks,_=encode(o,env.observation_names,env.action_names)
            np.testing.assert_array_equal(obs,expected);np.testing.assert_array_equal(masks,expected_masks)
            native=lab.visible()
            for key in set(native)-{'suggestions','outcomes'}:self.assertEqual(native[key],o[key],key)
            for bad in (text+' extra',text[:30],text.replace('sfld-planning-v1','sfld-planning-v99'),text.replace(' 0.6.12 ',' 0.0.0 ')):
                with self.assertRaises(RuntimeError):lab.planning_fixture(bad)
        self.assertEqual(session.observation,o)

    def test_unavailable_adviser_keeps_manual_choice_legal(self):
        session,payload=self.create()
        with patch('sfld_rl.runtime_stone_advice.compare',side_effect=RuntimeError('Test failure')):
            result=session.final_stone_advice(0,payload['stone_advice']['state_key'])
        self.assertEqual(result['stone_advice']['status'],'unavailable')
        after=session.step({'kind':'choose_gem','index':2})
        self.assertIn('pendant',after['observation']['gems'])

    def test_imported_advice_does_not_control_new_choice(self):
        session,_=self.create();recording=session.export();key=session.stone_state_key()
        recording['stone_advice_records']={key:dict(status='ready',action=dict(kind='choose_gem',index=1,value=0),fake=True)}
        replay=self.lab.create(recording['settings'],recording)
        self.assertEqual(replay['stone_advice']['status'],'pending')

    def test_retry_after_missing_library_preserves_the_current_run(self):
        session,payload=self.create();before=copy.deepcopy(session.observation)
        key=payload['stone_advice']['state_key']
        with patch('sfld_rl.runtime_stone_advice.compare',side_effect=FileNotFoundError('sfld_rl.dll')):
            failed=session.final_stone_advice(0,key)
        self.assertEqual(failed['stone_advice']['status'],'unavailable')
        with patch('sfld_rl.runtime_stone_advice.compare',side_effect=AssertionError('Must retry explicitly')):
            self.assertEqual(session.final_stone_advice(0,key),failed)
        with self.assertRaises(ValueError):session.final_stone_advice(0,key,retry='true')
        ready=session.final_stone_advice(0,key,retry=True)
        self.assertEqual(ready['stone_advice']['status'],'ready',ready['stone_advice'])
        self.assertEqual(session.observation,before);self.assertEqual(session.history,[])
        with patch('sfld_rl.runtime_stone_advice.compare',side_effect=AssertionError('Ready result should stay cached')):
            self.assertEqual(session.final_stone_advice(0,key,retry=True),ready)

    def test_earlier_choices_and_disabled_setting_do_not_request_rollouts(self):
        state='schema=1\nroom=26\nrun_number=2\nphase=gems\nhp_percent=50\nkeys=3\ngem_offers=moonstone,thirsty,deceit\n'
        session,payload=self.create({**CONFIG,'initial_state':state})
        self.assertIsNone(payload['stone_advice'])
        with self.assertRaises(ValueError):session.final_stone_advice(0)
        _,payload=self.create({**CONFIG,'final_stone_adviser':False})
        self.assertIsNone(payload['stone_advice'])


if __name__=='__main__':unittest.main()
