"""Visible-state counterfactuals and effect-slot challenge positions.

Snapshots deliberately omit the simulator RNG. Restoring always requires a
new independent seed, so a policy cannot benefit from a revealed future.
"""
import ctypes as ct
import json
import numpy as np


def mix64(value):
    mask = (1 << 64) - 1
    value = (int(value) + 0x9e3779b97f4a7c15) & mask
    value = ((value ^ (value >> 30)) * 0xbf58476d1ce4e5b9) & mask
    value = ((value ^ (value >> 27)) * 0x94d049bb133111eb) & mask
    return (value ^ (value >> 31)) & mask


class Snapshot:
    def __init__(self, lab, handle):
        self.lab, self.handle = lab, handle
        if not handle:
            raise RuntimeError(lab.env._error())

    def close(self):
        if self.handle:
            self.lab.env._lib.sfld_rl_snapshot_destroy(self.handle)
            self.handle = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class EffectLab:
    def __init__(self, env):
        self.env = env
        lib = env._lib
        signatures = {
            'capture': ([ct.c_void_p, ct.c_int32], ct.c_void_p),
            'fixture': ([ct.c_void_p, ct.c_int32, ct.c_int32, ct.c_char_p, ct.c_uint64], ct.c_void_p),
            'snapshot_destroy': ([ct.c_void_p], None),
            'restore': ([ct.c_void_p, ct.c_int32, ct.c_void_p, ct.c_uint64], ct.c_int32),
            'visible_json': ([ct.c_void_p, ct.c_int32], ct.c_char_p),
        }
        for name, (args, result) in signatures.items():
            method = getattr(lib, 'sfld_rl_' + name)
            method.argtypes, method.restype = args, result
        if hasattr(lib, 'sfld_rl_planning_fixture'):
            lib.sfld_rl_planning_fixture.argtypes = [ct.c_void_p, ct.c_int32, ct.c_char_p]
            lib.sfld_rl_planning_fixture.restype = ct.c_void_p

    def capture(self, slot):
        return Snapshot(self, self.env._lib.sfld_rl_capture(self.env._handle, slot))

    def fixture(self, progress, budget, profile=0, seed=0):
        return Snapshot(self, self.env._lib.sfld_rl_fixture(
            self.env._handle, profile, budget, progress.encode('utf-8'), seed))

    def restore(self, slot, snapshot, seed):
        if not snapshot.handle or not 0 <= int(seed) < 2**64:
            raise ValueError('Closed snapshot or invalid independent seed')
        self.env._check(self.env._lib.sfld_rl_restore(
            self.env._handle, slot, snapshot.handle, int(seed)))

    def planning_fixture(self, text, profile=0):
        if not hasattr(self.env._lib, 'sfld_rl_planning_fixture'):
            raise RuntimeError('The simulator library needs the planning-state bridge build')
        return Snapshot(self, self.env._lib.sfld_rl_planning_fixture(
            self.env._handle, profile, text.encode('utf-8')))

    def observe(self):
        obs, masks = self.env._outputs()
        self.env._check(self.env._lib.sfld_rl_observe(self.env._handle,
            self.env._ptr(obs, ct.c_float), self.env._ptr(masks, ct.c_uint8)))
        self.env._refresh_info()
        return obs, masks.astype(bool)

    def visible(self, slot=0):
        text = self.env._lib.sfld_rl_visible_json(self.env._handle, slot)
        if text is None: raise RuntimeError(self.env._error())
        return json.loads(text)


BLESSINGS = {
    'two_good': 'one_hit:strong:7,recovery:strong:5',
    'one_hit_oldest': 'one_hit:strong:7,recovery:strong:5,raider:strong:8',
    'one_hit_newest': 'raider:strong:8,recovery:strong:5,one_hit:strong:7',
    'recovery_oldest': 'recovery:strong:5,one_hit:strong:7,raider:strong:8',
    'escape_oldest': 'escape:strong:9,recovery:strong:5,raider:strong:8',
    'expiring': 'one_hit:weak:1,recovery:weak:1,raider:weak:1',
}
CURSES = {
    'none': '',
    'poison_oldest': 'poison:strong:9,broken_armor:strong:7,clumsy:strong:9',
    'poison_newest': 'clumsy:strong:9,broken_armor:strong:7,poison:strong:9',
    'mild_oldest': 'gold_hangover:weak:1,broken_armor:strong:7,poison:strong:9',
    'short_curses': 'poison:weak:1,broken_armor:weak:1,clumsy:weak:1',
}


def position(room=60, hp=50, gems='greed,rabbit', blessings='', curses='', encounter=None, doors=None):
    phase = 'encounter' if encounter else 'doors'
    prior_shops = ','.join(map(str, range(1, (room-1)//25+1)))
    fields = dict(schema=1, room=room, run_number=2, phase=phase, hp_percent=hp, keys=6,
                  gems=gems, blessings=blessings, curses=curses, shops_seen=prior_shops)
    fields['encounter' if encounter else 'doors'] = encounter or doors
    return ''.join(f'{k}={v}\n' for k, v in fields.items() if v != '')


def challenge_cases():
    """Declared factorial curriculum, not a sample of natural state frequency."""
    result = []
    for room in (35, 60, 85, 97):
        for stone in ('hero', 'greed', 'gambler', 'time_traveler'):
            gems = ','.join(([stone, 'rabbit', 'moonstone'])[:(room-1)//25])
            for hp in (21, 50, 85):
                for label, blessings in BLESSINGS.items():
                    for encounter, doors in (('crate', None), ('barrel', None), (None, 'blessing,monster')):
                        name = f'blessing-{label}-{encounter or "door"}-r{room}-{stone}-h{hp}'
                        result.append({'name': name, 'family': 'blessings', 'progress': position(
                            room, hp, gems, blessings=blessings, encounter=encounter, doors=doors),
                            'actions': ['interact', 'skip'] if encounter else ['door_0', 'door_1']})
                for label, curses in CURSES.items():
                    if label == 'none': continue
                    for blessings in ('', 'recovery:strong:5,one_hit:strong:7'):
                        for encounter, doors in (('cursed_chest', None), (None, 'cursed,monster')):
                            name = f'curse-{label}-{encounter or "door"}-r{room}-{stone}-h{hp}-b{bool(blessings)}'
                            result.append({'name': name, 'family': 'curses', 'progress': position(
                                room, hp, gems, blessings, curses, encounter, doors),
                                'actions': ['interact', 'skip'] if encounter else ['door_0', 'door_1']})
    return result


def probe_cases():
    result = []
    # Fixed diagnostic panel selected before any outcome is observed.
    for label in ('two_good', 'one_hit_oldest', 'one_hit_newest', 'recovery_oldest', 'expiring'):
        for encounter, doors in (('crate', None), (None, 'blessing,monster')):
            for stone in ('hero', 'greed'):
                result.append({'name': f'{label}-{encounter or "blessing_door"}-{stone}',
                    'progress': position(gems=stone+',rabbit', blessings=BLESSINGS[label],
                                         encounter=encounter, doors=doors),
                    'actions': ['interact', 'skip'] if encounter else ['door_0', 'door_1']})
    for label in ('poison_oldest', 'poison_newest', 'mild_oldest', 'short_curses'):
        for hp in (21, 50):
            for encounter, doors in (('cursed_chest', None), (None, 'cursed,monster')):
                result.append({'name': f'{label}-{encounter or "cursed_door"}-h{hp}',
                    'progress': position(hp=hp, gems='hero,rabbit', curses=CURSES[label],
                                         encounter=encounter, doors=doors),
                    'actions': ['interact', 'skip'] if encounter else ['door_0', 'door_1']})
    return result


class EffectCurriculum:
    """25% of reset episodes start at a declared effect challenge.

These imports start the remaining horizon at zero and have no past completion
or spend counters. They are logged as partial scenarios, never as full weeks.
    """
    def __init__(self, env, root, fraction=.25, cases=None):
        self.env, self.root, self.fraction = env, root, fraction
        self.lab = EffectLab(env)
        self.cases = challenge_cases() if cases is None else cases
        if not self.cases or any(not isinstance(c.get('progress'), str) for c in self.cases):
            raise ValueError('Curriculum requires nonempty progress-state cases')
        self.snapshots = {}
        self.partial = np.zeros(env.n_envs, bool)
        self.started_partial = self.finished_partial = self.finished_whole = 0

    def restore_resets(self, indices):
        for slot in indices:
            episode = int(self.env.episode_indices[slot])
            key = mix64(self.root ^ mix64(episode))
            self.partial[slot] = key % 10000 < round(self.fraction * 10000)
            if not self.partial[slot]: continue
            case = mix64(key) % len(self.cases)
            budget, profile = int(self.env.episode_budgets[slot]), int(self.env.profile_indices[slot])
            identity = (case, budget, profile)
            if identity not in self.snapshots:
                self.snapshots[identity] = self.lab.fixture(self.cases[case]['progress'], budget, profile)
            self.lab.restore(int(slot), self.snapshots[identity], mix64(key ^ 0xEFFEC75))
            self.started_partial += 1
        return self.lab.observe()

    def completed(self, indices):
        count = int(self.partial[indices].sum())
        self.finished_partial += count
        self.finished_whole += len(indices)-count

    def counts(self):
        return dict(partial_scenarios_started=self.started_partial,
                    completed_partial_scenarios=self.finished_partial,
                    completed_whole_events=self.finished_whole)

    def close(self):
        for snapshot in self.snapshots.values(): snapshot.close()
