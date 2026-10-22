"""Paired seven-day test of the deployed policy with daily 00:00-08:00 sleep.

No policy selection or training uses these events. All arms start playing at
17:00 local time, keep the same budget, and run on the same native episode seeds.
"""
import argparse
import json
from pathlib import Path
from statistics import mean, stdev, NormalDist
import time
import numpy as np
import torch
import train_rl
from benchmark_player_ideas import write_once, transfer_changes
from train_continuation_study import audit_seeds, status
from train_current_study import copy
from sfld_rl.env import VectorEnv
from sfld_rl.interaction_policy import InteractionPolicy
from sfld_rl.player_ideas import profile_values
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = [0, 20, 50, 100, 500, 1000]
METRICS = ('completions', 'mushrooms', 'recovery_mushrooms', 'reroll_mushrooms',
           'deaths', 'legendaries', 'elapsed_hours', 'actions', 'invalid_actions', 'first_completion_hours')


def interval(values, z=1.959963984540054):
    m = mean(values)
    se = stdev(values) / len(values)**.5 if len(values) > 1 else 0
    return dict(mean=m, standard_error=se, ci=[m-z*se, m+z*se])


def parity(model, profile, old_library, new_library):
    """Exact observation/mask/metric/reward parity while sleep is disabled."""
    checks = []
    for budget in (0, 100, 1000):
        args = dict(profiles=[profile], n_envs=16, budgets=[budget], seed=2026101560,
                    seed_namespace=220, auto_reset=False)
        with VectorEnv(old_library, **args) as old, VectorEnv(new_library, event_start_hour=17, **args) as new:
            a, am = old.reset(); b, bm = new.reset()
            rule = InteractionPolicy(new, profile_values(profile), 'base')
            seen = set(); decisions = 0
            while len(seen) < 16:
                if not np.array_equal(a, b) or not np.array_equal(am, bm):
                    raise RuntimeError('Disabled schedule changed native observations or masks')
                proposed = train_rl.policy_actions(model, b, bm, 'cuda', greedy=True)[0].cpu().numpy()
                actions, targets, _ = rule.apply(b, bm, proposed)
                if np.any(targets >= 0): raise RuntimeError('Unexpected custom wait')
                left = old.step(actions); right = new.step(actions)
                for i in range(2, 5):
                    if not np.array_equal(left[i], right[i]): raise RuntimeError('Disabled schedule changed transitions')
                for key in METRICS:
                    if not np.array_equal(left[5][key], right[5][key]):
                        raise RuntimeError('Disabled schedule changed metric: ' + key)
                a, am = left[:2]; b, bm = right[:2]; decisions += 16
                ended = np.flatnonzero(right[3] | right[4])
                seen.update(int(right[5]['episode_index'][slot]) for slot in ended
                            if right[5]['episode_index'][slot] < 16)
                if len(ended):
                    a, am = old.reset_done(ended); b, bm = new.reset_done(ended)
            checks.append(dict(budget=budget, original_events=16, compared_decisions=decisions, exact=True))
    return checks


def evaluate(out, plan, model, saved, budget, wake):
    label = 'continuous' if wake == 0 else 'sleep_00_08'
    dest = out / f'{label}-{budget}.json'
    if dest.exists(): raise FileExistsError(dest)
    setup = plan['cohort']; n = min(512, setup['events']); rows = {}; started = time.monotonic()
    with VectorEnv(out/'sfld_rl.dll', [out/'synthetic.profile'], n, budgets=[budget],
                   seed=setup['seed'], seed_namespace=setup['namespace'], auto_reset=False,
                   event_start_hour=17, sleep_until_hour=wake) as env:
        # Deliberate transfer to an availability restriction; policy/schema unchanged.
        transfer_changes(saved['metadata']['environment_signature'], env.metadata, True)
        rule = InteractionPolicy(env, profile_values(out/'synthetic.profile'), 'base')
        rule.count_before_episode = setup['events']
        obs, masks = env.reset(); elapsed = np.zeros(n); decision_count = 0
        minimum_awake_hour = 24.0
        while len(rows) < setup['events']:
            if wake:
                clock = (17 + elapsed) % 24
                if np.any(clock < wake - 1e-9): raise RuntimeError('Decision during sleep')
                minimum_awake_hour = min(minimum_awake_hour, float(clock.min()))
            actions = train_rl.policy_actions(model, obs, masks, 'cuda', greedy=True)[0].cpu().numpy()
            actions, targets, _ = rule.apply(obs, masks, actions)
            if np.any(targets >= 0): raise RuntimeError('Unexpected custom wait')
            decision_count += int((env.episode_indices < setup['events']).sum())
            obs, masks, _, done, trunc, info = env.step(actions)
            elapsed = info['elapsed_hours'].copy()
            ended = np.flatnonzero(done | trunc)
            if len(ended):
                totals = env.reward_totals()
                for slot in ended:
                    index = int(info['episode_index'][slot])
                    if index >= setup['events']: continue
                    if index in rows: raise RuntimeError('Duplicate week')
                    row = dict(event=index, **{key: float(info[key][slot]) for key in METRICS})
                    row.update({k: float(v[slot]) for k, v in totals.items()})
                    if (trunc[slot] or not done[slot] or row['invalid_actions'] or row['mushrooms'] > budget or
                        row['mushrooms'] != row['recovery_mushrooms'] + row['reroll_mushrooms'] or
                        not 167.99 <= row['elapsed_hours'] <= 168.000001):
                        raise RuntimeError('Invalid week')
                    rows[index] = row
                obs, masks = env.reset_done(ended); elapsed[ended] = 0
    ordered = [rows[i] for i in range(setup['events'])]
    first = [r['first_completion_hours'] for r in ordered if r['first_completion_hours'] >= 0]
    result = dict(arm=label, budget=budget, cohort=setup, rows=ordered, environment=env.metadata,
        corrections=rule.counts, decisions=decision_count, minimum_decision_local_hour=minimum_awake_hour if wake else None,
        summary={k: interval([r[k] for r in ordered]) for k in ordered[0] if k not in ('event','first_completion_hours')},
        first_completion_fraction=len(first)/len(ordered), conditional_first_hours=interval(first) if first else None,
        seconds=time.monotonic()-started)
    write_once(dest,result)
    print(json.dumps(dict(cell=dest.name, completions=result['summary']['completions'],
                         first_hours=result['conditional_first_hours'], seconds=result['seconds'])),flush=True)
    return result


def run(out, events):
    if events < 2: raise ValueError('At least two events required')
    if out.exists(): raise FileExistsError(out)
    if train_rl.sha(CHECKPOINT) != CHECKPOINT_SHA: raise RuntimeError('Deployed checkpoint changed')
    audit = audit_seeds([2026101560, 2026101561])
    torch.set_num_threads(2)
    saved, model = train_rl.load_checkpoint(CHECKPOINT, torch.device('cuda')); model.eval()
    out.mkdir(parents=True)
    sources = [Path(__file__), ROOT/'CMakeLists.txt', ROOT/'tools/train_rl.py', ROOT/'tools/benchmark_player_ideas.py']
    sources += sorted((ROOT/'include/sfld').glob('*.hpp')) + sorted((ROOT/'src').glob('*.cpp'))
    sources += sorted((ROOT/'tools/sfld_rl').glob('*.py'))
    copy(ROOT/'build-v0612/Release/sfld_rl.dll',out/'sfld_rl.dll')
    copy(ROOT/'profiles/synthetic.profile',out/'synthetic.profile')
    plan = dict(schema='sfld-daily-schedule-v1', engine='0.6.12', budgets=BUDGETS,
        cohort=dict(seed=2026101561, namespace=221, events=events),
        parity=dict(seed=2026101560, namespace=220, events_per_budget=16),
        checkpoint=str(CHECKPOINT),checkpoint_sha256=CHECKPOINT_SHA, deadline_hours=168,
        event_start_hour=17,sleep_hours=[0,8], policy='Deployed checkpoint plus InteractionPolicy(base), including hungry-chest guard',
        comparison='No selection or optimization. Same episode seeds in both arms and every budget.',
        sources={p.relative_to(ROOT).as_posix():train_rl.sha(p) for p in sources},
        library_sha256=train_rl.sha(out/'sfld_rl.dll'),profile_sha256=train_rl.sha(out/'synthetic.profile'),
        intervals='Pointwise normal 95% mean CIs; six paired completion contrasts also get simultaneous Bonferroni 95% CIs.',
        limits='Monte Carlo uncertainty only, conditional on simulator mechanics. Policy has not been trained for sleep. '
               'Fixed 24-hour local days, no DST transition or thinking time. Healing only during recovery. '
               'This seven-day comparison is not the announced September 2026 event duration (220 hours).')
    write_once(out/'plan.json',plan);write_once(out/'seed-audit.json',audit)
    for source in sources: copy(source,out/'source'/source.relative_to(ROOT))
    with torch.inference_mode():
        status(out,'checking continuous-engine parity')
        write_once(out/'parity.json',parity(model,out/'synthetic.profile',ROOT/'build-v0611/Release/sfld_rl.dll',out/'sfld_rl.dll'))
        cells={}
        for budget in BUDGETS:
            for wake in (0,8):
                status(out,'comparing',budget=budget,sleep_until_hour=wake)
                cells[budget,wake]=evaluate(out,plan,model,saved,budget,wake)
    contrasts=[]; z=NormalDist().inv_cdf(1-.05/(2*len(BUDGETS)))
    for budget in BUDGETS:
        old,new=cells[budget,0]['rows'],cells[budget,8]['rows']
        if [r['event'] for r in old] != [r['event'] for r in new]: raise RuntimeError('Unpaired events')
        difference=[y['completions']-x['completions'] for x,y in zip(old,new)]
        contrasts.append(dict(budget=budget, sleep_minus_continuous=interval(difference),
                              simultaneous_95=interval(difference,z)))
    for path, sha in plan['sources'].items():
        if train_rl.sha(ROOT/path) != sha: raise RuntimeError('Frozen source changed: '+path)
    write_once(out/'summary.json',dict(contrasts=contrasts,cells=[{k:v for k,v in c.items() if k!='rows'} for c in cells.values()]))
    status(out,'complete',events_per_cell=events,total_events=events*len(cells))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--events',type=int,default=4096)
    args=parser.parse_args();run(args.output.resolve(),args.events)
