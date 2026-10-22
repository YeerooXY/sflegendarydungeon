"""Last-day recovery buffer, using public state and configured damage bounds.

This is a tested runtime safeguard, not an optimal stopping proof. It applies
only to the final four rooms, continuous availability, and no affordable refill.
"""
from .visual_advice import EPS


def recovery_action(o, proposed, profile):
    if (o['phase'] != 'recovery' or not 97 <= o['room'] <= 100
            or o['hours_left'] > 24 or o.get('sleep_until_hour', 0)
            or proposed['kind'] not in ('wait', 'reenter')
            or any(a['kind'] in ('heal_step', 'heal_full') for a in o['legal_actions'])):
        return proposed, '', None
    rooms = 100 - o['room']
    seconds = float(profile.get('action_seconds', 2))
    # Leave time to enter, resolve encounters and fight. Waiting all the way
    # to the deadline is terminal, even when the recovered HP would suffice.
    reserve = (60 + (3 + 6 * rooms) * seconds) / 3600
    hours = o['scenario']['recovery_hours']
    available = min(1., o['hp'] + max(0., o['hours_left'] - reserve) / hours)
    if available < .2 - EPS:
        return proposed, '', None
    gems = o['gems']
    def future(kind, offset):
        if kind == 'recovery' and 'greasy' in gems:
            return .1  # all at most three ordinary rooms are covered on revive
        if kind == 'poison' and 'rusty' in gems:
            return .05
        e = next((e for group in ('blessings', 'curses') for e in o[group] if e['kind'] == kind), None)
        if not e: return 0.
        delay = max(0, e['starts_at'] - o['turn'])
        return e['magnitude'] if offset >= delay and (e['clock'] != 'room' or offset-delay < e['remaining']) else 0.
    need = o['boss_damage_max'] + .001
    fight_max = float(profile['monster_damage'].split(',')[3].split(':')[1])
    flee_max = float(profile['escape_damage'].split(',')[3].split(':')[1])
    for offset in reversed(range(rooms)):
        armor = future('broken_armor', offset)
        mult = 1 + armor + .25 * ('rabbit' in gems) + .25 * ('devil' in gems) - .2 * sum(g in gems for g in ('hero', 'bull', 'deceit'))
        flee_mult = (1 + armor if profile.get('fight_only_damage_stones', 'false') == 'true' else mult)
        flee_mult += -.4 * ('pearl' in gems) + .3 * ('hick' in gems)
        chance = o['scenario']['escape_chance'] + .4 * ('rabbit' in gems) + .2 * ('moonstone' in gems) - .3 * ('bull' in gems)
        chance += future('escape', offset) - future('clumsy', offset)
        hit = min(fight_max * max(0, mult), flee_max * max(0, flee_mult))
        if future('one_hit', offset) or chance >= 1-EPS: hit = 0.
        trap = 0 if future('disarm', offset) or 'gambler' in gems else .1 * (.8 if 'masochist' in gems else 1)
        damage = trap + hit
        healing = future('recovery', offset) * (1.4 if 'rusty' in gems else 1)
        poison = future('poison', offset)
        need = max(damage + .001, damage - healing + poison + need)
    target = min(available, max(.2, need))
    enter = next((a for a in o['legal_actions'] if a['kind'] == 'reenter'), None)
    if o['hp'] >= target-EPS:
        if not enter or proposed == enter: return proposed, '', None
        return enter, 'Use the remaining time for the final attempt.', 'late_recovery_buffer'
    wait = dict(kind='wait', index=0, value=(target-o['hp'])*hours)
    if proposed['kind'] == 'wait' and abs(proposed['value']-wait['value']) < EPS:
        return proposed, '', None
    return wait, f'Wait to {target*100:.1f}% HP for the final rooms; leave time to finish.', 'late_recovery_buffer'
