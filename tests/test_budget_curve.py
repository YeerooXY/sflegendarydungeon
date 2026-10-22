import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from benchmark_budget_curve import FIELDS,project


def fixture():
    def snap(clears,started,spent,gold,legend):
        return {**dict.fromkeys(FIELDS,0),'completions':clears,'runs_started':started,
                'mushrooms':spent,'recovery_mushrooms':spent,'gold_units':gold,
                'legendaries':legend,'epics':clears,'elapsed_hours':40*started}
    row=snap(2,3,90,30,3);row.update(event=0,supply=100,
        prefixes=[snap(1,1,20,8,1),snap(2,2,60,18,3)])
    return row


class UltimateAccounting(unittest.TestCase):
    def test_fee_is_paid_on_entry_including_unfinished_run(self):
        r=project(fixture(),2050,100,3,'ultimate')
        self.assertEqual(r['entry_mushrooms'],1950)
        self.assertEqual(r['legendaries'],9)
        self.assertEqual(r['legendary_final_boss_only'],7)
        self.assertEqual(r['mushrooms'],2040)

    def test_stopping_and_normal_fallback_use_only_reserved_runs(self):
        r=project(fixture(),750,100,1,'ultimate')
        self.assertEqual((r['completions'],r['legendaries'],r['gold_units']), (1,3,24))
        self.assertEqual(r['mushrooms'],670)
        r=project(fixture(),750,100,1,'mixed')
        self.assertEqual((r['completions'],r['legendaries'],r['gold_units']), (2,5,46))
        self.assertEqual(r['mushrooms'],740)

    def test_normal_is_identical_and_unaffordable_plans_fail(self):
        row=fixture();r=project(row,100,100,0,'normal')
        for key in FIELDS:self.assertEqual(row[key],r[key])
        with self.assertRaises(ValueError):project(row,649,0,1,'ultimate')


if __name__=='__main__':unittest.main()
