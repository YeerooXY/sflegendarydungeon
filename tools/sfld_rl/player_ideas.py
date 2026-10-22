"""Explicit visible-state interventions; game mechanics stay in the native engine."""
from dataclasses import asdict, dataclass
from pathlib import Path
import numpy as np


@dataclass(frozen=True)
class Ideas:
    name: str
    reentry: float = 0
    boss_buffer: bool = False
    recovery_fights: bool = False
    free_escape: bool = False
    keys: bool = False
    stones: bool = False

    def record(self):
        return asdict(self)


CASES = {p.name: p for p in (
    Ideas('control'), Ideas('reentry21', reentry=.21),
    Ideas('boss_buffer', boss_buffer=True), Ideas('recovery_fights', recovery_fights=True),
    Ideas('free_escape', free_escape=True), Ideas('keys10_shop4', keys=True),
    Ideas('stone_priority', stones=True),
    Ideas('combined_no_stones', .21, True, True, True, True, False),
    Ideas('combined', .21, True, True, True, True, True),
)}
REASONS = ('reentry', 'boss_buffer', 'recovery_fights', 'free_escape', 'keys', 'stones')


def profile_values(path):
    return dict(line.split('=', 1) for line in Path(path).read_text(encoding='utf8').splitlines()
                if '=' in line and not line.startswith('#'))


class PlayerIdeas:
    def __init__(self, observation_names, action_names, ideas, profile):
        self.oi = {n: i for i, n in enumerate(observation_names)}
        self.ai = {n: i for i, n in enumerate(action_names)}
        self.ideas = ideas
        self.base_escape = float(profile['escape_chance'])
        self.monster = np.array([[float(v) for v in row.split(':')] for row in profile['monster_damage'].split(',')])
        self.boss = np.array([[float(v) for v in row.split(':')] for row in profile['boss_damage'].split(',')])
        self.high = self.monster[:, 1].copy()
        self.mean = self.monster[:, :2].mean(axis=1)
        shape = profile['damage_distribution']
        if shape == 'triangular_mean': self.mean = self.monster[:, 2]
        elif shape == 'low': self.high = self.mean = self.monster[:, 0]
        elif shape == 'high': self.mean = self.high
        elif shape == 'midpoint': self.high = self.mean
        if not 0 <= ideas.reentry <= 1: raise ValueError('Invalid recovery floor')

    def v(self, obs, name):
        return obs[:, self.oi[name]]

    def flag(self, obs, name):
        return self.v(obs, name) > .5

    def effect(self, obs, kind):
        group = 'curses' if kind in ('clumsy', 'poison', 'broken_armor') else 'blessings'
        result = np.zeros(len(obs))
        for i in range(3):
            prefix = f'{group}.{i}'
            active = self.flag(obs, prefix+'.active') & self.flag(obs, prefix+'.kind.'+kind)
            result += active * self.v(obs, prefix+'.magnitude')
        return result

    def apply(self, obs, masks, proposed):
        actual = np.asarray(proposed, dtype=np.int64).copy()
        targets = np.full(len(obs), -1., dtype=np.float64)
        reasons = np.zeros((len(obs), len(REASONS)), dtype=bool)
        p = self.ideas
        if p == CASES['control']:
            return actual, targets, reasons
        hp = self.v(obs, 'hp').astype(np.float64)
        room = np.rint(self.v(obs, 'room_div100') * 100).astype(int)
        stage = np.clip((room-1)//25, 0, 3)
        keys = np.rint(self.v(obs, 'keys_div20') * 20)
        stone = lambda kind: self.flag(obs, 'owned_gem.'+kind)
        recovery = self.effect(obs, 'recovery') * np.where(stone('rusty'), 1.4, 1.)
        poison = self.effect(obs, 'poison')
        one_hit = self.effect(obs, 'one_hit') > 0
        disarm = self.effect(obs, 'disarm') > 0
        multiplier = np.maximum(0, 1+.25*stone('rabbit')+.25*stone('devil')-
                                .2*stone('hero')-.2*stone('bull')-.2*stone('deceit')+self.effect(obs, 'broken_armor'))
        high, mean = self.high[stage]*multiplier, self.mean[stage]*multiplier
        escape = np.clip(self.base_escape+.4*stone('rabbit')+.2*stone('moonstone')-
                         .3*stone('bull')+self.effect(obs, 'escape')-self.effect(obs, 'clumsy'), 0, 1)
        safe_hit = (hp > np.where(one_hit, 0, high)+1e-6) & (np.minimum(1, hp-np.where(one_hit, 0, high)+recovery)-poison > 1e-6)
        covered = (recovery > 0) & (hp > high+1e-6) & (mean+poison <= recovery+1e-6) & safe_hit
        safe_escape = (escape >= 1-1e-6) & (np.minimum(1, hp+recovery)-poison > 1e-6)
        doors = self.flag(obs, 'phase.doors')
        fighting = self.flag(obs, 'phase.encounter') & self.flag(obs, 'encounter.monster')
        choosing_door = doors & np.isin(proposed, [self.ai['door_0'], self.ai['door_1']])
        committing_fight = fighting & np.isin(proposed, [self.ai['fight'], self.ai['flee']])

        def choose(where, name, reason):
            use = where & masks[:, self.ai[name]] & (actual != self.ai[name])
            actual[use] = self.ai[name]
            reasons[use, REASONS.index(reason)] = True

        if p.keys:
            # Keep the safe mystery beside a visible monster. Farm when combat
            # is already entered, or avoid a paid/trapped choice when safe.
            target = np.where(stage == 0, 10, np.where(stage == 1, 5, 0))
            choose(committing_fight & safe_hit & (keys < target), 'fight', 'keys')
            unvisited = ~np.column_stack([self.flag(obs, 'shops_seen.'+str(i)) for i in range(1, 5)])[np.arange(len(obs)), stage]
            for side in range(2):
                other = 1-side
                monster = self.flag(obs, f'doors.{side}.kind.monster')
                safe_entry = ~self.flag(obs, f'doors.{side}.trap') | disarm
                paid_cost = self.v(obs, f'doors.{other}.key_cost_div6')*6
                save = (paid_cost > 0) & unvisited & (keys-paid_cost < 4-1e-6)
                bad_trap = (stage == 0) & self.flag(obs, f'doors.{other}.kind.mystery') & self.flag(obs, f'doors.{other}.trap')
                choose(choosing_door & monster & safe_entry & safe_hit & (save | bad_trap), f'door_{side}', 'keys')
        if p.recovery_fights:
            choose(committing_fight & covered, 'fight', 'recovery_fights')
            for side in range(2):
                other = 1-side
                alternative = (self.v(obs, f'doors.{other}.key_cost_div6') > 0) | self.flag(obs, f'doors.{other}.kind.mystery')
                safe_entry = ~self.flag(obs, f'doors.{side}.trap') | disarm
                choose(choosing_door & self.flag(obs, f'doors.{side}.kind.monster') & safe_entry & covered & alternative,
                       f'door_{side}', 'recovery_fights')
        if p.free_escape:
            # One Hit and useful Recovery may still farm keys without wasting HP.
            choose(committing_fight & safe_escape & ~one_hit & ~covered, 'flee', 'free_escape')
            soldier = self.flag(obs, 'phase.encounter') & self.flag(obs, 'encounter.skeleton')
            choose(soldier & safe_escape & (proposed == self.ai['skip']), 'interact', 'free_escape')
            for side in range(2):
                other = 1-side
                alternative = (self.v(obs, f'doors.{other}.key_cost_div6') > 0) | (
                    self.flag(obs, f'doors.{other}.kind.mystery') & self.flag(obs, f'doors.{other}.trap'))
                safe_entry = ~self.flag(obs, f'doors.{side}.trap') | disarm
                choose(choosing_door & self.flag(obs, f'doors.{side}.kind.monster') & safe_entry & safe_escape & alternative,
                       f'door_{side}', 'free_escape')
        if p.stones:
            gem_actions = [self.ai[f'gem_{i}'] for i in range(3)]
            offered = np.column_stack([self.flag(obs, f'gem_offers.{i}.pendant') for i in range(3)])
            remaining = self.flag(obs, 'phase.gems') & np.isin(proposed, gem_actions) & np.any(offered, axis=1)
            for gem in ('rabbit', 'moonstone', 'greed', 'pendant'):
                for side in range(3):
                    use = remaining & self.flag(obs, f'gem_offers.{side}.{gem}') & masks[:, self.ai[f'gem_{side}']]
                    choose(use, f'gem_{side}', 'stones')
                    remaining[use] = False
        if p.reentry or p.boss_buffer:
            recovering = self.flag(obs, 'phase.recovery')
            target = np.where(room % 25 != 0, max(.2, p.reentry), .2)
            near_boss = (room >= 97) & (room < 100) & p.boss_buffer
            buffer = np.minimum(1, self.boss[3, 1]+np.minimum(2, 100-room)*np.maximum(.1, high)+.001)
            target = np.where(near_boss, np.maximum(target, buffer), target)
            planned = np.full(len(obs), np.inf)
            planned[proposed == self.ai['reenter']] = hp[proposed == self.ai['reenter']]
            for percent in (20, 40, 60, 80, 100): planned[proposed == self.ai[f'wait_hp_{percent}']] = percent/100
            use = recovering & (hp+1e-6 < target) & (planned+1e-6 < target)
            actual[use] = self.ai['wait_deadline']  # Carrier for an explicit native custom wait.
            targets[use] = target[use]
            reasons[use & near_boss, REASONS.index('boss_buffer')] = True
            reasons[use & ~near_boss, REASONS.index('reentry')] = True
        normal = targets < 0
        if not masks[np.flatnonzero(normal), actual[normal]].all():
            raise RuntimeError('Player idea produced an illegal menu action')
        return actual, targets, reasons
