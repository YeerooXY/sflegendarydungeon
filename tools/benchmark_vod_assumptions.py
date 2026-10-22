"""Measure sensitivity of VOD-informed working assumptions using the C++ engine."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from benchmark_strategies import setting


def mixture(text, key):
    line = re.search(rf'^{re.escape(key)}=(.+)$', text, re.M)
    if line is None:
        raise ValueError(f'Missing {key}')
    return {k: float(w) for k, w in (pair.split(':') for pair in line[1].split(','))}


def monster_share(text, key, probability):
    weights = mixture(text, key)
    other = sum(v for k, v in weights.items() if k != 'monster')
    if other <= 0:
        raise ValueError('Need nonmonster outcomes')
    result = {k: v*(1-probability)/other for k, v in weights.items() if k != 'monster'}
    result['monster'] = probability
    return setting(text, key, ','.join(f'{k}:{v:.12g}' for k, v in result.items()))


def blessing_share(weights, target):
    total = sum(weights)
    current = sum(weights[1::2])/total
    result = list(weights)
    for i in range(0, 8, 2):
        if target > current:
            moved = weights[i]*(target-current)/(1-current)
        else:
            moved = -weights[i+1]*(current-target)/current
        result[i] -= moved
        result[i+1] += moved
    return result


def variants(text):
    result = {'base': text}
    for key, low, high in [('trap_chance', .21, .38), ('barrel_blessing', .35, .65),
                           ('combat_curse_chance', .05, .20), ('shop_strong_effect', .35, .65)]:
        for value in (low, high):
            result[f'{key}-{value:g}'] = setting(text, key, str(value))
    for value in (.4, .8):
        result[f'other-mystery-monsters-{value:g}'] = monster_share(text, 'mystery_without_monster', value)
    result['locked-monsters-0.2'] = monster_share(text, 'locked', .2)
    weights = [float(v) for v in re.search(r'^container_joint=(.+)$', text, re.M)[1].split(',')]
    for value in (.15, .45):
        changed = setting(text, 'container_joint', ','.join(f'{v:.12g}' for v in blessing_share(weights, value)))
        changed = setting(changed, 'container_joint_greed', ','.join(f'{v:.12g}' for v in blessing_share(weights, value+.2)))
        result[f'ordinary-blessing-{value:g}'] = changed
    result['greed-no-loot-uplift'] = setting(text, 'container_joint_greed', ','.join(map(str, weights)))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--exe', type=Path, default=Path('build/Release/sfld.exe'))
    p.add_argument('--profile', type=Path, default=Path('profiles/synthetic.profile'))
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--runs', type=int, default=3000)
    p.add_argument('--seed', type=int, default=2026100819)
    p.add_argument('--threads', type=int, default=3)
    a = p.parse_args()
    if a.runs < 1 or not 1 <= a.threads <= 256 or not 0 <= a.seed < 2**64:
        p.error('Invalid runs, threads or seed')
    exe = a.exe.resolve(strict=True)
    target = a.output_dir.resolve()
    target.mkdir(parents=True, exist_ok=False)
    (target/'profiles').mkdir(); (target/'results').mkdir()
    original = a.profile.read_text(encoding='utf-8')
    report = {'schema': 1, 'engine': subprocess.check_output([str(exe), '--version'], text=True).strip(),
              'executable_sha256': hashlib.sha256(exe.read_bytes()).hexdigest(),
              'strategy_sha256': hashlib.sha256((Path(__file__).resolve().parents[1]/'src/strategy.cpp').read_bytes()).hexdigest(),
              'seed': a.seed, 'runs_per_case': a.runs, 'cases': [], 'profiles': {},
              'interpretation': 'One assumption at a time, using the key-stockpile policy and its updated planner. Bounds are exploratory scenarios, not joint confidence bounds or a fitted posterior. Paired event seeds do not guarantee paired actions. Gross mushrooms per completion includes free recovery.'}
    for name, text in variants(original).items():
        identity = re.search(r'^id=(.+)$', text, re.M)[1]
        text = setting(text, 'id', identity+'-sensitivity-'+name)
        path = Path('profiles')/(name+'.profile')
        (target/path).write_text(text, encoding='utf-8', newline='\n')
        report['profiles'][name] = {'text': text, 'sha256': hashlib.sha256(text.encode()).hexdigest()}
        for budget in (0, 500):
            output = Path('results')/f'{name}-{budget}.json'
            args = ['event','--allow-assumptions','--profile',path.as_posix(),'--strategy','key-stockpile',
                    '--budget',str(budget),'--deadline-hours','168','--runs',str(a.runs),'--seed',str(a.seed),
                    '--threads',str(a.threads),'--barrels','adaptive','--shop-policy','adaptive','--rerolls','0',
                    '--output',output.as_posix()]
            subprocess.run([str(exe), *args], cwd=target, check=True, capture_output=True, text=True)
            result = json.loads((target/output).read_text(encoding='utf-8'))
            if not result['valid'] or result['action_limit_events'] or result['events'] != a.runs:
                raise RuntimeError('Invalid experiment '+name)
            report['cases'].append({'scenario': name, 'budget': budget, 'arguments': args, 'report': result})
            (target/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
            print(f"{name}, budget{budget}: {result['mean_completions']:.4f}", flush=True)


if __name__ == '__main__':
    main()
