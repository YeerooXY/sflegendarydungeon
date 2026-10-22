"""Visible-state stone constraints applied consistently in PPO and inference."""
import torch
from torch import nn

from .ppo import ActorCritic

MODES = ('learned', 'pendant_priority', 'pendant_escape', 'pendant_greed_priority')


class StonePriorityActor(ActorCritic):
    def __init__(self, base, mode, observation_names, action_names):
        nn.Module.__init__(self)
        if mode not in MODES[1:]:
            raise ValueError('Invalid stone priority')
        self.architecture = dict(base.architecture)
        self.trunk, self.actor, self.critic = base.trunk, base.actor, base.critic
        self.priority_mode = mode
        self.oi = {name: i for i, name in enumerate(observation_names)}
        self.ai = {name: i for i, name in enumerate(action_names)}
        self.gem_actions = [self.ai[f'gem_{i}'] for i in range(3)]
        self.phase_index = self.oi['phase.gems']
        self.owned_indices = [self.oi[f'owned_gem.{gem}'] for gem in ('rabbit', 'moonstone')]
        preference = ('rabbit', 'moonstone', 'greed', 'pendant') if mode == 'pendant_greed_priority' else ('rabbit', 'moonstone', 'pendant')
        self.offer_indices = [[self.oi[f'gem_offers.{i}.{gem}'] for i in range(3)] for gem in preference]
        if len(observation_names) != self.architecture['observation_size'] or len(action_names) != self.architecture['action_size']:
            raise ValueError('Priority schema dimensions differ')
        self.train(base.training)

    def constrained_masks(self, observations, masks):
        masks = masks.to(dtype=torch.bool)
        legal_gems = masks[:, self.gem_actions]
        pendant = (observations[:, self.offer_indices[-1]] > .5) & legal_gems
        active = (observations[:, self.phase_index] > .5) & pendant.any(dim=1)
        if self.priority_mode == 'pendant_escape':
            active &= (observations[:, self.owned_indices] > .5).any(dim=1)
        chosen = pendant
        for indices in reversed(self.offer_indices[:-1]):
            offered = (observations[:, indices] > .5) & legal_gems
            chosen = torch.where(offered.any(dim=1, keepdim=True), offered, chosen)
        result = masks.clone()
        result[:, self.gem_actions] = torch.where(active[:, None], chosen, legal_gems)
        # Non-stone legal actions, including preparatory healing, are preserved.
        return result

    def distribution(self, observations, masks):
        return super().distribution(observations, self.constrained_masks(observations, masks))


def with_priority(model, mode, schema):
    if mode not in MODES:
        raise ValueError('Unknown stone priority')
    if mode == 'learned':
        if isinstance(model, StonePriorityActor):
            base = ActorCritic(**model.architecture).to(next(model.parameters()).device)
            base.load_state_dict(model.state_dict())
            base.train(model.training)
            return base
        return model
    return StonePriorityActor(model, mode, schema['observation_names'], schema['action_names'])
