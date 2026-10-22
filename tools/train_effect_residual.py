"""Learn narrow effect decisions from paired remaining-week counterfactuals.

This is supervised policy improvement, not more PPO. The parent, game odds,
shop constraints and completion-only evaluation objective stay frozen.
"""
import argparse
from collections import Counter
import gzip
import json
from pathlib import Path
import re
from statistics import mean, NormalDist
import time
import numpy as np
import torch
from sklearn.tree import DecisionTreeRegressor
from sklearn.ensemble import ExtraTreesRegressor

import train_rl
import train_current_study as study
from train_effect_study import migrate, PARENT
from benchmark_player_ideas import transfer_changes, write_once
from probe_effect_choices import decisions
from publish_effect_study import stats
from sfld_rl.env import VectorEnv
from sfld_rl.effect_lab import EffectLab, BLESSINGS, CURSES, position, mix64
from sfld_rl.effect_residual import EffectFeatures, EffectResidual, FAMILIES, export_tree
from sfld_rl.practical import Practical
from sfld_rl.player_ideas import profile_values

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0, 20, 50, 100, 500, 1000]
TRAIN_BUDGETS = [0, 50, 500, 1000]


def engineered_cases(seed, per_family):
    rng = np.random.default_rng(seed)
    result = []
    blessing_kinds = ('one_hit', 'recovery', 'escape', 'disarm', 'lockpick', 'key_moment', 'raider')
    curse_kinds = ('poison', 'broken_armor', 'clumsy', 'hard_lock', 'gold_hangover')
    gems = ('rabbit', 'moonstone', 'greed', 'hero', 'gambler', 'time_traveler', 'greasy',
            'spying', 'pendant', 'rusty', 'pearl', 'bull', 'thirsty', 'explorer', 'deceit')
    for family in range(len(FAMILIES)):
        for j in range(per_family):
            room = int(rng.choice([32, 39, 46, 53, 59, 67, 72, 78, 84, 91, 96, 99]))
            selected = list(rng.choice(gems, (room-1)//25, replace=False))
            hp = float(rng.choice([21, 31, 43, 56, 72, 89, 100]))
            curse = ''
            bless = ''
            if family in (0, 1, 2):
                if j % 2 == 0:
                    bless = list(BLESSINGS.values())[j//2 % len(BLESSINGS)]
                else:
                    kinds = list(rng.choice(blessing_kinds, int(rng.choice([2, 3, 3])), replace=False))
                    if not set(kinds) & {'one_hit', 'recovery', 'escape'}: kinds[0] = 'recovery'
                    bless = ','.join(f'{k}:{rng.choice(["weak", "strong"])}:{int(rng.integers(1, 7))}' for k in kinds)
                if j % 4 == 0: curse = list(CURSES.values())[1 + j//4 % 4]
            else:
                kinds = list(rng.choice(curse_kinds, 3, replace=False))
                curse = ','.join(f'{k}:strong:{int(rng.choice([1, 2, 4, 6, 9]))}' for k in kinds)
                if j % 3 == 0: bless = 'recovery:strong:4,one_hit:strong:6'
                elif j % 3 == 1: bless = 'escape:strong:4'
            if family in (0, 1, 3):
                encounter = str(rng.choice(['crate', 'bronze', 'silver'])) if family == 0 else ('barrel' if family == 1 else 'cursed_chest')
                doors = None
            else:
                encounter = None
                target = 'blessing' if family == 2 else 'cursed'
                # Include a few known trapped comparisons; never invent two traps.
                if j % 8 == 0: target += ':trap'
                other = 'monster:trap' if j % 8 == 1 else 'monster'
                doors = f'{target},{other}' if j % 2 else f'{other},{target}'
            progress = position(room, hp, ','.join(selected), bless, curse, encounter, doors)
            progress = progress.replace('keys=6\n', f'keys={int(rng.choice([0, 2, 4, 6, 10, 16]))}\n')
            result.append({'family': family, 'source': 'engineered', 'progress': progress,
                           'name': f'{FAMILIES[family]}-{j}'})
    return result


def prepare(out, smoke):
    out.mkdir(parents=True, exist_ok=True)
    for source, name in ((PARENT, 'original-checkpoint.pt'),
                         (ROOT/'build-perf/Release/sfld_rl.dll', 'sfld_rl.dll'),
                         (ROOT/'profiles/synthetic.profile', 'synthetic.profile')):
        study.copy(source, out/name)
    paths = [Path(__file__), ROOT/'tools/probe_effect_choices.py', ROOT/'tools/train_rl.py',
             ROOT/'tools/train_current_study.py', ROOT/'tools/train_effect_study.py',
             ROOT/'tools/benchmark_player_ideas.py'] + sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    paths += [p for d in ('src', 'include') for p in (ROOT/d).rglob('*') if p.suffix in ('.cpp', '.hpp')]
    plan = {'schema': 'sfld-effect-residual-study-v1', 'smoke': smoke, 'budgets': BUDGETS,
        'training_budgets': TRAIN_BUDGETS, 'parent_sha256': train_rl.sha(PARENT),
        'library_sha256': train_rl.sha(out/'sfld_rl.dll'), 'profile_sha256': train_rl.sha(out/'synthetic.profile'),
        'sources': {p.relative_to(ROOT).as_posix(): train_rl.sha(p) for p in paths},
        'collection': {'seed': 12250011 if smoke else 12251001, 'namespace': 248,
                       'events': 4 if smoke else 256, 'natural_per_family': 2 if smoke else 32,
                       'engineered_per_family': 4 if smoke else 64},
        'rollouts': {'seed': 12250012 if smoke else 12252001, 'namespace': 249,
                     'pairs_per_state': 2 if smoke else 64},
        'selection': {'seed': 12250013 if smoke else 12253001, 'namespace': 250, 'events': 4 if smoke else 1024},
        'final': {'seed': 12250014 if smoke else 12254001, 'namespace': 251, 'events': 8 if smoke else 8192},
        'model_seed': 12256001,
        'candidates': [{'name': 'tree5', 'kind': 'tree', 'depth': 5, 'leaf': 20, 'margin': 0.0},
                       {'name': 'tree7', 'kind': 'tree', 'depth': 7, 'leaf': 12, 'margin': 0.0},
                       {'name': 'forest6', 'kind': 'forest', 'depth': 6, 'leaf': 12, 'margin': 0.0},
                       {'name': 'forest6-cautious', 'kind': 'forest', 'depth': 6, 'leaf': 12, 'margin': .005}],
        'target': 'Mean paired difference in remaining weekly completions: acquire minus avoid. First action forced, frozen parent thereafter until the actual 168-hour deadline. No proxy HP, gold, mushroom penalty, time reward, or omitted timeouts.',
        'sampling': 'Uniform reservoirs within five opportunity families from 256 natural parent weeks per training budget, plus declared engineered states. Artificial state frequencies are not estimates of occurrence.',
        'training': 'Unweighted regression on each state mean. 20% of states withheld for prediction checks; fit uses the remaining 80%. Same tree/forest shared across budgets.',
        'selection_rule': 'Highest equal-budget mean completions on fresh selection weeks; ties retain parent then earlier candidate. Freeze before final.',
        'promotion_gate': 'Selected candidate versus parent only on untouched final weeks. Positive lower family-adjusted 99% interval for equal-budget mean and no budget with a negative upper family-adjusted interval. Seven predeclared contrasts. Browser acceptance must pass.',
        'limits': 'One fitted data cohort. Engine acquisition timing and odds remain provisional; this is not proof of live-game optimality.'}
    write_once(out/'plan.json', plan)
    write_once(out/'engineered-cases.json', engineered_cases(plan['collection']['seed'], plan['collection']['engineered_per_family']))
    if not (out/'seed-audit.json').exists():
        pattern = re.compile(r'"(?:root_seed|seed)"\s*:\s*(\d+)')
        old = set()
        for path in (ROOT/'docs').rglob('*.json'):
            old.update(map(int, pattern.findall(path.read_text(encoding='utf-8-sig'))))
        roots = [plan[k]['seed'] for k in ('collection', 'rollouts', 'selection', 'final')]
        fps = [train_rl.seed_fingerprint(x) for x in roots]
        if len(set(fps)) != len(fps) or set(fps) & {train_rl.seed_fingerprint(x) for x in old}:
            raise RuntimeError('Seed fingerprint overlaps a published cohort')
        write_once(out/'seed-audit.json', {'roots': roots, 'fingerprints': fps, 'overlap': False, 'old_roots_checked': len(old)})
    for path in paths: study.copy(path, out/'source'/path.relative_to(ROOT))
    if not (out/'parent-checkpoint.pt').exists(): migrate(out)
    return plan


def collect(out, plan, budget, model, profile):
    setup = plan['collection']; bank = [[] for _ in FAMILIES]; seen = set(); counts = Counter()
    rng = np.random.default_rng(setup['seed'] + budget)
    with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], min(128, setup['events']), budgets=[budget],
                   seed=setup['seed'], seed_namespace=setup['namespace'], auto_reset=False) as env:
        obs, masks = env.reset(); lab = EffectLab(env)
        features = EffectFeatures(env.observation_names, env.action_names)
        rule = Practical(env.observation_names, env.action_names, 'learned', profile)
        finished = set()
        while len(finished) < setup['events']:
            actions, targets = decisions(model, rule, obs, masks, profile, 'cuda')
            family, first, second = features.opportunities(obs, masks)
            for slot in np.flatnonzero(family >= 0):
                episode = int(env.episode_indices[slot])
                if episode >= setup['events']: continue
                key = (episode, round(features.v(obs[slot:slot+1], 'run_number_div10')[0]*10),
                       round(features.v(obs[slot:slot+1], 'room_div100')[0]*100), int(family[slot]))
                if key in seen or actions[slot] not in (first[slot], second[slot]): continue
                seen.add(key); f = int(family[slot]); counts[f] += 1
                index = len(bank[f]) if len(bank[f]) < setup['natural_per_family'] else int(rng.integers(counts[f]))
                if index >= setup['natural_per_family']: continue
                case = {'source': 'natural', 'family': f, 'episode': episode,
                        'at_action': int(env._episode_info['actions'][slot]), 'visible': lab.visible(int(slot)),
                        'snapshot': lab.capture(int(slot))}
                if index < len(bank[f]): bank[f][index]['snapshot'].close(); bank[f][index] = case
                else: bank[f].append(case)
            obs, masks, _, done, trunc, info = env.step(actions, wait_targets=targets)
            if trunc.any() or info['invalid_actions'].any(): raise RuntimeError('Invalid natural collection')
            ended = np.flatnonzero(done)
            for slot in ended:
                index = int(info['episode_index'][slot])
                if index < setup['events']: finished.add(index)
            if len(ended): obs, masks = env.reset_done(ended)
        for case in json.loads((out/'engineered-cases.json').read_text()):
            bank[case['family']].append({**case, 'snapshot': lab.fixture(case['progress'], budget)})
    cases = [case for group in bank for case in group]
    write_once(out/f'cases-{budget}.json', [{k: v for k, v in c.items() if k != 'snapshot'} for c in cases])
    write_once(out/f'collection-{budget}.json', {'natural_weeks': len(finished), 'seen_by_family': dict(counts),
               'retained_natural': sum(c['source'] == 'natural' for c in cases), 'total_cases': len(cases)})
    print(json.dumps({'collected_budget': budget, 'cases': len(cases), 'natural_seen': dict(counts)}), flush=True)
    return cases


def label(out, plan, budget, cases, model, profile):
    setup = plan['rollouts']; pairs = setup['pairs_per_state']; total = len(cases)*pairs*2
    n = min(512, total); cursor = completed = 0; start = last_report = time.monotonic()
    outcome = np.full((len(cases), pairs, 2, 4), np.nan)
    jobs = np.zeros((n, 3), np.int64); active = np.zeros(n, bool); first_step = np.zeros(n, bool)
    base = np.zeros((n, 4)); next_clear_hours = np.full(n, np.nan)
    x = {}; observations = {}; actions_by_case = {}; masters = {}
    with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], n, budgets=[budget],
                   seed=setup['seed'], seed_namespace=setup['namespace'], auto_reset=False) as env:
        env.reset(); lab = EffectLab(env); features = EffectFeatures(env.observation_names, env.action_names)
        rule = Practical(env.observation_names, env.action_names, 'learned', profile)
        master = (setup['namespace'] << 56) | (train_rl.seed_fingerprint(setup['seed']) << 24)
        master = mix64(master ^ (budget << 32))
        def assign(slot):
            nonlocal cursor
            if cursor >= total:
                active[slot] = False
                lab.restore(slot, cases[0]['snapshot'], mix64(master ^ 0xffffffff))
                return
            c, rem = divmod(cursor, pairs*2); t, arm = divmod(rem, 2); cursor += 1
            jobs[slot] = c, t, arm; active[slot] = first_step[slot] = True
            next_clear_hours[slot] = np.nan
            lab.restore(slot, cases[c]['snapshot'], mix64(master ^ (c*pairs+t)))
        for slot in range(n): assign(slot)
        obs, masks = lab.observe()
        while active.any():
            actions, targets = decisions(model, rule, obs, masks, profile, 'cuda')
            family, first, second = features.opportunities(obs, masks)
            rows = np.flatnonzero(first_step & active)
            for slot in rows:
                c, t, arm = jobs[slot]
                if family[slot] != cases[c]['family']: raise RuntimeError('Opportunity changed on restore')
                if c not in x:
                    x[c] = features.encode(obs[slot:slot+1], family[slot:slot+1], first[slot:slot+1])[0]
                    observations[c] = obs[slot].copy()
                    actions_by_case[c] = [int(first[slot]), int(second[slot]), int(actions[slot])]
                elif not np.array_equal(observations[c], obs[slot]): raise RuntimeError('Independent restore changed visible state')
                base[slot] = [env._episode_info[k][slot] for k in ('completions', 'elapsed_hours', 'mushrooms', 'deaths')]
                actions[slot] = first[slot] if arm == 0 else second[slot]
                targets[slot] = -1
            first_step[:] = False
            obs, masks, _, done, trunc, info = env.step(actions, wait_targets=targets)
            if trunc.any() or info['invalid_actions'].any(): raise RuntimeError('Invalid counterfactual week')
            cleared = active & np.isnan(next_clear_hours) & (info['completions'] > base[:, 0])
            next_clear_hours[cleared] = info['elapsed_hours'][cleared] - base[cleared, 1]
            ended = np.flatnonzero(active & done)
            for slot in ended:
                c, t, arm = jobs[slot]
                if np.isfinite(outcome[c, t, arm]).any(): raise RuntimeError('Duplicate trial')
                outcome[c, t, arm] = [info['completions'][slot] - base[slot, 0],
                    info['mushrooms'][slot] - base[slot, 2], info['deaths'][slot] - base[slot, 3],
                    next_clear_hours[slot] if np.isfinite(next_clear_hours[slot]) else 168-base[slot, 1]]
                completed += 1; assign(int(slot))
            placeholders = np.flatnonzero(~active & done)
            for slot in placeholders: lab.restore(int(slot), cases[0]['snapshot'], mix64(master ^ 0xffffffff))
            if len(ended) or len(placeholders): obs, masks = lab.observe()
            if time.monotonic()-last_report >= 20:
                last_report = time.monotonic()
                print(json.dumps({'label_budget': budget, 'partial_weeks': completed, 'total': total,
                                  'seconds': round(last_report-start, 1)}), flush=True)
    if not np.isfinite(outcome).all(): raise RuntimeError('Missing counterfactual result')
    np.savez_compressed(out/f'labels-{budget}.npz', outcomes=outcome,
        x=np.stack([x[i] for i in range(len(cases))]), observations=np.stack([observations[i] for i in range(len(cases))]),
        actions=np.array([actions_by_case[i] for i in range(len(cases))]))
    write_once(out/f'labels-{budget}.json', {'cases': len(cases), 'paired_futures': len(cases)*pairs,
        'completed_partial_weeks': total, 'wall_seconds': time.monotonic()-start,
        'metrics': ['future_completions', 'additional_mushrooms', 'additional_deaths', 'next_clear_hours_capped'],
        'feature_names': features.feature_names, 'data_sha256': train_rl.sha(out/f'labels-{budget}.npz')})
    for case in cases: case['snapshot'].close()


def fit(out, plan):
    arrays = [np.load(out/f'labels-{b}.npz') for b in TRAIN_BUDGETS]
    x = np.concatenate([a['x'] for a in arrays])
    paired = np.concatenate([a['outcomes'][:, :, 0, 0]-a['outcomes'][:, :, 1, 0] for a in arrays])
    y = paired.mean(axis=1)
    # Split by the underlying engineered position or natural source event,
    # not by individual futures. Shared engineered cases across budgets stay
    # together, avoiding near-identical training/diagnostic positions.
    keys = []
    for budget in TRAIN_BUDGETS:
        for case in json.loads((out/f'cases-{budget}.json').read_text()):
            keys.append(('engineered', case['name']) if case['source'] == 'engineered' else ('natural', budget, case['episode']))
    import hashlib
    holdout = np.array([int.from_bytes(hashlib.sha256(json.dumps(k).encode()).digest()[:4], 'little') % 5 == 0 for k in keys])
    features = json.loads((out/'labels-0.json').read_text())['feature_names']
    diagnostics = []
    for candidate in plan['candidates']:
        minimum = 2 if plan['smoke'] else candidate['leaf']
        if candidate['kind'] == 'tree':
            model = DecisionTreeRegressor(max_depth=candidate['depth'], min_samples_leaf=minimum,
                                           random_state=plan['model_seed'])
        else:
            model = ExtraTreesRegressor(n_estimators=16, max_depth=candidate['depth'],
                min_samples_leaf=minimum, max_features=.8, n_jobs=2, random_state=plan['model_seed'])
        model.fit(x[~holdout], y[~holdout])
        trees = model.estimators_ if hasattr(model, 'estimators_') else [model]
        artifact = {'schema': 'sfld-effect-residual-v1', 'name': candidate['name'],
            'parent_sha256': plan['parent_sha256'], 'profile_sha256': plan['profile_sha256'],
            'target': plan['target'], 'features': features, 'margin': candidate['margin'],
            'trees': [export_tree(t) for t in trees]}
        write_once(out/(candidate['name']+'.json'), artifact)
        predicted = model.predict(x)
        # Checking the exported evaluator prevents a silent training/serving mismatch.
        from sfld_rl.effect_residual import predict_tree
        exported = sum(predict_tree(tuple(np.asarray(t[k]) for k in ('left','right','feature','threshold','value')), x)
                       for t in artifact['trees'])/len(trees)
        np.testing.assert_allclose(exported, predicted, rtol=0, atol=1e-12)
        diagnostics.append({'candidate': candidate['name'], 'train_states': int((~holdout).sum()),
            'held_out_states': int(holdout.sum()), 'held_out_mse': float(np.mean((predicted[holdout]-y[holdout])**2)),
            'held_out_null_mse': float(np.mean(y[holdout]**2)),
            'feature_importance': sorted(zip(features, model.feature_importances_.tolist()), key=lambda p: -p[1])[:18]})
    write_once(out/'fit.json', {'states': len(x), 'partial_weeks': sum(a['outcomes'].shape[0]*a['outcomes'].shape[1]*2 for a in arrays),
        'target_distribution': {'mean': float(y.mean()), 'std': float(y.std())}, 'models': diagnostics})
    print(json.dumps({'fit_states': len(x), 'models': diagnostics}), flush=True)


class ResidualPractical(Practical):
    artifact = None
    def __init__(self, names, actions, *args):
        super().__init__(names, actions, *args)
        self.residual = EffectResidual(names, actions, type(self).artifact)
    def apply(self, obs, masks, proposed):
        actual, targets, reasons = super().apply(obs, masks, proposed)
        actual, _ = self.residual.apply(obs, masks, actual)
        return actual, targets, reasons


def evaluate(out, plan, candidate, cohort, budget):
    original = study.Practical
    try:
        if candidate['name'] != 'parent':
            ResidualPractical.artifact = out/(candidate['name']+'.json')
            study.Practical = ResidualPractical
        return study.evaluate(out, plan, candidate, cohort, budget)
    finally:
        study.Practical = original


def run(out, smoke, stage):
    torch.set_num_threads(2); plan = prepare(out, smoke)
    if stage in ('all', 'data'):
        saved, model = train_rl.load_checkpoint(out/'parent-checkpoint.pt', torch.device('cuda')); model.eval()
        profile = profile_values(out/'synthetic.profile')
        with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], 1, budgets=[0]) as env:
            transfer_changes(saved['metadata']['environment_signature'], env.metadata, False)
        with torch.inference_mode():
            for budget in TRAIN_BUDGETS:
                if (out/f'labels-{budget}.json').exists():
                    metadata = json.loads((out/f'labels-{budget}.json').read_text())
                    if train_rl.sha(out/f'labels-{budget}.npz') != metadata['data_sha256']: raise RuntimeError('Labels changed')
                    continue
                cases = collect(out, plan, budget, model, profile)
                label(out, plan, budget, cases, model, profile)
    if stage == 'data': return
    fit(out, plan)
    base = {'checkpoint': 'parent-checkpoint.pt', 'sha256': train_rl.sha(out/'parent-checkpoint.pt'), 'steps': 0, 'style': 'practical'}
    candidates = [{'name': 'parent', **base}] + [{**base, 'name': c['name'], 'effect_artifact_sha256': train_rl.sha(out/(c['name']+'.json'))} for c in plan['candidates']]
    validation = {(c['name'], b): evaluate(out, plan, c, 'selection', b) for c in candidates for b in BUDGETS}
    means = {c['name']: mean(validation[c['name'], b]['summary']['completions']['mean'] for b in BUDGETS) for c in candidates}
    selection = {'winner': max(means, key=means.get), 'scores': means}
    write_once(out/'selection.json', selection); print(json.dumps({'frozen_selection': selection}), flush=True)
    winner = next(c for c in candidates if c['name'] == selection['winner'])
    if winner['name'] == 'parent':
        write_once(out/'summary.json', {'selection': selection, 'promotion': False, 'reason': 'Parent won selection; no final cohort consumed.'})
        return
    cells = {(c['name'], b): evaluate(out, plan, c, 'final', b) for c in (candidates[0], winner) for b in BUDGETS}
    differences = []; contrasts = []; z = NormalDist().inv_cdf(1-.01/(2*7))
    for b in BUDGETS:
        old, new = cells['parent', b]['rows'], cells[winner['name'], b]['rows']
        if [r['event'] for r in old] != [r['event'] for r in new]: raise RuntimeError('Unpaired weeks')
        delta = [n['completions']-o['completions'] for o,n in zip(old,new)]
        differences.append(delta); contrasts.append({'budget': b, **stats(delta, z)})
    contrasts.append({'budget': 'equal-budget mean', **stats([mean(x) for x in zip(*differences)], z)})
    promote = contrasts[-1]['family_99_ci'][0] > 0 and all(c['family_99_ci'][1] >= 0 for c in contrasts[:-1]) and not smoke
    for path, digest in plan['sources'].items():
        if train_rl.sha(ROOT/path) != digest: raise RuntimeError('Frozen study source changed: '+path)
    write_once(out/'summary.json', {'selection': selection, 'promotion': promote, 'contrasts': contrasts,
        'cells': [{k:v for k,v in c.items() if k != 'rows'} for c in cells.values()]})
    with gzip.open(out/'final-rows.jsonl.gz', 'wt', encoding='utf8') as f:
        for cell in cells.values():
            for row in cell['rows']: f.write(json.dumps(row)+'\n')
    print(json.dumps({'promotion': promote, 'selected_vs_parent': contrasts[-1]}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--stage', choices=['all', 'data', 'evaluate'], default='all')
    a = parser.parse_args(); run(a.output.resolve(), a.smoke, a.stage)
