"""Synthetic fixtures only: no credentials, internet, or game commands."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from sfld_study.automatic import AutomaticPilotDriver, project
from sfld_study.controller import Controller, arm_pilot
from sfld_study.protocol import AUTOMATIC_CLIENT_VERSION
from sfld_study.schema import StudyError, action
from sfld_study.store import Store


def packet(status=1,room=0,types=(1,1001,99999),payloads=(0,0,87654321),traps=(0,0),**extra):
    save=[0]*50
    save[0]=123456789;save[2:5]=[10000,10000,10000];save[15]=status
    save[17]=room;save[18]=100;save[19:22]=types;save[22:25]=payloads;save[25:27]=traps
    now=int(datetime.now(timezone.utc).timestamp())
    value={'iadungeonsave':'/'.join(map(str,save)),'iadungeonsoulstones':'/'.join(['0']*36),
           'resources':'500000/100','serverversion':'31500','timestamp':str(now),
           'iadungeontime':f'8/{now-100}/{now+604700}/{now+604800}',
           'ownplayersavecharacter':'0/1/0/50','iadungeon20cost':'10'}
    value.update(extra);return value


class Projection(unittest.TestCase):
    def test_entry_needs_observed_health_level_and_theme(self):
        v,_,why=project(packet(status=0))
        self.assertEqual(why,'automatic_menu');self.assertEqual(v['legal_actions'][0]['kind'],'start')
        p=packet(status=0,ownplayersavecharacter='0/1/0/49')
        self.assertEqual(project(p)[2],'entry_state_incomplete_or_not_ready')
        self.assertEqual(project(packet(status=101))[2],'recovery_or_restart_requires_review')
    def test_only_visible_doors_and_current_payload(self):
        p=packet();v,raw,why=project(p)
        self.assertEqual(why,'automatic_menu');self.assertEqual(v['room'],1)
        self.assertEqual([d['kind'] for d in v['doors']],['monster','mystery'])
        self.assertIsNone(v['encounter_variant'])
        self.assertNotIn('87654321',json.dumps(v));self.assertIn('87654321',raw['iadungeonsave'])
        v,_,_=project(packet(status=10,room=1,types=(100,9999,9999),payloads=(300,2222222,3333333)))
        self.assertEqual(v['room'],1);self.assertEqual(v['container_kind'],'warrior_skeleton')
        self.assertNotIn('2222222',json.dumps(v))

    def test_unknown_version_delta_and_room_stop(self):
        with self.assertRaises(StudyError):project(packet(),'31.999.1')
        p=packet();p['iadungeonsave(0)']='1/2'
        self.assertEqual(project(p)[2],'delta_requires_review')
        v,_,why=project(packet(status=10,room=1,types=(319,0,0)))
        self.assertEqual(why,'special_room_requires_review');self.assertIsNone(v['legal_actions'])

    def test_key_door_masks_trap_and_modified_cost_stop(self):
        p=packet(types=(1002,1001,0),traps=(1,0))
        v,_,_=project(p);self.assertEqual([a['code'] for a in v['legal_actions']],[2])
        s=list(map(int,p['iadungeonsave'].split('/')));s[39]=1
        p['iadungeonsave']='/'.join(map(str,s));v,_,_=project(p)
        self.assertEqual([a['code'] for a in v['legal_actions']],[5,2])
        s[5]=4;s[11]=40001;p['iadungeonsave']='/'.join(map(str,s))
        self.assertEqual([a['code'] for a in project(p)[0]['legal_actions']],[13,2])
        s[8]=105;s[14]=40002;p['iadungeonsave']='/'.join(map(str,s))
        self.assertEqual(project(p)[2],'modified_key_door_requires_review')

    def test_opening_shop_is_free_and_paid_shop_checks_costs_separately(self):
        p=packet(status=10,room=5,types=(315,0,0),iamerchant='2/40001/0/3/50080/0')
        v,_,why=project(p);self.assertEqual(why,'automatic_menu')
        self.assertEqual(len(v['legal_actions']),3)
        self.assertTrue(all(a['mushroom_quote']==0 for a in v['legal_actions']))
        self.assertNotIn(80,[a.get('code') for a in v['legal_actions']])
        p['iamerchant']='2/40001/4/3/50080/5'
        paid_menu=project(p)[0]['legal_actions']
        self.assertEqual([a['code'] for a in paid_menu],[51,80])
        self.assertEqual([a['mushroom_quote'] for a in paid_menu],[0,1])


class Execution(unittest.TestCase):
    def test_journal_before_send_timeout_is_not_retried(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td);store=Store(directory/'study.sqlite',create=True)
            sid=store.initialize([{'study_id':'fixture','world':'fixture'}],evidence='live')[0]['subject']
            now=datetime.now(timezone.utc)
            store.configure((now-timedelta(minutes=1)).isoformat(),(now+timedelta(days=7)).isoformat(),
                            AUTOMATIC_CLIENT_VERSION,server_version='31500')
            store.set_policy(sid,'human','1'*64);store.resume(sid);arm_pilot(store,[sid],2)
            calls=[]
            class Session:
                def execute(self,chosen):
                    self_check=store.unresolved(sid)
                    assert len(self_check)==1 and self_check[0]['state']=='pending'
                    calls.append(chosen)
                    raise OSError('synthetic timeout')
            driver=AutomaticPilotDriver(store,directory,lambda subject:(Session(),packet()),lambda:None)
            controller=Controller(store,driver,live=True)
            for _ in range(2):
                with self.assertRaises(StudyError):controller.step(sid,selected={'kind':'interact','code':2})
            self.assertEqual(len(calls),1)
            self.assertEqual(store.unresolved(sid)[0]['state'],'unknown')
            store.close()


try:
    import numpy
    import torch
    from sfld_study.learned import LearnedPilot, encode
except ImportError:
    LearnedPilot=None


@unittest.skipIf(LearnedPilot is None,'RL optional dependencies unavailable')
class Learned(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.policy=LearnedPilot()

    def visible(self,p):
        v,_,why=project(p);self.assertEqual(why,'automatic_menu')
        v.update(budget_remaining=0,elapsed_seconds=100,run_number_event=1)
        return v

    def test_actual_checkpoint_selects_exact_menu_and_tracks_only_seen_stones(self):
        from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA
        from sfld_study import learned
        self.assertEqual((learned.CHECKPOINT,learned.CHECKPOINT_SHA),(CHECKPOINT,CHECKPOINT_SHA))
        self.assertEqual(self.policy.rule.variant,'learned')
        v=self.visible(packet());result=self.policy.choose(v,None)
        self.assertIn(result['action'],v['legal_actions'])
        v=self.visible(packet(status=10,room=1,types=(100,99999,0),payloads=(300,777777,0)))
        obs,mask,mapped,memory=encode(v,result['memory'],self.policy.names,self.policy.actions)
        self.assertEqual(obs.shape,(1,460));self.assertEqual(mask.sum(),2)
        self.assertEqual(obs[0,self.policy.names.index('encounter.skeleton')],1)
        self.assertEqual(obs[0,self.policy.names.index('heal_step_price_div100')],numpy.float32(.1))
        self.assertEqual(sum(obs[0,i] for i,n in enumerate(self.policy.names) if n.startswith('offered_gem.')),0)

    def test_missing_history_and_unknown_required_values_stop(self):
        v=self.visible(packet(room=40))
        with self.assertRaises(StudyError):self.policy.choose(v,None)
        v=self.visible(packet());v['event_seconds_left']=None
        with self.assertRaises(StudyError):self.policy.choose(v,None)

    def test_guaranteed_escape_search_uses_effective_chance(self):
        from sfld_rl.escape_loot import EscapeLoot
        v=self.visible(packet());memory=self.policy.choose(v,None)['memory']
        p=packet(status=10,room=1,types=(100,0,0),payloads=(300,0,0))
        g=[0]*36;g[0]=2;g[6]=18;p['iadungeonsoulstones']='/'.join(map(str,g))
        v=self.visible(p);obs,mask,mapped,_=encode(v,memory,self.policy.names,self.policy.actions)
        skip=self.policy.actions.index('skip');interact=self.policy.actions.index('interact')
        actual,_,_=self.policy.rule.apply(obs,mask,numpy.array([skip]))
        self.assertEqual(actual[0],interact)
        obs[0,self.policy.names.index('owned_gem.moonstone')]=0
        actual,_,_=self.policy.rule.apply(obs,mask,numpy.array([skip]));self.assertEqual(actual[0],skip)

    def test_paid_reroll_quote_unrestricted_keys_and_exhausted_budget_mask(self):
        initial=self.visible(packet());initial['budget_remaining']=5
        memory=self.policy.choose(initial,None)['memory'];memory['room']=34;memory['turn']=33
        p=packet(status=10,room=35,types=(315,0,0),iamerchant='1/50010/1/7/10030/1')
        raw=list(map(int,p['iadungeonsave'].split('/')));raw[39]=4;p['iadungeonsave']='/'.join(map(str,raw))
        v=self.visible(p);v['budget_remaining']=5
        reroll=next(a for a in v['legal_actions'] if a.get('code')==80)
        self.assertEqual((reroll['mushroom_quote'],reroll['quote_source']),(1,'ui'))
        _,mask,_,_=encode(v,memory,self.policy.names,self.policy.actions)
        self.assertTrue(mask[0,self.policy.actions.index('reroll')])
        v['budget_remaining']=0
        _,mask,_,_=encode(v,memory,self.policy.names,self.policy.actions)
        self.assertFalse(mask[0,self.policy.actions.index('reroll')])
        raw[39]=3;p['iadungeonsave']='/'.join(map(str,raw))
        self.assertTrue(any(a.get('code')==80 for a in project(p)[0]['legal_actions']))

    def test_policy_history_cannot_silently_change_checkpoint(self):
        from sfld_rl.visual_policy import CHECKPOINT_SHA
        v=self.visible(packet());memory=self.policy.choose(v,None)['memory']
        self.assertEqual(memory['checkpoint_sha256'],CHECKPOINT_SHA)
        memory['checkpoint_sha256']='0'*64
        with self.assertRaisesRegex(StudyError,'different checkpoint'):self.policy.choose(v,memory)

    def test_fresh_entry_then_learned_moves_are_journaled(self):
        with tempfile.TemporaryDirectory() as td:
            directory=Path(td);store=Store(directory/'study.sqlite',create=True)
            sid=store.initialize([{'study_id':'fixture','world':'fixture'}],evidence='live')[0]['subject']
            now=datetime.now(timezone.utc)
            store.configure((now-timedelta(minutes=1)).isoformat(),(now+timedelta(days=7)).isoformat(),
                            AUTOMATIC_CLIENT_VERSION,server_version='31500')
            store.set_policy(sid,'learned','2'*64);store.resume(sid);arm_pilot(store,[sid],4)
            current=[packet(status=0)]
            calls=[]
            class Session:
                def execute(self,chosen):
                    calls.append(chosen)
                    assert len(store.unresolved(sid))==1
                    if chosen['kind']=='start':current[0]=packet()
                    elif chosen['code'] in (1,2):
                        current[0]=packet(status=10,room=1,types=(1 if chosen['code']==1 else 100,0,0),payloads=(0,0,0))
                    else:current[0]=packet(status=100,room=1)
                    return current[0]
            session=Session()
            driver=AutomaticPilotDriver(store,directory,lambda subject:(session,current[0]),lambda:None)
            controller=Controller(store,driver,live=True)
            class Policy:
                expected_hash='2'*64
                choose=self.policy.choose
            for _ in range(3):self.assertEqual(controller.step(sid,external=Policy())['state'],'confirmed')
            self.assertEqual(len(calls),3);self.assertEqual(calls[0]['kind'],'start')
            self.assertEqual(store.spent(sid),{'charged':0,'reserved':0})
            self.assertEqual(store.unresolved(sid),[])
            self.assertIsNotNone(store.meta('memory:'+sid))
            store.close()


if __name__=='__main__':unittest.main()
