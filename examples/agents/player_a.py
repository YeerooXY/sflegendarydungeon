"""Independent visible-state rule policy for the synthetic LD challenge."""

def _kind(value):
    if isinstance(value, str):
        return value
    if 'effect' in value:
        return _kind(value['effect'])
    return value.get('kind', value.get('type', value.get('id', value.get('name', ''))))


def _effects(observation, category):
    return {_kind(effect): effect for effect in observation.get(category, [])}


def _strength(effect):
    if not isinstance(effect, dict):
        return False
    if 'effect' in effect:
        effect = effect['effect']
    if 'strong' in effect:
        return bool(effect['strong'])
    kind = _kind(effect)
    if kind == 'elixir':
        return effect.get('magnitude', 0) > .25
    if kind == 'recovery':
        return effect.get('magnitude', 0) > .1
    weak_duration = {'one_hit': 4, 'escape': 5, 'disarm': 4,
                     'lockpick': 2, 'key_moment': 4, 'raider': 5}
    return effect.get('remaining', 0) > weak_duration.get(kind, 5)


def _remaining(effect):
    if not isinstance(effect, dict):
        return 1
    return effect.get('remaining', effect.get('charges', effect.get('rooms', 1)))


def choose(observation):
    """Return one action object supplied by the current observation."""
    legal = observation['legal_actions']
    choices = {}
    for action in legal:
        choices.setdefault(_kind(action), []).append(action)
    first = lambda name: choices[name][0]
    hp = observation.get('hp', 1)
    room = observation.get('room', 1)
    stage = min(3, max(0, (room - 1) // 25))
    keys = observation.get('keys', 0)
    gems = {_kind(g) for g in observation.get('gems', [])}
    blessings = _effects(observation, 'blessings')
    curses = _effects(observation, 'curses')
    phase = observation.get('phase', '')
    encounter = observation.get('encounter', observation.get('room_kind', ''))
    encounter_kind = _kind(encounter)

    if 'restart' in choices:
        return first('restart')

    if phase == 'recovery' or 'reenter' in choices or ('wait' in choices and 'choose_door' not in choices):
        if 'heal_full' in choices and hp < 0.20 and 'greasy' not in gems:
            return first('heal_full')
        boss = room % 25 == 0
        target = (0.20, 0.40, 0.40, 0.60)[stage] if boss else 0.20
        if 'greasy' in gems and not boss:
            target = 0.20
        if 'heal_step' in choices and hp + 1e-8 < target and 'heal_full' not in choices:
            return first('heal_step')
        if 'reenter' in choices and hp + 1e-8 >= target:
            return first('reenter')
        if 'wait' in choices:
            need = max(0, target - hp) * 24
            waits = choices['wait']
            sufficient = [a for a in waits if a.get('value', 0) + 1e-6 >= need]
            return min(sufficient, key=lambda a: a.get('value', 0)) if sufficient else max(waits, key=lambda a: a.get('value', 0))
        if 'reenter' in choices:
            return first('reenter')

    # A refill is most efficient near empty; bosses require their own threshold.
    boss_fight = ('fight' in choices and room % 25 == 0)
    threshold = (0.1962, 0.2805, 0.2805, 0.5250)[stage] if boss_fight else 0.10
    if 'heal_full' in choices and hp <= threshold and phase not in ('shop', 'blessing_shop') and (boss_fight or 'greasy' not in gems):
        return first('heal_full')
    if 'heal_step' in choices and 'heal_full' not in choices and hp <= threshold:
        return first('heal_step')

    if 'choose_door' in choices:
        base = {
            'golden': 12, 'blessing': 13, 'shop': 9, 'unlocked': 10,
            'locked': 7.5, 'double_locked': 5, 'epic': 6,
            'wood': 10, 'stone': 10, 'souls': 10, 'metal': 10,
            'arcane': 10, 'hourglasses': 10, 'trial': 8,
            'mystery': 3, 'destiny': 1.5, 'cursed': -1,
            'sacrifice': -3, 'monster': 0, 'wall': -100,
        }
        def door_score(action):
            door = observation['doors'][action['index']]
            kind = _kind(door)
            value = base.get(kind, 2)
            if kind == 'shop':
                value += 3 if keys >= 2 or (observation.get('run_number', 1) == 1 and stage == 0) else -6
            if kind in ('locked', 'double_locked', 'epic'):
                value -= door.get('key_cost', 0) * (1.5 if keys <= 2 else 0.3)
            if kind == 'monster':
                if 'one_hit' in blessings:
                    value = 13 if keys < 3 else 8
                elif 'escape' in blessings or {'rabbit', 'moonstone'} <= gems:
                    value = 8
                elif 'rabbit' in gems:
                    value = 5
            if door.get('trap') and 'disarm' not in blessings:
                value -= 6
            if door.get('cursed_trap'):
                value -= 4
            if kind == 'sacrifice' and hp <= 0.12:
                value -= 100
            return value
        return max(choices['choose_door'], key=door_score)

    for name in ('choose_gem', 'choose_stone', 'take_gem'):
        if name in choices:
            offers = observation.get('gem_offers', observation.get('stone_offers', observation.get('offers', [])))
            ranks = {
                'rabbit': 100, 'greasy': 95, 'moonstone': 88,
                'gambler': 83, 'pearl': 65, 'greed': 64, 'pendant': 61,
                'spying': 58, 'time_traveler': 54, 'explorer': 50,
                'hero': 45, 'misadventurer': 41, 'deceit': 35,
                'lodestone': 30, 'thirsty': 27, 'blood': 21,
                'rusty': 18, 'devil': 15, 'hick': 5, 'bull': 0,
            }
            if 'rabbit' in gems:
                ranks['moonstone'] = 120
                ranks['pendant'] = 87
                ranks['pearl'] = 38
            if 'moonstone' in gems:
                ranks['rabbit'] = 120
            return max(choices[name], key=lambda a: ranks.get(_kind(offers[a['index']]), 10))

    if 'fight' in choices:
        if encounter_kind in ('mimic', 'trial') and 'skip' in choices:
            return first('skip')
        if boss_fight or 'flee' not in choices:
            return first('fight')
        if 'one_hit' in blessings:
            return first('fight')
        escape = 0.5 + 0.4 * ('rabbit' in gems) + 0.2 * ('moonstone' in gems) - 0.3 * ('bull' in gems)
        escape += 0.8 * ('escape' in blessings) - 0.8 * ('clumsy' in curses)
        escape = min(1.0, max(0.0, escape))
        fight_modifier = 1 + .25 * ('rabbit' in gems) + .25 * ('devil' in gems) - .2 * ('hero' in gems) - .2 * ('deceit' in gems) - .2 * ('bull' in gems)
        fail_modifier = 1 - .4 * ('pearl' in gems) + .3 * ('hick' in gems)
        if escape <= 0.1 or (keys == 0 and escape < 0.55 and fight_modifier < 0.65):
            return first('fight')
        if 'key_moment' in blessings and keys < 3 and escape < .7 and hp > (.18, .25, .28, .31)[stage] * fight_modifier:
            return first('fight')
        if (1 - escape) * fail_modifier > fight_modifier:
            return first('fight')
        return first('flee')

    if 'buy' in choices:
        offers = observation.get('shop_offers', observation.get('offers', []))
        if phase == 'curse_shop':
            def curse_score(action):
                offer = offers[action['index']]
                kind = _kind(offer)
                reward = offer.get('keys', 0)
                if kind == 'gold_hangover':
                    return 10 + reward
                if kind == 'hard_lock':
                    return 3 + reward - .3 * keys
                if kind == 'poison' and hp < .06 and 'recovery' not in blessings:
                    return reward
                return -1
            pick = max(choices['buy'], key=curse_score)
            if curse_score(pick) > 0:
                return pick
            if 'skip' in choices:
                return first('skip')
        def offer_score(action):
            offer = offers[action['index']]
            kind = _kind(offer)
            strong = _strength(offer)
            value = {
                'recovery': 20 if strong else 10,
                'elixir': min(1 - hp, .5 if strong else .25) * 35,
                'one_hit': 9 if strong else 5,
                'escape': 10 if strong else 6,
                'disarm': 5 if strong else 3,
                'lockpick': 7 if strong else 4,
                'key_moment': 4 if strong else 2,
                'raider': 0,
            }.get(kind, -10)
            if kind in blessings and kind not in ('elixir', 'recovery'):
                value *= .3
            if kind == 'recovery':
                value *= .65 + .35 * (1 - hp)
            if kind == 'escape' and {'rabbit', 'moonstone'} <= gems:
                value = 0
            cost = offer.get('keys', offer.get('key_cost', offer.get('price', offer.get('cost', 0))))
            if isinstance(cost, (int, float)):
                value -= cost * (2 if keys <= 2 else 1)
            return value
        pick = max(choices['buy'], key=offer_score)
        score = offer_score(pick)
        if 'reroll' in choices and observation.get('shop_rerolls', 0) < 2 and score < 5 and keys >= 1:
            return first('reroll')
        if score > 0:
            return pick
        if 'skip' in choices:
            return first('skip')

    # Skip optional damage and effect gambles with no progression advantage.
    bad_rooms = {'skeleton', 'mimic', 'sacrifice', 'sacrifice_chest', 'cursed',
                 'cursed_chest', 'trial', 'rps', 'spider_legs', 'spider_head',
                 'spider_full', 'wheel', 'locked_sarcophagus'}
    if encounter_kind in bad_rooms and 'skip' in choices:
        return first('skip')
    if encounter_kind == 'barrel' and 'skip' in choices:
        if 'time_traveler' in gems or ('gambler' not in gems and 'greed' not in gems and hp > .55):
            return first('skip')

    for name in ('reenter', 'interact', 'open', 'drink', 'take', 'leave', 'exit', 'continue', 'skip'):
        if name in choices:
            return first(name)
    raise RuntimeError('Unhandled legal action kinds: ' + ', '.join(choices))
