"""The early trap preference must not alter healing or unrelated room choices."""
import importlib.util
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
AVAILABLE=importlib.util.find_spec('numpy') is not None
if AVAILABLE:
    import numpy as np
    from sfld_rl.door_safety import EarlyTrapSafety


@unittest.skipUnless(AVAILABLE,'Optional NumPy absent')
class EarlyTrapTests(unittest.TestCase):
    def test_only_legal_early_safe_monster_replaces_chosen_trap(self):
        names=['phase.doors','room_div100']+[f'doors.{i}.{f}' for i in range(2) for f in ('kind.monster','trap','cursed_trap')]
        guard=EarlyTrapSafety(names,['door_0','door_1','heal_full'])
        obs=np.zeros((8,len(names)),np.float32);mask=np.ones((8,3),bool)
        obs[:,0]=1;obs[:,1]=.1
        obs[:,guard.oi['doors.0.trap']]=1;obs[:,guard.oi['doors.1.kind.monster']]=1
        obs[1,1]=.3                         # Later stage: retain learned choice.
        obs[2,guard.oi['doors.1.trap']]=1   # Both choices trapped.
        obs[3,guard.oi['doors.0.trap']]=0   # Safe mystery room.
        obs[5,0]=0                         # Different phase.
        mask[6,1]=False                    # Safe monster unavailable.
        obs[7,guard.oi['doors.0.trap']]=0
        obs[7,guard.oi['doors.0.cursed_trap']]=1
        proposed=np.array([0,0,0,0,2,0,0,0]);original=proposed.copy()
        np.testing.assert_array_equal(guard.apply(obs,mask,proposed),[1,0,0,0,2,0,0,1])
        np.testing.assert_array_equal(proposed,original)
        # Mirror the door positions.
        mirrored=obs[[0]].copy()
        mirrored[:,2:5],mirrored[:,5:8]=obs[[0],5:8],obs[[0],2:5]
        np.testing.assert_array_equal(guard.apply(mirrored,mask[[0]],np.array([1])),[0])


if __name__=='__main__':unittest.main()
