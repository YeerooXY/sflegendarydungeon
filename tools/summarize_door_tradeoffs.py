"""Print the trap and pre-boss tables from benchmark_door_tradeoffs shards."""
import argparse
import json
from pathlib import Path


def load(directory, study):
    rows = []
    for path in sorted(Path(directory).glob(f'{study}-budget-*/results.json')):
        rows += json.loads(path.read_text(encoding='utf-8'))['rows']
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__);p.add_argument('directory', type=Path)
    a = p.parse_args()
    traps = load(a.directory, 'traps');hps = sorted({r['hp'] for r in traps})
    for budget in sorted({r['budget'] for r in traps}):
        for trapped in ('mystery', 'monster', 'destiny'):
            for other in ('sacrifice', 'cursed'):
                cells = [r for r in traps if r['budget'] == budget and r['trapped'] == trapped and r['other'] == other]
                print(f'\nbudget {budget}: {trapped}+trap minus {other} (clears/week; * CI excludes 0; ! adviser picks the worse door)')
                print('room ' + ''.join(f'{h:>9}%' for h in hps))
                for room in sorted({r['room'] for r in cells}):
                    line = [r for r in cells if r['room'] == room]
                    print(f'{room:>4} ' + ''.join(f"{r['trapped_minus_other']:+8.3f}{'*' if r['verdict'] != 'tie' else ' '}"
                                                  f"{'!' if not r['adviser_agrees'] else ' '}" for r in line))
        wrong = [r for r in traps if r['budget'] == budget and not r['adviser_agrees']]
        print(f'\nbudget {budget}: adviser picks a significantly worse door in {len(wrong)} of '
              f'{sum(r["budget"] == budget for r in traps)} trap positions')
        for r in sorted(wrong, key=lambda r: -abs(r['trapped_minus_other']))[:10]:
            print(f"   {r['name']}: picked {r['adviser_pick']}, trapped-other {r['trapped_minus_other']:+.3f}")
    pre = load(a.directory, 'preboss')
    for budget in sorted({r['budget'] for r in pre}):
        print(f'\nbudget {budget}: clears/week from a door screen (rooms before each boss); +10% HP gain in brackets')
        print('room ' + ''.join(f'{h:>13}%' for h in hps))
        for room in sorted({r['room'] for r in pre if r['budget'] == budget}):
            line = sorted((r for r in pre if r['budget'] == budget and r['room'] == room), key=lambda r: r['hp'])
            gains = [None] + [b['clears'] - a['clears'] for a, b in zip(line, line[1:])]
            print(f'{room:>4} ' + ''.join(f"{r['clears']:7.3f}" + (f"({g:+.2f})" if g is not None else '       ')
                                         for r, g in zip(line, gains)))


if __name__ == '__main__':
    main()
