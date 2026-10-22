"""Paired frozen-policy tests of player-proposed shops, arena, revives and stones."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import statistics
import time

import numpy as np
import torch

import train_rl
from sfld_rl.env import VectorEnv
from sfld_rl.shop_experiments import EFFECTS, POLICIES, Policy, Rules

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'docs/rl-history-2026-10-11'
POLICIES.update({p.name: p for p in (
    Policy('strong_arena15', 'strong_pair_greed', 'protected', True, 15),
    Policy('strong_arena20', 'strong_pair_greed', 'protected', True, 20),
    Policy('first_arena15', 'first_useful', 'protected', True, 15),
    Policy('onehit_arena15', 'healthy_one_hit8', 'protected', True, 15),
)})
COUNTERS = ('decisions', 'shop_overrides', 'arena_overrides', 'prep_overrides', 'greasy_overrides',
            'arena_entries', 'arena_entry_hp_sum', 'arena_entry_one_hit', 'arena_entry_recovery',
            'arena_fights', 'arena_flees', 'arena_deaths', 'arena_legendaries', 'arena_epics',
            'arena_chests_1', 'arena_chests_2', 'arena_chests_3', 'arena_chests_4', 'arena_chests_5',
            'greasy_step_purchases', 'greasy_step_spending', 'shop_purchases', 'strong_shop_purchases')
EXTRA_COUNTERS = ('gem_overrides',)


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != data:
            raise RuntimeError(f'Existing artifact differs: {path}')
        return
    with path.open('x', encoding='utf-8') as f:
        json.dump(data, f, indent=2, allow_nan=False)
        f.write('\n')


def profile_values(path):
    return dict(line.split('=', 1) for line in Path(path).read_text().splitlines() if '=' in line and not line.startswith('#'))


def interval(values):
    mean = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else 0
    return {'mean': mean, 'standard_error': se, 'normal_99_ci': [mean - 2.5758293035489 * se, mean + 2.5758293035489 * se]}


def summarize(rows):
    result = {'events': len(rows), 'policy': rows[0]['policy'], 'budget': rows[0]['budget']}
    for metric in ('completions', 'legendaries', 'expected_legendaries', 'arena_legendaries', 'expected_arena_legendaries'):
        result[metric] = interval([r[metric] for r in rows])
    for metric in ('mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'deaths', *COUNTERS):
        result['mean_' + metric] = statistics.mean(r[metric] for r in rows)
    for metric in EXTRA_COUNTERS:
        if metric in rows[0]:
            result['mean_' + metric] = statistics.mean(r[metric] for r in rows)
    entries = sum(r['arena_entries'] for r in rows)
    result['arena_mean_entry_hp'] = sum(r['arena_entry_hp_sum'] for r in rows) / entries if entries else None
    result['arena_five_chest_fraction_per_entry'] = sum(r['arena_chests_5'] for r in rows) / entries if entries else None
    return result


def validate_rows(rows, n, budget, policy, p5):
    if len(rows) != n or {r['event_index'] for r in rows} != set(range(n)):
        raise RuntimeError('Incomplete/duplicate event cohort')
    for r in rows:
        if (r['budget'] != budget or r['policy'] != policy or not r['terminated'] or r['truncated'] or
                r['invalid_actions'] or r['actions'] != r['decisions'] or r['mushrooms'] > budget or
                r['mushrooms'] != r['recovery_mushrooms'] + r['reroll_mushrooms'] or
                not 167.999 < r['elapsed_hours'] < 168.000001):
            raise RuntimeError('Event accounting failed')
        if (r['legendaries'] != r['completions'] + r['arena_legendaries'] or
                r['arena_chests_5'] > r['arena_entries'] or
                r['arena_legendaries'] > r['arena_chests_4'] + r['arena_chests_5']):
            raise RuntimeError('Reward attribution failed')
        if abs(r['expected_arena_legendaries'] - (.01 * r['arena_chests_4'] + p5 * r['arena_chests_5'])) > 1e-9:
            raise RuntimeError('Expected reward accounting failed')
        for k in (*COUNTERS, *(k for k in EXTRA_COUNTERS if k in r)):
            if not math.isfinite(r[k]) or r[k] < 0:
                raise RuntimeError('Invalid diagnostic counter')


def paired(left, right):
    a = {r['event_index']: r for r in left}
    b = {r['event_index']: r for r in right}
    if len(a) != len(left) or len(b) != len(right) or a.keys() != b.keys():
        raise RuntimeError('Mismatched paired events')
    return {metric: interval([b[i][metric] - a[i][metric] for i in sorted(a)])
            for metric in ('completions', 'legendaries', 'expected_legendaries', 'expected_arena_legendaries')}


def evaluate(args, saved, model, device, budget, policy, profile, p5):
    started = time.monotonic()
    values = profile_values(profile)
    boss_high = [float(r.split(':')[1]) for r in values['boss_damage'].split(',')]
    collected, shop_counts, stone_counts = {}, Counter(), {}
    stones_by_run = {'first': {}, 'later': {}}
    with VectorEnv(args.library, [profile], min(args.n_envs, args.events), budgets=[budget],
                   seed=args.seed, seed_namespace=args.namespace, deadline_hours=168,
                   max_actions=100000, auto_reset=False) as env:
        changes = train_rl.environment_changes(saved['metadata']['environment_signature'], env.metadata)
        if set(changes) - {'profile_sha256'}:
            raise RuntimeError('Unplanned evaluation mismatch')
        rules = Rules(env.observation_names, env.action_names, policy, boss_high)
        obs, masks = env.reset()
        counters = {k: np.zeros(env.n_envs, dtype=np.float64) for k in (*COUNTERS, *EXTRA_COUNTERS)}
        ai, v, flag = rules.ai, rules.v, rules.flag
        with torch.inference_mode():
            while len(collected) < args.events:
                actions = model.actions(train_rl.tensor(obs, device), train_rl.tensor(masks, device, torch.bool), greedy=True)[0].cpu().numpy()
                actual, reasons = rules.apply(obs, masks, actions)
                active = env.episode_indices < args.events
                before = env._episode_info
                for number, name in enumerate(('shop', 'arena', 'prep', 'greasy', 'gem'), 1):
                    counters[name + '_overrides'] += reasons == number
                counters['decisions'] += 1
                door_phase = flag(obs, 'phase.doors')
                entry = np.zeros(env.n_envs, dtype=bool)
                for i in range(2):
                    entry |= door_phase & (actual == ai[f'door_{i}']) & flag(obs, f'doors.{i}.kind.trial') & (v(obs, 'trial_depth_div5') < .01)
                counters['arena_entries'] += entry
                counters['arena_entry_hp_sum'] += entry * v(obs, 'hp')
                counters['arena_entry_one_hit'] += entry & rules.effect(obs, 'one_hit')
                counters['arena_entry_recovery'] += entry & rules.effect(obs, 'recovery')
                trial = flag(obs, 'phase.encounter') & flag(obs, 'encounter.trial_monster')
                counters['arena_fights'] += trial & (actual == ai['fight'])
                counters['arena_flees'] += trial & (actual == ai['flee'])
                in_arena = entry | trial | (v(obs, 'trial_depth_div5') > .01)
                prize = flag(obs, 'phase.encounter') & flag(obs, 'encounter.prize') & (actual == ai['interact'])
                tiers = np.rint(v(obs, 'pending_trial_reward_div5') * 5).astype(int)
                for tier in range(1, 6):
                    counters[f'arena_chests_{tier}'] += prize & (tiers == tier)
                greasy_buy = flag(obs, 'phase.recovery') & flag(obs, 'owned_gem.greasy') & (actual == ai['heal_step'])
                counters['greasy_step_purchases'] += greasy_buy
                counters['greasy_step_spending'] += greasy_buy * np.rint(v(obs, 'heal_step_price_div100') * 100)
                buying = flag(obs, 'phase.shop') & np.isin(actual, [ai['buy_0'], ai['buy_1']])
                kinds, strengths = rules.offer_arrays(obs) if np.any(buying) else (None, None)
                for slot in np.flatnonzero(buying):
                    side = int(actual[slot] == ai['buy_1'])
                    counters['shop_purchases'][slot] += 1
                    counters['strong_shop_purchases'][slot] += strengths[slot, side]
                    if active[slot]:
                        opening = flag(obs, 'first_event_run')[slot] and round(v(obs, 'room_div100')[slot] * 100) == 5
                        key = ('opening' if opening else 'regular') + ':' + EFFECTS[kinds[slot, side]] + ':' + ('strong' if strengths[slot, side] else 'weak')
                        shop_counts[key] += 1
                choosing = flag(obs, 'phase.gems') & np.isin(actual, [ai['gem_0'], ai['gem_1'], ai['gem_2']]) & active
                gem_names = [n.split('.', 1)[1] for n in rules.oi if n.startswith('owned_gem.')]
                for slot in np.flatnonzero(choosing):
                    run_class = 'first' if flag(obs, 'first_event_run')[slot] else 'later'
                    for i in range(3):
                        gem = next(g for g in gem_names if flag(obs, f'gem_offers.{i}.{g}')[slot])
                        count = stone_counts.setdefault(gem, {'offered': 0, 'picked': 0})
                        count['offered'] += 1
                        count['picked'] += int(actual[slot] == ai[f'gem_{i}'])
                        by_run = stones_by_run[run_class].setdefault(gem, {'offered': 0, 'picked': 0})
                        by_run['offered'] += 1
                        by_run['picked'] += int(actual[slot] == ai[f'gem_{i}'])
                epics_before = np.rint(v(obs, 'epics_div100') * 100)
                obs, masks, rewards, terminated, truncated, info = env.step(actual)
                counters['arena_deaths'] += in_arena * (info['deaths'] - before['deaths'])
                counters['arena_legendaries'] += prize * (info['legendaries'] - before['legendaries'])
                counters['arena_epics'] += prize * (np.rint(v(obs, 'epics_div100') * 100) - epics_before)
                ended = np.flatnonzero(terminated | truncated)
                for slot in ended:
                    event = int(info['episode_index'][slot])
                    if event >= args.events:
                        continue
                    if event in collected:
                        raise RuntimeError('Duplicated event')
                    row = {'event_index': event, 'budget': budget, 'policy': policy.name,
                           'terminated': bool(terminated[slot]), 'truncated': bool(truncated[slot])}
                    for k in ('completions', 'legendaries', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms',
                              'invalid_actions', 'elapsed_hours', 'actions', 'deaths'):
                        row[k] = float(info[k][slot]) if k == 'elapsed_hours' else int(info[k][slot])
                    row['first_completion_hours'] = float(info['first_completion_hours'][slot])
                    for k in counters:
                        row[k] = float(counters[k][slot]) if k == 'arena_entry_hp_sum' else int(counters[k][slot])
                    row['expected_arena_legendaries'] = .01 * row['arena_chests_4'] + p5 * row['arena_chests_5']
                    row['expected_legendaries'] = row['completions'] + row['expected_arena_legendaries']
                    collected[event] = row
                if len(ended):
                    obs, masks = env.reset_done(ended)
                    for c in counters.values():
                        c[ended] = 0
        rows = [collected[i] for i in range(args.events)]
        validate_rows(rows, args.events, budget, policy.name, p5)
        if sum(shop_counts.values()) != sum(r['shop_purchases'] for r in rows):
            raise RuntimeError('Purchase counts differ from event totals')
        return {'summary': summarize(rows), 'outcomes': rows, 'shop_buys': dict(sorted(shop_counts.items())),
                'stones': stone_counts, 'stones_by_run': stones_by_run,
                'environment': env.metadata, 'environment_changes': changes,
                'wall_seconds': time.monotonic() - started}


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(2)
    device = torch.device(args.device)
    saved, model = train_rl.load_checkpoint(args.checkpoint, device)
    model.eval()
    profile = args.output / 'scenario.profile'
    content = args.profile.read_text()
    if args.arena_rate == '10':
        content = content.replace('trial_legendary_chances=0,0,0,0.01,0.0625', 'trial_legendary_chances=0,0,0,0.01,0.10')
        content = content.replace('trial_rewards_5=19,20,6,3', 'trial_rewards_5=57,60,18,15')
        content += '\n# Player-requested sensitivity:10% legendary; preserve conditional nonlegendary mix.\n'
    if profile.exists() and profile.read_text() != content:
        raise RuntimeError('Scenario changed')
    profile.write_text(content, encoding='utf-8')
    weights = [float(x) for x in profile_values(profile)['trial_rewards_5'].split(',')]
    p5 = weights[3] / sum(weights)
    if abs(p5 - float(args.arena_rate) / 100) > 1e-10:
        raise RuntimeError('Scenario rate edit did not apply')
    sources = {'checkpoint': args.checkpoint, 'library': args.library, 'profile': profile,
               'base_profile': args.profile, 'runner': Path(__file__),
               'rules': ROOT / 'tools/sfld_rl/shop_experiments.py', 'trainer': ROOT / 'tools/train_rl.py',
               'priority': ROOT / 'tools/sfld_rl/stone_priority.py',
               'adapter': ROOT / 'tools/sfld_rl/env.py', 'network': ROOT / 'tools/sfld_rl/ppo.py'}
    plan = {'schema': 'sfld-shop-arena-study-v2', 'seed': str(args.seed), 'namespace': args.namespace,
            'events_per_cell': args.events, 'budgets': args.budgets, 'arena_legendary_rate': p5,
            'policies': [POLICIES[n].record() for n in args.policies],
            'hashes': {k: train_rl.sha(v) for k, v in sources.items()},
            'n_envs': min(args.n_envs, args.events), 'device': str(device),
            'notes': ['Frozen learner with observable-state action interventions; no retraining.',
                      'The checkpoint training budget range is recorded in its metadata; rules do not retrain it.',
                      'Pendant variants only override gem choices in menus containing a legal Pendant offer: Rabbit, then Moonstone, then Pendant. Other menus retain learned choices.',
                      'pendant_escape additionally requires an already-owned Rabbit or Moonstone; pendant_priority has no ownership gate.',
                      'High HP=70%; comfortable=boss maximum unmodified hit+20%+0.1% margin.',
                      'No per-shop reroll cap; require4keys; weekly gross spending is a hard cap.',
                      'Target offered:buy immediately. If multiple qualify, retain learner choice when eligible; otherwise Recovery below70%, OneHit above.',
                      'Instant healing only allowed as learned fallback when search impossible; Raider forbidden in shop interventions.',
                      'Learner pre-action healing remains legal. Greasy interventions force20% ordinary reentry, never boss recovery.',
                      'Expected legendaries=completions+0.01*tier4chests+p5*tier5chests; observed drops separately recorded.',
                      'Paired seeds align initial worlds; later RNG consumption and visited states may diverge.',
                      'Select on screening data, then confirm fixed policies on a fresh seed.99% intervals are per comparison, not model uncertainty.']}
    write_once(args.output / 'plan.json', plan)
    for key in ('runner', 'rules', 'priority'):
        dest = args.output / sources[key].name
        if dest.exists() and dest.read_bytes() != sources[key].read_bytes():
            raise RuntimeError('Archived source differs')
        dest.write_bytes(sources[key].read_bytes())
    cells = {}
    for budget in args.budgets:
        for name in args.policies:
            path = args.output / f'cap-{budget}-{name}.json'
            if path.exists():
                cell = json.loads(path.read_text())
                if cell['plan'] != plan:
                    raise RuntimeError('Cached cell provenance differs')
                validate_rows(cell['outcomes'], args.events, budget, name, p5)
                if cell['summary'] != summarize(cell['outcomes']):
                    raise RuntimeError('Cached summary mismatch')
            else:
                cell = evaluate(args, saved, model, device, budget, POLICIES[name], profile, p5)
                cell['plan'] = plan
                write_once(path, cell)
            cells[budget, name] = cell
            s = cell['summary']
            print(json.dumps({'budget': budget, 'policy': name, 'completions': s['completions']['mean'],
                              'expected_legendaries': s['expected_legendaries']['mean'],
                              'rerolls': s['mean_reroll_mushrooms'], 'arena5': s['mean_arena_chests_5'],
                              'seconds': cell['wall_seconds']}), flush=True)
    comparisons = []
    selections = {}
    for budget in args.budgets:
        group = [c for (b, _), c in cells.items() if b == budget]
        selections[str(budget)] = {metric: max(group, key=lambda c: (c['summary'][metric]['mean'], c['summary']['policy'] == 'baseline'))['summary']['policy']
                                   for metric in ('completions', 'expected_legendaries')}
        if (budget, 'baseline') in cells:
            for c in group:
                if c['summary']['policy'] != 'baseline':
                    comparisons.append({'budget': budget, 'policy': c['summary']['policy'],
                                        'deltas': paired(cells[budget, 'baseline']['outcomes'], c['outcomes'])})
    result = {'plan': plan, 'summaries': [c['summary'] for c in cells.values()],
              'comparisons': comparisons, 'descriptive_best_in_this_cohort': selections}
    write_once(args.output / 'summary.json', result)
    for k, path in sources.items():
        if train_rl.sha(path) != plan['hashes'][k]:
            raise RuntimeError('Input changed during evaluation:' + k)
    return result


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--checkpoint', type=Path, default=ARCHIVE / 'selected-checkpoint.pt')
    p.add_argument('--library', type=Path, default=ARCHIVE / 'sfld_rl.dll')
    p.add_argument('--profile', type=Path, default=ARCHIVE / 'synthetic.profile')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--budgets', default='100,500,1000')
    p.add_argument('--policies', default=','.join(POLICIES))
    p.add_argument('--arena-rate', choices=['6.25', '10'], default='10')
    p.add_argument('--events', type=int, default=512)
    p.add_argument('--n-envs', type=int, default=256)
    p.add_argument('--seed', type=int, default=9801001)
    p.add_argument('--namespace', type=lambda s: int(s, 0), default=0xDA)
    p.add_argument('--device', default='cuda')
    a = p.parse_args(argv)
    a.budgets = [int(s) for s in a.budgets.split(',')]
    a.policies = a.policies.split(',')
    if a.events < 1 or a.n_envs < 1 or any(b < 0 for b in a.budgets) or len(set(a.policies)) != len(a.policies) or any(n not in POLICIES for n in a.policies):
        p.error('Invalid cohort or policies')
    return a


if __name__ == '__main__':
    run(parse())
