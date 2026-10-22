"""Temporal counter regression cases: omissions must never become observations."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from sfld_vod.room_sequence import build_spans,sequence_blocks,associate_candidates,attach_shared_sequence,SEQUENCE_DIR
import json
import tempfile


def sample(t,room,fresh=True):
    return {'time':t,'room':room,'fresh':fresh,
            'proof':{'image':f'{t}.png','source_seconds':t,'sha256':'example'} if fresh else None}


def span_samples(start,room,count=5):
    return [sample(start+i*.125,room) for i in range(count)]


class RoomSequenceTests(unittest.TestCase):
    def test_single_read_is_not_temporal_corroboration(self):
        rows=[sample(0,12)]+[sample(i*.125,12,False) for i in range(1,8)]
        self.assertFalse(build_spans(rows,'s')[0]['stable'])

    def test_short_same_room_blank_preserves_missing_interval(self):
        rows=span_samples(0,12)+[sample(.625,None)]+span_samples(.75,12)
        spans=build_spans(rows,'s')
        self.assertEqual(len(spans),1)
        self.assertEqual(spans[0]['blank_intervals'],[[.625,.625]])
        self.assertTrue(spans[0]['stable'])

    def test_gap_numbers_are_reported_not_fabricated(self):
        spans=build_spans(span_samples(0,12)+span_samples(1,16),'s')
        blocks=sequence_blocks(spans,'s')
        self.assertEqual(blocks[0]['observed_rooms'],[12,16])
        self.assertEqual(blocks[0]['missing_between_anchors'],[13,14,15])

    def test_reset_creates_new_unverified_sequence(self):
        spans=build_spans(span_samples(0,99)+span_samples(1,100)+span_samples(2,1),'s')
        blocks=sequence_blocks(spans,'s')
        self.assertEqual(len(blocks),2)
        self.assertEqual(blocks[1]['run_identity'],'unverified')
        self.assertNotEqual(spans[0]['sequence_id'],spans[-1]['sequence_id'])

    def test_same_number_after_long_absence_cannot_deduplicate(self):
        spans=build_spans(span_samples(0,25)+span_samples(200,25),'s')
        blocks=sequence_blocks(spans,'s')
        self.assertEqual(len(blocks),2)
        self.assertNotEqual(spans[0]['id'],spans[1]['id'])

    def test_boundary_crossing_candidate_is_not_collapsed(self):
        spans=build_spans(span_samples(0,8)+span_samples(.625,9),'s')
        sequence_blocks(spans,'s')
        rows=[{'id':'a','best_time':.5,'positive_frames':[.5,.625]}]
        link=associate_candidates(rows,spans)['a']
        self.assertEqual(link['status'],'crosses_counter_boundary')
        self.assertIsNone(link['room'])
        self.assertEqual(link['possible_rooms'],[8,9])
        self.assertEqual(link['group_id'],'a')

    def test_repeated_views_share_a_review_group_without_becoming_verified(self):
        spans=build_spans(span_samples(0,8),'s');sequence_blocks(spans,'s')
        rows=[{'id':ident,'best_time':t} for ident,t in [('a',.125),('b',.375)]]
        links=associate_candidates(rows,spans)
        self.assertEqual(links['a']['group_id'],links['b']['group_id'])
        self.assertIn('Proposed',links['a']['deduplication'])

    def test_exact_next_sample_belongs_only_to_new_number(self):
        spans=build_spans(span_samples(0,8)+span_samples(.625,9),'s')
        sequence_blocks(spans,'s')
        link=associate_candidates([{'id':'a','best_time':.625}],spans)['a']
        self.assertEqual(link['room'],9)

    def test_best_image_inside_bridged_blank_remains_unread(self):
        spans=build_spans(span_samples(0,8)+[sample(.625,None)]+span_samples(.75,8),'s')
        sequence_blocks(spans,'s')
        link=associate_candidates([{'id':'a','best_time':.625}],spans)['a']
        self.assertIsNone(link['room'])

    def test_visible_number_with_no_target_pair_still_counts_as_counter_coverage(self):
        spans=build_spans(span_samples(0,8)+span_samples(1,9)+span_samples(2,10),'s')
        blocks=sequence_blocks(spans,'s')
        rows=[{'id':'a','best_time':.25},{'id':'b','best_time':2.25}]
        links=associate_candidates(rows,spans)
        self.assertEqual(blocks[0]['observed_rooms'],[8,9,10])
        self.assertEqual(blocks[0]['missing_between_anchors'],[])
        self.assertEqual({r['room'] for r in links.values()},{8,10})

    def test_shared_timeline_preserves_verified_context_and_separates_players(self):
        left=build_spans(span_samples(0,8),'s-left');right=build_spans(span_samples(0,63),'s-right')
        blocks=sequence_blocks(left,'s-left')+sequence_blocks(right,'s-right')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/SEQUENCE_DIR;path.mkdir()
            (path/'catalog.json').write_text(json.dumps({'version':'test','sources':[],
                                                        'spans':left+right,'blocks':blocks}))
            rows=[{'id':'a','start':.125,'end':.375,'counter_view_id':'s-left','context':{'room':99}},
                  {'id':'b','start':.125,'end':.375,'counter_view_id':'s-right'},
                  {'id':'c','start':.125,'end':.375}]
            attach_shared_sequence(rows,folder,'s')
            self.assertEqual(rows[0]['context']['room'],99)
            self.assertEqual(rows[0]['counter_sequence']['room'],8)
            self.assertEqual(rows[1]['counter_sequence']['room'],63)
            self.assertIsNone(rows[2]['counter_sequence']['room'])


if __name__=='__main__':unittest.main()
