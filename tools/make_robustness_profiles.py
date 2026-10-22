"""Write one-at-a-time low/high variants of the shipped profile's uncertain values."""
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def scale_damage(text, factor):
    pairs=[p.split(':') for p in text.split(',')]
    return ','.join(f'{float(a)*factor:.4f}:{float(b)*factor:.4f}' for a,b in pairs)


def rescale_monster(text, share):
    """Set the monster weight to a share of the total, keeping other outcomes' mix."""
    items=[p.split(':') for p in text.split(',')];total=sum(float(w) for _,w in items)
    other=total-float(dict(items)['monster'])
    return ','.join(f"{k}:{share*total if k=='monster' else float(w)/other*(1-share)*total:.6g}" for k,w in items)


def variants(values):
    damage=lambda f:{k:scale_damage(values[k],f) for k in ('monster_damage','escape_damage','boss_damage')}
    return {
        'keys-low':{'key_chance':'0.35'},'keys-high':{'key_chance':'0.85'},
        'damage-low':damage(.85),'damage-high':damage(1.15),
        'heal-cheap':{'full_heal_cost_per_fraction':'36'},'heal-dear':{'full_heal_cost_per_fraction':'60'},
        'mystery-calm':{'mystery_without_monster':rescale_monster(values['mystery_without_monster'],.6)},
        'mystery-fierce':{'mystery_without_monster':rescale_monster(values['mystery_without_monster'],.9)},
        'destiny-calm':{'destiny':rescale_monster(values['destiny'],.2)},
        'destiny-fierce':{'destiny':rescale_monster(values['destiny'],.6)},
        'destiny-no-curse':{'destiny_rewards':'1,1,1,1,1,0'},'destiny-curses':{'destiny_rewards':'1,1,1,1,1,3'},
        'combat-curse-low':{'combat_curse_chance':'0.05'},'combat-curse-high':{'combat_curse_chance':'0.20'},
        'barrel-low':{'barrel_blessing':'0.35'},'barrel-high':{'barrel_blessing':'0.65'},
        'shop-weak':{'shop_strong_effect':'0.35'},'shop-strong':{'shop_strong_effect':'0.65'},
        'traps-low':{'trap_chance':'0.21'},'traps-high':{'trap_chance':'0.38'},
        'skeleton-low':{'skeleton_wakes':'0.25'},'skeleton-high':{'skeleton_wakes':'0.75'},
        'hidden-low':{'hidden_monster_chance':'0.10'},'hidden-high':{'hidden_monster_chance':'0.30'},
    }


def main(output):
    lines=(ROOT/'profiles/synthetic.profile').read_text(encoding='utf-8').splitlines()
    values=dict(l.split('=',1) for l in lines if '=' in l and not l.startswith('#'))
    output.mkdir(parents=True,exist_ok=True)
    for name,changes in variants(values).items():
        missing=set(changes)-set(values)
        if missing:raise KeyError(f'{name}: {missing}')
        body=[f'{l.split("=",1)[0]}={changes[l.split("=",1)[0]]}' if '=' in l and not l.startswith('#') and l.split('=',1)[0] in changes
              else f'id=robustness-2026-10-22-{name}' if l.startswith('id=') else l for l in lines]
        text=f'# Robustness variant {name}: {", ".join(changes)} changed from profiles/synthetic.profile.\n'+'\n'.join(body)+'\n'
        (output/f'{name}.profile').write_text(text,encoding='utf-8')
    print(len(variants(values)),'profiles written to',output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);main(p.parse_args().output)
