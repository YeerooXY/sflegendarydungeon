"""Shop-reserve hypotheses over a frozen policy, using public observations only."""
from dataclasses import asdict, dataclass
import numpy as np
from .practical import Practical


@dataclass(frozen=True)
class ReserveCase:
    reserve: int = 0
    carry: int = 0
    cheap_elixir: bool = False

    @property
    def name(self):
        base = f'reserve{self.reserve}_carry{self.carry}' if self.reserve else 'control'
        return base + ('_elixir' if self.cheap_elixir else '')

    def record(self):
        return {'name': self.name, **asdict(self)}


CASES = [ReserveCase(), ReserveCase(cheap_elixir=True)] + [
    ReserveCase(r, c, e) for e in (False, True) for r in range(1, 5) for c in (1, 2)]


class ShopReserve(Practical):
    def __init__(self, names, actions, case, profile):
        super().__init__(names, actions, 'learned', profile)
        self.case = case
        self.shop_earliest = np.array([int(w.split(':')[0]) for w in profile['shop_windows'].split(',')])
        self.full_elixir = profile.get('time_traveler_elixir_full_heal') == 'true'
        self.later_stage_one_shop = profile.get('later_stage_one_shop', 'true') == 'true'

    def target(self, obs):
        room = np.rint(self.v(obs, 'room_div100') * 100).astype(int)
        stage = np.clip((room - 1) // 25, 0, 3)
        seen = np.column_stack([self.flag(obs, f'shops_seen.{s}') for s in range(1, 5)])[
            np.arange(len(obs)), stage]
        # One key early, then the full target in the four-room approach to the
        # earliest possible shop. No access to the actual hidden scheduled room.
        approach = np.clip((room - (self.shop_earliest[stage] - 4)) / 4, 0, 1)
        before = np.ceil(1 + (self.case.reserve - 1) * approach)
        result = np.where(seen, np.where(stage < 3, self.case.carry, 0), before)
        free_opening = self.flag(obs, 'first_event_run') & (stage == 0) & ~seen
        result[free_opening] = 0
        # Later-run stage one has no shop. Carry keys toward stage two instead
        # of holding the full reserve for a nonexistent room-six shop.
        if not self.later_stage_one_shop:
            result[(stage == 0) & ~self.flag(obs, 'first_event_run')] = self.case.carry
        return result if self.case.reserve else np.zeros(len(obs))

    def apply(self, obs, masks, proposed):
        actual, targets, old = super().apply(obs, masks, proposed)
        reasons = np.zeros((len(obs), 3), bool)
        reasons[:, 0] = old[:, 0]
        if self.case.reserve:
            keys = np.rint(self.v(obs, 'keys_div20') * 20)
            reserve = self.target(obs)
            doors = self.flag(obs, 'phase.doors')
            for side in range(2):
                other = 1 - side
                cost = np.rint(self.v(obs, f'doors.{side}.key_cost_div6') * 6)
                other_cost = np.rint(self.v(obs, f'doors.{other}.key_cost_div6') * 6)
                use = (doors & (actual == self.ai[f'door_{side}']) & (cost > 0)
                       & (keys - cost < reserve) & (other_cost == 0)
                       & masks[:, self.ai[f'door_{other}']])
                actual[use] = self.ai[f'door_{other}']
                reasons[use, 1] = True
        if self.case.cheap_elixir and self.full_elixir:
            shop = self.flag(obs, 'phase.shop') & self.flag(obs, 'owned_gem.time_traveler')
            # Test a predeclared 20% missing-health threshold. Both strengths
            # fully heal with this gem (ordinary weak potion heals 25%).
            need = shop & (self.v(obs, 'hp') <= .8 + 1e-6)
            for i in range(2):
                price = np.rint(self.v(obs, f'offers.{i}.keys_div6') * 6)
                potion = self.flag(obs, f'offers.{i}.effect.kind.elixir')
                buy = self.ai[f'buy_{i}']
                cheap = need & potion & (price == 1) & masks[:, buy]
                reasons[cheap, 2] |= actual[cheap] != buy
                actual[cheap] = buy
                # Buying three-key full healing is not dominated by rerolling:
                # the latter costs mushrooms and may fail. Test, do not assume.
                costly = shop & potion & (price == 3) & (actual == buy)
                replacement = np.where(masks[:, self.ai['reroll']], self.ai['reroll'], self.ai['skip'])
                actual[costly] = replacement[costly]
                reasons[costly, 2] = True
        if not masks[np.arange(len(obs)), actual].all():
            raise RuntimeError('Illegal shop-reserve action')
        return actual, targets, reasons
