"""Fresh final-stone comparisons from visible state, with the deployed actor.

The planning snapshot contains no game RNG. Each offered stone gets the same
independent future seeds. The source environment is never stepped or restored.
This ranks a single final-stage choice, not a globally optimal weekly policy.
"""
from itertools import combinations
from pathlib import Path
from statistics import NormalDist
import secrets
import time

import numpy as np
import torch
import train_rl
from publish_final_stones import rank_arrays
from .effect_lab import EffectLab, mix64
from .env import VectorEnv
from .final_stones import METRICS
from .interaction_policy import InteractionPolicy
from .player_ideas import profile_values
from .visual_policy import CHECKPOINT, CHECKPOINT_SHA


class StoneAdvisor:
    def __init__(self, checkpoint=CHECKPOINT, device=None):
        self.checkpoint = Path(checkpoint).resolve(strict=True)
        self.checkpoint_sha = train_rl.sha(self.checkpoint)
        if self.checkpoint == CHECKPOINT.resolve() and self.checkpoint_sha != CHECKPOINT_SHA:
            raise RuntimeError('The deployed actor no longer matches its declared hash')
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.saved, self.model = train_rl.load_checkpoint(self.checkpoint, torch.device(self.device))
        self.model.eval()

    def validate_environment(self, env):
        # A continuation deliberately varies the remaining horizon and may
        # preserve historical action counts. Only compatible feature/action
        # schemas can be evaluated by this frozen actor; record all other
        # differences instead of silently claiming a training resume.
        saved = self.saved['metadata']['environment_signature']
        current = train_rl.compact_metadata(env.metadata)
        old,new = dict(saved['schema']),dict(current['schema'])
        old.pop('engine',None);new.pop('engine',None)
        if old != new:
            raise RuntimeError('Incompatible continuation schema')
        return {k:dict(training=saved.get(k),current=current.get(k))
                for k in sorted(set(saved)|set(current)) if saved.get(k)!=current.get(k)}

    def compare(self, source, slot=0, *, samples=2048, planning_seed=None, batch_size=1024):
        """Compare the three actual final offers; preserve every snapshot field.

        `source` is an initialized VectorEnv. It remains unchanged. Capture and
        use it under the caller's session lock if another thread can play it.
        The caller may reuse this adviser/model across consecutive choices.
        """
        if int(samples) != samples or not 32 <= samples <= 131072:
            raise ValueError('Use a fixed 32..131072 samples per offered stone')
        if int(batch_size) != batch_size or not 1 <= batch_size <= 4096:
            raise ValueError('Batch size must be 1..4096')
        if not 0 <= slot < source.n_envs:
            raise ValueError('Invalid source slot')
        seed = secrets.randbits(64) if planning_seed is None else planning_seed
        if int(seed) != seed or not 0 <= seed < 2**64:
            raise ValueError('Planning seed must be an unsigned 64-bit integer')
        samples, seed = int(samples), int(seed)
        source_lab = EffectLab(source)
        before = source_lab.visible(slot)
        if before['terminal'] or before['phase'] != 'gems' or before['room'] != 76 or len(before['gems']) != 2:
            raise ValueError('This adviser needs the final stone choice after room 75, with two held stones')
        offers = before['gem_offers']
        if len(offers) != 3 or len(set(offers)) != 3:
            raise ValueError('The current menu must contain three distinct offers')
        started = time.monotonic()
        total, n = 3*samples, min(int(batch_size), 3*samples)
        data = np.full((3, samples, len(METRICS)), np.nan, dtype=np.float64)
        metadata = source.metadata
        profile = source.profile_paths[int(source.profile_indices[slot])]
        budget = int(source.episode_budgets[slot])
        schedule = metadata['daily_schedule']
        base_hours, remaining = before['elapsed_hours'], before['hours_left']
        base_spend, base_deaths = before['mushrooms_spent'], before['deaths']
        with source_lab.capture(slot) as snapshot, VectorEnv(
                source.library_path, [profile], n, budgets=[budget], seed=0, seed_namespace=233,
                auto_reset=False, deadline_hours=metadata['deadline_hours'], max_actions=metadata['max_actions'],
                event_start_hour=schedule['event_start_hour'], sleep_until_hour=schedule['sleep_until_hour']) as env:
            environment_changes = self.validate_environment(env)
            env.reset(); lab = EffectLab(env)
            rule = InteractionPolicy(env, profile_values(profile), 'base')
            jobs = np.full((n, 2), -1, dtype=np.int64)
            active, first = np.zeros(n, bool), np.zeros(n, bool)
            cursor = finished = steps = 0
            gem_actions = np.array([env.action_names.index(f'gem_{i}') for i in range(3)])

            def assign(index):
                nonlocal cursor
                if cursor < total:
                    # Interleave offers. Corresponding trials share an independent
                    # future seed, even when the batching or menu order changes.
                    replicate, arm = divmod(cursor, 3); cursor += 1
                    jobs[index] = arm, replicate
                    active[index] = first[index] = True
                    future = mix64(seed ^ mix64(replicate + 700001))
                else:
                    active[index] = first[index] = False
                    future = mix64(seed ^ 0xFADE)
                lab.restore(int(index), snapshot, future)

            for index in range(n): assign(index)
            obs, masks = lab.observe()
            # Native snapshots retain all features, including past counters and
            # effect activation clocks; no progress-format reconstruction here.
            if lab.visible(0) != before:
                raise RuntimeError('Planning snapshot changed the visible source state')
            with torch.inference_mode():
                while active.any():
                    proposed = train_rl.policy_actions(self.model, obs, masks, self.device, greedy=True)[0].cpu().numpy()
                    actions, targets, _ = rule.apply(obs, masks, proposed)
                    starting = active & first
                    actions[starting] = gem_actions[jobs[starting, 0]]
                    targets[starting] = -1
                    first[:] = False
                    if not masks[np.arange(n), actions].all():
                        raise RuntimeError('Illegal continuation action')
                    steps += int(active.sum())
                    obs, masks, _, done, trunc, info = env.step(
                        actions, wait_targets=targets if np.any(targets >= 0) else None)
                    if np.any(trunc & active) or np.any(info['invalid_actions'][active]):
                        raise RuntimeError('Invalid or action-truncated continuation')
                    # Unlike first_completion_hours, this works after past runs.
                    complete = info['completions'] > before['completed_runs']
                    ended = np.flatnonzero(active & (complete | done))
                    for index in ended:
                        arm, replicate = jobs[index]
                        hours = info['elapsed_hours'][index] - base_hours if complete[index] else remaining
                        spend = info['mushrooms'][index] - base_spend
                        deaths = info['deaths'][index] - base_deaths
                        if not 0 <= spend <= before['mushrooms_left'] or not 0 <= hours <= remaining + 1e-9:
                            raise RuntimeError('Continuation exceeded its time or spending cap')
                        data[arm, replicate] = [hours, int(complete[index]), deaths, spend,
                            info['actions'][index] - before['actions'], int(complete[index] and deaths == 0)]
                        finished += 1; assign(int(index))
                    idle_done = np.flatnonzero(~active & done)
                    for index in idle_done: assign(int(index))
                    if len(ended) or len(idle_done): obs, masks = lab.observe()
            if finished != total or not np.isfinite(data).all():
                raise RuntimeError('Missing continuation outcomes')
        if source_lab.visible(slot) != before:
            raise RuntimeError('Source state changed during advice; discard this recommendation')
        # One fixed, three-arm decision. No repeated peeking/early stopping.
        family = len(list(combinations(range(3), 2)))
        z = NormalDist().inv_cdf(1-.05/(2*family))
        ranking, contrasts = rank_arrays(offers, [arm[None, ...] for arm in data], z)
        result = dict(schema='sfld-final-stone-advice-v1', **ranking,
            action=dict(kind='choose_gem', index=offers.index(ranking['estimated_best']), value=0),
            source_observation=before, samples_per_offer=samples, trajectories=total,
            planning_seed=seed, metrics=METRICS, actions=steps, seconds=time.monotonic()-started,
            contrasts=[dict(stone_a=offers[a], stone_b=offers[b], **value) for (a,b),value in contrasts.items()],
            confidence=dict(level=.95, simultaneous_contrasts=family, z=z, method='Fixed-sample paired normal intervals with Bonferroni correction for this decision'),
            checkpoint=str(self.checkpoint), checkpoint_sha256=self.checkpoint_sha,
            environment_changes=environment_changes,
            library_sha256=metadata['library_sha256'], profile_sha256=train_rl.sha(profile),
            objective='Minimize mean remaining time to finish this run; unfinished continuations count as all remaining hours.',
            limits='Conditional on this exact visible state, current simulator probabilities and frozen continuation policy. '
                   'The intervals describe Monte Carlo error, not uncertainty in real game mechanics or global optimality. '
                   'A null supported_best means the offered choices are not separated confidently. '
                   'This is a local final-stage recommendation, not evidence of improved full-week performance.')
        return result, data

    def from_progress(self, progress, library, profile, *, budget=5, hours=168, local_hour=17,
                      sleep_until_hour=0, **options):
        """A declared remaining-event forecast; past event counters start at zero.

        The native .state format preserves resources/effects/offer history and
        paid_heals. Use compare() on a live native snapshot to retain historical
        event counters as well. `hours` and `budget` are what remains from now.
        """
        if not np.isfinite(hours) or not 0 < hours <= 8760:
            raise ValueError('Remaining hours must be positive and at most 8760')
        fields = {}
        for line in progress.splitlines():
            line = line.split('#',1)[0].strip()
            if '=' in line:
                key,value = line.split('=',1); fields[key.strip()] = value.strip()
        if fields.get('gem_offers', 'unknown') == 'unknown':
            raise ValueError('Supply the three actual gem_offers; the adviser does not generate a menu')
        if fields.get('phase') != 'gems' or fields.get('room') != '76':
            raise ValueError('This adviser needs the final stone choice after room 75')
        with VectorEnv(library, [profile], 1, budgets=[budget], seed=0, auto_reset=False,
                       deadline_hours=hours, event_start_hour=local_hour, sleep_until_hour=sleep_until_hour) as source:
            source.reset(); lab = EffectLab(source)
            with lab.fixture(progress, budget) as snapshot: lab.restore(0, snapshot, 0)
            lab.observe()
            result, data = self.compare(source, **options)
            result['input_mode'] = 'remaining-event progress import: past event counters start at zero'
            result['complete_offer_history'] = len(result['source_observation']['offered_gems']) == 9
            return result, data
