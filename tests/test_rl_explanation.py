"""Accounting checks for the separate explanatory wrapper."""
import importlib.util
from pathlib import Path
import sys
import unittest

AVAILABLE = all(importlib.util.find_spec(name) is not None for name in ('numpy', 'torch'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
if AVAILABLE:
    import explain_rl_policy as explain


@unittest.skipUnless(AVAILABLE, 'Optional NumPy/Torch dependencies are not installed')
class ExplanationTests(unittest.TestCase):
    def state(self, **changes):
        return dict(budget=50, phase='gems', stage=3, room=51, run_number=2, run_type='later', hp=.4,
                    keys=7, owned_gems=['moonstone'], one_hit=False, escape=False, recovery=False,
                    poison=False, broken_armor=False, clumsy=False, disarm=False, resume_phase='gems',
                    hours_left=80., mushrooms_left=30, heal_step_price=15, heal_full_price=29,
                    gem_offers=['rabbit','greasy','hero'], **changes)

    def test_stone_matchups_survive_without_duplicate_heal_exposure(self):
        model=explain.ExplainAudit();state=self.state()
        model.record('fixture',state,'heal_step','gem_0')
        model.record('fixture',state,'gem_1','gem_0')
        result=model.export()
        self.assertEqual(result['tables']['gem_choices'][0]['offered'],['rabbit','greasy','hero'])
        self.assertEqual(result['tables']['gem_choices'][0]['chosen'],'greasy')
        self.assertEqual(result['tables']['gem_choices'][0]['decisions'],1)
        self.assertEqual(result['tables']['healing'][0]['decisions'],1)
        self.assertEqual(result['summaries'][0]['decisions'],2)

    def test_door_availability_cost_and_traps_are_retained(self):
        state=self.state();state.update(phase='doors',doors=[dict(kind='locked',trap=False,cursed_trap=False,key_cost=1,available=False),
                                                           dict(kind='mystery',trap=True,cursed_trap=True,key_cost=0,available=True)])
        model=explain.ExplainAudit();model.record('fixture',state,'door_1','door_1')
        row=model.export()['tables']['doors'][0]
        self.assertEqual(row['doors'],state['doors'])
        self.assertEqual(row['chosen_types'],{'mystery':1})

    def test_overflow_keeps_counts_and_existing_contexts(self):
        counts=explain.BoundedCounts(limit=1);state=self.state()
        for context in ('a','b','c','a'):
            counts.add(dict(profile='fixture',budget=50,context=context),state,'fight','flee')
        rows=counts.export()
        self.assertEqual(sum(row['decisions'] for row in rows),4)
        self.assertEqual(counts.overflow_decisions,2)
        self.assertEqual(counts.detailed_rows,1)
        self.assertEqual(sum(row['decisions'] for row in rows if row.get('context_overflow')),2)
        self.assertTrue(all(sum(row['actions'].values())==row['decisions'] for row in rows))

    def test_shop_offer_strength_and_resource_direction(self):
        for kind,magnitude,duration,strong in [('one_hit',1.,4,False),('one_hit',1.,8,True),('elixir',.5,1,True),('recovery',.1,3,False)]:
            self.assertEqual(explain.offer_tier(dict(kind=kind,magnitude=magnitude,remaining=duration)), 'strong' if strong else 'weak')
        state=self.state();state.update(phase='curse_shop',opening_shop=False,
            offers=[dict(kind='poison',magnitude=.05,remaining=5,keys=1),dict(kind='hard_lock',magnitude=2,remaining=8,keys=4)])
        state['shop_offer_tiers']=[{**offer,'strength':explain.offer_tier(offer),'price_tier':'1-3','affordable':True} for offer in state['offers']]
        model=explain.ExplainAudit();model.record('fixture',state,'buy_0','skip')
        row=model.export()['tables']['shop_buys'][0]
        self.assertEqual(row['key_direction'],'received')
        self.assertEqual(row['key_amount'],1)
        self.assertEqual(model.export()['tables']['shop_choices'][0]['offers'],state['shop_offer_tiers'])

    def test_caps_are_per_budget_and_coarse_policy_tables_do_not_drop_contexts(self):
        counts=explain.BoundedCounts(limit=1);state=self.state()
        for budget in (0,50):
            for context in ('a','b'):
                counts.add(dict(profile='fixture',budget=budget,context=context),state,'fight','flee')
        self.assertEqual(counts.detailed_rows,2)
        self.assertEqual(counts.overflow_decisions,2)
        model=explain.ExplainAudit();state.update(phase='encounter',encounter='monster')
        model.tables['combat_context'].limit=1
        for stage in (1,2,3):
            model.record('fixture',{**state,'stage':stage},'fight','flee')
        result=model.export()
        self.assertEqual(result['explanation_extension']['tables']['combat_policy']['overflow_decisions'],0)
        self.assertEqual(sum(r['decisions'] for r in result['tables']['combat_policy']),3)
        self.assertEqual(len(result['tables']['combat_policy']),3)


if __name__=='__main__':
    unittest.main()
