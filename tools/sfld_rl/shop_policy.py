"""Shop preferences in the PPO distribution, not post-sampling action overrides.

The environment's legal moves and completion reward remain unchanged. Stones,
doors, combat and recovery stay learned. This is an explicit policy constraint.
"""
import numpy as np
import torch
from torch import nn
from .ppo import ActorCritic

MODES = ('learned', 'shop5')


def profile_config(profiles, mode):
    if mode == 'learned':
        return None
    from .player_ideas import profile_values
    probabilities = {float(profile_values(p)['escape_chance']) for p in profiles}
    if len(probabilities) != 1:
        raise ValueError('Shop policy needs the same base escape chance in every profile')
    return {'mode': mode, 'allowance': 5, 'key_reserve': 4,
            'base_escape': probabilities.pop(), 'version': 1}


class ShopPolicyActor(ActorCritic):
    def __init__(self, base, schema, config):
        nn.Module.__init__(self)
        if config['mode'] != 'shop5' or config.get('version') != 1:
            raise ValueError('Unsupported shop policy')
        self.architecture = dict(base.architecture)
        self.trunk, self.actor, self.critic = base.trunk, base.actor, base.critic
        self.shop_config = dict(config)
        self.oi = {n: i for i, n in enumerate(schema['observation_names'])}
        self.ai = {n: i for i, n in enumerate(schema['action_names'])}
        fields = ['phase.shop', 'room_div100', 'hp', 'keys_div20',
                  'reroll_shop_visits_div20', 'reroll_bank_used_div100',
                  'owned_gem.rabbit', 'owned_gem.moonstone', 'owned_gem.bull']
        kinds = ('one_hit', 'recovery', 'escape', 'lockpick')
        for i in range(3):
            fields += [f'blessings.{i}.{n}' for n in ('active', 'magnitude', 'remaining_div10')]
            fields += [f'blessings.{i}.kind.{k}' for k in kinds]
            fields += [f'curses.{i}.{n}' for n in ('active', 'magnitude', 'kind.clumsy')]
        for i in range(2):
            fields += [f'offers.{i}.effect.{n}' for n in ('remaining_div10', 'magnitude')]
            fields += [f'offers.{i}.effect.kind.{k}' for k in kinds]
        self._shop_oi = {n: i for i, n in enumerate(fields)}
        # Derived indices are not checkpoint weights. This also preserves old
        # checkpoint state dictionaries and moves correctly with model.to().
        self.register_buffer('_shop_columns', torch.tensor([self.oi[n] for n in fields],
                             device=next(base.parameters()).device), persistent=False)
        self.train(base.training)

    def constrained_masks(self, obs, masks):
        # These discrete rules need no autograd. A small CPU calculation avoids
        # hundreds of tiny CUDA launches; transfer only their 51 input columns.
        # Most rows are not shops and need no rule calculation at all.
        if obs.device.type == 'cpu':
            array, columns = obs.detach().numpy(), self.oi
        else:
            array = obs.detach().index_select(1, self._shop_columns).cpu().numpy()
            columns = self._shop_oi
        result = self.numpy_masks(array, masks.detach().cpu().numpy(), columns)
        return torch.from_numpy(result).to(device=masks.device)

    def numpy_masks(self, obs, masks, columns=None):
        columns = self.oi if columns is None else columns
        result = np.array(masks, dtype=bool, copy=True)
        rows = np.flatnonzero(obs[:, columns['phase.shop']] > .5)
        if not len(rows):
            return result
        obs = obs[rows]
        legal = result[rows]
        v = lambda n: obs[:, columns[n]]
        f = lambda n: v(n) > .5
        effects = {}
        def effect(kind, remaining=False):
            if (kind, remaining) in effects:
                return effects[kind, remaining]
            group = 'curses' if kind == 'clumsy' else 'blessings'
            value = sum((np.rint(v(f'{group}.{i}.remaining_div10')*10) if remaining
                        else v(f'{group}.{i}.magnitude')) *
                       f(f'{group}.{i}.active') * f(f'{group}.{i}.kind.{kind}') for i in range(3))
            effects[kind, remaining] = value
            return value
        scalar = lambda x: np.asarray(x, dtype=obs.dtype)
        chance = (scalar(self.shop_config['base_escape']) + scalar(.4)*f('owned_gem.rabbit') +
                  scalar(.2)*f('owned_gem.moonstone') - scalar(.3)*f('owned_gem.bull') +
                  effect('escape') - effect('clumsy'))
        chance = np.clip(chance, 0, 1)
        room = np.rint(v('room_div100')*100)
        normal_left = 100-room-np.floor((100-room)/25)
        good, values = [], []
        for i in range(2):
            prefix = f'offers.{i}.effect'
            duration = np.rint(v(prefix+'.remaining_div10')*10)
            useful = np.zeros(len(obs), dtype=bool)
            for kind in ('one_hit', 'recovery', 'escape', 'lockpick'):
                condition = f(prefix+'.kind.'+kind) & (normal_left >= 2) & (duration > effect(kind, True))
                if kind == 'escape': condition &= chance < 1-1e-6
                if kind == 'lockpick': condition &= (duration >= 4) & (v('hp') >= .5)
                useful |= condition
            good.append(useful & legal[:, self.ai[f'buy_{i}']])
            power = np.where(f(prefix+'.kind.one_hit'), np.float32(.18),
                             np.where(f(prefix+'.kind.recovery'), v(prefix+'.magnitude'), np.float32(.08)))
            values.append(np.minimum(duration, normal_left)*power)
        good = np.stack(good, axis=1)
        values = np.where(good, np.stack(values, axis=1), np.float32(-1))
        best = values.argmax(axis=1)
        any_good = good.any(axis=1)
        bank = self.shop_config['allowance']*np.rint(v('reroll_shop_visits_div20')*20)-np.rint(v('reroll_bank_used_div100')*100)
        enough_keys = np.rint(v('keys_div20')*20) >= self.shop_config['key_reserve']
        reroll = ~any_good & enough_keys & (bank > 0) & legal[:, self.ai['reroll']]
        forced = any_good | reroll
        chosen = np.where(reroll, self.ai['reroll'],
                          np.where(best == 0, self.ai['buy_0'], self.ai['buy_1']))
        legal[forced] = False
        legal[np.flatnonzero(forced), chosen[forced]] = True
        # When no refresh is available, keep other legal choices learned.
        legal[:, self.ai['reroll']] &= ~((bank <= 0) | ~enough_keys)
        result[rows] = legal
        return result

    def distribution(self, observations, masks):
        return super().distribution(observations, self.constrained_masks(observations, masks))

    def ppo_masks(self, observations, masks):
        return self.constrained_masks(observations, masks)

    def ppo_distribution(self, observations, constrained_masks):
        return super().distribution(observations, constrained_masks)


def with_shop(model, schema, config):
    if config is None:
        if isinstance(model, ShopPolicyActor):
            base = ActorCritic(**model.architecture).to(next(model.parameters()).device)
            base.load_state_dict(model.state_dict())
            base.train(model.training)
            return base
        return model
    if getattr(model, 'priority_mode', 'learned') != 'learned':
        raise ValueError('Shop training and fixed stone priority cannot be combined')
    return ShopPolicyActor(model, schema, config)
