"""Small, visible-state action-value trees for effect replacement decisions.

Only optional containers/barrels and blessing/curse versus monster pairs are
eligible. The frozen parent still chooses all other actions, including healing,
shops and stones. Tree targets are paired *remaining weekly completions*, not
hand-assigned effect scores or damage rewards.
"""
import json
from pathlib import Path
import numpy as np

FAMILIES = ('container', 'barrel', 'blessing_door', 'cursed_chest', 'cursed_door')
GOOD = ('one_hit', 'recovery', 'escape')


class EffectFeatures:
    def __init__(self, names, actions):
        self.oi = {n: i for i, n in enumerate(names)}
        self.ai = {n: i for i, n in enumerate(actions)}
        self.fields = [n for n in names if n.startswith(('owned_gem.', 'blessings.', 'curses.'))]
        self.fields += ['hp', 'keys_div20', 'room_div100', 'first_event_run',
                        'hours_left_div168', 'mushrooms_left_div500', 'budget_div500',
                        'heal_step_price_div100', 'paid_heals_div20']
        self.columns = np.array([self.oi[n] for n in self.fields])
        self.feature_names = self.fields + ['rooms_to_boss', 'blessing_count', 'curse_count',
            'target_trap', 'target_cursed_trap', 'other_trap', 'other_cursed_trap'] + [
            'family.' + n for n in FAMILIES]

    def v(self, obs, name): return obs[:, self.oi[name]]
    def f(self, obs, name): return self.v(obs, name) > .5

    def opportunities(self, obs, masks):
        """Return family and two canonical actions: acquire effect / avoid it."""
        n = len(obs)
        group = np.full(n, -1, np.int64)
        first = np.full(n, self.ai['interact'], np.int64)
        second = np.full(n, self.ai['skip'], np.int64)
        blessings = sum(self.f(obs, f'blessings.{i}.present') for i in range(3))
        curses = sum(self.f(obs, f'curses.{i}.present') for i in range(3))
        valuable = np.zeros(n, bool)
        for i in range(3):
            for kind in GOOD:
                valuable |= self.f(obs, f'blessings.{i}.kind.{kind}')
        protect = (blessings >= 2) & valuable
        purge = curses == 3
        encounter = self.f(obs, 'phase.encounter')
        containers = sum(self.f(obs, 'encounter.'+k) for k in ('crate', 'bronze', 'silver')) > 0
        group[encounter & containers & protect] = 0
        group[encounter & self.f(obs, 'encounter.barrel') & (protect | purge)] = 1
        group[encounter & self.f(obs, 'encounter.cursed_chest') & purge] = 3
        for side in range(2):
            pair = self.f(obs, 'phase.doors') & self.f(obs, f'doors.{1-side}.kind.monster')
            # Preserve the user's first-stage safe-monster preference.
            unsafe_early = (self.v(obs, 'room_div100') < .25) & self.f(obs, f'doors.{side}.trap') & ~self.f(obs, f'doors.{1-side}.trap')
            for family, kind, relevant in ((2, 'blessing', protect), (4, 'cursed', purge)):
                use = pair & self.f(obs, f'doors.{side}.kind.{kind}') & relevant & ~unsafe_early
                group[use] = family
                first[use], second[use] = self.ai[f'door_{side}'], self.ai[f'door_{1-side}']
        legal = masks[np.arange(n), first] & masks[np.arange(n), second]
        group[~legal] = -1
        return group, first, second

    def encode(self, obs, group, first):
        n = len(obs)
        side = (first == self.ai['door_1']).astype(int)
        extra = [25 - (np.rint(self.v(obs, 'room_div100')*100).astype(int)-1) % 25 - 1,
                 sum(self.f(obs, f'blessings.{i}.present') for i in range(3)),
                 sum(self.f(obs, f'curses.{i}.present') for i in range(3))]
        doors = (group == 2) | (group == 4)
        for other in (False, True):
            index = 1-side if other else side
            for field in ('trap', 'cursed_trap'):
                values = np.column_stack([self.v(obs, f'doors.{i}.{field}') for i in range(2)])
                extra.append(values[np.arange(n), index] * doors)
        extra += [(group == i).astype(float) for i in range(len(FAMILIES))]
        return np.column_stack([obs[:, self.columns], *extra]).astype(np.float32)


def predict_tree(tree, x):
    """Vectorized traversal; no sklearn dependency in the running adviser."""
    left, right, feature, threshold, values = tree
    node = np.zeros(len(x), np.int64)
    while True:
        live = feature[node] >= 0
        if not live.any(): break
        rows = np.flatnonzero(live)
        here = node[rows]
        node[rows] = np.where(x[rows, feature[here]] <= threshold[here], left[here], right[here])
    return values[node]


class EffectResidual:
    def __init__(self, names, actions, artifact):
        self.features = EffectFeatures(names, actions)
        self.artifact = json.loads(Path(artifact).read_text()) if isinstance(artifact, (str, Path)) else artifact
        if self.artifact['schema'] != 'sfld-effect-residual-v1': raise ValueError('Unsupported effect model')
        if self.artifact['features'] != self.features.feature_names: raise ValueError('Effect feature schema changed')
        self.margin = float(self.artifact['margin'])
        self.trees = [tuple(np.asarray(t[k], dtype=np.float64 if k in ('threshold', 'value') else np.int64)
                           for k in ('left', 'right', 'feature', 'threshold', 'value')) for t in self.artifact['trees']]

    def predict(self, x):
        return sum(predict_tree(t, x) for t in self.trees) / len(self.trees)

    def apply(self, obs, masks, proposed):
        group, first, second = self.features.opportunities(obs, masks)
        eligible = (group >= 0) & ((proposed == first) | (proposed == second))
        rows = np.flatnonzero(eligible)
        actual = np.array(proposed, copy=True)
        advantage = np.zeros(len(obs))
        if len(rows):
            values = self.predict(self.features.encode(obs[rows], group[rows], first[rows]))
            advantage[rows] = values
            take = values > self.margin
            leave = values < -self.margin
            actual[rows[take]] = first[rows[take]]
            actual[rows[leave]] = second[rows[leave]]
        return actual, {'family': group, 'advantage': advantage, 'changed': actual != proposed}


def export_tree(estimator):
    tree = estimator.tree_
    return {'left': tree.children_left.tolist(), 'right': tree.children_right.tolist(),
            'feature': tree.feature.tolist(), 'threshold': tree.threshold.tolist(),
            'value': tree.value[:, 0, 0].tolist(), 'samples': tree.n_node_samples.tolist()}
