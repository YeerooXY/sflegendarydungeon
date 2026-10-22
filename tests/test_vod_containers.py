"""Container denominators preserve skips, uncertainty and multiple rewards."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from analyze_vod_containers import describe

def row(ident='one'):
    return {'observation_id':ident,'context':{'gems':[],'stone_status':'verified'},
            'initial_style':'wooden_box','interaction':'opened','ordinary_loot_eligible':True,
            'persistent_blessing':False,'instant_healing':False,'rewards':[]}

class ContainerCounts(unittest.TestCase):
    def test_unknown_refresh_is_not_a_negative(self):
        r=row();r['persistent_blessing']=None
        s=describe([r])['by_greed']['false']
        self.assertEqual(s['positive_effect_unresolved'],1)
        self.assertEqual(s['positive_effect_fraction_bounds'],[0,1])

    def test_skips_mimics_and_unobserved_searches_are_not_draws(self):
        for interaction in ['skipped','awakened_mimic','unknown']:
            r=row();r.update(interaction=interaction,ordinary_loot_eligible=False,
                             persistent_blessing=None,instant_healing=None)
            self.assertEqual(describe([r])['opened_ordinary_loot_containers'],0)
            r['ordinary_loot_eligible']=True
            with self.assertRaises(ValueError):describe([r])

    def test_multi_rewards_count_one_appearance_in_each_category(self):
        r=row();r['persistent_blessing']=True
        r['rewards']=[{'kind':'persistent_blessing'},{'kind':'gold'},{'kind':'gold'}]
        s=describe([r])['by_greed']['false']
        self.assertEqual(s['opened_ordinary_containers'],1)
        self.assertEqual(s['positive_effect_at_least_one'],1)
        self.assertEqual(s['reward_event_presence'],{'persistent_blessing':1,'gold':1})

    def test_instant_heal_is_positive_without_persistent_icon(self):
        r=row();r['instant_healing']=True
        self.assertEqual(describe([r])['by_greed']['false']['positive_effect_at_least_one'],1)

    def test_greed_groups_stay_separate_and_unknown_stones_are_rejected(self):
        r=row('greed');r['context']['gems']=['greed']
        result=describe([row(),r])
        for key in ['false','true']:self.assertEqual(result['by_greed'][key]['opened_ordinary_containers'],1)
        r['context']['stone_status']='unknown'
        with self.assertRaises(ValueError):describe([r])

    def test_duplicate_or_falsely_negative_unopened_visit_is_rejected(self):
        with self.assertRaises(ValueError):describe([row(),deepcopy(row())])
        r=row();r.update(interaction='skipped',ordinary_loot_eligible=False)
        with self.assertRaises(ValueError):describe([r])

if __name__=='__main__':unittest.main()
