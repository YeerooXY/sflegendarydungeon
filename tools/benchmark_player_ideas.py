"""Frozen-policy paired experiment on the corrected simulator; no training."""
from __future__ import annotations
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import shutil
import statistics
import time

import numpy as np
import torch
import train_rl
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import CASES, REASONS, PlayerIdeas, profile_values

ROOT = Path(__file__).resolve().parents[1]
COUNTERS = ('decisions', 'changed_actions', 'custom_waits', 'reentries', 'reentry_hp_sum',
            'near_boss_reentries', 'near_boss_hp_sum', 'shop_purchases', 'shop_rerolls',
            'first_paid_shop_keys', 'first_paid_shop_seen', 'first_paid_shop_four_keys',
            'first_boss25_keys', 'first_boss25_seen', 'deathless_first_clear',
            *('rule_'+name for name in REASONS))


def write_once(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text(encoding='utf8')) != data:
            raise RuntimeError('Existing artifact differs: '+str(path))
    else:
        with path.open('x', encoding='utf8') as f:
            json.dump(data, f, indent=2, allow_nan=False)
            f.write('\n')


def interval(values):
    mean = statistics.mean(values)
    se = statistics.stdev(values)/math.sqrt(len(values)) if len(values)>1 else 0
    return {'mean': mean, 'standard_error': se,
            'normal_99_ci': [mean-2.5758293035489*se, mean+2.5758293035489*se]}


def transfer_changes(saved, current, allowed):
    current = train_rl.compact_metadata(current)
    changes = {k: {'training': saved.get(k), 'current': current.get(k)}
               for k in sorted(set(saved)|set(current)) if saved.get(k) != current.get(k)}
    if changes and not allowed:
        raise RuntimeError('Explicit --allow-engine-transfer required: '+', '.join(changes))
    for key in ('observation_names', 'action_names', 'deadline_hours', 'max_actions'):
        if saved.get(key) != current.get(key): raise RuntimeError('Incompatible transfer: '+key)
    old, new = dict(saved['schema']), dict(current['schema'])
    old.pop('engine', None); new.pop('engine', None)
    if old != new: raise RuntimeError('Only the engine version may change in the schema')
    if set(changes)-{'library_sha256', 'profile_sha256', 'schema_sha256', 'schema'}:
        raise RuntimeError('Unexpected transfer metadata: '+str(set(changes)))
    return changes


def summarize(rows):
    result = {'events': len(rows), 'case': rows[0]['case'], 'budget': rows[0]['budget']}
    for key in ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms', 'deaths', 'legendaries'):
        result[key] = interval([r[key] for r in rows])
    clear = [r['first_completion_hours'] for r in rows if r['completions']]
    result['first_clear_fraction'] = len(clear)/len(rows)
    result['first_clear_hours_among_cleared'] = interval(clear) if clear else None
    for key in COUNTERS: result['mean_'+key] = statistics.mean(r[key] for r in rows)
    for label, numerator, denominator in (
            ('mushrooms_per_completion', 'mushrooms', 'completions'),
            ('reentry_mean_hp', 'reentry_hp_sum', 'reentries'),
            ('near_boss_reentry_mean_hp', 'near_boss_hp_sum', 'near_boss_reentries'),
            ('first_paid_shop_mean_keys', 'first_paid_shop_keys', 'first_paid_shop_seen'),
            ('first_paid_shop_four_key_fraction', 'first_paid_shop_four_keys', 'first_paid_shop_seen'),
            ('first_boss25_mean_keys', 'first_boss25_keys', 'first_boss25_seen')):
        den = sum(r[denominator] for r in rows)
        result[label] = sum(r[numerator] for r in rows)/den if den else None
    return result


def paired(left, right):
    if [r['event_index'] for r in left] != [r['event_index'] for r in right]:
        raise RuntimeError('Mismatched paired seeds')
    result = {k: interval([b[k]-a[k] for a,b in zip(left,right)])
              for k in ('completions', 'mushrooms', 'deaths', 'legendaries')}
    if all(r['completions'] for r in left+right):
        result['first_completion_hours'] = interval([b['first_completion_hours']-a['first_completion_hours']
                                                    for a,b in zip(left,right)])
    return result


def validate(rows, count, budget, name):
    if [r['event_index'] for r in rows] != list(range(count)): raise RuntimeError('Incomplete event cohort')
    for row in rows:
        if (row['case'] != name or row['budget'] != budget or not row['terminated'] or row['truncated']
                or row['invalid_actions'] or row['actions'] != row['decisions']
                or row['mushrooms'] > budget or row['mushrooms'] != row['recovery_mushrooms']+row['reroll_mushrooms']
                or not 167.999 < row['elapsed_hours'] < 168.000001):
            raise RuntimeError('Event accounting failed: '+str(row))


def evaluate(args, saved, model, device, budget, ideas):
    start = time.monotonic()
    with VectorEnv(args.library, [args.profile], min(args.n_envs,args.events), budgets=[budget],
                   seed=args.seed, seed_namespace=args.namespace, deadline_hours=168,
                   max_actions=100000, auto_reset=False) as env:
        changes = transfer_changes(saved['metadata']['environment_signature'], env.metadata, args.allow_engine_transfer)
        rules = PlayerIdeas(env.observation_names, env.action_names, ideas, profile_values(args.profile))
        obs, masks = env.reset()
        counters = {key: np.zeros(env.n_envs) for key in COUNTERS}
        collected, stone_counts = {}, Counter()
        ai, v, flag = rules.ai, rules.v, rules.flag
        with torch.inference_mode():
            while len(collected) < args.events:
                proposed = model.actions(train_rl.tensor(obs,device), train_rl.tensor(masks,device,torch.bool), greedy=True)[0].cpu().numpy()
                actual, targets, reasons = rules.apply(obs,masks,proposed)
                active = env.episode_indices < args.events
                before = env._episode_info
                room = np.rint(v(obs,'room_div100')*100).astype(int)
                keys = np.rint(v(obs,'keys_div20')*20)
                counters['decisions'] += 1
                counters['changed_actions'] += (actual != proposed) | (targets >= 0)
                counters['custom_waits'] += targets >= 0
                for i,reason in enumerate(REASONS): counters['rule_'+reason] += reasons[:,i]
                reentry = flag(obs,'phase.recovery') & (actual == ai['reenter'])
                near = reentry & (room >= 97) & (room < 100)
                counters['reentries'] += reentry
                counters['reentry_hp_sum'] += reentry*v(obs,'hp')
                counters['near_boss_reentries'] += near
                counters['near_boss_hp_sum'] += near*v(obs,'hp')
                shop = flag(obs,'phase.shop')
                counters['shop_purchases'] += shop & np.isin(actual,[ai['buy_0'],ai['buy_1']])
                counters['shop_rerolls'] += shop & (actual == ai['reroll'])
                first_shop = shop & (room > 5) & (counters['first_paid_shop_seen'] == 0)
                counters['first_paid_shop_seen'] += first_shop
                counters['first_paid_shop_keys'] += first_shop*keys
                counters['first_paid_shop_four_keys'] += first_shop*(keys >= 4)
                choosing = flag(obs,'phase.gems') & np.isin(actual,[ai[f'gem_{i}'] for i in range(3)]) & active
                for i in range(3):
                    picked = choosing & (actual == ai[f'gem_{i}'])
                    if np.any(picked):
                        for name in env.observation_names:
                            prefix = f'gem_offers.{i}.'
                            if name.startswith(prefix): stone_counts[name[len(prefix):]] += int(np.count_nonzero(picked & flag(obs,name)))
                obs, masks, reward, terminated, truncated, info = env.step(actual, wait_targets=targets) if np.any(targets>=0) else env.step(actual)
                passed25 = (room == 25) & (np.rint(v(obs,'room_div100')*100) == 26) & (counters['first_boss25_seen'] == 0)
                counters['first_boss25_seen'] += passed25
                counters['first_boss25_keys'] += passed25*np.rint(v(obs,'keys_div20')*20)
                counters['deathless_first_clear'] += (before['completions']==0) & (info['completions']>0) & (info['deaths']==0)
                ended = np.flatnonzero(terminated | truncated)
                for slot in ended:
                    index = int(info['episode_index'][slot])
                    if index >= args.events: continue
                    if index in collected: raise RuntimeError('Duplicate event')
                    row = {'event_index': index, 'case': ideas.name, 'budget': budget,
                           'terminated': bool(terminated[slot]), 'truncated': bool(truncated[slot])}
                    for key in ('completions','legendaries','mushrooms','recovery_mushrooms','reroll_mushrooms',
                                'invalid_actions','elapsed_hours','actions','deaths','first_completion_hours'):
                        row[key] = float(info[key][slot])
                    row.update({key: float(value[slot]) for key,value in counters.items()})
                    collected[index] = row
                if len(ended):
                    obs,masks = env.reset_done(ended)
                    for value in counters.values(): value[ended] = 0
        rows = [collected[i] for i in range(args.events)]
        validate(rows,args.events,budget,ideas.name)
        return {'summary': summarize(rows), 'outcomes': rows, 'stones_picked': dict(stone_counts),
                'environment': env.metadata, 'transfer': changes, 'wall_seconds': time.monotonic()-start}


def run(args):
    args.output.mkdir(parents=True,exist_ok=True)
    torch.set_num_threads(2)
    device = torch.device(args.device)
    saved, model = train_rl.load_checkpoint(args.checkpoint,device)
    model.eval()
    sources = {'checkpoint': args.checkpoint,'library': args.library,'profile': args.profile,
               'runner': Path(__file__),'rules': ROOT/'tools/sfld_rl/player_ideas.py',
               'adapter': ROOT/'tools/sfld_rl/env.py','trainer': ROOT/'tools/train_rl.py',
               'network': ROOT/'tools/sfld_rl/ppo.py','priority': ROOT/'tools/sfld_rl/stone_priority.py'}
    plan = {'schema':'sfld-player-ideas-v1','seed':str(args.seed),'namespace':args.namespace,
            'events_per_cell':args.events,'budgets':args.budgets,'cases':[CASES[c].record() for c in args.cases],
            'hashes':{k:train_rl.sha(p) for k,p in sources.items()},'n_envs':min(args.n_envs,args.events),
            'device':str(device),'torch':str(torch.__version__),'numpy':str(np.__version__),
            'allow_engine_transfer':args.allow_engine_transfer,
            'notes':['Seven days, full starting health, random offered stones, gross weekly mushroom spending cap.',
                     'Same corrected mechanics in every arm; frozen weights transferred explicitly, not retrained.',
                     'Player rules use visible state and configured damage bounds only; no RNG or hidden rooms.',
                     '21% is a floor for ordinary recovery: preserve larger learned waits and paid heals.',
                     'Boss buffer applies only to recovery in rooms97-99: boss maximum + up to two ordinary hit maxima +0.1pp, capped100%.',
                     'Pendant ordering only overrides menus containing Pendant; other stone menus retain learned choices.',
                     'Paired event seeds align starting RNG, not necessarily later encounters after decisions diverge.',
                     '99% intervals describe Monte Carlo uncertainty for each comparison, not model correctness or universal optimality.']}
    write_once(args.output/'plan.json',plan)
    for key in ('library','profile','runner','rules','adapter'):
        source = sources[key]; dest = args.output/source.name
        if dest.exists() and train_rl.sha(dest) != plan['hashes'][key]: raise RuntimeError('Snapshot differs: '+key)
        if not dest.exists(): shutil.copyfile(source,dest)
    args.library, args.profile = args.output/sources['library'].name, args.output/sources['profile'].name
    cells = {}
    for budget in args.budgets:
        for name in args.cases:
            path = args.output/f'cap-{budget}-{name}.json'
            if path.exists():
                cell = json.loads(path.read_text(encoding='utf8'))
                if cell['plan'] != plan: raise RuntimeError('Cached provenance differs')
                validate(cell['outcomes'],args.events,budget,name)
                if cell['summary'] != summarize(cell['outcomes']): raise RuntimeError('Cached summary differs')
            else:
                cell = evaluate(args,saved,model,device,budget,CASES[name])
                cell['plan'] = plan
                write_once(path,cell)
            cells[budget,name] = cell
            print(json.dumps({'budget':budget,'case':name,'completions':cell['summary']['completions']['mean'],
                              'mushrooms':cell['summary']['mushrooms']['mean'],'seconds':cell['wall_seconds']}),flush=True)
    comparisons = [{'budget':budget,'case':name,'deltas':paired(cells[budget,'control']['outcomes'],cell['outcomes'])}
                   for (budget,name),cell in cells.items() if name!='control' and (budget,'control') in cells]
    result = {'plan':plan,'summaries':[c['summary'] for c in cells.values()],'comparisons':comparisons}
    write_once(args.output/'summary.json',result)
    for key,path in sources.items():
        if train_rl.sha(path) != plan['hashes'][key]: raise RuntimeError('Source changed during evaluation: '+key)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint',type=Path,default=ROOT/'docs/pendant-adaptation-2026-10-11/control/selected-checkpoint.pt')
    parser.add_argument('--library',type=Path,default=ROOT/'build/Release/sfld_rl.dll')
    parser.add_argument('--profile',type=Path,default=ROOT/'profiles/synthetic.profile')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--events',type=int,default=512)
    parser.add_argument('--n-envs',type=int,default=256)
    parser.add_argument('--budgets',type=int,nargs='+',default=[0,20,50,100,500,1000])
    parser.add_argument('--cases',nargs='+',choices=list(CASES),default=list(CASES))
    parser.add_argument('--seed',type=int,default=11069001)
    parser.add_argument('--namespace',type=int,required=True)
    parser.add_argument('--device',default='cuda')
    parser.add_argument('--allow-engine-transfer',action='store_true')
    args = parser.parse_args()
    if args.events<2 or args.n_envs<1 or not 0<=args.namespace<=255: parser.error('Invalid cohort size or namespace')
    run(args)


if __name__ == '__main__': main()
