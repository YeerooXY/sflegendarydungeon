"""Visible-state explanations and narrowly scoped practice-adviser corrections.

These are UI/runtime rules, separate from the frozen policy's benchmark results.
They never inspect hidden room contents or consume the simulator's RNG.
"""
import struct

HUNGRY = {'wood', 'stone', 'souls', 'metal', 'arcane', 'hourglasses'}
EPS = 1e-9


def active(o, kind):
    return next((e for group in ('blessings', 'curses') for e in o[group]
                 if e['kind'] == kind and e['remaining'] > 0 and e['starts_at'] <= o['turn']), None)


def escape_chance(o, temporary=True):
    gems = o['gems']
    value = o['scenario']['escape_chance'] + .4 * ('rabbit' in gems) + .2 * ('moonstone' in gems) - .3 * ('bull' in gems)
    if temporary:
        value += (active(o, 'escape') or {}).get('magnitude', 0)
        value -= (active(o, 'clumsy') or {}).get('magnitude', 0)
    return min(1., max(0., value))


def recovery_potential(o, e):
    """Nominal healing over remaining room ticks, excluding bosses and full HP caps."""
    if not e or e['kind'] != 'recovery':
        return 0.
    start = o['room'] + max(0, e['starts_at'] - o['turn'])
    ticks = sum(room % 25 != 0 for room in range(start, min(101, start + e['remaining'])))
    return ticks * e['magnitude'] * (1.4 if 'rusty' in o['gems'] else 1)


def trial_route(o):
    """Check the remaining arena fights and prize exit using public effect clocks.

    A guaranteed escape is not a guarantee against poison or Pearl curses.
    Recovery and poison are evaluated in their native order on every exit.
    """
    remaining = max(0, 5 - o.get('trial_depth', 0))
    result = {'safe': False, 'remaining': remaining, 'reason': ''}
    if 'pearl' in o['gems']:
        result['reason'] = 'Cursed Pearl can add a curse after an escape.'
        return result

    def future(kind, offset):
        for group in ('blessings', 'curses'):
            for e in o[group]:
                if e['kind'] != kind or e['remaining'] <= 0:
                    continue
                starts = max(0, e['starts_at'] - o['turn'])
                if offset < starts:
                    continue
                if e['clock'] == 'room' and offset - starts >= e['remaining']:
                    continue
                return e['magnitude']
        return 0.

    permanent = o['scenario']['escape_chance'] + .4 * ('rabbit' in o['gems']) + .2 * ('moonstone' in o['gems']) - .3 * ('bull' in o['gems'])
    hp = o['hp']
    for offset in range(remaining + 1):
        if offset < remaining and permanent + future('escape', offset) - future('clumsy', offset) < 1 - EPS:
            result['reason'] = 'Escape is not guaranteed for every remaining arena fight.'
            return result
        if (o['room'] + offset) % 25:
            hp = min(1., hp + future('recovery', offset) * (1.4 if 'rusty' in o['gems'] else 1)) - future('poison', offset)
        if hp <= EPS:
            result['reason'] = 'Poison can kill you before the tier-five reward is collected.'
            return result
    result.update(safe=True, hp_after=hp,
        reason=f'Guaranteed escape through all {remaining} remaining arena fights; the reward exit is survivable.')
    return result


def shop_offer(o, offer, index):
    e = dict(offer['effect'])
    e['starts_at'] = o['turn'] + (0 if e['kind'] == 'recovery' else 1)
    if e['kind'] != 'elixir':
        e['remaining'] += int('time_traveler' in o['gems'])
    held = [b for b in o['blessings'] if b['remaining'] > 0]
    current = next((b for b in held if b['kind'] == e['kind']), None)
    displaced = None if e['kind'] == 'elixir' else current or (held[0] if len(held) >= 3 else None)
    affordable = any(a['kind'] == 'buy' and a['index'] == index for a in o['legal_actions'])
    old_remaining = max(0, current['remaining'] - int(current['clock'] == 'room' and current['starts_at'] <= o['turn'])) if current else 0
    result = {'index': index, 'acquired': e, 'current': current, 'displaced': displaced,
              'extra_uses': e['remaining'] - old_remaining, 'protect_reroll': False,
              'status': 'new', 'immediate_healing_lost': 0.}
    kind = e['kind']
    # Recovery includes this shop's exit heal. Compare the complete remaining
    # ticks, so refreshing two rooms to three buys just one extra tick.
    if displaced and displaced['kind'] == 'recovery' and e['kind'] != 'recovery' and displaced['starts_at'] <= o['turn']:
        result['immediate_healing_lost'] = min(1 - o['hp'], displaced['magnitude'] * (1.4 if 'rusty' in o['gems'] else 1))
    if kind == 'recovery':
        old = recovery_potential(o, current)
        new = recovery_potential(o, e)
        result.update(healing_before=old, healing_after=new, extra_healing=new-old)
        result['extra_uses'] = e['remaining'] - (current['remaining'] if current else 0)
        if new <= old + EPS:
            result['status'] = 'no_gain'
        elif current and offer['keys'] > 0 and new - old <= .1 * (1.4 if 'rusty' in o['gems'] else 1) + EPS:
            result['status'] = 'small_refresh'
        else:
            result['protect_reroll'] = new - old >= .2 - EPS or offer['keys'] == 0
        # Fresh paired follow-up supports saving the last key in this narrow
        # stage-three state. A refresh with one tick left still adds two ticks;
        # it is not generally a downgrade or a reason to skip at low health.
        other_affordable = any(a['kind'] == 'buy' and a['index'] != index for a in o['legal_actions'])
        if (current and current['remaining'] == 1 and current['starts_at'] <= o['turn']
                and abs(current['magnitude'] - .1) < EPS and abs(e['magnitude'] - .1) < EPS
                and e['remaining'] == 3 and offer['keys'] == o['keys'] == 1
                and 51 <= o['room'] <= 74 and min(1.,o['hp'] + .1) >= 1 - EPS
                and escape_chance(o,temporary=False) >= 1 - EPS
                and not any(c['remaining'] > 0 for c in o['curses']) and not other_affordable):
            result['status'] = 'save_last_key'
            result['protect_reroll'] = False
    elif kind == 'elixir':
        full = 'time_traveler' in o['gems'] and o['scenario'].get('time_traveler_elixir_full_heal', False)
        result['extra_healing'] = min(1-o['hp'], (1 if full else e['magnitude']) * (1.4 if 'rusty' in o['gems'] else 1))
        if result['extra_healing'] <= EPS:
            result['status'] = 'no_gain'
    elif current and current['magnitude'] >= e['magnitude'] and old_remaining >= e['remaining']:
        result['status'] = 'no_gain'
    else:
        result['protect_reroll'] = kind == 'one_hit' or (kind == 'lockpick' and result['extra_uses'] >= 4 and o['hp'] >= .4)
        if kind == 'escape':
            # Clumsy may still make Escape useful even with a permanent pair.
            without = dict(o)
            without['blessings'] = [b for b in held if b['kind'] != 'escape']
            result['protect_reroll'] = escape_chance(without) < 1 - EPS and result['extra_uses'] > 0
            if escape_chance(without) >= 1 - EPS:
                result['status'] = 'no_gain'
    # Never advertise a new buff as a free extra slot: the oldest is replaced.
    if displaced and displaced['kind'] != kind:
        result['protect_reroll'] = False
        result['status'] = 'replacement'
    # All purchases finish the room; new Recovery heals before this exit's poison.
    poison = (active(o, 'poison') or {}).get('magnitude', 0)
    recovery = active(o, 'recovery')
    retained_heal = 0 if not recovery or displaced == recovery else recovery['magnitude'] * (1.4 if 'rusty' in o['gems'] else 1)
    if kind == 'recovery':
        retained_heal = e['magnitude'] * (1.4 if 'rusty' in o['gems'] else 1)
    potion_heal = result.get('extra_healing', 0) if kind == 'elixir' else 0
    if min(1, o['hp'] + retained_heal + potion_heal) <= poison + EPS:
        result['status'] = 'lethal_exit'
        result['protect_reroll'] = False
    result['protect_reroll'] = result['protect_reroll'] and affordable and result['status'] not in ('no_gain', 'small_refresh', 'save_last_key', 'lethal_exit')
    return result


def annotate(o):
    o['combat_advice'] = {'escape_chance': escape_chance(o),
                          'clumsy_penalty': (active(o, 'clumsy') or {}).get('magnitude', 0),
                          'poison_tick': (active(o, 'poison') or {}).get('magnitude', 0),
                          'one_hit': active(o, 'one_hit') is not None}
    if o['phase'] == 'shop':
        o['offer_analysis'] = [shop_offer(o, offer, i) for i, offer in enumerate(o['offers'])]
    if ((o['phase'] == 'doors' and any(d['kind'] in ('trial', 'exit_trial') for d in o.get('doors', [])))
            or (o['phase'] == 'encounter' and o.get('encounter') == 'trial_monster')):
        o['trial_advice'] = trial_route(o)


def correct_action(o, proposed):
    """Return action, explanation, rule id. Only choose legal alternatives."""
    if 'combat_advice' not in o:
        annotate(o)
    if o['phase'] == 'encounter' and o.get('encounter') == 'mimic' and active(o, 'one_hit'):
        fight = next((a for a in o['legal_actions'] if a['kind'] == 'fight'), None)
        if fight and proposed != fight:
            return fight, 'Take the mimic reward.', 'one_hit_mimic'
    if o['phase'] == 'encounter' and o.get('encounter') == 'mimic' and proposed['kind'] == 'skip':
        heal = active(o, 'recovery')
        hit = o.get('combat_forecast', {}).get('fight', {})
        poison = (active(o, 'poison') or {}).get('magnitude', 0)
        healing = (heal or {}).get('magnitude', 0) * (1.4 if 'rusty' in o['gems'] else 1)
        worst = hit.get('max_damage', 1)
        # Recovery cannot rescue a lethal hit. At high HP, spend healing that
        # would otherwise be capped to collect an epic. Keep a boss buffer and
        # do not force this trade against stages 3/4's harmful combat curses.
        harmless_battle_curse = o['room'] <= 25 or (o['room'] <= 50 and o['scenario'].get('floor_specific_curses', False))
        after = min(1., o['hp'] - worst + healing) - poison
        boss_buffer = o.get('boss_damage_max', 1) if o['room'] % 25 == 24 else 0
        covered = heal and recovery_potential(o, heal) >= hit.get('mean_damage', 1) - EPS
        safe = o['hp'] >= .8 and o['hp'] > worst + EPS and after > max(EPS, boss_buffer)
        if covered and safe and harmless_battle_curse and not active(o, 'poison'):
            fight = next((a for a in o['legal_actions'] if a['kind'] == 'fight'), None)
            if fight:
                return fight, 'Use your remaining Recovery to collect the mimic reward.', 'recovery_mimic'
    if o['phase'] == 'doors' and proposed['kind'] == 'choose_door' and o.get('door_forecast'):
        views = {v['index']: v for v in o['door_forecast']}
        original = views[proposed['index']]
        heal = active(o, 'recovery')
        healing = (heal or {}).get('magnitude', 0) * (1.4 if 'rusty' in o['gems'] else 1)
        poison = (active(o, 'poison') or {}).get('magnitude', 0)
        escape_safe = escape_chance(o) >= 1 - EPS and 'pearl' not in o['gems']
        def loss(v):
            combat = 0 if active(o, 'one_hit') or escape_safe else min(v['monster_fight']['mean_damage'], v['monster_flee']['mean_damage'])
            return v['entry_damage'] + v['monster_chance'] * combat
        for a in o['legal_actions']:
            if a['kind'] != 'choose_door' or a == proposed:
                continue
            d, v = o['doors'][a['index']], views[a['index']]
            plain = d['kind'] in ('monster', 'mystery', 'locked', 'double_locked', 'epic', 'unlocked')
            if not plain or v['entry_curse_chance'] > 0 or d['key_cost'] > o['doors'][proposed['index']]['key_cost']:
                continue
            hp = o['hp'] - v['entry_damage']
            exit_safe = hp > EPS and min(1., hp + healing) > poison + EPS
            # With a fatal poison tick, a non-healing room is already lost.
            # Spend a free Lockpick on an ordinary loot room for a chance to
            # find Elixir/Recovery before that tick. Do not redirect through
            # an entry hazard, away from a special room, or from an OHW kill.
            if (poison > 0 and not exit_safe and hp > EPS and active(o, 'lockpick')
                    and not active(o, 'one_hit') and d['key_cost'] == 0
                    and d['kind'] in ('locked', 'double_locked') and v['entry_damage'] <= EPS
                    and o['doors'][proposed['index']]['kind'] in ('monster', 'mystery', 'unlocked')
                    and v['monster_chance'] + EPS < original['monster_chance']):
                return a, 'Use the free door for a chance to heal before Poison.', 'lockpick_poison_rescue'
            if not exit_safe:
                continue
            # A visible monster excludes a living monster in its paired mystery.
            # A nonlethal trap can therefore preserve a life that a failed
            # escape would lose. Optional contents can be skipped; do not roll
            # a cursed trap or spend extra keys to enforce this correction.
            if (d['kind'] == 'mystery' and d['trap'] and v['monster_chance'] <= EPS
                    and max(original['monster_fight']['survival'], original['monster_flee']['survival']) < 1 - EPS
                    and o['doors'][proposed['index']]['kind'] == 'monster'):
                return a, 'Survive this route and avoid the lethal combat risk.', 'survivable_noncombat_route'
            # A free, curse-free route preserves a currently certain escape or
            # One Hit Wonder. Do not block deliberate cycling of three curses.
            if (original['entry_curse_chance'] > 0 and (escape_safe or active(o, 'one_hit'))
                    and len([c for c in o['curses'] if c['remaining'] > 0]) < 3
                    and v['entry_damage'] <= original['entry_damage'] + .1 + EPS
                    and (v['entry_damage'] <= original['entry_damage'] or healing >= v['entry_damage'])):
                return a, 'Keep your combat protection and avoid the curse roll.', 'protect_combat_blessing'
            # Use a free lockpick while the final Recovery tick can still heal.
            # Compare only ordinary routes; optional rooms can be left safely.
            if (heal and heal['remaining'] == 1 and active(o, 'lockpick') and d['key_cost'] == 0
                    and d['kind'] in ('locked', 'double_locked', 'epic') and o['hp'] < 1 - healing / 2
                    and o['doors'][proposed['index']]['kind'] in ('monster', 'mystery', 'unlocked', 'cursed', 'destiny')
                    and loss(v) + EPS < loss(original) and v['monster_chance'] < original['monster_chance']):
                return a, 'Use Lockpick and keep the last Recovery tick for healing.', 'lockpick_last_recovery'
    # Preserve Rabbit + Moonstone's permanent escape instead of voluntarily
    # drawing a curse. A normal trap is a known 10% entry cost in the engine;
    # healing only happens afterward. Never force a lethal or cursed trap.
    if (o['phase'] == 'doors' and proposed['kind'] == 'choose_door'
            and o['doors'][proposed['index']]['kind'] == 'cursed'
            and {'rabbit', 'moonstone'}.issubset(o['gems']) and 'pearl' not in o['gems']
            and escape_chance(o) >= 1 - EPS):
        disarmed = active(o, 'disarm') is not None
        heal = (active(o, 'recovery') or {}).get('magnitude', 0) * (1.4 if 'rusty' in o['gems'] else 1)
        poison = (active(o, 'poison') or {}).get('magnitude', 0)
        for action in o['legal_actions']:
            if action['kind'] != 'choose_door' or action == proposed:
                continue
            door = o['doors'][action['index']]
            if door['kind'] not in ('monster', 'mystery', 'unlocked') or door.get('key_cost', 0):
                continue
            trapped = door['trap'] and o['room'] > 5
            if trapped and not disarmed and (door['cursed_trap'] or 'gambler' in o['gems']):
                continue
            damage = .1 * (.8 if 'masochist' in o['gems'] else 1) if trapped and not disarmed else 0
            hp = o['hp'] - damage
            if hp > EPS and min(1, hp + heal) > poison + EPS:
                return action, 'Keep guaranteed escape and avoid a new curse.', 'preserve_escape_pair'
    # An earned prize has the same exit ticks whether opened or skipped.
    if o['phase'] == 'encounter' and o.get('encounter') == 'prize':
        collect = next((a for a in o['legal_actions'] if a['kind'] == 'interact'), None)
        if collect and proposed != collect:
            return collect, 'Collect the arena reward you have already earned.', 'arena_prize'
    trial = o.get('trial_advice')
    if trial and trial['safe']:
        if o['phase'] == 'doors':
            target = 'trial' if trial['remaining'] else 'exit_trial'
            enter = next((a for a in o['legal_actions'] if a['kind'] == 'choose_door'
                          and o['doors'][a['index']]['kind'] == target
                          and not o['doors'][a['index']]['trap']
                          and not o['doors'][a['index']]['cursed_trap']), None)
            if enter and enter != proposed:
                return enter, trial['reason'], 'safe_arena'
        elif o.get('encounter') == 'trial_monster' and not (proposed['kind'] == 'fight' and active(o, 'one_hit')):
            flee = next((a for a in o['legal_actions'] if a['kind'] == 'flee'), None)
            if flee and flee != proposed:
                return flee, trial['reason'], 'safe_arena'
    # The resource was paid on entering the hungry door. Collecting its sated
    # chest has no additional cost, combat, curse or effect replacement. Both
    # interact and skip finish the room with the same healing/poison tick.
    if o['phase'] == 'encounter' and o.get('encounter') == 'sated_chest' and proposed['kind'] == 'skip':
        collect = next((a for a in o['legal_actions'] if a['kind'] == 'interact'), None)
        if collect:
            return collect, 'The hungry door is already paid. Open its chest for the resource reward; leaving has the same room-exit effects.', 'hungry_chest_reward'
    # The held-out interaction study confirmed this narrow reroll correction.
    # Match the float32 HP feature used by the tested batched policy.
    policy_hp = struct.unpack('f', struct.pack('f', o['hp']))[0]
    if (o['phase'] == 'shop' and proposed['kind'] == 'reroll' and policy_hp <= .5
            and 'time_traveler' in o['gems'] and o['scenario'].get('time_traveler_elixir_full_heal', False)):
        for a in o['legal_actions']:
            if a['kind'] != 'buy':
                continue
            offer = o['offers'][a['index']]
            if offer['effect']['kind'] == 'elixir' and offer['keys'] <= 1:
                cost = 'one key' if offer['keys'] == 1 else 'no keys'
                return a, f'Time Traveler makes this a full heal for {cost}. Take it before spending another mushroom on new goods.', 'shop_traveler_elixir'
    if o['phase'] == 'doors' and proposed['kind'] == 'choose_door' and any(d['kind'] in HUNGRY for d in o['doors']):
        # Escape covers combat, not traps or poison. Do not infer safety from
        # an Escape icon alone, or promise safety on an unknown trapped route.
        safe_combat = escape_chance(o) >= 1 - EPS and 'pearl' not in o['gems']
        poison = (active(o, 'poison') or {}).get('magnitude', 0)
        if safe_combat and poison <= EPS:
            for action in o['legal_actions']:
                if action['kind'] != 'choose_door':
                    continue
                door = o['doors'][action['index']]
                if door['kind'] in ('mystery', 'monster', 'unlocked') and door['key_cost'] == 0 and not door['trap'] and not door['cursed_trap']:
                    return action, '100% escape · no trap or poison. Free room: possible useful loot. Hungry door: resources only.', 'safe_free_door' if action != proposed else None
    if o['phase'] == 'shop' and proposed['kind'] == 'buy':
        value = o['offer_analysis'][proposed['index']]
        status = value['status']
        if status in ('small_refresh', 'save_last_key', 'no_gain', 'lethal_exit'):
            # Prefer another worthwhile offered blessing before leaving. Keep
            # mushroom spending with the learned adviser; this guard never
            # invents a refresh purchase or turns a key saving into a reroll.
            other = next((a for a in o['legal_actions'] if a['kind'] == 'buy' and o['offer_analysis'][a['index']]['protect_reroll']), None)
            poison = (active(o, 'poison') or {}).get('magnitude', 0)
            recovery = (active(o, 'recovery') or {}).get('magnitude', 0) * (1.4 if 'rusty' in o['gems'] else 1)
            safe_skip = min(1, o['hp'] + recovery) > poison + EPS
            alternative = other or next((a for a in o['legal_actions'] if a['kind'] == 'skip' and safe_skip), None)
            if alternative:
                reasons = {'small_refresh': f"This paid Recovery refresh adds only {value.get('extra_healing', 0) * 100:g}% potential healing over what you already have.",
                           'save_last_key': 'Your last Recovery tick already fills your health. With permanent 100% escape, save your last key instead of buying two extra weak healing ticks.',
                           'no_gain': 'This blessing adds no benefit over your current effects.',
                           'lethal_exit': 'This purchase would leave you with lethal poison damage on leaving the shop.'}
                return alternative, reasons[status], 'shop_' + status
    return proposed, '', None
