"""Observable effect summaries for the targeted policy-improvement follow-up."""
import json
from pathlib import Path
import numpy as np
from .effect_residual import EffectFeatures, EffectResidual

KINDS = {'blessings': ('one_hit', 'recovery', 'escape', 'disarm', 'lockpick', 'key_moment', 'raider'),
         'curses': ('poison', 'broken_armor', 'clumsy', 'hard_lock', 'gold_hangover')}


class EffectSummaries(EffectFeatures):
    def __init__(self, names, actions):
        super().__init__(names, actions)
        extra = []
        for group, kinds in KINDS.items():
            for kind in kinds:
                extra += [f'summary.{kind}.remaining', f'summary.{kind}.magnitude', f'summary.{kind}.slot']
            extra += [f'summary.{group}.max_remaining', f'summary.{group}.min_remaining']
        extra += ['summary.guaranteed_exit_hp', 'summary.escape_probability',
                  'summary.oldest_valuable_blessing_remaining']
        self.feature_names += extra

    def encode(self, obs, group, first):
        base = super().encode(obs, group, first)
        extra = []; duration = {}; magnitude = {}
        for kind_group, kinds in KINDS.items():
            for kind in kinds:
                flags = np.column_stack([self.f(obs, f'{kind_group}.{i}.kind.{kind}') for i in range(3)])
                remaining = np.column_stack([np.rint(self.v(obs, f'{kind_group}.{i}.remaining_div10')*10) for i in range(3)])
                active = np.column_stack([self.f(obs, f'{kind_group}.{i}.active') for i in range(3)])
                mags = np.column_stack([self.v(obs, f'{kind_group}.{i}.magnitude') for i in range(3)])
                duration[kind] = (flags*remaining).sum(axis=1)
                magnitude[kind] = (flags*active*mags).sum(axis=1)
                # -1 represents absence, independently of the slot's age.
                slot = np.where(flags.any(axis=1), flags.argmax(axis=1), -1)
                extra += [duration[kind], magnitude[kind], slot]
            present = np.column_stack([self.f(obs, f'{kind_group}.{i}.present') for i in range(3)])
            extra += [np.where(present, remaining, 0).max(axis=1),
                      np.where(present.any(axis=1), np.where(present, remaining, 100).min(axis=1), 0)]
        heal = magnitude['recovery'] * np.where(self.f(obs, 'owned_gem.rusty'), 1.4, 1)
        extra += [np.minimum(1, self.v(obs, 'hp')+heal)-magnitude['poison'],
                  np.clip(.4+.4*self.f(obs, 'owned_gem.rabbit')+.2*self.f(obs, 'owned_gem.moonstone')-
                          .3*self.f(obs, 'owned_gem.bull')+magnitude['escape']-magnitude['clumsy'], 0, 1),
                  np.rint(self.v(obs, 'blessings.0.remaining_div10')*10) *
                  (self.f(obs, 'blessings.0.kind.one_hit') | self.f(obs, 'blessings.0.kind.recovery') | self.f(obs, 'blessings.0.kind.escape'))]
        return np.column_stack([base, *extra]).astype(np.float32)


class RefinedEffectResidual(EffectResidual):
    def __init__(self, names, actions, artifact):
        self.features = EffectSummaries(names, actions)
        self.artifact = json.loads(Path(artifact).read_text()) if isinstance(artifact, (str, Path)) else artifact
        if self.artifact['schema'] != 'sfld-effect-residual-v2': raise ValueError('Unsupported refined effect model')
        if self.artifact['features'] != self.features.feature_names: raise ValueError('Effect summary schema changed')
        self.margin = float(self.artifact['margin'])
        self.trees = [tuple(np.asarray(t[k], dtype=np.float64 if k in ('threshold', 'value') else np.int64)
                           for k in ('left', 'right', 'feature', 'threshold', 'value')) for t in self.artifact['trees']]
