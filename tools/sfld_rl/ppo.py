"""Masked nonlinear PPO primitives; the only environment reward is completions."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical


@dataclass
class PPOConfig:
    learning_rate: float = 3e-4
    gae_lambda: float = .95
    clip: float = .2
    value_coefficient: float = .5
    entropy_coefficient: float = .01
    max_grad_norm: float = .5
    target_kl: float = .03
    epochs: int = 4
    minibatch_size: int = 1024

    def validate(self):
        if not all(math.isfinite(v) for v in asdict(self).values()):
            raise ValueError('PPO settings must be finite')
        if not 0 < self.learning_rate < 1 or not 0 <= self.gae_lambda <= 1:
            raise ValueError('Invalid learning rate or GAE lambda')
        if not 0 < self.clip < 1 or min(self.value_coefficient, self.entropy_coefficient,
                                       self.max_grad_norm, self.target_kl) < 0:
            raise ValueError('Invalid PPO loss settings')
        if self.epochs < 1 or self.minibatch_size < 2:
            raise ValueError('Need positive epochs and minibatches of at least two')


class ActorCritic(nn.Module):
    def __init__(self, observation_size, action_size, hidden=(256, 256)):
        super().__init__()
        if observation_size < 1 or action_size < 2 or not hidden or min(hidden) < 1:
            raise ValueError('Invalid network dimensions')
        self.architecture = {'observation_size': int(observation_size),
                             'action_size': int(action_size), 'hidden': list(hidden)}
        layers, size = [], observation_size
        for width in hidden:
            linear = nn.Linear(size, width)
            nn.init.orthogonal_(linear.weight, math.sqrt(2))
            nn.init.zeros_(linear.bias)
            layers.extend([linear, nn.Tanh()])
            size = width
        self.trunk = nn.Sequential(*layers)
        self.actor = nn.Linear(size, action_size)
        self.critic = nn.Linear(size, 1)
        nn.init.orthogonal_(self.actor.weight, .01)
        nn.init.orthogonal_(self.critic.weight, 1)
        nn.init.zeros_(self.actor.bias)
        nn.init.zeros_(self.critic.bias)

    def forward(self, observations):
        features = self.trunk(observations)
        return self.actor(features), self.critic(features).squeeze(-1)

    def values(self, observations):
        return self.critic(self.trunk(observations)).squeeze(-1)

    def distribution(self, observations, masks):
        logits, values = self(observations)
        masks = masks.to(dtype=torch.bool)
        if masks.shape != logits.shape or not bool(masks.any(dim=-1).all()):
            raise ValueError('Every active observation needs a legal action mask')
        if not bool(torch.isfinite(logits).all() and torch.isfinite(values).all()):
            raise FloatingPointError('Nonfinite policy output')
        return Categorical(logits=logits.masked_fill(~masks, -1e9)), values

    def actions(self, observations, masks, greedy=False):
        distribution, values = self.distribution(observations, masks)
        actions = distribution.logits.argmax(dim=-1) if greedy else distribution.sample()
        return actions, distribution.log_prob(actions), values


def advantages(rewards, values, next_values, terminated, truncated, gae_lambda=.95):
    """Gamma is exactly one. Rollout ends bootstrap; real deadlines do not.

    A safety truncation would bootstrap its last state while cutting the trace
    before a reset. Production training rejects such episodes and reports them
    as failures; this distinction prevents a truncation becoming a fake terminal.
    """
    rewards, values, next_values = [np.asarray(x, dtype=np.float32)
                                  for x in (rewards, values, next_values)]
    terminated, truncated = [np.asarray(x, dtype=bool) for x in (terminated, truncated)]
    if rewards.ndim != 2 or any(x.shape != rewards.shape for x in (values, next_values, terminated, truncated)):
        raise ValueError('GAE inputs must share shape [rollout, environments]')
    if not 0 <= gae_lambda <= 1 or not all(np.isfinite(x).all() for x in (rewards, values, next_values)):
        raise ValueError('Nonfinite GAE inputs or invalid lambda')
    result = np.zeros_like(rewards)
    carry = np.zeros(rewards.shape[1], dtype=np.float32)
    for t in reversed(range(rewards.shape[0])):
        delta = rewards[t] + (~terminated[t]) * next_values[t] - values[t]
        carry = delta + gae_lambda * (~(terminated[t] | truncated[t])) * carry
        result[t] = carry
    return result, result + values


def ppo_update(model, optimizer, batch, config, device):
    config.validate()
    tensors = {name: torch.as_tensor(value, device=device) for name, value in batch.items()}
    distribution = model.distribution
    if hasattr(model, 'ppo_masks'):
        # These masks depend only on visible observations, not learned weights.
        # Compute once per PPO batch; sampling uses the identical constraints.
        with torch.no_grad():
            tensors['masks'] = model.ppo_masks(tensors['observations'].float(), tensors['masks'])
        distribution = model.ppo_distribution
    adv = tensors['advantages'].float()
    tensors['advantages'] = (adv - adv.mean()) / adv.std(unbiased=False).clamp_min(1e-8)
    count = len(adv)
    totals = {'policy_loss': 0., 'value_loss': 0., 'entropy': 0., 'approx_kl': 0., 'clip_fraction': 0.}
    updates, stopped = 0, False
    for _ in range(config.epochs):
        order = torch.randperm(count, device=device)
        for start in range(0, count, config.minibatch_size):
            idx = order[start:start + config.minibatch_size]
            dist, values = distribution(tensors['observations'][idx].float(), tensors['masks'][idx])
            log_ratio = dist.log_prob(tensors['actions'][idx].long()) - tensors['log_probs'][idx]
            ratio = log_ratio.exp()
            a = tensors['advantages'][idx]
            policy_loss = torch.maximum(-a * ratio, -a * ratio.clamp(1 - config.clip, 1 + config.clip)).mean()
            value_loss = .5 * (values - tensors['returns'][idx]).square().mean()
            entropy = dist.entropy().mean()
            loss = policy_loss + config.value_coefficient * value_loss - config.entropy_coefficient * entropy
            if not bool(torch.isfinite(loss)):
                raise FloatingPointError('Nonfinite PPO loss')
            with torch.no_grad():
                kl = ((ratio - 1) - log_ratio).mean()
            if config.target_kl and float(kl) > 1.5 * config.target_kl:
                stopped = True
                break
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), config.max_grad_norm, error_if_nonfinite=True)
            optimizer.step()
            updates += 1
            values_out = [policy_loss, value_loss, entropy, kl, ((ratio - 1).abs() > config.clip).float().mean()]
            # One device-to-host transfer for reporting; keep the same Python
            # float accumulation order and every early-stop/finite check.
            reported = torch.stack([v.detach() for v in values_out]).cpu().tolist()
            for key, value in zip(totals, reported):
                totals[key] += value
        if stopped:
            break
    return {**{key: value / max(1, updates) for key, value in totals.items()},
            'gradient_updates': updates, 'kl_early_stop': stopped}


def cloning_update(model, optimizer, observations, masks, labels, device, minibatch_size=1024):
    observations = torch.as_tensor(observations, dtype=torch.float32, device=device)
    masks = torch.as_tensor(masks, dtype=torch.bool, device=device)
    labels = torch.as_tensor(labels, dtype=torch.long, device=device)
    if labels.ndim != 1 or len(labels) != len(observations) or bool((labels < 0).any()):
        raise ValueError('Teacher returned an unavailable action')
    if bool((labels >= masks.shape[-1]).any()) or not bool(masks.gather(1, labels[:, None]).all()):
        raise ValueError('Teacher selected an illegal action')
    losses = []
    order = torch.randperm(len(labels), device=device)
    for start in range(0, len(order), minibatch_size):
        idx = order[start:start + minibatch_size]
        distribution, _ = model.distribution(observations[idx], masks[idx])
        loss = -distribution.log_prob(labels[idx]).mean()
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), .5, error_if_nonfinite=True)
        optimizer.step()
        losses.append(float(loss.detach()))
    return float(np.mean(losses))
