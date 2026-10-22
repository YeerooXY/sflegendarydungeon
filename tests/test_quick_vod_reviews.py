import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from import_quick_vod_reviews import prepare,fingerprint
from prepare_quick_vod_review import proposed_labels
from recover_vod_choice_gaps import missing_spans,sample_times
from publish_quick_vod_review import apply_corrections


class QuickReviewTests(unittest.TestCase):
    def setUp(self):
        self.prediction={'id':'a','source_id':'s','image':{'image':'a.jpg','sha256':'hash','source_seconds':1},
                         'doors':[{'kind':'monster','trap':False},{'kind':'mystery','trap':None}]}
        self.vote={'id':'a','image':self.prediction['image'],'verdict':'correct','corrected':False,
                   'labels':copy.deepcopy(self.prediction['doors']),'prediction_fingerprint':fingerprint(self.prediction)}

    def run_vote(self,vote):
        return prepare({'schema':1,'kind':'sfld-quick-label-review','reviews':[vote]}, {'a':self.prediction})

    def test_correct_preserves_unknown_trap(self):
        summary,labels=self.run_vote(self.vote)
        self.assertEqual(summary['confirmed_image_records'],1)
        self.assertIsNone(labels[0]['doors'][1]['trap'])
        self.assertFalse(labels[0]['label_verification'][1]['trap'])

    def test_incorrect_and_duplicate_are_not_training_truth(self):
        for verdict in ('incorrect','duplicate'):
            self.vote['verdict']=verdict
            self.assertEqual(self.run_vote(self.vote)[1],[])

    def test_correction_records_confusion_without_changing_prediction(self):
        self.vote.update(verdict='incorrect',corrected=True,decision=True)
        self.vote['labels'][1]={'kind':'cursed','trap':True}
        summary,labels=self.run_vote(self.vote)
        self.assertEqual(labels[0]['doors'][1]['kind'],'cursed')
        self.assertEqual(self.prediction['doors'][1]['kind'],'mystery')
        self.assertIn({'suggested':'mystery','reviewed':'cursed','count':1},summary['door_confusions'])

    def test_changed_image_or_unshown_correct_label_is_rejected(self):
        for change in ('fingerprint','label'):
            vote=copy.deepcopy(self.vote)
            if change=='fingerprint':vote['prediction_fingerprint']='different image'
            else:vote['labels'][0]['kind']='wall'
            with self.assertRaises(ValueError):self.run_vote(vote)

    def test_abstention_does_not_promote_false_golden_alternative(self):
        door={'kind':None,'status':'unknown','alternatives':[['golden',.5067],['monster',.4591]]}
        trap={'present':True,'visual':'cleaver'}
        proposed=proposed_labels([door],[trap],True)
        self.assertIsNone(proposed[0]['kind'])
        self.assertTrue(proposed[0]['trap'])

    def test_model_update_keeps_votes_bound_to_original_displayed_labels(self):
        updated=copy.deepcopy(self.prediction);updated['doors'][0]['kind']=None
        summary,labels=prepare({'schema':1,'kind':'sfld-quick-label-review','reviews':[self.vote]},
                               {'a':updated},{'a':[self.prediction]})
        self.assertEqual(summary['confirmed_image_records'],1)
        self.assertEqual(labels[0]['doors'][0]['kind'],'monster')
        with self.assertRaises(ValueError):
            prepare({'schema':1,'kind':'sfld-quick-label-review','reviews':[self.vote]}, {'a':updated})

    def test_gaps_use_counter_sections_not_monster_mystery_filter(self):
        spans=[{'id':str(n),'stable':True,'room':n} for n in range(17,22)]
        rows=[{'frame_room_sequence':{'span_id':'17'}},{'frame_room_sequence':{'span_id':'21'}}]
        self.assertEqual([s['room'] for s in missing_spans({'spans':spans},rows)],[18,19,20])
        # A second visit with the same room number must not be suppressed.
        spans.append({'id':'18-again','stable':True,'room':18})
        self.assertIn('18-again',[s['id'] for s in missing_spans({'spans':spans},rows)])

    def test_recovery_samples_stay_in_visible_downloaded_counter_section(self):
        span={'start':10,'end':15,'blank_intervals':[[10.3,10.7]]}
        times=sample_times(span,9,14)
        self.assertTrue(times)
        self.assertTrue(all(10<=t<14 and not 10.3<=t<=10.7 for t in times))

    def test_manual_correction_preserves_raw_prediction_and_is_exact_image_only(self):
        bundle={'predictions':{'a':copy.deepcopy(self.prediction)}}
        evidence={'reviewer':'reviewer','scope':'image only','corrections':[
            {'id':'a','image_sha256':'hash','side':0,'kind':'golden','trap':False,'trap_visual':None}]}
        result=apply_corrections(bundle,evidence)
        self.assertEqual(result['predictions']['a']['doors'][0]['kind'],'golden')
        self.assertEqual(result['history']['a'][0]['doors'][0]['kind'],'monster')
        self.assertEqual(bundle['predictions']['a']['doors'][0]['kind'],'monster')
        self.assertEqual(result,apply_corrections(result,evidence))
        evidence['corrections'][0]['image_sha256']='different frame'
        with self.assertRaises(ValueError):apply_corrections(bundle,evidence)


if __name__=='__main__':unittest.main()
