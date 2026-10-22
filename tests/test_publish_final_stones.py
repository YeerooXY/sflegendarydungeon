"""Guard paired, stratified uncertainty and unresolved ranking behavior."""
from pathlib import Path
import sys
import unittest
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from publish_final_stones import paired,rank_arrays


class FinalStoneStatisticsTests(unittest.TestCase):
    def test_stratified_variance_does_not_resample_fixed_histories(self):
        values=np.array([[0.,2.,4.],[100.,102.,104.]])
        result=paired(values,2)
        self.assertAlmostEqual(result['mean'],52)
        self.assertAlmostEqual(result['se'],(2/3)**.5)

    def test_identical_candidates_remain_unresolved(self):
        base=np.ones((2,4,6));base[...,0]=[[10,12,14,16],[20,22,24,26]]
        row,_=rank_arrays(['a','b'],[base,base.copy()],5)
        self.assertIsNone(row['supported_best']);self.assertEqual(set(row['contenders']),{'a','b'})

    def test_paired_common_noise_can_identify_clear_winner(self):
        base=np.ones((2,4,6));base[...,0]=[[10,12,14,16],[20,22,24,26]]
        slower=base.copy();slower[...,0]+=3
        row,comparisons=rank_arrays(['a','b'],[base,slower],5)
        self.assertEqual(row['supported_best'],'a');self.assertEqual(row['contenders'],['a'])
        self.assertEqual(comparisons[0,1]['ci'],[-3,-3])

    def test_small_point_advantage_is_not_a_supported_winner(self):
        a=np.ones((2,8,6));b=a.copy();a[...,0]=50
        b[...,0]=np.array([[-8,-6,-4,-2,2,4,6,8]]*2)+50.1
        row,_=rank_arrays(['a','b'],[a,b],5)
        self.assertEqual(row['estimated_best'],'a');self.assertIsNone(row['supported_best'])


if __name__=='__main__':unittest.main()
