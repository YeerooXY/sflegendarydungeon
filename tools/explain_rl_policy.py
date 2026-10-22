"""Extend the frozen policy audit with state-dependent explanatory counts.

Uses the existing diagnostic runner unchanged. These are descriptions of a
learner's visited states and chosen actions, not causal or optimal action rules.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

import audit_rl_policy as audit


def time_bin(hours):
    return '<6h' if hours < 6 else '6-24h' if hours < 24 else '24-72h' if hours < 72 else '72h+'


def money_bin(amount):
    return '0' if amount == 0 else '1-9' if amount < 10 else '10-24' if amount < 25 else '25-49' if amount < 50 else '50-99' if amount < 100 else '100+'


def offer_tier(offer):
    kind = offer['kind']
    weak_duration = {'raider': 5, 'one_hit': 4, 'escape': 5, 'disarm': 4, 'lockpick': 2,
                     'key_moment': 4, 'broken_armor': 4, 'poison': 5, 'clumsy': 5,
                     'gold_hangover': 5, 'hard_lock': 4}
    if kind == 'elixir':
        strong = offer['magnitude'] > .25
    elif kind == 'recovery':
        strong = offer['magnitude'] > .1
    elif kind in weak_duration:
        strong = offer['remaining'] > weak_duration[kind]
    else:
        return 'unknown'
    return 'strong' if strong else 'weak'


class ExplainDecoder(audit.Decoder):
    def effects(self, row, label):
        result = []
        for i in range(3):
            prefix = f'{label}.{i}'
            if self.value(row, prefix + '.present') <= .5:
                continue
            result.append({'kind': self.category(row, prefix + '.kind'),
                           'magnitude': round(self.value(row, prefix + '.magnitude'), 6),
                           'remaining': round(self.value(row, prefix + '.remaining_div10') * 10),
                           'starts_in_turns': round(self.value(row, prefix + '.starts_in_turns_div10') * 10),
                           'clock': self.category(row, prefix + '.clock'),
                           'active': self.value(row, prefix + '.active') > .5})
        return result

    def state(self, row, budget, episode_index):
        state = super().state(row, budget, episode_index)
        state.update(owned_gems=self.owned(row), blessings=self.effects(row, 'blessings'), curses=self.effects(row, 'curses'),
                     resume_phase=self.category(row, 'resume_phase'),
                     hours_left=max(0., self.value(row, 'hours_left_div168') * 168),
                     mushrooms_left=round(self.value(row, 'mushrooms_left_div500') * 500),
                     heal_step_price=round(self.value(row, 'heal_step_price_div100') * 100),
                     heal_full_price=round(self.value(row, 'heal_full_price_div100') * 100))
        active = {effect['kind'] for effect in state['blessings'] + state['curses'] if effect['active']}
        state['active_effects'] = sorted(active)
        for kind in ('one_hit', 'escape', 'recovery', 'poison', 'broken_armor', 'clumsy', 'disarm', 'lockpick'):
            state[kind] = kind in active
        if state['phase'] == 'recovery':
            state['recovery_encounter'] = self.category(row, 'encounter')
        if state['phase'] == 'doors':
            for i, door in enumerate(state['doors']):
                door.update(cursed_trap=self.value(row, f'doors.{i}.cursed_trap') > .5,
                            key_cost=round(self.value(row, f'doors.{i}.key_cost_div6') * 6),
                            available=self.value(row, f'doors.{i}.available') > .5)
        if state['phase'] in ('shop', 'curse_shop'):
            state['opening_shop'] = state['phase'] == 'shop' and state['run_type'] == 'first' and state['room'] == 5
            state['shop_offer_tiers'] = [
                {**offer, 'strength': offer_tier(offer),
                 'price_tier': 'free' if offer['keys'] == 0 else '1-3' if offer['keys'] < 4 else '4' if offer['keys'] == 4 else '5+',
                 'affordable': state['phase'] == 'curse_shop' or state['keys'] >= offer['keys']}
                for offer in state['offers']]
        # The runner does not supply a mask to this decoder. Do not infer or
        # fabricate legal action names from incomplete observations.
        return state


class BoundedCounts(audit.Counts):
    """Bound detailed context growth, preserving every excess decision in totals."""
    def __init__(self, limit=1500):
        super().__init__()
        self.limit = limit
        self.detailed_rows = 0
        self.partition_rows = Counter()
        self.overflow_decisions = 0

    def add(self, dimensions, state, action, teacher):
        key = json.dumps(dimensions, sort_keys=True, separators=(',', ':'))
        partition = (dimensions['profile'], dimensions['budget'])
        if key not in self.rows:
            if self.partition_rows[partition] >= self.limit:
                self.overflow_decisions += 1
                dimensions = {key: dimensions[key] for key in ('profile', 'budget')}
                dimensions['context_overflow'] = True
            else:
                self.detailed_rows += 1
                self.partition_rows[partition] += 1
        return super().add(dimensions, state, action, teacher)


class ExplainAudit(audit.BehaviorAudit):
    EXTRA_TABLES = ('doors', 'recovery', 'specials', 'shop_buys', 'shop_choices', 'gem_choices', 'combat_context', 'healing')
    COARSE_TABLES = ('door_policy', 'recovery_policy', 'special_policy', 'combat_policy')

    def __init__(self):
        super().__init__()
        self.tables.update({name: BoundedCounts() for name in self.EXTRA_TABLES})
        for name in ('shop_buys', 'shop_choices', 'gem_choices', 'healing', *self.COARSE_TABLES):
            self.tables[name] = audit.Counts()

    def record(self, profile, state, action, teacher):
        super().record(profile, state, action, teacher)
        common = {'profile': profile, 'budget': state['budget']}
        hp, keys = audit.hp_bin(state['hp']), audit.key_bin(state['keys'])
        flags = {kind: state[kind] for kind in ('one_hit', 'escape', 'recovery', 'poison', 'broken_armor', 'clumsy')}
        if state['phase'] == 'doors' and action in ('door_0', 'door_1'):
            coarse = self.tables['door_policy'].add({**common, 'stage': state['stage'], 'one_hit': state['one_hit'],
                                                     'disarm': state['disarm'], 'doors': state['doors']}, state, action, teacher)
            coarse.setdefault('chosen_types', Counter())[state['doors'][int(action[-1])]['kind']] += 1
            row = self.tables['doors'].add({**common, 'stage': state['stage'], 'run_type': state['run_type'],
                                            'hp_bin': hp, 'key_bin': keys, 'one_hit': state['one_hit'],
                                            'escape': state['escape'], 'disarm': state['disarm'],
                                            'doors': state['doors']}, state, action, teacher)
            row.setdefault('chosen_types', Counter())[state['doors'][int(action[-1])]['kind']] += 1
        if state['phase'] == 'recovery':
            self.tables['recovery_policy'].add({**common, 'hp_bin': hp, 'destination': state['resume_phase'],
                                                'boss_room': state['room'] % 25 == 0,
                                                'greasy': 'greasy' in state['owned_gems'], 'rusty': 'rusty' in state['owned_gems'],
                                                'time_left_bin': time_bin(state['hours_left'])}, state, action, teacher)
            self.tables['recovery'].add({**common, 'stage': state['stage'], 'hp_bin': hp, 'key_bin': keys,
                                        'owned_gems': state['owned_gems'], 'destination': state['resume_phase'],
                                        'encounter': state.get('recovery_encounter'),
                                        'time_left_bin': time_bin(state['hours_left']),
                                        'money_left_bin': money_bin(state['mushrooms_left'])}, state, action, teacher)
        if state['phase'] == 'encounter' and state['encounter'] not in ('monster', 'boss', 'trial_monster', 'empty'):
            self.tables['special_policy'].add({**common, 'encounter': state['encounter'], 'hp_bin': hp,
                                               'one_hit': state['one_hit'], 'poison': state['poison'], 'recovery': state['recovery'],
                                               **{gem: gem in state['owned_gems'] for gem in ('time_traveler', 'gambler', 'thirsty')}},
                                              state, action, teacher)
            self.tables['specials'].add({**common, 'stage': state['stage'], 'encounter': state['encounter'],
                                        'hp_bin': hp, 'key_bin': keys, 'owned_gems': state['owned_gems'],
                                        **flags}, state, action, teacher)
        if state['phase'] == 'encounter' and action in ('fight', 'flee'):
            escape_bonus = round(.4 * ('rabbit' in state['owned_gems']) + .2 * ('moonstone' in state['owned_gems']) - .3 * ('bull' in state['owned_gems']), 2)
            self.tables['combat_policy'].add({**common, 'stage': state['stage'], 'encounter': state['encounter'],
                                              'greasy': 'greasy' in state['owned_gems'], 'escape_stone_bonus': escape_bonus,
                                              **flags}, state, action, teacher)
            self.tables['combat_context'].add({**common, 'stage': state['stage'], 'encounter': state['encounter'],
                                               'hp_bin': hp, 'key_bin': keys, 'owned_gems': state['owned_gems'],
                                               **flags}, state, action, teacher)
        if action in ('heal_step', 'heal_full'):
            self.tables['healing'].add({**common, 'stage': state['stage'], 'phase': state['phase'],
                                       'encounter': state.get('encounter'), 'destination': state['resume_phase'],
                                       'hp_bin': hp, 'key_bin': keys, 'money_left_bin': money_bin(state['mushrooms_left']),
                                       'owned_gems': state['owned_gems'],
                                       'step_affordable': state['heal_step_price'] <= state['mushrooms_left'],
                                       'full_affordable': state['heal_full_price'] <= state['mushrooms_left'],
                                       **flags}, state, action, teacher)
        if state['phase'] in ('shop', 'curse_shop') and action in ('buy_0', 'buy_1', 'reroll', 'skip'):
            self.tables['shop_choices'].add({**common, 'phase': state['phase'], 'stage': state['stage'],
                                            'opening_shop': state['opening_shop'], 'hp_bin': hp,
                                            'keys': state['keys'], 'owned_gems': state['owned_gems'],
                                            'offers': state['shop_offer_tiers'],
                                            'one_hit': state['one_hit'], 'escape': state['escape'],
                                            'poison': state['poison']}, state, action, teacher)
            if action in ('buy_0', 'buy_1'):
                chosen = state['shop_offer_tiers'][int(action[-1])]
                self.tables['shop_buys'].add({**common, 'phase': state['phase'], 'stage': state['stage'],
                                             'opening_shop': state['opening_shop'], 'hp_bin': hp, 'key_bin': keys,
                                             'owned_gems': state['owned_gems'], 'kind': chosen['kind'],
                                             'strength': chosen['strength'], 'offered_duration': chosen['remaining'],
                                             'key_amount': chosen['keys'], 'price_tier': chosen['price_tier'],
                                             'key_direction': 'received' if state['phase'] == 'curse_shop' else 'paid'},
                                            state, action, teacher)
        if state['phase'] == 'gems' and action in ('gem_0', 'gem_1', 'gem_2'):
            self.tables['gem_choices'].add({**common, 'stage': state['stage'], 'run_type': state['run_type'],
                                           'owned_gems': state['owned_gems'], 'offered': state['gem_offers'],
                                           'chosen': state['gem_offers'][int(action[-1])]}, state, action, teacher)

    def export(self):
        result = super().export()
        source = Path(__file__).resolve()
        result['explanation_extension'] = {
            'schema': 'sfld-policy-explanation-v1', 'source': str(source.relative_to(audit.ROOT)),
            'sha256': audit.train_rl.sha(source), 'detailed_row_limit_per_budget_for_capped_tables': 1500,
            'tables': {name: {'detailed_rows': getattr(self.tables[name], 'detailed_rows', len(self.tables[name].rows)),
                              'overflow_decisions': getattr(self.tables[name], 'overflow_decisions', 0),
                              'capped': isinstance(self.tables[name], BoundedCounts),
                              'decisions': sum(row['decisions'] for row in result['tables'][name])}
                       for name in (*self.EXTRA_TABLES, *self.COARSE_TABLES)},
            'notes': ['Ordered door choices retain actual availability, key costs and both trap flags; exclude forced choices when interpreting preference.',
                      'Healing decisions are counted separately from committed fight/flee and doorway decisions.',
                      'Recovery wait action names are requested targets; waits may clip to the deadline and duplicate targets share the first legal action ID. No actual post-wait health is inferred.',
                      'Shop duration and strength describe the displayed offer before stone duration adjustments. Curse-shop key amounts are rewards, not prices.',
                      'Exact three-stone offer sets are recorded at committed picks only; repeated pre-pick heals do not duplicate exposure.',
                      'No legal-action list is synthesized: the diagnostic runner does not pass its action mask to the state decoder.',
                      'door_policy, recovery_policy, special_policy and combat_policy contain complete uncapped coarse contexts across every budget. Exact shop_choices, shop_buys, gem_choices and healing are also uncapped.',
                      'Remaining detailed contexts have separate caps per budget; excess decisions are retained in explicitly labeled overflow totals, not silently dropped. Use complete policy tables for aggregate advice.',
                      'State samples include ordered effect descriptors and approximate float32-derived time/budget balances; tables deliberately use coarse bins.']}
        return result


def run(args):
    original_decoder, original_audit = audit.Decoder, audit.BehaviorAudit
    try:
        # Process-local extension only: original audit/training source files and
        # checkpoint inputs remain untouched.
        audit.Decoder, audit.BehaviorAudit = ExplainDecoder, ExplainAudit
        return audit.run(args)
    finally:
        audit.Decoder, audit.BehaviorAudit = original_decoder, original_audit


if __name__ == '__main__':
    try:
        run(audit.parse())
    except (ValueError, RuntimeError, OSError) as error:
        print(f'explain_rl_policy: {error}', file=sys.stderr)
        raise SystemExit(1)
