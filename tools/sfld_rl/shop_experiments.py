"""Observable-state interventions for the October 11 player-proposed study.

The frozen learner receives unmodified observations and action masks. Rules
change only committed actions in their declared scope; no future draws enter.
"""
from dataclasses import dataclass, asdict

import numpy as np


@dataclass(frozen=True)
class Policy:
    name: str
    shop: str = 'learned'
    arena: str = 'learned'
    prepare_arena: bool = False
    greasy_price_cap: int = 0
    gems: str = 'learned'

    def record(self):
        return asdict(self)


POLICIES = {
    p.name: p for p in (
        Policy('baseline'),
        Policy('first_useful', shop='first_useful'),
        Policy('healthy_one_hit8', shop='healthy_one_hit8'),
        Policy('strong_pair', shop='strong_pair'),
        Policy('strong_pair_greed', shop='strong_pair_greed'),
        Policy('arena_flee', arena='flee'),
        Policy('arena_protected', arena='protected'),
        Policy('arena_prepared', arena='protected', prepare_arena=True),
        Policy('greasy15', greasy_price_cap=15),
        Policy('greasy20', greasy_price_cap=20),
        Policy('pendant_priority', gems='pendant_priority'),
        Policy('pendant_escape', gems='pendant_escape'),
        Policy('pendant_greed_priority', gems='pendant_greed_priority'),
    )
}

EFFECTS = ('raider', 'one_hit', 'escape', 'disarm', 'lockpick', 'key_moment', 'elixir', 'recovery')


class Rules:
    def __init__(self, observation_names, action_names, policy, boss_high):
        self.oi = {n: i for i, n in enumerate(observation_names)}
        self.ai = {n: i for i, n in enumerate(action_names)}
        self.policy = policy
        self.boss_high = np.asarray(boss_high)
        if self.boss_high.shape != (4,):
            raise ValueError('Four boss damage ceilings required')
        if policy.shop not in ('learned', 'first_useful', 'healthy_one_hit8', 'strong_pair', 'strong_pair_greed'):
            raise ValueError('Unknown shop intervention')
        if policy.arena not in ('learned', 'flee', 'protected') or policy.greasy_price_cap not in (0, 15, 20):
            raise ValueError('Unknown arena/recovery intervention')
        if policy.gems not in ('learned', 'pendant_priority', 'pendant_escape', 'pendant_greed_priority'):
            raise ValueError('Unknown stone intervention')

    def v(self, obs, name):
        return obs[:, self.oi[name]]

    def flag(self, obs, name):
        return self.v(obs, name) > .5

    def effect(self, obs, kind):
        prefix = 'curses' if kind in ('poison', 'broken_armor', 'clumsy', 'hard_lock', 'gold_hangover') else 'blessings'
        result = np.zeros(len(obs), dtype=bool)
        for i in range(3):
            result |= self.flag(obs, f'{prefix}.{i}.kind.{kind}') & self.flag(obs, f'{prefix}.{i}.active')
        return result

    def offer_arrays(self, obs):
        kinds, strong = [], []
        weak_duration = dict(raider=5, one_hit=4, escape=5, disarm=4, lockpick=2, key_moment=4)
        for slot in range(2):
            p = f'offers.{slot}.effect'
            k = np.argmax(np.column_stack([self.v(obs, p + '.kind.' + e) for e in EFFECTS]), axis=1)
            strength = np.zeros(len(obs), dtype=bool)
            for i, effect in enumerate(EFFECTS):
                if effect in ('elixir', 'recovery'):
                    s = self.v(obs, p + '.magnitude') > (.251 if effect == 'elixir' else .101)
                else:
                    s = self.v(obs, p + '.remaining_div10') * 10 > weak_duration[effect] + .1
                strength |= (k == i) & s
            kinds.append(k); strong.append(strength)
        return np.column_stack(kinds), np.column_stack(strong)

    def apply(self, obs, masks, actions):
        actual = np.asarray(actions, dtype=np.int64).copy()
        reasons = np.zeros(len(obs), dtype=np.int8)  # 1 shop, 2 arena, 3 preparation, 4 recovery, 5 stones
        hp = self.v(obs, 'hp')
        rooms = np.rint(self.v(obs, 'room_div100') * 100).astype(int)
        doors = self.flag(obs, 'phase.doors')
        encounter = self.flag(obs, 'phase.encounter')
        ordinary_commit = np.isin(actual, [self.ai[x] for x in ('door_0', 'door_1', 'fight', 'flee', 'interact', 'skip')])
        if self.policy.gems != 'learned':
            # Only reconsider menus containing a legal Pendant offer. Preserve
            # preparatory healing and every menu where Pendant is unavailable.
            gem_actions = [self.ai[f'gem_{i}'] for i in range(3)]
            choosing = self.flag(obs, 'phase.gems') & np.isin(actual, gem_actions)
            pendant = np.column_stack([self.flag(obs, f'gem_offers.{i}.pendant') for i in range(3)])
            choosing &= np.any(pendant & masks[:, gem_actions], axis=1)
            if self.policy.gems == 'pendant_escape':
                choosing &= self.flag(obs, 'owned_gem.rabbit') | self.flag(obs, 'owned_gem.moonstone')
            remaining = choosing.copy()
            order = ('rabbit', 'moonstone', 'greed', 'pendant') if self.policy.gems == 'pendant_greed_priority' else ('rabbit', 'moonstone', 'pendant')
            for gem in order:
                for i in range(3):
                    use = remaining & self.flag(obs, f'gem_offers.{i}.{gem}') & masks[:, self.ai[f'gem_{i}']]
                    actual[use] = self.ai[f'gem_{i}']; reasons[use] = 5
                    remaining[use] = False
        if self.policy.shop != 'learned':
            shop = self.flag(obs, 'phase.shop')
            # Retain a learner's preparatory heal. Reconsider offers on the next action.
            eligible = shop & ~np.isin(actual, [self.ai['heal_step'], self.ai['heal_full']])
            if np.any(eligible):
                kinds, strong = self.offer_arrays(obs)
                one_hit = kinds == EFFECTS.index('one_hit')
                recovery = kinds == EFFECTS.index('recovery')
                lockpick = kinds == EFFECTS.index('lockpick')
                targets = one_hit | recovery | (lockpick & strong & (hp[:, None] >= .7 - 1e-6))
                if self.policy.shop == 'healthy_one_hit8':
                    comfortable = hp > self.boss_high[np.minimum(3, (rooms - 1) // 25)] + .2 + .001
                    targets[comfortable] = (one_hit & strong)[comfortable]
                if self.policy.shop in ('strong_pair', 'strong_pair_greed'):
                    targets = (one_hit | recovery) & strong
                    if self.policy.shop == 'strong_pair_greed':
                        targets |= lockpick & strong & self.flag(obs, 'owned_gem.greed')[:, None]
                # Every policy retains the fixed first-run free One Hit offer.
                opening = self.flag(obs, 'first_event_run') & (rooms == 5)
                targets[opening] = one_hit[opening]
                affordable = targets & masks[:, [self.ai['buy_0'], self.ai['buy_1']]]
                for row in np.flatnonzero(eligible):
                    choices = np.flatnonzero(affordable[row])
                    if len(choices):
                        if actual[row] in [self.ai[f'buy_{i}'] for i in choices]:
                            selected = actual[row]
                        else:
                            # If both qualify, prefer healing below70%, combat protection above.
                            preferred = EFFECTS.index('recovery' if hp[row] < .7 else 'one_hit')
                            pick = next((i for i in choices if kinds[row, i] == preferred), choices[0])
                            selected = self.ai[f'buy_{pick}']
                    elif masks[row, self.ai['reroll']] and self.v(obs, 'keys_div20')[row] >= .2 - 1e-6:
                        selected = self.ai['reroll']
                    else:
                        # Search is impossible: retain an affordable learned fallback, except gold.
                        selected = self.ai['skip']
                        for i in range(2):
                            if actual[row] == self.ai[f'buy_{i}'] and kinds[row, i] != EFFECTS.index('raider'):
                                selected = actual[row]
                    actual[row] = selected; reasons[row] = 1
        if self.policy.prepare_arena:
            prep = doors & ordinary_commit & (rooms >= 46) & (rooms <= 49)
            one_hit = self.effect(obs, 'one_hit')
            no_trap_damage = self.effect(obs, 'disarm') | self.flag(obs, 'owned_gem.gambler')
            for row in np.flatnonzero(prep):
                available = [i for i in range(2) if masks[row, self.ai[f'door_{i}']]]
                # Preserve golden/shop opportunities; target locked loot versus ordinary alternatives.
                if any(self.flag(obs, f'doors.{i}.kind.golden')[row] or self.flag(obs, f'doors.{i}.kind.shop')[row] for i in available):
                    continue
                monster = [i for i in available if self.flag(obs, f'doors.{i}.kind.monster')[row]]
                farm = [i for i in monster if one_hit[row] and (no_trap_damage[row] or not self.flag(obs, f'doors.{i}.trap')[row] or hp[row] >= .7)]
                loot = [i for i in available if any(self.flag(obs, f'doors.{i}.kind.{kind}')[row] for kind in ('locked', 'unlocked'))
                        and (no_trap_damage[row] or not self.flag(obs, f'doors.{i}.trap')[row] or hp[row] > .101)]
                candidates = farm or loot
                if candidates:
                    actual[row] = self.ai[f'door_{candidates[0]}']; reasons[row] = 3
        if self.policy.arena != 'learned':
            for i in range(2):
                use = doors & ordinary_commit & self.flag(obs, f'doors.{i}.kind.trial') & masks[:, self.ai[f'door_{i}']]
                actual[use] = self.ai[f'door_{i}']; reasons[use] = 2
                # At depth5 only the claim door remains. Never skip its reward.
                claim = doors & ordinary_commit & (self.v(obs, 'trial_depth_div5') >= .999) & self.flag(obs, f'doors.{i}.kind.exit_trial') & masks[:, self.ai[f'door_{i}']]
                actual[claim] = self.ai[f'door_{i}']; reasons[claim] = 2
            fighting = encounter & ordinary_commit & self.flag(obs, 'encounter.trial_monster')
            actual[fighting] = self.ai['flee']; reasons[fighting] = 2
            if self.policy.arena == 'protected':
                actual[fighting & self.effect(obs, 'one_hit')] = self.ai['fight']
            prize = encounter & ordinary_commit & self.flag(obs, 'encounter.prize')
            actual[prize] = self.ai['interact']; reasons[prize] = 2
        if self.policy.greasy_price_cap:
            recovery = self.flag(obs, 'phase.recovery') & self.flag(obs, 'owned_gem.greasy') & (rooms % 25 != 0)
            cheap = self.v(obs, 'heal_step_price_div100') * 100 <= self.policy.greasy_price_cap + .001
            buy = recovery & (hp < .2 - 1e-6) & cheap & masks[:, self.ai['heal_step']]
            actual[buy] = self.ai['heal_step']; reasons[buy] = 4
            enter = recovery & masks[:, self.ai['reenter']]
            actual[enter] = self.ai['reenter']; reasons[enter] = 4
        changed = actual != actions
        reasons[~changed] = 0
        if not masks[np.arange(len(actual)), actual].all():
            raise RuntimeError('Intervention selected illegal action')
        return actual, reasons
