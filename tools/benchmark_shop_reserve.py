"""Predeclared reserve/elixir screening, frozen selection, untouched final cohort."""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes, interval
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.shop_reserve import CASES, ShopReserve
from sfld_rl.visual_policy import CHECKPOINT

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0, 20, 50, 100, 500, 1000]


def evaluate(out, plan, model, saved, cohort, case, budget):
    setup = plan[cohort]
    dest = out / f'{cohort}-{case.name}-{budget}.json'
    identity = {'cohort': setup, 'case': case.record(), 'budget': budget, 'hashes': plan['hashes']}
    if dest.exists():
        result = json.loads(dest.read_text())
        if result['identity'] != identity: raise RuntimeError('Changed cached experiment')
        return result
    start = time.monotonic(); rows = {}
    with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], min(512, setup['events']),
                   budgets=[budget], seed=setup['seed'], seed_namespace=setup['namespace'],
                   deadline_hours=168, max_actions=100000, auto_reset=False) as env:
        changes = transfer_changes(saved['metadata']['environment_signature'], env.metadata, True)
        write_once(out/'transfer.json', {'changes': changes, 'reason': 'Frozen actor evaluated on corrected opening, shop and Traveler-barrel mechanics; no claim of retraining.'})
        rule = ShopReserve(env.observation_names, env.action_names, case, profile_values(out/'synthetic.profile'))
        obs, masks = env.reset()
        count_names = ['reserve_overrides', 'elixir_overrides', 'shop_visits', 'shop_keys', 'shop_with_four',
                       'potion_1_buys', 'potion_3_buys']
        counts = {n: np.zeros(env.n_envs) for n in count_names}
        was_shop = np.zeros(env.n_envs, bool)
        with torch.inference_mode():
            while len(rows) < setup['events']:
                proposed = train_rl.policy_actions(model, obs, masks, 'cuda', greedy=True)[0].cpu().numpy()
                actions, targets, why = rule.apply(obs, masks, proposed)
                shop = rule.flag(obs, 'phase.shop') & (rule.v(obs, 'room_div100') > .05 + 1e-6)
                entry = shop & ~was_shop
                keys = np.rint(rule.v(obs, 'keys_div20') * 20)
                counts['reserve_overrides'] += why[:, 1]; counts['elixir_overrides'] += why[:, 2]
                counts['shop_visits'] += entry; counts['shop_keys'] += entry * keys
                counts['shop_with_four'] += entry & (keys >= 4)
                for side in range(2):
                    selected = shop & (actions == rule.ai[f'buy_{side}']) & rule.flag(obs, 'owned_gem.time_traveler') & rule.flag(obs, f'offers.{side}.effect.kind.elixir')
                    price = np.rint(rule.v(obs, f'offers.{side}.keys_div6') * 6)
                    for cost in (1, 3): counts[f'potion_{cost}_buys'] += selected & (price == cost)
                was_shop = shop.copy()
                obs, masks, _, done, truncated, info = env.step(actions)
                ended = np.flatnonzero(done | truncated)
                for slot in ended:
                    index = int(info['episode_index'][slot])
                    if index >= setup['events']: continue
                    if index in rows: raise RuntimeError('Duplicate event')
                    row = {'event': index}
                    for name in ('completions','mushrooms','recovery_mushrooms','reroll_mushrooms','deaths','legendaries','elapsed_hours','invalid_actions'):
                        row[name] = float(info[name][slot])
                    row.update({n: float(a[slot]) for n, a in counts.items()})
                    if (truncated[slot] or not done[slot] or row['invalid_actions'] or row['mushrooms'] > budget
                        or row['mushrooms'] != row['recovery_mushrooms'] + row['reroll_mushrooms']
                        or not 167.999 <= row['elapsed_hours'] <= 168.000001):
                        raise RuntimeError('Invalid event accounting')
                    rows[index] = row
                if len(ended):
                    obs, masks = env.reset_done(ended)
                    for a in counts.values(): a[ended] = 0
                    was_shop[ended] = False
    ordered = [rows[i] for i in range(setup['events'])]
    result = {'identity': identity, 'rows': ordered, 'seconds': time.monotonic()-start,
              'means': {n: statistics.mean(r[n] for r in ordered) for n in ordered[0] if n != 'event'}}
    write_once(dest, result)
    print(json.dumps({'cohort': cohort, 'case': case.name, 'budget': budget,
                      'clears': result['means']['completions'], 'seconds': result['seconds']}), flush=True)
    return result


def run(out, smoke):
    out.mkdir(parents=True, exist_ok=True); torch.set_num_threads(2)
    sources = {'checkpoint': CHECKPOINT, 'library': ROOT/'build-v0610/Release/sfld_rl.dll',
               'profile': ROOT/'profiles/synthetic.profile', 'runner': Path(__file__),
               'rules': ROOT/'tools/sfld_rl/shop_reserve.py', 'practical': ROOT/'tools/sfld_rl/practical.py',
               'escape': ROOT/'tools/sfld_rl/escape_loot.py', 'env': ROOT/'tools/sfld_rl/env.py',
               'ppo': ROOT/'tools/sfld_rl/ppo.py', 'train': ROOT/'tools/train_rl.py'}
    plan = {'schema': 'sfld-shop-reserve-2026-10-12-v3', 'budgets': BUDGETS,
            'cases': [c.record() for c in CASES], 'hashes': {n: train_rl.sha(p) for n,p in sources.items()},
            'selection': {'seed': 12511001 if smoke else 12512001, 'namespace': 252, 'events': 2 if smoke else 512},
            'final': {'seed': 12511002 if smoke else 12513001, 'namespace': 253, 'events': 4 if smoke else 4096},
            'selection_rule': 'Freeze best reserve-only and best reserve+elixir by equal-budget mean. Final also tests elixir-only and control. No final-result retuning.',
            'promotion_rule': 'Positive lower simultaneous 99% bound on equal-budget paired improvement; no individual budget with negative upper bound. Bonferroni over 21 contrasts (3 challengers x 7).',
            'reserve_rule': 'Start at one key, ramp to 1/2/3/4 over four rooms before public earliest shop; hold until seen. After shop and in later-run stage one keep 1 or 2 for next stage, zero after final shop. Free opening shop exempt. Replace selected key door by legal zero-key alternative even if risky; otherwise retain learned choice.',
            'elixir_rule': 'With Time Traveler, buy available one-key full heal when missing at least 20% HP; replace selected three-key elixir by legal reroll else skip. No hidden offers or free future rolls.',
            'scope': 'Same 168-hour simulator and random eligible stones; frozen policy. Paired starting seeds do not keep later encounters identical after actions diverge. No global optimality claim.'}
    plan['corrections'] = ['Special opening doors without traps', 'No later-run stage-one shop',
        'Traveler barrels can bless (relative curse multiplier provisional)',
        'Pendant preserves special physical door styles', 'All cases prevent strictly weaker One Hit Wonder shop replacement']
    historical = set()
    for p in (ROOT/'docs').rglob('*.json'):
        historical.update(map(int, re.findall(r'"(?:root_seed|seed)"\s*:\s*"?(\d+)', p.read_text(encoding='utf-8-sig'))))
    roots = [plan[k]['seed'] for k in ('selection','final')]
    fps = [train_rl.seed_fingerprint(r) for r in roots]
    if len(set(fps)) != 2 or set(fps) & {train_rl.seed_fingerprint(r) for r in historical}:
        raise RuntimeError('Seed reuse')
    write_once(out/'plan.json', plan)
    write_once(out/'seed-audit.json', {'roots': roots, 'fingerprints': fps, 'historical_overlap': False})
    for name, path in sources.items():
        dest = out/({'library':'sfld_rl.dll','profile':'synthetic.profile','checkpoint':'checkpoint.pt'}.get(name, 'source/'+path.relative_to(ROOT).as_posix()))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists(): shutil.copyfile(path, dest)
        if train_rl.sha(dest) != plan['hashes'][name]: raise RuntimeError('Changed snapshot')
    saved, model = train_rl.load_checkpoint(out/'checkpoint.pt', torch.device('cuda')); model.eval()
    cells = {(c.name,b): evaluate(out,plan,model,saved,'selection',c,b) for c in CASES for b in BUDGETS}
    score = {c.name: statistics.mean(cells[c.name,b]['means']['completions'] for b in BUDGETS) for c in CASES}
    selected = [max((c for c in CASES if c.reserve and c.cheap_elixir==e), key=lambda c:score[c.name]) for e in (False,True)]
    finalists = CASES[:2] + selected
    write_once(out/'frozen-selection.json', {'scores': score, 'finalists': [c.record() for c in finalists]})
    finals = {(c.name,b): evaluate(out,plan,model,saved,'final',c,b) for c in finalists for b in BUDGETS}
    z = statistics.NormalDist().inv_cdf(1-.01/(2*21)); comparisons = {}
    for c in finalists[1:]:
        deltas = [[r['completions']-a['completions'] for r,a in zip(finals[c.name,b]['rows'],finals['control',b]['rows'])] for b in BUDGETS]
        estimates = {}
        for name, values in list(zip(map(str,BUDGETS), deltas))+[('pooled',[statistics.mean(v) for v in zip(*deltas)])]:
            m = statistics.mean(values); se = statistics.stdev(values)/math.sqrt(len(values))
            estimates[name] = {'gain':m, 'family_99_ci':[m-z*se,m+z*se]}
        comparisons[c.name] = {'estimates': estimates, 'passes': estimates['pooled']['family_99_ci'][0]>0 and all(estimates[str(b)]['family_99_ci'][1]>=0 for b in BUDGETS)}
    write_once(out/'results.json', {'comparisons': comparisons, 'final_means': {c.name:{str(b):finals[c.name,b]['means'] for b in BUDGETS} for c in finalists}})
    for n,p in sources.items():
        if train_rl.sha(p) != plan['hashes'][n]: raise RuntimeError('Experiment source changed during execution: '+n)
    print(json.dumps({'complete': True, 'comparisons': comparisons}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--output', type=Path, required=True); parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args(); run(args.output.resolve(), args.smoke)
