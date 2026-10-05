"""Independent rule-based player B; uses only the supplied observation."""

MONSTER = ((.1178, .1440), (.1683, .2057), (.1457, .2338), (.2104, .2571))
ESCAPE = ((.1178, .1440), (.1683, .2057), (.1457, .2338), (.1731, .2571))
BOSS = ((.1608, .1962), (.2295, .2805), (.2295, .2805), (.4303, .5250))


def _label(x):
    if isinstance(x, str):
        return x
    if isinstance(x, dict):
        return str(x.get('kind', x.get('type', x.get('id', x.get('name', '')))))
    return str(x)


def _effects(obs, category):
    return {_label(x): x for x in obs.get(category, [])}



def _first(actions, kind):
    return next((a for a in actions if _label(a) == kind), None)


def _combat(obs):
    gems = {_label(x) for x in obs.get('gems', [])}
    bless = _effects(obs, 'blessings')
    curses = _effects(obs, 'curses')
    stage = min(3, max(0, (obs.get('room', 1) - 1) // 25))
    p = .5 + .4 * ('rabbit' in gems) + .2 * ('moonstone' in gems) - .3 * ('bull' in gems)
    p += .8 * ('escape' in bless) - .8 * ('clumsy' in curses)
    p = min(1., max(0., p))
    fight_mult = 1 + .25 * ('rabbit' in gems) + .25 * ('devil' in gems)
    fight_mult -= .2 * ('hero' in gems) + .2 * ('deceit' in gems) + .2 * ('bull' in gems)
    fight_mult += .5 * ('broken_armor' in curses)
    escape_mult = 1 - .4 * ('pearl' in gems) + .3 * ('hick' in gems)
    escape_mult += .5 * ('broken_armor' in curses)
    fight_dmg = sum(MONSTER[stage]) / 2 * max(0, fight_mult)
    fight_max = MONSTER[stage][1] * max(0, fight_mult)
    if 'one_hit' in bless:
        fight_dmg = fight_max = 0
    flee_dmg = sum(ESCAPE[stage]) / 2 * max(0, escape_mult) * (1 - p)
    return gems, bless, curses, stage, p, fight_dmg, fight_max, flee_dmg


def choose(observation):
    obs = observation
    actions = obs['legal_actions']
    if not actions:
        raise ValueError('No legal action')
    kinds = {_label(a) for a in actions}
    hp = float(obs.get('hp', 1))
    room = int(obs.get('room', 1))
    phase = obs.get('phase', '')
    gems, bless, curses, stage, p, fight_dmg, fight_max, flee_dmg = _combat(obs)

    if 'restart' in kinds:
        return _first(actions, 'restart')

    if 'choose_gem' in kinds:
        scores = {'rabbit': 100, 'gambler': 90, 'greasy': 85, 'moonstone': 80,
                  'greed': 65, 'pearl': 60, 'spying': 55, 'time_traveler': 50,
                  'pendant': 36, 'misadventurer': 40, 'hero': 35, 'blood': 30,
                  'lodestone': 28, 'devil': 24, 'explorer': 18, 'thirsty': 33,
                  'deceit': 8, 'bull': 0, 'hick': -5, 'rusty': 15}
        if 'rabbit' in gems:
            scores['moonstone'] = 130
            scores['pendant'] = 78
            scores['pearl'] = 32
        if 'moonstone' in gems:
            scores['rabbit'] = 130
        if 'time_traveler' in gems:
            scores['gambler'] = 35
            scores['thirsty'] = 8
        if 'gambler' in gems:
            scores['time_traveler'] = -10
            scores['thirsty'] = 88
        return max((a for a in actions if _label(a) == 'choose_gem'), key=lambda a: scores.get(_label(obs['gem_offers'][a['index']]), 0))

    money = obs.get('mushrooms_left', 0)
    full_price = obs.get('heal_full_price', 999)
    if 'heal_full' in kinds and 4 <= money < 48 and full_price >= money - 2 and hp < .9:
        return _first(actions, 'heal_full')

    if 'wait' in kinds:
        # Greasy rewards repeated reentry. Otherwise carry enough health to
        # absorb bad luck; at a boss heal only the amount needed to beat it.
        target = (.4 if stage >= 2 else .2) if 'greasy' in gems else 1.
        if obs.get('resume_phase') == 'boss' or room % 25 == 0:
            target = BOSS[stage][1] + .01
        if money and 'heal_full' not in kinds and 'greasy' not in gems:
            target = min(target, max(.2, (int((1 - money / 48) * 5) + 1) / 5))
        available_hp = hp + max(0, obs.get('hours_left', 168) - .05) / 24
        if available_hp < target:
            target = max(.2, int(available_hp * 5) / 5)
        if 'reenter' in kinds and hp >= min(.99, target):
            return _first(actions, 'reenter')
        if 'heal_full' in kinds and hp < .2 and 'greasy' not in gems:
            return _first(actions, 'heal_full')
        waits = [a for a in actions if _label(a) == 'wait']
        desired = max(0., (target - hp) * 24)
        return min(waits, key=lambda a: abs(float(a.get('value', 0)) - desired))

    # Paid healing is most efficient in large increments. Delay it while a
    # revealed beneficial room can provide the health without expenditure.
    if 'heal_full' in kinds and hp < .15 and phase in {'doors', 'combat', 'boss'}:
        return _first(actions, 'heal_full')

    if 'choose_door' in kinds:
        def door_value(action):
            door = obs.get('doors', [])[action.get('index', 0)]
            name = _label(door)
            key_cost = float(door.get('key_cost', 0))
            keys = obs.get('keys', 0)
            key_value = .020 if keys >= 5 else .035 if keys >= 2 else .045
            damage = min(fight_dmg, flee_dmg)
            values = {
                'blessing': -.065, 'golden': -.038, 'shop': -.025,
                'unlocked': -.006, 'epic': -.002, 'trial': damage,
                'wood': -.003, 'stone': -.003, 'souls': -.003,
                'metal': -.003, 'arcane': -.003, 'hourglasses': -.003,
                'locked': -.006, 'double_locked': -.006,
                'mystery': .40 * damage - .009,
                'monster': damage, 'destiny': .070,
                'sacrifice': .12 * (.6 if 'blood' in gems else 1),
                'cursed': .13, 'wall': 10,
            }
            key_value *= min(1., max(.05, (100 - room) / 12))
            value = values.get(name, .03) + key_cost * key_value
            if door.get('trap') and 'disarm' not in bless:
                value += .055 if 'gambler' in gems or door.get('cursed_trap') else .1
            if name == 'monster' and fight_dmg == 0:
                value -= .025
            if name in {'locked', 'epic', 'double_locked'} and 'deceit' in gems:
                value += .2 * damage
            return value, action.get('index', 0)
        return min((a for a in actions if _label(a) == 'choose_door'), key=door_value)

    if 'fight' in kinds and 'skip' in kinds and fight_dmg > 0:
        return _first(actions, 'skip')

    if 'fight' in kinds:
        is_boss = 'flee' not in kinds or phase == 'boss' or room % 25 == 0
        if is_boss:
            if hp <= BOSS[stage][1]:
                if room == 100 and 'heal_step' in kinds:
                    missing = max(0, BOSS[stage][1] + .001 - hp)
                    count = max(1, int(missing / .2 + .999999))
                    used = obs.get('paid_heals', 0)
                    prices = [10 if used + i == 0 else 15 if used + i == 1 else 20 for i in range(count)]
                    if sum(prices) <= obs.get('mushrooms_left', 0) and ('heal_full' not in kinds or sum(prices) < obs.get('heal_full_price', 999)):
                        return _first(actions, 'heal_step')
                if 'heal_full' in kinds:
                    return _first(actions, 'heal_full')
                if 'heal_step' in kinds:
                    return _first(actions, 'heal_step')
            return _first(actions, 'fight')
        if 'flee' in kinds:
            # Keys offset some fight damage, but a free escape remains better
            # than taking a hit for a speculative key.
            keys = obs.get('keys', 0)
            key_value = .02 if keys >= 5 else .035
            fight_key_gain = 1.4 if 'key_moment' in bless else .5
            fight_key_gain += .3 * ('lodestone' in gems) - .15 * ('spying' in gems)
            fight_score = fight_dmg - max(0, fight_key_gain) * key_value
            flee_score = flee_dmg - (.4 * p * key_value if 'pendant' in gems else 0)
            if hp <= fight_max and fight_dmg > 0:
                fight_score += .05
            return _first(actions, 'fight' if fight_score <= flee_score else 'flee')
        return _first(actions, 'fight')

    if phase == 'curse_shop' and 'buy' in kinds:
        def curse_value(action):
            offer = obs['offers'][action['index']]
            effect = offer['effect']
            name = _label(effect)
            count = effect.get('remaining', 5)
            key_value = .025 if obs.get('keys', 0) >= 5 else .04
            harm = {'gold_hangover': 0, 'poison': count * .05,
                    'broken_armor': count * .025 * (1 - p),
                    'clumsy': count * .025,
                    'hard_lock': .012 * count if obs.get('keys', 0) else .004 * count}.get(name, .2)
            if 'one_hit' in bless and name in {'broken_armor', 'clumsy'}:
                harm *= .3
            return offer.get('keys', 0) * key_value - harm
        buys = [a for a in actions if _label(a) == 'buy']
        best = max(buys, key=curse_value)
        return best if curse_value(best) > .005 else _first(actions, 'skip')

    if 'buy' in kinds and 'offers' in obs:
        def benefit(effect):
            name = _label(effect)
            remaining = effect.get('remaining', 1)
            magnitude = effect.get('magnitude', 0)
            owned = bless.get(name, {})
            previous = owned.get('remaining', 0) if isinstance(owned, dict) else 0
            extra = max(0, remaining - previous)
            if name == 'elixir':
                return min(1 - hp, magnitude)
            if name == 'recovery':
                return min(1 - hp + .06 * extra, magnitude * extra)
            if name == 'one_hit':
                return extra * (.035 + .05 * stage / 3) if 'one_hit' not in bless else extra * .04
            if name == 'escape':
                return extra * min(.055, flee_dmg * .6)
            if name == 'disarm':
                return extra * .026
            if name == 'lockpick':
                return extra * .039
            if name == 'key_moment':
                return extra * (.025 if 'one_hit' in bless else .003)
            return 0.
        def buy_value(action):
            offer = obs['offers'][action['index']]
            key_value = .025 if obs.get('keys', 0) >= 5 else .04
            return benefit(offer['effect']) - offer.get('keys', 0) * key_value
        buys = [a for a in actions if _label(a) == 'buy']
        best = max(buys, key=buy_value) if buys else None
        value = buy_value(best) if best else -1
        if value > .045:
            return best
        free_shop = obs.get('run_number', 1) == 1 and room <= 25
        if 'reroll' in kinds and obs.get('mushrooms_left', 0) >= 40 and obs.get('shop_rerolls', 0) < 2 and (free_shop or obs.get('keys', 0) >= 2):
            return _first(actions, 'reroll')
        if best is not None and value > .002:
            return best
        return _first(actions, 'skip') or best or actions[0]

    encounter = obs.get('encounter', '')
    if 'interact' in kinds:
        avoid = {'cursed_chest', 'sacrifice_chest', 'skeleton', 'mimic', 'locked_sarcophagus', 'rps', 'spider_head', 'spider_full', 'spider_legs', 'wheel', 'trial', 'undead', 'shakes', 'valaraukar', 'rainbow'}
        interact = encounter not in avoid
        if encounter == 'barrel':
            interact = 'time_traveler' not in gems and ('gambler' in gems or ('greed' in gems and hp < .8) or hp < .55)
        if encounter == 'pig':
            interact = .40 < hp < .8
        if encounter == 'spider_legs':
            interact = False
        if interact or 'skip' not in kinds:
            return _first(actions, 'interact')
        return _first(actions, 'skip')

    # Leave optional trial sequences as soon as that choice is available.
    for action in actions:
        name = _label(action)
        if 'trial' in name and ('leave' in name or 'exit' in name):
            return action

    # Unknown harmless menus use their first non-healing progress action.
    for wanted in ('reenter', 'leave', 'skip', 'interact', 'open', 'continue'):
        if wanted in kinds:
            return _first(actions, wanted)
    nonheal = [a for a in actions if _label(a) not in {'heal_full', 'heal_step', 'reroll'}]
    return (nonheal or actions)[0]
