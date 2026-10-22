"""Offline correctness checks: no VOD downloads or proprietary art required."""
from copy import deepcopy
import json
import importlib.util
from pathlib import Path
import random
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_vod.media import chunks,load_sources,seconds,sha256,write_json
from sfld_vod.batch import select_clips,subtract_intervals,screened_end
from sfld_vod.audit import sample_times
from sfld_vod.review import make_review,reviewed_counts,wilson
from sfld_vod.vision import Episodes,merge_intervals,rect


class ManifestTests(unittest.TestCase):
    def test_timestamps(self):
        self.assertEqual(seconds('2:44:00'),9840)
        self.assertEqual(seconds('38:00.5'),2280.5)
        for value in (True,-1,float('nan'),float('inf'),'1:60','-0.1:59','1:2:3:4','nan'):
            with self.subTest(value=value),self.assertRaises(ValueError):seconds(value)

    def test_windows_are_exact_and_disjoint(self):
        self.assertEqual(list(chunks([[100,1301],[2000,None]],2100,600)),
                         [(100,700),(700,1300),(1300,1301),(2000,2100)])
        with self.assertRaises(ValueError):list(chunks([[100,None]],50,600))
        with self.assertRaises(ValueError):list(chunks([[0,None]],50,0))

    def test_manifest_rejects_overlap_and_unsafe_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'sources.json'
            base={'schema':1,'sources':[{'id':'abc','url':'https://www.twitch.tv/videos/1','windows':[[0,10],[10,20]]}]}
            write_json(path,base);self.assertEqual(len(load_sources(path)),1)
            variants=[]
            for key,value in [('id','../outside'),('url','https://user:password@example.org'),('windows',[[0,20],[10,30]])]:
                data=deepcopy(base);data['sources'][0][key]=value;variants.append(data)
            data=deepcopy(base);data['sources']*=2;variants.append(data)
            for data in variants:
                write_json(path,data)
                with self.assertRaises(ValueError):load_sources(path)

    def test_clips_skip_contained_pilot_but_reject_partial_overlap(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for name,a,b in [('pilot',0,120),('full',0,600),('next',600,1200)]:
                write_json(folder/(name+'.json'),{'schema':1,'file':name+'.mp4','source_start':a,'source_end':b})
            self.assertEqual([c['file'] for _,c in select_clips(folder)],['full.mp4','next.mp4'])
            write_json(folder/'overlap.json',{'schema':1,'file':'x.mp4','source_start':900,'source_end':1500})
            with self.assertRaises(ValueError):select_clips(folder)

    def test_redundant_proof_crossing_full_section_seam_is_not_double_counted(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            for name,start,end in [('first',0,600),('second',600,1200),('proof',450,810)]:
                write_json(folder/(name+'.json'),{'schema':1,'file':name+'.mp4','source_start':start,'source_end':end})
            self.assertEqual([c['file'] for _,c in select_clips(folder)],['first.mp4','second.mp4'])

    def test_crop_validation(self):
        self.assertEqual(rect([.25,0,.5,1],640,360),(160,0,320,360))
        for value in ([0,0,2,1],[float('nan'),0,.5,1],[True,0,.5,1],[.9,0,.2,1]):
            with self.assertRaises(ValueError):rect(value,640,360)


class IntervalTests(unittest.TestCase):
    def test_union_does_not_double_count(self):
        self.assertEqual(merge_intervals([[10,20],[0,15],[30,30],[25,28],[28,29]]),[[0,20],[25,29]])
        self.assertEqual(merge_intervals([[0,2],[3,4]],gap=1),[[0,4]])

    def test_missing_coverage_and_complement(self):
        self.assertEqual(subtract_intervals([[0,100],[200,250]],[[10,20],[15,40],[80,205]]),
                         [[0,10],[40,80],[205,250]])
        self.assertEqual(subtract_intervals([[0,100]],[[0,120]]),[])

    def test_header_rounding_is_distinct_from_a_real_coverage_gap(self):
        clip={'source_start':100,'source_end':700}
        self.assertEqual(screened_end(clip,{'duration':599.9993333}),700)
        self.assertEqual(screened_end(clip,{'duration':599}),699)
        self.assertEqual(screened_end(clip,{'duration':605.1}),700)

    def test_audit_samples_weight_by_duration_not_number_of_fragments(self):
        samples=sample_times([[0,1],[100,199]],100,random.Random(42))
        self.assertEqual(sum(t<1 for t in samples),1)
        self.assertEqual(len(samples),100)
        self.assertEqual(samples,sample_times([[0,1],[100,199]],100,random.Random(42)))
        self.assertEqual(sample_times([],10,random.Random(42)),[])

    def test_episodes_count_stable_view_once_and_separate_after_encounter(self):
        class Frame:
            def copy(self):return self
        out=[];episodes=Episodes(out.append)
        # A pair prohibited by an earlier simulator assumption must still survive.
        choices=[{'kind':'mystery','score':.9,'alternatives':[['mystery',.9]]} for _ in range(2)]
        for t in (0,.125,.25):episodes.push(t,choices,Frame(),None)
        episodes.push(1,None,Frame(),None)
        episodes.push(2,choices,Frame(),None);episodes.flush()
        self.assertEqual(len(out),2);self.assertEqual(out[0]['samples'],3)
        self.assertEqual(out[0]['signature'],('mystery','mystery'))


def candidate(ident='a',source='source-2026'):
    return {'id':ident,'source_id':source,'image_sha256':'hash-'+ident}


def review(ident='a',status='decision',generation='ordinary',doors=('monster','locked'),traps=(None,None),context=None):
    return {'id':ident,'image_sha256':'hash-'+ident,'status':status,'generation':generation,
            'doors':[{'kind':kind,'trap':trap} for kind,trap in zip(doors,traps)],'context':context or {}}


class StatisticsTests(unittest.TestCase):
    def test_wilson_known_values_and_extremes(self):
        estimate=wilson(500,1000)
        self.assertAlmostEqual(estimate['wilson_95'][0],.4690696,places=6)
        self.assertAlmostEqual(estimate['wilson_95'][1],.5309304,places=6)
        self.assertLess(wilson(0,1000)['wilson_95'][1],.004)
        self.assertGreater(wilson(1000,1000)['wilson_95'][0],.996)
        for k,n in ((0,0),(-1,2),(3,2),(1.5,3)):
            with self.assertRaises(ValueError):wilson(k,n)

    def test_only_reviewed_ordinary_decisions_count(self):
        candidates=[candidate(c) for c in 'abcde']
        reviews=[review('a'),review('b',status='duplicate'),review('c',generation='scripted'),review('d',generation='unknown')]
        result=reviewed_counts(candidates,reviews)
        self.assertEqual(result['coverage']['unreviewed'],1)
        self.assertEqual(result['groups'][0]['decisions'],1)
        self.assertIsNone(result['groups'][0]['pair_has_trap'])

    def test_unknown_traps_are_never_negative(self):
        result=reviewed_counts([candidate(c) for c in 'abc'],[
            review('a',traps=(True,None)),review('b',traps=(False,False)),review('c',traps=(False,None))])['groups'][0]
        self.assertEqual(result['pair_has_trap']['successes'],1)
        self.assertEqual(result['pair_has_trap']['opportunities'],2)
        self.assertEqual(result['trap_status_unknown'],1)

    def test_source_and_stone_context_remain_separate(self):
        result=reviewed_counts([candidate('a','2024'),candidate('b'),candidate('c')],
                               [review('a'),review('b'),review('c',context={'gems':[]})])
        self.assertEqual(len(result['groups']),3)

    def test_pair_and_door_denominators(self):
        result=reviewed_counts([candidate(c) for c in 'ab'],[
            review('a',doors=('mystery','mystery')),review('b',doors=('monster','locked'))])['groups'][0]
        self.assertEqual(result['decisions'],2)
        self.assertEqual(result['presence_in_pair']['mystery']['successes'],1)
        self.assertEqual(result['physical_door_opportunities'],1)
        self.assertEqual(result['physical_door_frequencies']['locked']['estimate'],1)

    def test_duplicate_or_wrong_image_review_fails(self):
        for originals,reviews in [([candidate(),candidate()],[]),([candidate()],[review(),review()]),
                                   ([candidate()],[{**review(),'image_sha256':'different'}])]:
            with self.assertRaises(ValueError):reviewed_counts(originals,reviews)

    def test_repeated_source_run_room_is_not_two_observations(self):
        ctx={'run_id':'run-2','room':33,'stage':2}
        with self.assertRaises(ValueError):reviewed_counts([candidate('a'),candidate('b')],[review('a',context=ctx),review('b',context=ctx)])
        with self.assertRaises(ValueError):reviewed_counts([candidate()],[review(context={'room':33,'stage':1})])
        result=reviewed_counts([candidate()],[review(context={'room':33})])
        self.assertEqual(result['groups'][0]['context']['stage'],2)


class ReviewPageTests(unittest.TestCase):
    def test_generated_page_has_valid_escapes_and_local_image_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);image=folder/'frame.jpg';image.write_bytes(b'fixture image')
            row={**candidate(),'image':'frame.jpg','image_sha256':sha256(image),
                 'source_url':'https://www.youtube.com/watch?v=abc','note':'</script><script>alert(1)</script>'}
            (folder/'candidates.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
            page=make_review(folder).read_text(encoding='utf-8')
            self.assertNotIn('</script><script>alert',page)
            self.assertIn("join('\\n')+'\\n'",page)
            image.write_bytes(b'changed')
            with self.assertRaises(ValueError):make_review(folder)

    def test_review_rejects_active_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);image=folder/'frame.jpg';image.write_bytes(b'fixture')
            row={**candidate(),'image':'frame.jpg','image_sha256':sha256(image),'source_url':'javascript:alert(1)'}
            (folder/'candidates.jsonl').write_text(json.dumps(row)+'\n',encoding='utf-8')
            with self.assertRaises(ValueError):make_review(folder)


NATIVE_AVAILABLE=all(importlib.util.find_spec(m) for m in ('cv2','numpy','imageio_ffmpeg'))


@unittest.skipUnless(NATIVE_AVAILABLE,'Optional native VOD dependencies are not installed.')
class NativePipelineTests(unittest.TestCase):
    def test_additional_hud_reference_recovers_changed_appearance(self):
        import cv2
        import numpy as np
        from sfld_vod.vision import calibrate,gate_video,add_anchor
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);video=folder/'fixture.avi'
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'FFV1'),24,(320,224))
            self.assertTrue(writer.isOpened())
            rng=np.random.default_rng(43)
            first=rng.integers(0,255,(224,320,3),dtype=np.uint8)
            second=rng.integers(0,255,(224,320,3),dtype=np.uint8)
            for i in range(144):writer.write(first if i<48 else second if i<96 else np.zeros_like(first))
            writer.release()
            layout=folder/'layout.json';calibrate(video,layout,[0,0,1,1],1,'fixture',0)
            initial=gate_video(video,layout,folder/'before',fps=1,padding=0)
            self.assertEqual(initial['intervals'],[[0,2]])
            image=folder/'reference.png';cv2.imwrite(str(image),second)
            add_anchor(layout,image);add_anchor(layout,image)
            self.assertEqual(len(json.loads(layout.read_text())['additional_anchors']),1)
            updated=gate_video(video,layout,folder/'after',fps=1,padding=0)
            self.assertEqual(updated['intervals'],[[0,4]])

    def test_native_frame_stream_and_gate_on_known_positive_negative_scenes(self):
        import cv2
        import numpy as np
        from sfld_vod.vision import calibrate,gate_video
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);video=folder/'fixture.avi'
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'FFV1'),24,(320,224))
            self.assertTrue(writer.isOpened())
            positive=np.random.default_rng(42).integers(0,255,(224,320,3),dtype=np.uint8)
            for i in range(96):writer.write(positive if i<48 else np.zeros_like(positive))
            writer.release()
            layout=folder/'layout.json';calibrate(video,layout,[0,0,1,1],1,'fixture',0)
            result=gate_video(video,layout,folder/'gate',fps=1,padding=0)
            self.assertEqual(result['samples'],4)
            self.assertEqual(result['intervals'],[[0,2]])
            self.assertFalse(result['decode_warnings'])

    def test_scan_cannot_reuse_results_after_input_changes(self):
        from sfld_vod.vision import scan
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'video.mp4').write_bytes(b'changed video')
            write_json(folder/'layout.json',{});write_json(folder/'manifest.json',{})
            write_json(folder/'scan.json',{'inputs':{'video_sha256':'old'}})
            with self.assertRaisesRegex(ValueError,'Scan inputs changed'):
                scan(folder/'video.mp4',folder/'layout.json',folder,folder)


if __name__=='__main__':unittest.main()
