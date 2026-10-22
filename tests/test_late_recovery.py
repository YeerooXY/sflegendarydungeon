"""The final attempt must reserve boss health and time to actually act."""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from sfld_rl.late_recovery import recovery_action
from sfld_rl.player_ideas import profile_values
from visual_lab import Session

PROFILE=ROOT/'docs/key-calibration-2026-10-18/updated.profile'
BUILD=ROOT/os.environ.get('SFLD_TEST_BUILD','build-calibration-20261018')/'Release'


class LateRecoveryTests(unittest.TestCase):
    def observation(self,room=98,hours=16,hp=0,budget=0,**changes):
        fields=dict(schema=1,room=room,run_number=3,phase='recovery',resume_phase='doors',
                    hp_percent=hp,keys=0,gems='moonstone,spying,pearl',doors='cursed,mystery')
        if room==100:fields.update(resume_phase='encounter',encounter='boss');fields.pop('doors')
        fields.update(changes)
        state=''.join(f'{k}={v}\n' for k,v in fields.items())
        session=Session(BUILD/'sfld.exe',PROFILE,dict(initial_state=state,run_number=3,deadline_hours=hours,
                        budget=budget,neural_adviser=False,final_stone_adviser=False))
        self.addCleanup(session.close)
        return session

    def test_reported_room98_waits_for_boss_buffer_then_enters(self):
        s=self.observation();o=s.raw_observation;p=profile_values(PROFILE)
        original=next(a for a in o['legal_actions'] if a['kind']=='wait')
        chosen,_,rule=recovery_action(o,original,p)
        self.assertEqual(rule,'late_recovery_buffer')
        self.assertGreater(chosen['value']/24,.65)
        self.assertLess(chosen['value'],o['hours_left']-60/3600)
        s.observation['suggestions'].append(dict(strategy='test',action=chosen))
        s.step(chosen);o=s.raw_observation
        self.assertFalse(o['terminal'])
        chosen,_,_=recovery_action(o,next(a for a in o['legal_actions'] if a['kind']=='wait'),p)
        self.assertEqual(chosen['kind'],'reenter')

    def test_boss_wait_stops_at_max_damage_and_keeps_an_attempt(self):
        p=profile_values(PROFILE)
        for hours in (11,20):
            s=self.observation(room=100,hours=hours);o=s.raw_observation
            chosen,_,_=recovery_action(o,dict(kind='wait',index=0,value=hours),p)
            s.observation['suggestions'].append(dict(strategy='test',action=chosen))
            s.step(chosen);o=s.raw_observation
            self.assertFalse(o['terminal'])
            self.assertLessEqual(o['hp'],o['boss_damage_max']+.001+1e-9)
            self.assertGreater(o['hp'],.43)
            enter,_,_=recovery_action(o,next(a for a in o['legal_actions'] if a['kind']=='wait'),p)
            self.assertEqual(enter['kind'],'reenter')
            s.step(enter);s.step(dict(kind='fight',index=0,value=0))
            self.assertTrue(any(e['kind'] in ('damage','completed') for e in s.raw_observation['outcomes']))

    def test_does_not_override_refills_early_rooms_or_sleep(self):
        p=profile_values(PROFILE)
        for settings in (dict(budget=100),dict(room=96),dict(hours=48)):
            o=self.observation(**settings).raw_observation
            a=next(a for a in o['legal_actions'] if a['kind']=='wait')
            self.assertEqual(recovery_action(o,a,p),(a,'',None))
        o=self.observation().raw_observation;o['sleep_until_hour']=8
        a=o['legal_actions'][0]
        self.assertEqual(recovery_action(o,a,p),(a,'',None))

    def test_desktop_and_batch_execute_identical_recovery(self):
        import numpy as np
        import torch
        from sfld_rl.env import VectorEnv
        from sfld_rl.effect_lab import EffectLab
        from sfld_rl.visual_policy import VisualPolicy
        from sfld_rl.interaction_policy import InteractionPolicy
        s=self.observation();s.process.stdin.write('planning_snapshot\n');s.process.stdin.flush()
        snapshot=s._read_wire()['planning_state']
        policy=VisualPolicy()
        with VectorEnv(BUILD/'sfld_rl.dll',[PROFILE],1,budgets=[0],deadline_hours=16,auto_reset=False) as env:
            env.reset();lab=EffectLab(env)
            with lab.planning_fixture(snapshot) as snap:lab.restore(0,snap,773773)
            obs,masks=lab.observe();o=lab.visible()
            with torch.inference_mode():proposed=policy.model.actions(torch.as_tensor(obs),torch.as_tensor(masks),greedy=True)[0].numpy()
            actions,targets,_=InteractionPolicy(env,profile_values(PROFILE)).apply(obs,masks,proposed)
            policy.advise(o,{},PROFILE);choice=o['suggestions'][-1]['action']
            self.assertEqual(choice['kind'],'wait');self.assertGreater(targets[0],.65)
            self.assertAlmostEqual(targets[0],o['hp']+choice['value']/24)
            s.observation['suggestions'].append(dict(strategy='neural',action=choice))
            s.step(choice);env.step(actions,wait_targets=targets)
            self.assertAlmostEqual(s.raw_observation['hp'],lab.visible()['hp'])
            self.assertAlmostEqual(s.raw_observation['hours_left'],lab.visible()['hours_left'])

    def test_custom_target_rounding_stays_inside_native_limits(self):
        import numpy as np
        from sfld_rl.env import VectorEnv
        from sfld_rl.effect_lab import EffectLab
        from sfld_rl.interaction_policy import InteractionPolicy
        with VectorEnv(BUILD/'sfld_rl.dll',[PROFILE],1,budgets=[0],deadline_hours=24,auto_reset=False) as env:
            env.reset();lab=EffectLab(env);rule=InteractionPolicy(env,profile_values(PROFILE))
            for hp in (0,12.1,20,33.1,66.6,80):
                state=f'schema=1\nroom=97\nrun_number=3\nphase=recovery\nresume_phase=doors\nhp_percent={hp}\nkeys=0\ngems=moonstone,spying,pearl\ndoors=monster,mystery\n'
                with lab.fixture(state,0) as snap:lab.restore(0,snap,315)
                obs,masks=lab.observe();start=env.action_names.index('wait_hp_20')
                enter=env.action_names.index('reenter')
                proposed=np.array([enter if masks[0,enter] else start+int(np.flatnonzero(masks[0,start:])[0])])
                actions,targets,_=rule.apply(obs,masks,proposed)
                self.assertGreaterEqual(targets[0],.2);self.assertLessEqual(targets[0],1)
                env.step(actions,wait_targets=targets)


if __name__=='__main__':unittest.main()
