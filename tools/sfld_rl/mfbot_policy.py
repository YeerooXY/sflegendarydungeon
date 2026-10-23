"""MFBot 6.35's Legendary Dungeon strategy as a simulator policy.

Ported rule for rule from MFBot.Common.LegendaryDungeonTasker (MFBotCore 6.35,
the latest release on 2026-10-23) with its default settings unless overridden:
FARM_KEYS on, LOW_HP_ENTRY off, MIN_HEALING_PERCENT 20. MFBot never buys heals or rerolls, so
it spends no mushrooms. Differences from the live bot are listed in the
benchmark note: it polls every 15-30 minutes while healing (here it re-enters at
exactly 20%), and rooms the simulator merges are mapped to MFBot's handler for
the closest room type.

The game's floor counter (save word 17) equals the simulator room inside a room
and the simulator room minus one on the door screen.
"""
import math

import numpy as np

DOOR_SCORE = dict(boss=9999, golden=2000, shop=1800, blessing=1700, destiny=1600, unlocked=1500, exit_trial=1450,
                  wood=1420, stone=1420, souls=1420, metal=1420, arcane=1420, hourglasses=1420, monster=1400,
                  trial=1350, mystery=1300, sacrifice=100, cursed=50)
GEM_SCORE = dict(rabbit=100, explorer=90, pendant=85, spying=80, bull=75, greed=70, thirsty=65, time_traveler=60,
                 devil=55, greasy=50, hero=50, masochist=45, gambler=40, kidney=35, hick=30, misadventurer=25,
                 old_sacrifice=20, blood=15, moonstone=10, pearl=5, rusty=1, deceit=-10, lodestone=-20)
BLESSING_SCORE = dict(recovery=100, one_hit=85, lockpick=80, disarm=70, escape=60, raider=20)
ALWAYS = {'bronze', 'silver', 'epic', 'crate', 'prize', 'sated_chest'}
FIGHT_AFTER_OPEN = {'skeleton', 'mimic'}
RISKY_OPEN = {'barrel', 'sacrifice_chest', 'cursed_chest'}
GOOD_ROOMS = {'fountain', 'cleansing_fountain', 'rocks', 'lava', 'narrator', 'flooded', 'sarcophagus', 'wood', 'souls',
              'arcane', 'armory'}
DAMAGE_ROOMS = {'undead', 'valaraukar', 'wheel', 'beta', 'tube', 'rainbow', 'pig', 'shakes'}
ITEM_ROOMS = {'wishing_well', 'sewers', 'locker', 'auction'}
SKIP_ROOMS = {'spider_legs', 'spider_head', 'spider_full', 'locked_sarcophagus'}
MIN = -2**31


def fight_threshold(floor):
    return 14 if floor < 25 else 20 if floor < 50 else 22 if floor < 75 else 25


def boss_threshold(floor):
    return 20 if floor <= 25 else 30 if floor <= 75 else 50


def can_take_damage(hp, floor):
    """MFBot's flag3: HP percent clears the band threshold by more than 5 points."""
    pre_boss = 20 <= floor % 25 < 25
    return hp > (boss_threshold(floor) if pre_boss else fight_threshold(floor)) + 5


def score_door(kind, trap, keys, floor, hp):
    left = 100 - floor % 100;plenty = keys != 0 and keys*2 >= left
    if kind == 'epic':score = 2100 if keys > 1 else MIN
    elif kind == 'locked':score = (1550 if plenty else 1350) if keys else MIN
    elif kind == 'double_locked':score = (1540 if plenty else 1340) if keys > 1 else MIN
    else:score = DOOR_SCORE.get(kind, MIN)
    if score == MIN:return MIN
    if floor % 100 < 25 and kind in ('monster', 'boss'):score += 500
    if kind == 'mystery':score += 250
    if hp < 30 and kind in ('sacrifice', 'cursed'):score += 200
    return score-400 if trap else score


def healing_target(floor, low_hp_entry, minimum=20):
    """HP percent MFBot waits for before re-entering; floor is the save counter while healing."""
    if not low_hp_entry:return minimum
    if floor < 25:return max(minimum, 80)
    if 20 <= floor % 25 < 25:return max(minimum, boss_threshold(floor)+5)
    return max(minimum, 27) if floor > 25 else minimum


def boss_safe_targets(profile):
    """Smallest whole HP percent above each stage boss's maximum damage in a profile dict."""
    return [math.floor(float(row.split(':')[1])*100)+1 for row in profile['boss_damage'].split(',')]


def score_blessing(kind, magnitude, uses, hp, keys, low_hp_entry=False):
    if kind == 'elixir':base = 95 if low_hp_entry or hp < 50 else 40
    elif kind == 'key_moment':base = 90 if keys < 5 else 50
    else:base = BLESSING_SCORE.get(kind, -1)
    return base if base < 0 else base*1000+int(magnitude*10)+int(uses*5)


class MFBotPolicy:
    """Callable like audit.Adviser: (obs, masks) -> (actions, wait targets)."""
    def __init__(self, env, low_hp_entry=False, min_healing=20, farm_keys=True, boss_safe=None):
        self.low_hp_entry, self.min_healing, self.farm_keys = low_hp_entry, min_healing, farm_keys
        # Optional per-stage re-entry HP (percent): the current stage boss's worst hit, rounded up.
        self.boss_safe = boss_safe
        self.env = env;names = env.observation_names;self.i = {n: k for k, n in enumerate(names)}
        self.a = {n: k for k, n in enumerate(env.action_names)}
        self.encounters = [n.split('.', 1)[1] for n in names if n.startswith('encounter.')]
        self.door_kinds = [n.split('.')[-1] for n in names if n.startswith('doors.0.kind.')]
        self.effect_kinds = [n.split('.')[-1] for n in names if n.startswith('offers.0.effect.kind.')]
        self.gems = [n.split('.')[-1] for n in names if n.startswith('gem_offers.0.')]
        self.bought = np.zeros(env.n_envs, bool)
        self.counts = {}

    def _one_hot(self, row, prefix, kinds):
        return next((k for k in kinds if row[self.i[prefix+k]] > .5), None)

    def _decide(self, row, legal):
        v = lambda n: float(row[self.i[n]])
        hp = int(v('hp')*100+1e-6);keys = int(round(v('keys_div20')*20));room = int(round(v('room_div100')*100))
        pick = lambda *names: next((n for n in names if n in legal), None)
        if v('phase.doors') > .5:
            floor = room-1;best = None;top = MIN
            for d in (0, 1):
                if f'door_{d}' not in legal:continue
                kind = self._one_hot(row, f'doors.{d}.kind.', self.door_kinds)
                s = score_door(kind, v(f'doors.{d}.trap') > .5, keys, floor, hp)
                if best is None or s > top:best, top = f'door_{d}', s
            return best, 'door'
        if v('phase.gems') > .5:
            gem_score = lambda g:95 if g == 'greasy' and self.low_hp_entry else GEM_SCORE.get(g, 0)
            scored = [(gem_score(self._one_hot(row, f'gem_offers.{g}.', self.gems)), -g) for g in range(3) if f'gem_{g}' in legal]
            return f'gem_{-max(scored)[1]}', 'gem'
        if v('phase.shop') > .5:
            options = []
            for o in (0, 1):
                if f'buy_{o}' not in legal or v(f'offers.{o}.keys_div6')*6 > keys+1e-9:continue
                kind = self._one_hot(row, f'offers.{o}.effect.kind.', self.effect_kinds)
                options.append((score_blessing(kind, v(f'offers.{o}.effect.magnitude')*100, v(f'offers.{o}.effect.remaining_div10')*10, hp, keys, self.low_hp_entry), -o))
            options = [x for x in options if x[0] >= 0]
            if options and not self.bought_now:return f'buy_{-max(options)[1]}', 'buy_blessing'
            return 'skip', 'leave_shop'
        if v('phase.curse_shop') > .5:
            if 'buy_0' in legal and not self.bought_now and can_take_damage(hp, room):return 'buy_0', 'buy_curse'
            return 'skip', 'leave_curse_shop'
        if v('phase.recovery') > .5:
            target = healing_target(room-1, self.low_hp_entry, self.min_healing)
            if self.boss_safe:
                stage = next((k for k in range(4) if v(f'stage.{k+1}') > .5), 0)
                target = max(self.min_healing, self.boss_safe[stage])
            if 'reenter' in legal and hp >= target:return 'reenter', 'reenter'
            # Any wait action accepts an exact HP target, so MFBot's 27% or 35% is kept as is.
            waits = [w for w in ('wait_hp_20', 'wait_hp_40', 'wait_hp_60', 'wait_hp_80', 'wait_hp_100') if w in legal]
            if not waits:return pick('reenter'), 'wait'
            goal = max(.2, target/100)
            if goal > v('hp')+1e-4 and v('hours_left_div168')*168 > 1e-3:self.target_now = goal
            return waits[0], 'wait'
        if v('phase.complete') > .5:return pick('restart'), 'restart'
        if v('phase.encounter') > .5:
            enc = self._one_hot(row, 'encounter.', self.encounters);floor = room
            take = can_take_damage(hp, floor) or (self.farm_keys and floor < 25)  # FARM_KEYS fights everything before floor 25
            go, leave = pick('interact', 'fight'), pick('skip', 'flee')
            if enc == 'boss':return pick('fight'), 'boss'
            if enc in ('monster', 'trial_monster'):return (pick('fight') if take else pick('flee')), 'monster'
            if enc in ALWAYS:return go, 'open'
            if enc in FIGHT_AFTER_OPEN or enc in RISKY_OPEN:return (go if take else leave), 'risky_open'
            if enc in GOOD_ROOMS or enc in ITEM_ROOMS:return go, 'room'
            if enc in DAMAGE_ROOMS:
                return (go if hp <= 80 and (hp < 30 or can_take_damage(hp, floor)) else leave), 'damage_room'
            if enc == 'rps':return (go if can_take_damage(hp, floor) else leave), 'rps'
            if enc in SKIP_ROOMS or enc == 'empty':return leave or go, 'skip_room'
            return leave or go, 'unmapped'
        return None, 'unmapped'

    def __call__(self, obs, masks):
        n = len(obs);actions = np.zeros(n, np.int64);targets = np.full(n, -1.)
        shop = (obs[:, self.i['phase.shop']] > .5) | (obs[:, self.i['phase.curse_shop']] > .5)
        self.bought &= shop
        for s in range(n):
            legal = {self.env.action_names[k] for k in np.flatnonzero(masks[s])}
            if not legal:continue
            self.bought_now = bool(self.bought[s]);self.target_now = -1.
            name, why = self._decide(obs[s], legal)
            if name not in legal:name, why, self.target_now = sorted(legal)[0], 'fallback', -1.
            targets[s] = self.target_now
            if name.startswith('buy_'):self.bought[s] = True
            self.counts[why] = self.counts.get(why, 0)+1
            actions[s] = self.a[name]
        return actions, targets
