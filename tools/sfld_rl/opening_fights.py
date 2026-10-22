"""Experimental first-five-room fights. Never imported by the deployed adviser."""
import numpy as np
from .interaction_policy import InteractionPolicy

VARIANTS = ('control', 'two_all', 'two_first', 'two_later', 'two_buffed')


class OpeningFightPolicy(InteractionPolicy):
    def __init__(self, env, profile, variant='control'):
        if variant not in VARIANTS:
            raise ValueError(variant)
        super().__init__(env, profile, 'base')
        self.variant_opening = variant
        damage = [float(x) for x in profile['monster_damage'].split(',')[0].split(':')]
        self.hit_max, self.hit_mean = damage[1], damage[2] if len(damage) > 2 else sum(damage[:2])/2
        self.fights = np.zeros(env.n_envs, dtype=int)
        self.pending = np.zeros(env.n_envs, dtype=bool)
        self.identities = np.full((env.n_envs, 2), -1, dtype=np.int64)
        self.counts.update(opening_doors_changed=0, opening_combats_changed=0)

    def apply(self, obs, masks, proposed):
        # Episode IDs are reset bookkeeping only. Decisions use observed run,
        # health, effects, doors and the player's own previous fight count.
        identities = np.column_stack((self.lab.env.episode_indices,
                                      np.rint(self.v(obs, 'run_number_div10')*10).astype(int)))
        reset = (identities != self.identities).any(axis=1)
        self.fights[reset] = 0
        self.pending[reset] = False
        self.identities = identities
        actions, targets, reasons = super().apply(obs, masks, proposed)
        f = lambda n: self.flag(obs, n)
        v = lambda n: self.v(obs, n)
        room = np.rint(v('room_div100')*100)
        opening = (room >= 1) & (room <= 5)
        self.pending[f('phase.doors')] = False
        if self.variant_opening != 'control':
            multiplier = np.maximum(0, 1 + .5*(self.effect(obs, 'broken_armor') > 0)
                + .25*f('owned_gem.rabbit') + .25*f('owned_gem.devil')
                - .2*f('owned_gem.hero') - .2*f('owned_gem.deceit') - .2*f('owned_gem.bull'))
            one_hit = self.effect(obs, 'one_hit') > 0
            hit = np.where(one_hit, 0, self.hit_max*multiplier)
            heal = self.effect(obs, 'recovery')*np.where(f('owned_gem.rusty'), 1.4, 1)
            safe = (v('hp') > hit + 1e-6) & (np.minimum(1, v('hp')-hit+heal) > self.effect(obs, 'poison') + 1e-6)
            use = opening & (self.fights < 2) & safe
            if self.variant_opening == 'two_first': use &= f('first_event_run')
            if self.variant_opening == 'two_later': use &= ~f('first_event_run')
            if self.variant_opening == 'two_buffed': use &= one_hit | (heal >= self.hit_mean*multiplier)
            counted = np.ones(len(obs), bool) if self.count_before_episode is None else self.lab.env.episode_indices < self.count_before_episode
            for side in range(2):
                other = 1-side
                choose = (use & f('phase.doors') & f(f'doors.{side}.kind.monster')
                    & ~f(f'doors.{side}.trap') & ~f(f'doors.{side}.cursed_trap')
                    & (f(f'doors.{other}.kind.mystery') | f(f'doors.{other}.kind.destiny'))
                    & np.isin(actions, [self.ai['door_0'], self.ai['door_1']]) & masks[:, self.ai[f'door_{side}']])
                self.counts['opening_doors_changed'] += int((choose & (actions != self.ai[f'door_{side}']) & counted).sum())
                actions[choose] = self.ai[f'door_{side}']
                self.pending[choose] = True
            fight = (use & self.pending & f('phase.encounter') & f('encounter.monster')
                     & np.isin(actions, [self.ai['fight'], self.ai['flee']]) & masks[:, self.ai['fight']])
            self.counts['opening_combats_changed'] += int((fight & (actions != self.ai['fight']) & counted).sum())
            actions[fight] = self.ai['fight']
        self.fights += (opening & f('phase.encounter') & f('encounter.monster') & (actions == self.ai['fight']))
        self.pending[f('phase.encounter') & np.isin(actions, [self.ai['fight'], self.ai['flee']])] = False
        if not masks[np.arange(len(obs)), actions].all():
            raise RuntimeError('Illegal opening intervention')
        return actions, targets, reasons
