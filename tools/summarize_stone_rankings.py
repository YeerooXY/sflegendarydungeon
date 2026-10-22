"""Describe frozen final-stage results and the deployed actor's opening preferences.

No training, gameplay edits, new reward rollouts or global-optimality claims.
"""
from collections import defaultdict
from itertools import combinations, permutations, product
from pathlib import Path
import csv
import hashlib
import json
import statistics

import numpy as np

from sfld_rl.effect_lab import EffectLab
from sfld_rl.env import VectorEnv
from sfld_rl.player_ideas import profile_values
from sfld_rl.visual_policy import CHECKPOINT, CHECKPOINT_SHA, VisualPolicy

ROOT = Path(__file__).resolve().parents[1]
STUDY = ROOT / 'docs/final-stones-five-2026-10-15'
PROFILE = ROOT / 'profiles/synthetic.profile'
LIBRARY = ROOT / 'build-adaptive-stones/Release/sfld_rl.dll'
NAMES = dict(rabbit='Soul of the Rabbit', greasy='Greasy Healing Stone',
    moonstone='Cursed Moonstone', pearl='Cursed Pearl', gambler='Boulder of the Gambler',
    hero='Treasure of the Hero', time_traveler='Diamond of the Time Traveler',
    greed='Boulder of Greed', deceit='Pebble of Deceit', spying='Spying Gem',
    thirsty='Hope of the Thirsty One', blood='Blood Drop of Sacrifice',
    misadventurer='Sapphire of the Misadventurer', explorer='Emerald of the Explorer',
    pendant='Pendant of the Key Master', bull='Eye of the Bull', lodestone='Lodestone',
    hick='Erratic Boulder of the Hick', devil='Crown Jewel of the Devil', rusty='Rusty Healing Stone')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def final_ranks():
    verification = json.loads((STUDY / 'verification.json').read_text())
    assert sha(STUDY / 'rankings.json') == verification['rankings_sha256']
    data = list(csv.DictReader((STUDY / 'rankings.csv').open(newline='')))
    assert len(data) == verification['candidate_additions'] == 18900
    # Cross-check CSV mean times/ranks against the verified JSON, not just a row count.
    canonical = json.loads((STUDY / 'rankings.json').read_text())
    lookup = {}
    for row in canonical['rows']:
        for entry in row['rankings']:
            lookup[(row['run'], tuple(sorted(row['held'])), row['keys'], entry['stone'])] = entry
    assert len(lookup) == len(data)
    groups = defaultdict(list)
    for row in data:
        identity = (int(row['run']), tuple(sorted((row['held_1'], row['held_2']))), int(row['keys']), row['stone'])
        reference = lookup[identity]
        assert int(row['rank']) == reference['rank']
        assert abs(float(row['mean_hours']) - reference['mean_hours']) < 1e-10
        groups[(identity[0], row['stone'])].append(row)
    result = {}
    for run in (1, 2):
        entries = []
        for (which, stone), rows in groups.items():
            if which != run:
                continue
            assert len(rows) == (180 if run == 1 else 855)
            entries.append(dict(stone=stone, name=NAMES[stone], situations=len(rows),
                mean_rank=statistics.mean(int(r['rank']) for r in rows),
                estimated_firsts=sum(int(r['rank']) == 1 for r in rows),
                mean_rank_by_keys={str(k):statistics.mean(int(r['rank']) for r in rows if int(r['keys']) == k)
                                   for k in (0, 1, 3, 5, 10)}))
        entries.sort(key=lambda r: (r['mean_rank'], r['stone']))
        for rank, entry in enumerate(entries, 1):
            entry['overall_rank'] = rank
        assert abs(statistics.mean(r['mean_rank'] for r in entries) - (4.5 if run == 1 else 9.5)) < 1e-10
        result[str(run)] = entries
    return result


def opening_preferences():
    actor = VisualPolicy()
    features = {name:i for i,name in enumerate(actor.names)}
    gem_actions = [actor.actions.index('gem_' + str(i)) for i in range(3)]
    clear = [i for name,i in features.items() if name.startswith(('gem_offers.', 'offered_gem.'))]
    profile = profile_values(PROFILE)
    result = {}
    for run in (1, 2):
        pool = profile['gems_first_run' if run == 1 else 'gems_later_runs'].split(',')
        menus = np.asarray([order for triple in combinations(range(len(pool)), 3) for order in permutations(triple)])
        totals = np.zeros(len(pool)); counts = np.zeros(len(pool), dtype=np.int64)
        chosen = np.zeros(len(pool), dtype=np.int64); appearances = np.zeros(len(pool), dtype=np.int64)
        records = []
        for budget in (0, 20, 100, 1000):
            with VectorEnv(LIBRARY, [PROFILE], 1, budgets=[budget], auto_reset=False,
                           deadline_hours=168, event_start_hour=17) as env:
                assert env.observation_names == actor.names and env.action_names == actor.actions
                env.reset(); lab = EffectLab(env)
                for hp, keys in product((25, 50, 75), (0, 3, 10)):
                    progress = f'schema=1\nroom=26\nrun_number={run}\nphase=gems\nhp_percent={hp}\nkeys={keys}\ngem_offers={",".join(pool[:3])}\n'
                    if run == 1:
                        progress += 'shops_seen=1\n'
                    with lab.fixture(progress, budget) as snapshot:
                        lab.restore(0, snapshot, 0)
                    obs, masks = lab.observe(); visible = lab.visible()
                    assert visible['gems'] == [] and masks[0, gem_actions].all()
                    blank = obs[0].copy(); blank[clear] = 0
                    state_totals = np.zeros(len(pool)); state_counts = np.zeros(len(pool), dtype=np.int64)
                    for start in range(0, len(menus), 512):
                        chunk = menus[start:start+512]
                        batch = np.tile(blank, (len(chunk), 1))
                        for position in range(3):
                            batch[np.arange(len(chunk)), [features[f'gem_offers.{position}.{pool[g]}'] for g in chunk[:,position]]] = 1
                            batch[np.arange(len(chunk)), [features[f'offered_gem.{pool[g]}'] for g in chunk[:,position]]] = 1
                        with actor.torch.inference_mode():
                            logits = actor.model(actor.torch.as_tensor(batch))[0][:, gem_actions].numpy()
                        winners = chunk[np.arange(len(chunk)), logits.argmax(axis=1)]
                        np.add.at(chosen, winners, 1)
                        np.add.at(appearances, chunk.ravel(), 1)
                        for i in range(3):
                            for j in range(3):
                                if i == j:
                                    continue
                                preference = 1 / (1 + np.exp(-np.clip(logits[:,i] - logits[:,j], -60, 60)))
                                np.add.at(state_totals, chunk[:,i], preference)
                                np.add.at(state_counts, chunk[:,i], 1)
                    assert len(set(state_counts)) == 1
                    totals += state_totals; counts += state_counts
                    records.append(dict(hp=hp, keys=keys, budget=budget,
                        preference_points={stone:float(100*state_totals[i]/state_counts[i]) for i,stone in enumerate(pool)}))
        assert len(records) == 36 and len(set(appearances)) == 1
        assert chosen.sum() == 36 * len(menus)
        points = 100 * totals / counts
        assert abs(float(points.mean()) - 50) < 1e-4
        entries = [dict(stone=stone, name=NAMES[stone], preference_points=float(points[i]),
                        chosen_when_offered=float(chosen[i]/appearances[i]),
                        offered_menus=int(appearances[i])) for i,stone in enumerate(pool)]
        entries.sort(key=lambda r:(-r['preference_points'],r['stone']))
        for rank, entry in enumerate(entries,1):
            entry['overall_rank'] = rank
        result[str(run)] = dict(rankings=entries, states=records, evaluated_menus=36*len(menus))
        print('Opening pool', run, [(r['stone'], round(r['preference_points'],1)) for r in entries], flush=True)
    return result


def main():
    final = final_ranks()
    early = opening_preferences()
    result = dict(date='2026-10-16', scope='Descriptive pooled rankings, not universal strategy or proven stone values',
        final_stage=dict(method='Equal-weight mean ordinal rank in the verified held-pair/key grid',
            hp=1, budget=5, room=76, keys=[0,1,3,5,10], effects='none',
            trajectories=6374400, rankings=final),
        opening=dict(method='Deployed actor balanced pairwise logistic preference; every three-offer combination in every slot permutation',
            room=26, hp=[25,50,75], keys=[0,3,10], budgets=[0,20,100,1000],
            effects='none', held='none', elapsed_hours=0, previous_spend=0,
            horizon_hours=168, sleep=False, start_hour=17,
            notice='Engineered positions equally weighted. Preference points are not measured completion gains or win probabilities.',
            runs=early),
        hashes=dict(checkpoint=CHECKPOINT_SHA, library=sha(LIBRARY), profile=sha(PROFILE),
                    rankings_json=sha(STUDY/'rankings.json'), script=sha(Path(__file__))))
    output = ROOT / 'docs/stone-rankings-2026-10-16.json'
    output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    text = ['# Current stone rankings, 16 October 2026', '',
        'These are two different summaries. The first-pick column describes what the current trained policy prefers in balanced diagnostic positions. The final-pick column summarizes measured remaining clear time, averaging each stone\'s rank across eligible held pairs and key levels. Neither is a universal stone order.', '',
        'Final-stage results: 6,374,400 frozen continuations, room 76, 1% HP, five remaining mushrooms, no temporary effects, keys 0/1/3/5/10 equally weighted. Each later-run stone is evaluated in 855 pair/key states; each first-run stone in 180. The average position is among 18 possible additions on later runs and eight on the first run. Prior histories are averaged as documented in the original study.', '',
        'First-pick preferences: room 26, no held stones or temporary effects, HP 25/50/75%, keys 0/3/10 and budgets 0/20/100/1000. All 36 conditions have equal weight. All three-stone menus and all six slot orders are evaluated. Elapsed time and earlier spending are zero, with 168 hours remaining; this is a standardized probe, not the distribution of actual play. Preference points use the same pairwise-logit method as the interface and are not probabilities of winning or estimates of extra clears.', '']
    for run in ('2','1'):
        text += ['## '+('Later runs' if run=='2' else 'First run of an event'), '',
                 '| Final-pick rank | Stone | Mean final-choice position | First-pick preference rank | Preference points |',
                 '|---:|---|---:|---:|---:|']
        lookup = {r['stone']:r for r in early[run]['rankings']}
        for row in final[run]:
            pref = lookup[row['stone']]
            text.append(f"| {row['overall_rank']} | {row['name']} | {row['mean_rank']:.2f} | {pref['overall_rank']} | {pref['preference_points']:.1f} |")
        text.append('')
    text += ['## Reading the results', '',
        'A rank difference can reflect health, keys, partners, policy behavior and remaining time. A low final-pick position does not establish that a stone is a poor first pick. The aggregate order does not have a new confidence guarantee; the original study retains its within-state simultaneous intervals, and the live final-stone adviser reports uncertainty for the actual three offers.', '',
        'No priorities, policy weights or game mechanics were changed. The exact final choice still uses fresh rollouts, so it can override this pooled order.', '',
        '[Machine-readable results](stone-rankings-2026-10-16.json), [original study](final-stones-five-2026-10-15/report.md), [interactive conditional rankings](final-stone-guide-2026-10-15/index.html).', '']
    (ROOT/'docs/STONE_RANKINGS_2026_10_16.md').write_text('\n'.join(text),encoding='utf-8')
    print('Saved',output,flush=True)


if __name__ == '__main__':
    main()
