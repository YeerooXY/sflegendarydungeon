"""Declared player-reported interaction challenges; no prescribed action labels.

These states deliberately oversample difficult combinations. Their scores are
conditional diagnostics, never estimates of how often the situations occur.
"""
from .effect_lab import probe_cases


def state(room=60, hp=50, keys=4, gems='hero,rabbit', phase='doors', **fields):
    values = dict(schema=1, room=room, run_number=2, phase=phase,
                  hp_percent=hp, keys=keys, gems=gems or 'none')
    values.update(fields)
    return ''.join(f'{k}={str(v).lower() if isinstance(v, bool) else v}\n' for k, v in values.items())


def cases():
    result = [dict(c, family='effect_slots') for c in probe_cases()]
    def add(name, family, actions, **fields):
        result.append(dict(name=name, family=family, actions=actions, progress=state(**fields)))
    for hp in (21, 50, 85):
        for keys in (1, 4, 8):
            tag=f'h{hp}-k{keys}'
            for gems, blessing, label in (
                ('rabbit,moonstone', 'none', 'permanent'),
                ('hero,greed', 'escape:weak:5', 'temporary'),
                ('rabbit,moonstone', 'recovery:strong:5', 'escape_healing'),
                ('hero,greed', 'recovery:strong:5', 'healing')):
                for doors in ('monster,locked', 'monster,mystery:trap', 'monster,blessing', 'monster,wood'):
                    add(f'{label}-{doors}-{tag}', 'doors', ['door_0','door_1'], hp=hp, keys=keys,
                        gems=gems, blessings=blessing, doors=doors, resources='10,10,10,10,10,10')
                add(f'{label}-fight-{tag}', 'combat', ['fight','flee'], hp=hp, keys=keys,
                    gems=gems, blessings=blessing, phase='encounter', encounter='monster')
            for curse, blessing, label in (
                ('broken_armor:strong:8,clumsy:strong:10,poison:strong:10', 'none', 'poison_newest'),
                ('poison:strong:10,broken_armor:strong:8,clumsy:strong:10', 'none', 'poison_oldest'),
                ('poison:strong:10,broken_armor:strong:8,clumsy:strong:10', 'recovery:strong:5', 'healing_curses')):
                add(f'{label}-chest-{tag}', 'curse_replacement', ['interact','skip'], hp=hp, keys=keys,
                    curses=curse, blessings=blessing, phase='encounter', encounter='cursed_chest')
            for blessings, label in (('none','unprotected'),('one_hit:strong:7','one_hit'),('recovery:strong:5','healing')):
                add(f'trade-{label}-{tag}', 'curse_trade', ['buy_0','buy_1','skip'], hp=hp, keys=keys,
                    blessings=blessings, phase='curse_shop', offers='broken_armor:strong:8:5,clumsy:weak:5:1')
            for remaining in (2, 5):
                add(f'recovery-refresh-{remaining}-{tag}', 'shop', ['buy_0','buy_1','skip','reroll'],
                    hp=hp, keys=keys, blessings=f'recovery:strong:{remaining}', phase='shop',
                    offers='recovery:weak:3:1,one_hit:strong:8:4')
            add(f'traveler-cheap-elixir-{tag}', 'shop', ['buy_0','buy_1','skip','reroll'], hp=hp, keys=keys,
                gems='time_traveler,rabbit', phase='shop', offers='elixir:weak:1:1,one_hit:strong:8:4')
            add(f'traveler-strong-elixir-{tag}', 'shop', ['buy_0','buy_1','skip','reroll'], hp=hp, keys=keys,
                gems='time_traveler,rabbit', phase='shop', offers='elixir:strong:1:3,one_hit:strong:8:4')
            add(f'onehit-downgrade-{tag}', 'shop', ['buy_0','buy_1','skip','reroll'], hp=hp, keys=keys,
                blessings='one_hit:strong:8', phase='shop', offers='one_hit:weak:4:2,escape:weak:5:3')
        for room in (30, 34, 38, 60, 85):
            gems='hero' if room<51 else 'hero,rabbit' if room<76 else 'hero,rabbit,greed'
            for keys in (1, 2, 3, 4):
                add(f'preshop-r{room}-k{keys}-h{hp}', 'shop_reserve', ['door_0','door_1'],
                    room=room, hp=hp, keys=keys, gems=gems, doors='monster,locked')
        for depth in (0, 4):
            for gems,label in (('rabbit,moonstone','guaranteed'),('rabbit,greed','rabbit')):
                add(f'trial-{label}-d{depth}-h{hp}', 'trial', ['door_0','door_1'],
                    room=51+depth, hp=hp, gems=gems, trial_seen=bool(depth), trial_depth=depth,
                    doors='trial,exit_trial' if depth else 'trial,monster')
    for room in (35, 60, 100):
        for paid in (0, 1, 2):
            gems='greasy' if room<51 else 'greasy,hero' if room<76 else 'greasy,hero,greed'
            for hp in (0, 20, 40):
                extra=dict(resume_phase='encounter',encounter='boss') if room==100 else dict(resume_phase='doors',doors='unknown')
                add(f'revive-r{room}-paid{paid}-h{hp}', 'recovery',
                    ['heal_step','heal_full','wait_hp_20','wait_hp_40','wait_hp_60','wait_boss_safe','reenter'],
                    room=room, hp=hp, gems=gems, phase='recovery', paid_heals=paid, **extra)
    for hp in (21, 50, 85):
        add(f'opening-onehit-8-h{hp}', 'shop', ['buy_0','buy_1','skip'], room=5, hp=hp, keys=0,
            gems='', phase='shop', run_number=1, blessings='one_hit:strong:8', offers='unknown')
    return result
