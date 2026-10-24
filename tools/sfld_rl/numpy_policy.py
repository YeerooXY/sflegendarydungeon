"""PyTorch-free inference for a frozen adviser checkpoint (the desktop app).

The adviser is ppo.ActorCritic: a tanh MLP with an actor head. export() writes
<checkpoint>.npz next to a PyTorch checkpoint (needs torch once); load() reads
it with NumPy only. Greedy actions are the argmax of the actor logits with
illegal actions set to -1e9, as in ActorCritic.actions(greedy=True). Only plain
unrestricted checkpoints are exported (no stone-priority or shop wrapper).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class NumpyPolicy:
    def __init__(self, layers, actor):
        self.layers = [(np.asarray(w, np.float32), np.asarray(b, np.float32)) for w, b in layers]
        self.actor = (np.asarray(actor[0], np.float32), np.asarray(actor[1], np.float32))

    def logits(self, observations):
        x = np.asarray(observations, np.float32)
        for weight, bias in self.layers:
            x = np.tanh(x @ weight.T + bias)
        return x @ self.actor[0].T + self.actor[1]

    def greedy(self, observations, masks):
        masks = np.asarray(masks, bool)
        logits = self.logits(observations)
        if masks.shape != logits.shape or not masks.any(axis=-1).all():
            raise ValueError('Every active observation needs a legal action mask')
        if not np.isfinite(logits).all():
            raise FloatingPointError('Nonfinite policy output')
        return np.where(masks, logits, np.float32(-1e9)).argmax(axis=-1)


def export(checkpoint, output=None):
    """Write the NumPy copy of a plain ActorCritic checkpoint; returns its path."""
    import torch
    checkpoint = Path(checkpoint)
    saved = torch.load(checkpoint, map_location='cpu', weights_only=True)
    if saved.get('schema') != 'sfld-ppo-v1':
        raise ValueError('Unsupported RL checkpoint schema')
    metadata = saved['metadata']
    if metadata.get('stone_priority', 'learned') != 'learned' or metadata.get('shop_policy') is not None:
        raise ValueError('Only unrestricted checkpoints without wrappers can be exported')
    state = {k: v.numpy() for k, v in saved['model'].items()}
    hidden = saved['architecture']['hidden']
    arrays = {f'layer{i}_weight': state[f'trunk.{2*i}.weight'] for i in range(len(hidden))}
    arrays.update({f'layer{i}_bias': state[f'trunk.{2*i}.bias'] for i in range(len(hidden))})
    arrays.update(actor_weight=state['actor.weight'], actor_bias=state['actor.bias'])
    schema = metadata['environment_signature']['schema']
    info = {'source_sha256': sha(checkpoint), 'architecture': saved['architecture'],
            'observation_names': schema['observation_names'], 'action_names': schema['action_names'],
            'engine': schema.get('engine'), 'environment_signature': metadata['environment_signature']}
    output = Path(output) if output else checkpoint.with_suffix('.npz')
    np.savez_compressed(output, metadata=np.array(json.dumps(info)), **arrays)
    return output


def load(checkpoint, expected_sha256):
    """(metadata, NumpyPolicy) for <checkpoint>.npz exported from that checkpoint."""
    path = Path(checkpoint).with_suffix('.npz')
    with np.load(path, allow_pickle=False) as data:
        info = json.loads(str(data['metadata']))
        if info['source_sha256'] != expected_sha256:
            raise ValueError('The NumPy adviser was exported from a different checkpoint')
        count = len(info['architecture']['hidden'])
        layers = [(data[f'layer{i}_weight'], data[f'layer{i}_bias']) for i in range(count)]
        policy = NumpyPolicy(layers, (data['actor_weight'], data['actor_bias']))
    return info, policy
