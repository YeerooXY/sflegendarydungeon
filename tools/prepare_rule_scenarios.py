"""Freeze the working profile and one-factor sensitivities for rule evaluation.

These are alternative assumptions, not confidence limits on live-game values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


def setting(text: str, key: str, value: str) -> str:
    result, count = re.subn(rf'^{re.escape(key)}=.*$', lambda _: f'{key}={value}', text, flags=re.MULTILINE)
    if count != 1:
        raise ValueError(f'Expected exactly one {key}')
    return result


def mystery_rate(text: str, monster_percent: float, key: str = 'mystery_without_monster') -> str:
    match = re.search(rf'^{re.escape(key)}=(.+)$', text, re.MULTILINE)
    if not match:
        raise ValueError('Missing fallback mystery contents')
    weights = dict((k, float(v)) for k, v in (x.split(':') for x in match[1].split(',')))
    remaining = sum(v for k, v in weights.items() if k != 'monster')
    if remaining <= 0:
        raise ValueError('No non-monster contents')
    return setting(text, key, ','.join(
        f'{k}:{(monster_percent if k == "monster" else v * (100-monster_percent)/remaining):.12g}'
        for k, v in weights.items()))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--profile', type=Path, default=Path('profiles/synthetic.profile'))
    p.add_argument('--alternate', type=Path, default=Path('profiles/mushroom-school-2024.profile'))
    args = p.parse_args()
    base = args.profile.read_text(encoding='utf-8')
    alternate = args.alternate.read_text(encoding='utf-8')
    sources = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (args.profile, args.alternate)}
    scenarios = [
        ('working', base, 'Current v3 working assumptions.'),
        ('alternate-damage', alternate, 'Historical Mushroom School damage table; other mechanics unchanged.'),
        ('mystery-60', mystery_rate(base, 60), 'Fallback mystery monsters 60%; non-monster mix rescaled.'),
        ('mystery-90', mystery_rate(base, 90), 'Fallback mystery monsters 90%; non-monster mix rescaled.'),
        ('trap-20', setting(base, 'trap_chance', '0.20'), 'Per-eligible-side trap input 20%.'),
        ('trap-35', setting(base, 'trap_chance', '0.35'), 'Per-eligible-side trap input 35%.'),
        ('traveler-ordinary-potion', setting(base, 'time_traveler_elixir_full_heal', 'false'),
         'Instant healing keeps its ordinary magnitude with Time Traveler.'),
        ('key-40', setting(base, 'key_chance', '0.40'), 'Base combat key chance 40%.'),
        ('key-60', setting(base, 'key_chance', '0.60'), 'Base combat key chance 60%.'),
        ('unlocked-monster-0', mystery_rate(base, 0, 'mystery'),
         'Unlocked base pool: no initial regular monsters. Non-monster relative mix unchanged; v3 mystery overrides remain.'),
        ('unlocked-monster-80', mystery_rate(base, 80, 'mystery'),
         'Unlocked base pool: 80% initial regular monsters. Non-monster relative mix unchanged; v3 mystery overrides remain.'),
    ]
    args.output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {'schema': 1, 'source_sha256': sources,
                'interpretation': 'One-factor model sensitivities; not measured bounds or new live-game evidence.',
                'scenarios': []}
    for name, text, description in scenarios:
        if name not in ('working', 'alternate-damage'):
            identity = re.search(r'^id=(.+)$', text, re.MULTILINE)[1]
            text = setting(text, 'id', identity + '-rules-' + name)
        target = args.output_dir / (name + '.profile')
        target.write_text(text, encoding='utf-8', newline='\n')
        manifest['scenarios'].append({'name': name, 'path': target.name, 'description': description,
                                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'profile_text': text})
    (args.output_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(f'Frozen {len(scenarios)} scenarios in {args.output_dir}')


if __name__ == '__main__':
    main()
