"""Preserve the player's early-stage preference for a safe monster over a trap."""
import numpy as np


class EarlyTrapSafety:
    def __init__(self, names, actions):
        self.oi = {n: i for i, n in enumerate(names)}
        self.ai = {n: i for i, n in enumerate(actions)}

    def apply(self, obs, masks, proposed):
        actual = proposed.copy()
        flag = lambda n: obs[:, self.oi[n]] > .5
        early = flag('phase.doors') & (np.rint(obs[:, self.oi['room_div100']]*100) < 25)
        for safe, trapped in ((0, 1), (1, 0)):
            is_trap = lambda i: flag(f'doors.{i}.trap') | flag(f'doors.{i}.cursed_trap')
            prefer = (early & flag(f'doors.{safe}.kind.monster') & ~is_trap(safe) & is_trap(trapped)
                      & (actual == self.ai[f'door_{trapped}']) & masks[:, self.ai[f'door_{safe}']])
            actual[prefer] = self.ai[f'door_{safe}']
        return actual
