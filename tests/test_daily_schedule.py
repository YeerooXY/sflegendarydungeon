"""Native vector/CLI boundaries for optional daily availability."""
import ctypes as ct
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab

LIB=Path(os.environ.get('SFLD_RL_LIB',ROOT/'build-v0612/Release/sfld_rl.dll'))
EXE=LIB.with_name('sfld.exe')
PROFILE=ROOT/'profiles/synthetic.profile'
RECOVERY='schema=1\nroom=10\nrun_number=1\nphase=recovery\nresume_phase=doors\nhp_percent=0\nkeys=0\ndoors=monster,mystery\n'


class DailyScheduleTests(unittest.TestCase):
    def env(self,**kwargs):
        return VectorEnv(LIB,[PROFILE],1,budgets=[100],auto_reset=False,**kwargs)

    def test_recovery_wait_and_schedule_preserving_snapshot(self):
        with self.env(event_start_hour=17,sleep_until_hour=8) as env:
            env.reset(); lab=EffectLab(env)
            with lab.fixture(RECOVERY,100) as snap: lab.restore(0,snap,5)
            lab.observe()
            actions=np.array([env.action_names.index('wait_hp_40')])
            env.step(actions)
            o=lab.visible()
            self.assertEqual(o['elapsed_hours'],15)
            self.assertEqual(o['local_hour'],8)
            self.assertAlmostEqual(o['hp'],15/o['scenario']['recovery_hours'])
            self.assertEqual(o['phase'],'recovery')
            self.assertEqual(o['mushrooms_spent'],0)
            self.assertTrue(any(e['kind']=='sleep' for e in o['outcomes']))
            with lab.capture(0) as snap:
                lab.restore(0,snap,9)
                self.assertEqual(o,lab.visible())

    def test_snapshot_with_different_schedule_rejected_atomically(self):
        with self.env(event_start_hour=17,sleep_until_hour=8) as a, self.env(event_start_hour=17) as b:
            a.reset();b.reset(); x=EffectLab(a);y=EffectLab(b);before=y.visible()
            with x.capture(0) as snap:
                with self.assertRaisesRegex(RuntimeError,'limits mismatch'): y.restore(0,snap,0)
            self.assertEqual(before,y.visible())

    def test_invalid_setter_and_reconfiguration_are_atomic(self):
        with self.env(event_start_hour=17,sleep_until_hour=8) as env:
            for wake,start in ((24,17),(8,-1),(float('nan'),17),(8,float('inf'))):
                self.assertEqual(env._lib.sfld_rl_set_daily_schedule(env._handle,wake,start),-1)
            env.reset();lab=EffectLab(env);before=lab.visible()
            self.assertEqual(before['event_start_hour'],17)
            self.assertEqual(before['sleep_until_hour'],8)
            self.assertEqual(env._lib.sfld_rl_set_daily_schedule(env._handle,0,0),-1)
            self.assertEqual(before,lab.visible())
        for key in ('sleep_until_hour','event_start_hour'):
            for value in (-1,24,float('nan'),float('inf')):
                with self.assertRaises(ValueError): self.env(**{key:value})

    def test_initial_sleep_and_terminal_deadline(self):
        with self.env(sleep_until_hour=8) as env:
            _,mask=env.reset();o=EffectLab(env).visible()
            self.assertEqual(o['elapsed_hours'],8)
            self.assertEqual(o['hp'],1)
            self.assertTrue(mask.any())
        with self.env(sleep_until_hour=8,deadline_hours=4) as env:
            _,mask=env.reset();o=EffectLab(env).visible()
            self.assertTrue(o['terminal'])
            self.assertEqual(o['elapsed_hours'],4)
            self.assertFalse(mask.any())

    def test_cli_records_schedule_and_rejects_bad_hours(self):
        args=[str(EXE),'event','--allow-assumptions','--profile',str(PROFILE),
              '--runs','4','--threads','1','--deadline-hours','168','--budget','0',
              '--sleep-until-hour','8','--event-start-hour','17']
        result=subprocess.run(args,text=True,capture_output=True,check=True)
        report=json.loads(result.stdout)
        self.assertEqual(report['event_start_hour'],17)
        self.assertEqual(report['sleep_until_hour'],8)
        self.assertTrue(report['valid'])
        result=subprocess.run(args[:-1]+['24'],text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0)


if __name__=='__main__':unittest.main()
