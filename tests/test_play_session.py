"""Recommendation refreshes never send a command; execution is one-shot."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from sfld_study.controller import Controller,builtin_hash
from sfld_study.protocol import CLIENT_VERSION
from sfld_study.schema import StudyError
from sfld_study.session import PlaySession
from sfld_study.store import Store


class PlaySessionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'study.sqlite',create=True)
        self.sid=self.store.initialize([{'study_id':'fixture','world':'fixture'}],evidence='synthetic')[0]['subject']
        now=datetime.now(timezone.utc)
        self.store.configure((now-timedelta(minutes=1)).isoformat(),(now+timedelta(days=7)).isoformat(),CLIENT_VERSION)
        self.store.set_policy(self.sid,'conservative',builtin_hash());self.store.resume(self.sid)
        self.visible={'game_version':CLIENT_VERSION,'room':10,'stage':1,'status':10,'level':50,
                      'event_open':True,'ultimate':False,'theme':8,'hp_current':70,'hp_max':100,
                      'mushrooms':100,'keys':4,'encounter_type':1,'legal_actions':[{'kind':'interact','code':20}]}
        self.calls=[];self.fail=False;outer=self
        class Driver:
            def observe(self,subject):return deepcopy(outer.visible),None
            def execute(self,subject,chosen):
                outer.calls.append(chosen)
                assert len(outer.store.unresolved(subject))==1
                if outer.fail:raise OSError('synthetic timeout')
                outer.visible['room']+=1
                return deepcopy(outer.visible),None
        self.controller=Controller(self.store,Driver());self.now=1.
        self.session=PlaySession(self.controller,self.sid,clock=lambda:self.now)

    def tearDown(self):self.store.close();self.temp.cleanup()

    def test_recommendation_default_and_refresh_do_not_execute_or_commit_memory(self):
        for _ in range(3):self.assertFalse(self.session.tick()['executed'])
        self.assertEqual(self.calls,[]);self.assertEqual(self.store.subject(self.sid)['sequence'],0)
        self.assertEqual(self.store.spent(self.sid),{'charged':0,'reserved':0})
        self.assertIsNone(self.store.meta('memory:'+self.sid))

    def test_manual_execution_once_and_returned_action_cannot_be_modified(self):
        p=self.session.recommend();p['action']['code']=21
        self.assertTrue(self.session.execute(p['token'])['executed'])
        self.assertEqual(self.calls[0]['code'],20)
        with self.assertRaises(StudyError):self.session.execute(p['token'])
        self.assertEqual(len(self.calls),1)

    def test_state_and_menu_changes_invalidate_preview_without_sending(self):
        for key,value in [('keys',3),('legal_actions',[{'kind':'interact','code':21}])]:
            before=deepcopy(self.visible);p=self.session.recommend();self.visible[key]=value
            with self.assertRaisesRegex(StudyError,'state changed'):self.session.execute(p['token'])
            self.visible=before
        self.assertEqual(self.calls,[])

    def test_autoplay_requires_opt_in_and_stops_after_uncertain_outcome(self):
        with self.assertRaises(StudyError):self.session.set_autoplay('true')
        p=self.session.recommend()
        with self.assertRaises(StudyError):self.session.execute(p['token'],automatic=True)
        self.session.set_autoplay(True);self.assertTrue(self.session.tick()['executed'])
        self.fail=True
        with self.assertRaises(StudyError):self.session.tick()
        self.assertFalse(self.session.autoplay)
        self.assertEqual(self.store.unresolved(self.sid)[0]['state'],'unknown')
        with self.assertRaises(StudyError):self.session.tick()
        self.assertEqual(len(self.calls),2)

    def test_expiry_and_stop_consume_pending_recommendation(self):
        p=self.session.recommend();self.now+=31
        with self.assertRaisesRegex(StudyError,'expired'):self.session.execute(p['token'])
        p=self.session.recommend();self.session.set_autoplay(False)
        with self.assertRaises(StudyError):self.session.execute(p['token'])
        self.assertEqual(self.calls,[])

    def test_unknown_screen_stops_autoplay_before_any_command(self):
        self.session.set_autoplay(True);self.visible['legal_actions']=None
        with self.assertRaises(StudyError):self.session.tick()
        self.assertFalse(self.session.autoplay);self.assertEqual(self.calls,[])

    def test_direct_preview_failure_and_stale_tokens_disarm_autoplay(self):
        self.session.set_autoplay(True);self.visible['legal_actions']=None
        with self.assertRaises(StudyError):self.session.recommend()
        self.assertFalse(self.session.autoplay)
        self.session.set_autoplay(True)
        with self.assertRaises(StudyError):self.session.execute('stale')
        self.assertFalse(self.session.autoplay);self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
