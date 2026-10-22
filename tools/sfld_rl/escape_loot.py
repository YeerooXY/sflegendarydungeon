"""Narrow guaranteed-escape routing and loot rules, with learned stone choices."""
import numpy as np
from .player_ideas import CASES, PlayerIdeas

CASES_LOOT = ('control', 'soldier_only', 'previous_escape', 'safe_escape', 'safe_escape_fallback')
REASONS = ('soldier', 'save_key', 'avoid_trap', 'escape', 'pendant_fallback')


class EscapeLoot(PlayerIdeas):
    def __init__(self, names, actions, case, profile):
        if case not in CASES_LOOT: raise ValueError('Unknown escape/loot case')
        super().__init__(names, actions, CASES['free_escape'], profile)
        self.case = case

    def apply(self, obs, masks, proposed):
        actual = np.asarray(proposed, dtype=np.int64).copy()
        reasons = np.zeros((len(obs), len(REASONS)), bool)
        if self.case == 'control': return actual, reasons
        if self.case == 'previous_escape':
            actual, targets, old = super().apply(obs,masks,proposed)
            reasons[:,3] = old.any(axis=1)
            return actual,reasons
        hp = self.v(obs,'hp')
        gem = lambda name: self.flag(obs,'owned_gem.'+name)
        chance = np.clip(self.base_escape+.4*gem('rabbit')+.2*gem('moonstone')-.3*gem('bull')
                         +self.effect(obs,'escape')-self.effect(obs,'clumsy'),0,1)
        recovery = self.effect(obs,'recovery')*np.where(gem('rusty'),1.4,1)
        safe = (chance >= 1-1e-6) & (np.minimum(1,hp+recovery)-self.effect(obs,'poison')>1e-6)
        def choose(condition, name, reason):
            use = condition & masks[:,self.ai[name]] & (actual!=self.ai[name])
            actual[use] = self.ai[name]; reasons[use,REASONS.index(reason)] = True
        soldier = self.flag(obs,'phase.encounter') & self.flag(obs,'encounter.skeleton')
        choose(safe & soldier & (proposed==self.ai['skip']), 'interact', 'soldier')
        if self.case == 'soldier_only': return actual,reasons
        fighting = self.flag(obs,'phase.encounter') & self.flag(obs,'encounter.monster')
        # Keep the free kill under One Hit. Otherwise take the guaranteed escape;
        # this version does not force Recovery fights to farm keys.
        choose(safe & fighting & (self.effect(obs,'one_hit')==0)
               & np.isin(proposed,[self.ai['fight'],self.ai['flee']]), 'flee', 'escape')
        doors = self.flag(obs,'phase.doors') & np.isin(proposed,[self.ai['door_0'],self.ai['door_1']])
        disarm = self.effect(obs,'disarm')>0
        for side in range(2):
            other = 1-side
            untrapped = ~self.flag(obs,f'doors.{side}.trap') | disarm
            monster = self.flag(obs,f'doors.{side}.kind.monster')
            paid = self.v(obs,f'doors.{other}.key_cost_div6')>0
            choose(safe & doors & monster & untrapped & paid, f'door_{side}', 'save_key')
            # If an otherwise legal alternative is untrapped, avoid a selected
            # trap. Door contents stay hidden and are never used by this rule.
            other_trapped = self.flag(obs,f'doors.{other}.trap') & ~disarm
            choose(safe & doors & untrapped & other_trapped, f'door_{side}', 'avoid_trap')
        if self.case == 'safe_escape_fallback':
            # A deliberately narrow last resort, not a general stone ranking.
            # Only replace a learned Bull/Hick pick when Pendant is offered.
            choosing = self.flag(obs,'phase.gems')
            bad = np.zeros(len(obs),bool)
            for side in range(3):
                bad |= (proposed==self.ai[f'gem_{side}']) & (
                    self.flag(obs,f'gem_offers.{side}.bull') | self.flag(obs,f'gem_offers.{side}.hick'))
            for side in range(3):
                choose(choosing & bad & self.flag(obs,f'gem_offers.{side}.pendant'),f'gem_{side}','pendant_fallback')
        if not masks[np.arange(len(obs)),actual].all(): raise RuntimeError('Illegal escape/loot action')
        return actual,reasons
