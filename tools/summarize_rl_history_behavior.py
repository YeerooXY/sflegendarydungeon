"""Compact descriptive audits; never use these counts to select a checkpoint."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path

import train_rl


def compact(data):
    if data['accounting']['illegal_actions'] or data['accounting']['truncations'] or not data['accounting']['episode_actions_match']:
        raise ValueError('Audit accounting failed')
    tables = data['tables']
    output = []
    for total in data['summaries']:
        budget = total['budget']
        def rows(name):
            return [r for r in tables[name] if r['budget'] == budget]
        shops = [r for r in rows('shop_choices') if r.get('phase') == 'shop' and not r.get('opening_shop')]
        barrel = [r for r in rows('special_policy') if r.get('encounter') == 'barrel' and r.get('time_traveler')]
        offered, picked, buys = Counter(), Counter(), Counter()
        for r in rows('gem_choices'):
            for gem in r['offered']:
                offered[gem] += r['decisions']
            picked[r['chosen']] += r['decisions']
        for r in rows('shop_buys'):
            if r.get('phase') == 'shop' and not r.get('opening_shop'):
                buys[r['kind'] + ':' + r['strength']] += r['decisions']
        opening = Counter()
        for r in rows('shop_buys'):
            if r.get('phase') == 'shop' and r.get('opening_shop'):
                opening[r['kind']] += r['decisions']
        ordinary_recovery = Counter()
        for r in rows('recovery_policy'):
            if not r.get('boss_room'):
                ordinary_recovery.update(r['actions'])
        output.append(dict(budget=budget, events=total['events'], decisions=total['decisions'],
            shop_rerolls=sum(r['actions'].get('reroll', 0) for r in shops),
            shop_rerolls_at_zero_keys=sum(r['actions'].get('reroll', 0) for r in shops if r['keys'] == 0),
            traveler_barrels={a: sum(r['actions'].get(a, 0) for r in barrel) for a in ('interact', 'skip')},
            stones={g: {'offered': offered[g], 'picked': picked[g]} for g in sorted(offered)},
            ordinary_shop_buys=dict(sorted(buys.items())), opening_shop_buys=dict(opening),
            ordinary_recovery_actions=dict(sorted(ordinary_recovery.items()))))
    return {'checkpoint': data['checkpoint'], 'cohort': data['cohort'], 'accounting': data['accounting'],
            'per_budget': output,
            'interpretation': 'Descriptive frequencies on each policy\'s own visited states; not causal action values or a validated rule table.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = compact(json.loads(args.input.read_text(encoding='utf-8')))
    result['raw_audit'] = {'path': str(args.input), 'sha256': train_rl.sha(args.input)}
    train_rl.write_json(args.output, result)
    print(json.dumps({'output': str(args.output), 'events': result['accounting']['completed_events']}))


if __name__ == '__main__':
    main()
