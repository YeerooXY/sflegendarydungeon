"""Explain an effect residual on the fixed panel and fresh natural weeks."""
import argparse
from collections import Counter
import json
from pathlib import Path
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes
from probe_effect_choices import decisions
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, probe_cases
from sfld_rl.effect_residual import EffectResidual, FAMILIES
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values


def run(out, candidate, events):
    torch.set_num_threads(2)
    plan = json.loads((out/'plan.json').read_text())
    artifact = out/(candidate+'.json')
    residual_type = EffectResidual
    if json.loads(artifact.read_text())['schema'].endswith('-v2'):
        from sfld_rl.effect_refinement import RefinedEffectResidual
        residual_type = RefinedEffectResidual
    saved, model = train_rl.load_checkpoint(out/'parent-checkpoint.pt', torch.device('cuda')); model.eval()
    profile = profile_values(out/'synthetic.profile')
    cases = probe_cases(); panel = []; counts = []; examples = []
    root, namespace = 12255001, 252
    audit_plan = {'seed': root, 'namespace': namespace, 'events_per_budget': events,
                  'candidate': candidate, 'artifact_sha256': train_rl.sha(artifact),
                  'scope': 'Fixed visible-state diagnostic panel, then descriptive natural behavior. Does not select or promote a policy.'}
    write_once(out/'behavior-plan.json', audit_plan)
    with torch.inference_mode():
        for budget in plan['budgets']:
            with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], len(cases), budgets=[budget], auto_reset=False) as env:
                transfer_changes(saved['metadata']['environment_signature'], env.metadata, False)
                env.reset(); lab = EffectLab(env)
                for i, case in enumerate(cases):
                    with lab.fixture(case['progress'], budget) as snapshot: lab.restore(i, snapshot, 0)
                obs, masks = lab.observe(); rule = Practical(env.observation_names, env.action_names, 'learned', profile)
                residual = residual_type(env.observation_names, env.action_names, artifact)
                proposed, _ = decisions(model, rule, obs, masks, profile, 'cuda')
                actual, info = residual.apply(obs, masks, proposed)
                for i, case in enumerate(cases):
                    panel.append({'budget': budget, 'case': case['name'], 'parent': env.action_names[int(proposed[i])],
                        'chosen': env.action_names[int(actual[i])], 'predicted_completion_difference_acquire_minus_avoid': float(info['advantage'][i])})
            counter = Counter(); natural_examples = []; ended_events = set()
            with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], min(256, events), budgets=[budget],
                           seed=root, seed_namespace=namespace, auto_reset=False) as env:
                obs, masks = env.reset(); lab = EffectLab(env)
                rule = Practical(env.observation_names, env.action_names, 'learned', profile)
                residual = residual_type(env.observation_names, env.action_names, artifact)
                while len(ended_events) < events:
                    proposed, targets = decisions(model, rule, obs, masks, profile, 'cuda')
                    actual, details = residual.apply(obs, masks, proposed)
                    valid = env.episode_indices < events
                    counter['decisions'] += int(valid.sum())
                    for f, name in enumerate(FAMILIES):
                        relevant = valid & (details['family'] == f)
                        counter[name+'.seen'] += int(relevant.sum())
                        counter[name+'.changed'] += int((relevant & details['changed']).sum())
                        for action in ('interact', 'skip', 'door_0', 'door_1'):
                            counter[name+'.'+action] += int((relevant & (actual == env.action_names.index(action))).sum())
                    for slot in np.flatnonzero(valid & details['changed']):
                        f = int(details['family'][slot]); name = FAMILIES[f]
                        if sum(e['family'] == name for e in natural_examples) >= 8: continue
                        natural_examples.append({'family': name, 'event': int(env.episode_indices[slot]),
                            'parent': env.action_names[int(proposed[slot])], 'chosen': env.action_names[int(actual[slot])],
                            'predicted_completion_difference': float(details['advantage'][slot]), 'visible': lab.visible(int(slot))})
                    obs, masks, _, done, truncated, info = env.step(actual, wait_targets=targets)
                    if truncated.any() or info['invalid_actions'].any(): raise RuntimeError('Invalid behavior week')
                    ended = np.flatnonzero(done)
                    for slot in ended:
                        index = int(info['episode_index'][slot])
                        if index < events: ended_events.add(index)
                    if len(ended): obs, masks = env.reset_done(ended)
            counts.append({'budget': budget, 'events': events, 'counts': dict(counter)})
            examples.extend({'budget': budget, **x} for x in natural_examples)
            print(json.dumps({'audited_budget': budget, 'changes': sum(v for k,v in counter.items() if k.endswith('.changed'))}), flush=True)
    write_once(out/'effect-decisions.json', {'identity': audit_plan, 'panel': panel, 'behavior': counts, 'examples': examples})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--study', type=Path, required=True)
    p.add_argument('--candidate')
    p.add_argument('--events', type=int, default=256)
    a = p.parse_args()
    candidate = a.candidate or json.loads((a.study/'selection.json').read_text())['winner']
    if candidate == 'parent': raise ValueError('No residual selected')
    run(a.study.resolve(), candidate, a.events)
