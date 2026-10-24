"""Damage thresholds, native revival effects and stateful recovery plans."""
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from engine_build import MISSING, build_dir, engine, library  # noqa: E402
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab
from sfld_rl.player_ideas import profile_values
from sfld_rl.recovery_thresholds import RecoveryThresholds, required_health

LIB=library('build-v0612')
PROFILE=ROOT/'profiles/synthetic.profile'


def recovery(room=10,hp=0,gems='',paid=0,encounter=None):
    return (f'schema=1\nroom={room}\nrun_number=2\nphase=recovery\nhp_percent={hp}\nkeys=0\n'+
            (f'gems={gems}\n' if gems else '')+f'paid_heals={paid}\nresume_phase='+('encounter' if encounter else 'doors')+'\n'+
            (f'encounter={encounter}\n' if encounter else 'doors=monster,mystery\n'))


@unittest.skipUnless(LIB.is_file(), MISSING)
class ThresholdTests(unittest.TestCase):
    def env(self,**kw):
        return VectorEnv(LIB,[PROFILE],1,budgets=[100],auto_reset=False,**kw)

    def restore(self,env,text):
        lab=EffectLab(env)
        with lab.fixture(text,100) as snap:lab.restore(0,snap,1234)
        return lab,lab.observe()

    def choose(self,rule,state,name,fallback='wait_hp_20'):
        return rule.apply(*state,np.array([rule.ai[name]]),np.array([rule.ai[fallback]]))[:2]

    def test_backward_thresholds_and_heal_cap(self):
        plain=required_health([[.13]*4],0,0)
        np.testing.assert_allclose(plain,[[.131,.261,.391,.521]])
        # Healing cannot rescue a lethal first hit, but reduces later needs.
        np.testing.assert_allclose(required_health([[.13]*3],.14,.05),[[.131,.171,.211]])
        # A >100% heal still cannot leave more than 100% before poison.
        self.assertTrue(np.isinf(required_health([[0,.9]],[[2,0]],[[.2,0]])[0,1]))
        self.assertTrue(np.isinf(required_health([[0]],0,1)[0,0]))
        np.testing.assert_allclose(required_health([[.13,.13]],0,.05,entry_damage=.1),[[.281,.461]])

    def test_thresholds_survive_forward_damage_heal_poison(self):
        rng=np.random.default_rng(10)
        damage=rng.uniform(.01,.35,(80,8));heal=rng.uniform(0,.4,(80,8));poison=rng.uniform(0,.1,(80,8))
        target=required_health(damage,heal,poison)
        for count in range(1,9):
            possible=target[:,count-1]<=1
            hp=target[possible,count-1].copy()
            for i in range(count):
                hp-=damage[possible,i]
                self.assertTrue((hp>0).all())
                hp=np.minimum(1,hp+heal[possible,i])-poison[possible,i]
                self.assertTrue((hp>0).all())

    def test_native_four_charges_and_rusty_heal_before_poison(self):
        with self.env() as env:
            env.reset();lab,_=self.restore(env,recovery(85,50,'greasy,rusty,time_traveler',encounter='empty'))
            env.step([env.action_names.index('reenter')]);o=lab.visible()
            self.assertEqual(o['blessings'][0]['remaining'],4)
            self.assertEqual(o['curses'][0]['remaining'],5)
            env.step([env.action_names.index('skip')]);o=lab.visible()
            self.assertAlmostEqual(o['hp'],.59)
            self.assertEqual(o['blessings'][0]['remaining'],3)
            self.assertEqual([x['kind'] for x in o['outcomes']],['healing','damage'])
            # At full health the capped heal occurs before the poison tick.
            lab,_=self.restore(env,recovery(85,100,'greasy,rusty,time_traveler',encounter='empty'))
            env.step([env.action_names.index('reenter')]);env.step([env.action_names.index('skip')])
            self.assertAlmostEqual(lab.visible()['hp'],.95)

    def test_rusty_and_fourth_heal_change_targets(self):
        with self.env() as env:
            env.reset();rule=RecoveryThresholds(env,profile_values(PROFILE),'wait_max')
            _,state=self.restore(env,recovery(35,20,'rusty'))
            target,_=rule.health_targets(state[0],np.array([.2]))
            self.assertAlmostEqual(target[0],.2057+.05+.001)
            _,state=self.restore(env,recovery(60,20,'greasy,time_traveler'))
            _,th=rule.health_targets(state[0],np.array([.2]))
            self.assertAlmostEqual(th[0,4],5*.2338-4*.1+.001)
            _,state=self.restore(env,recovery(60,20,'greasy,explorer'))
            _,th=rule.health_targets(state[0],np.array([.2]))
            self.assertAlmostEqual(th[0,4],5*.2338-3*.1+.001)
            _,state=self.restore(env,recovery(60,20,'rusty,moonstone'))
            lab=EffectLab(env);env.step([rule.ai['reenter']])
            self.assertEqual(lab.visible()['curses'][0]['remaining'],6)

    def test_native_poison_kills_at_twentyone_but_target_survives(self):
        with self.env() as env:
            env.reset()
            for hp,survives in ((21,False),(25.67,True)):
                lab,_=self.restore(env,recovery(35,hp,'rusty',encounter='monster'))
                env.step([env.action_names.index('reenter')]);env.step([env.action_names.index('fight')])
                o=lab.visible()
                self.assertEqual(o['room'],36)
                self.assertEqual(o['hp']>0,survives)
            lab,_=self.restore(env,recovery(85,20,'greasy,rusty,time_traveler',encounter='monster'))
            env.step([env.action_names.index('reenter')]);env.step([env.action_names.index('fight')])
            self.assertEqual(lab.visible()['hp'],0)
            self.assertFalse(any(x['kind']=='healing' for x in lab.visible()['outcomes']))

    def test_one_partial_then_wait_without_moving_target(self):
        with self.env() as env:
            env.reset();lab,state=self.restore(env,recovery())
            rule=RecoveryThresholds(env,profile_values(PROFILE),'hybrid_max')
            actions,targets=self.choose(rule,state,'heal_full')
            self.assertEqual(actions[0],rule.ai['heal_step'])
            state=env.step(actions,wait_targets=targets)[:2]
            actions,targets=self.choose(rule,state,'heal_full')
            self.assertAlmostEqual(targets[0],.289)
            state=env.step(actions,wait_targets=targets)[:2]
            self.assertAlmostEqual(lab.visible()['mushrooms_spent'],10)
            actions,targets=self.choose(rule,state,'reenter')
            self.assertEqual(actions[0],rule.ai['reenter'])
            self.assertEqual(targets[0],-1)

    def test_twenty_allowed_twentyfive_blocked(self):
        for paid,expected in ((2,'heal_step'),(3,'wait_hp_20')):
            with self.env() as env:
                env.reset();_,state=self.restore(env,recovery(paid=paid))
                rule=RecoveryThresholds(env,profile_values(PROFILE),'hybrid_max')
                actions,targets=self.choose(rule,state,'heal_full')
                self.assertEqual(actions[0],rule.ai[expected])
                if paid==3:self.assertAlmostEqual(targets[0],.289)

    def test_sleep_overshoot_enters_without_new_threshold(self):
        with self.env(sleep_until_hour=8,event_start_hour=17) as env:
            env.reset()
            rule=RecoveryThresholds(env,profile_values(PROFILE),'wait_max')
            # Stage-four hit plus poison needs >7 hours, crossing midnight.
            lab,state=self.restore(env,recovery(85,0,'rusty,explorer,greed'))
            actions,targets=self.choose(rule,state,'wait_hp_40')
            state=env.step(actions,wait_targets=targets)[:2]
            self.assertEqual(lab.visible()['local_hour'],8)
            self.assertGreater(lab.visible()['hp'],targets[0])
            actions,_=self.choose(rule,state,'wait_hp_80')
            self.assertEqual(actions[0],rule.ai['reenter'])

    def test_late_event_uses_legal_current_health(self):
        with self.env(deadline_hours=1) as env:
            env.reset();_,state=self.restore(env,recovery(hp=20))
            rule=RecoveryThresholds(env,profile_values(PROFILE),'entry_max')
            actions,targets=self.choose(rule,state,'reenter')
            self.assertEqual(actions[0],rule.ai['reenter'])
            self.assertEqual(targets[0],-1)


if __name__=='__main__':unittest.main()
