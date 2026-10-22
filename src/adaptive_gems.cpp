#include "sfld/policy.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace sfld {
namespace {
// A cheap, deterministic expectation model for the *offered* stones. This is a
// heuristic, not a solved policy: future choices use expected contents, a key
// shadow price, and approximate effect occupancy. No hidden draw is inspected.
struct GemHorizon {
    const Policy& policy;
    const Game& game;
    const Profile& p;
    State s;
    std::size_t stage = 0;
    double fight_damage = 0, flee_damage = 0, escape = 0, key_price = 0;
    double fight_multiplier = 1, flee_multiplier = 1;
    double blessing = 0, curse = 0;
    double one_hit_fraction = 0;
    std::array<double, ix(Encounter::count)> encounter_costs{};

    double mean(Range r) const {
        if (p.damage_distribution == "low") return r.low;
        if (p.damage_distribution == "high") return r.high;
        if (p.damage_distribution == "triangular_mean") return r.mean;
        return (r.low + r.high) / 2;
    }
    double effect_value(Effect e) const {
        const bool bad = is_curse(e.kind);
        if (s.has(bad ? Gem::moonstone : Gem::time_traveler) && e.kind != EffectKind::elixir) ++e.remaining;
        const double rooms = std::min(e.remaining, 101 - std::max(s.room, static_cast<int>(stage) * 25 + 1));
        const double healing = s.has(Gem::rusty) ? 1.4 : 1;
        switch (e.kind) {
        case EffectKind::elixir:
            // Expected missing HP in a future encounter, not a claim that the
            // current HP stays fixed throughout the remaining run.
            return p.time_traveler_elixir_full_heal && s.has(Gem::time_traveler) ? .50 : std::min(.50, e.magnitude * healing);
        case EffectKind::recovery: return .75 * e.magnitude * rooms * healing;
        case EffectKind::one_hit: return rooms * .50 * ((1 - escape) * flee_damage + p.key_chance * key_price);
        case EffectKind::escape: return rooms * .50 * flee_damage * std::min(e.magnitude, 1 - escape);
        case EffectKind::disarm: return .1 * std::min(rooms, (101 - s.room) * p.trap_chance * .45);
        case EffectKind::lockpick: return key_price * std::min(rooms, (101 - s.room) * .20);
        case EffectKind::key_moment: return key_price * std::max(0.0, 2 * e.magnitude - p.key_chance) * rooms * .40;
        case EffectKind::poison: return e.magnitude * rooms * .80;
        case EffectKind::broken_armor: return e.magnitude * rooms * .50 * mean(p.escape_damage[stage]) * (1 - escape);
        case EffectKind::clumsy: return std::min(e.magnitude, escape) * rooms * .50 * flee_damage;
        case EffectKind::hard_lock: return key_price * .18 * rooms;
        default: return 0; // Items/gold do not advance the completion objective.
        }
    }
    double random_effect(bool bad, bool barrel = false) const {
        const double strong = std::clamp(p.strong_effect + (bad && s.has(Gem::explorer) ? .2 : 0) +
            (!bad && barrel && s.has(Gem::thirsty) ? .4 : 0), 0.0, 1.0);
        const auto size = bad ? p.curse_weights.size() : p.blessing_weights.size();
        double value = 0, total = 0;
        for (std::size_t i = 0; i < size; ++i) {
            const double weight = bad ? p.curse_weights[i] : p.blessing_weights[i];
            const auto kind = static_cast<EffectKind>(i + (bad ? ix(EffectKind::broken_armor) : 0));
            value += weight * ((1 - strong) * effect_value(effect_template(kind, false)) +
                strong * effect_value(effect_template(kind, true)));
            total += weight;
        }
        return total > 0 ? value / total : 0;
    }
    double combat(double scale = 1, double fixed_base = -1, double fight_reward = 0, bool can_flee = true) const {
        const double key_chance = std::clamp(p.key_chance + (s.has(Gem::lodestone) ? .3 : 0) -
            (s.has(Gem::spying) ? .15 : 0), 0.0, 1.0);
        double fight_curse = curse;
        if (p.floor_specific_curses && stage > 0) {
            constexpr EffectKind kinds[]{EffectKind::gold_hangover, EffectKind::poison, EffectKind::broken_armor};
            const auto kind = kinds[stage - 1];
            const double strong = std::clamp(p.strong_effect + (s.has(Gem::explorer) ? .2 : 0), 0.0, 1.0);
            fight_curse = (1 - strong) * effect_value(effect_template(kind, false)) + strong * effect_value(effect_template(kind, true));
        }
        const double fight_hit = fixed_base >= 0 ? fixed_base * fight_multiplier : fight_damage * scale;
        const double flee_hit = fixed_base >= 0 ? fixed_base * flee_multiplier : flee_damage * scale;
        const double fight = (1 - one_hit_fraction) * fight_hit - key_chance * key_price - fight_reward +
            (stage > 0 ? std::clamp(p.combat_curse_chance + (s.has(Gem::misadventurer) ? .1 : 0), 0.0, 1.0) * fight_curse : 0) -
            (s.has(Gem::kidney) ? .1 * blessing : 0);
        if (!can_flee) return fight;
        const double flee = (1 - escape) * flee_hit -
            (s.has(Gem::pendant) ? escape * .4 * key_price : 0) +
            (s.has(Gem::pearl) ? escape * .2 * curse : 0);
        return std::min(fight, flee);
    }
    double container_chance(bool ordinary) const {
        if (ordinary && p.container_joint_weights) {
            const auto& w = s.has(Gem::greed) && p.container_joint_greed_weights ? *p.container_joint_greed_weights : *p.container_joint_weights;
            double yes = 0;
            for (std::size_t i = 1; i < w.size(); i += 2) yes += w[i];
            const double total = std::accumulate(w.begin(), w.end(), 0.0);
            return total > 0 ? yes / total : 0;
        }
        const double total = std::accumulate(p.container_weights.begin(), p.container_weights.end(), 0.0);
        return total > 0 && (s.has(Gem::greed) || !p.container_blessing_requires_greed) ? p.container_weights[0] / total : 0;
    }
    double encounter(Encounter e) const {
        if (e == Encounter::boss) return mean(p.boss_damage[stage]);
        if (is_enemy(e)) {
            double scale = 1;
            if (e == Encounter::undead || e == Encounter::tube || e == Encounter::beta) scale = .5;
            if (e == Encounter::trial_monster) scale = p.trial_multipliers[static_cast<std::size_t>(std::clamp(s.trial_depth, 0, 4))];
            double fixed_base = -1, reward = 0;
            if (e == Encounter::shakes) { fixed_base = .3; reward = blessing; }
            if (e == Encounter::pig) { fixed_base = .4; reward = .45 * (s.has(Gem::rusty) ? 1.4 : 1); }
            if (e == Encounter::valaraukar) { fixed_base = .6; reward = effect_value({EffectKind::recovery, .1, 3, Clock::room}); }
            // These encounter rewards require a fight; escaping earns none.
            const double cost = combat(scale, fixed_base, reward, e != Encounter::mimic);
            return is_optional(e) ? std::min(0.0, cost) : cost;
        }
        switch (e) {
        case Encounter::crate: case Encounter::bronze: case Encounter::silver: return -container_chance(true) * blessing;
        case Encounter::skeleton: return std::min(0.0, p.skeleton_wakes * combat() - (1 - p.skeleton_wakes) * container_chance(false) * blessing);
        case Encounter::barrel: {
            if (policy.barrels == BarrelPolicy::skip) return 0;
            const double chance = barrel_blessing_chance(p, s);
            const double cost = (1 - chance) * curse - chance * random_effect(false, true);
            return policy.barrels == BarrelPolicy::always ? cost : std::min(0.0, cost);
        }
        case Encounter::fountain: case Encounter::cleansing_fountain: return -.20 * (s.has(Gem::rusty) ? 1.4 : 1);
        case Encounter::narrator: return -.20 * (s.has(Gem::rusty) ? 1.4 : 1) - blessing;
        case Encounter::lava: return .1;
        case Encounter::wishing_well: return -.5 * blessing;
        case Encounter::rps: return std::min(0.0, (.1 + curse - blessing) / 3);
        case Encounter::wheel: return std::min(0.0, (curse - blessing) / 5);
        default: return 0;
        }
    }
    double contents(const std::array<double, ix(Encounter::count)>& w, bool exclude_enemy = false) const {
        double cost = 0, total = 0;
        for (std::size_t i = 0; i < w.size(); ++i) if (w[i] > 0 && !(exclude_enemy && is_enemy(static_cast<Encounter>(i)))) {
            cost += w[i] * encounter_costs[i]; total += w[i];
        }
        return total > 0 ? cost / total : 0;
    }
    double door(Door d, Door other) const {
        double cost = 0;
        switch (d) {
        case Door::monster: cost = combat(); break;
        case Door::mystery: cost = contents(p.mystery_for_alternative(other), other == Door::monster); break;
        case Door::unlocked: cost = contents(p.mystery_weights); break;
        case Door::locked: case Door::double_locked: case Door::epic:
            cost = d == Door::epic ? 0 : contents(p.locked_weights);
            if (s.has(Gem::old_sacrifice)) cost *= .8;
            if (s.has(Gem::kidney)) cost *= .8;
            if (s.has(Gem::deceit)) cost = (1 - p.hidden_monster_chance) * cost + p.hidden_monster_chance * combat();
            cost += key_price * (d == Door::double_locked ? 2 : 1); break;
        case Door::golden: return contents(p.golden_weights);
        case Door::cursed: cost = curse; break;
        case Door::sacrifice: cost = .12 * (s.has(Gem::blood) ? .6 : 1); break;
        case Door::blessing: cost = combat() - blessing; break;
        case Door::destiny: {
            const auto& w = p.destiny_reward_weights;
            const double total = std::accumulate(w.begin(), w.end(), 0.0);
            cost = contents(p.destiny_weights) + (total > 0 ? (w[5] * curse - w[0] * blessing) / total : 0); break;
        }
        case Door::wall: return 1e6;
        default: return 0;
        }
        // Traps are visible before choosing, so avoidable traps receive a smaller
        // exposure weight. Masochist makes the selected eligible door trapped.
        if (d == Door::monster || d == Door::mystery) {
            const double trap = s.has(Gem::gambler) ? curse : .1 * (s.has(Gem::masochist) ? .8 : 1);
            cost += trap * (s.has(Gem::masochist) ? 1 : p.trap_chance * .45);
        }
        return cost;
    }
    double ordinary_cost() const {
        auto weights = p.door_weights;
        weights[ix(Door::unlocked)] = weights[ix(Door::double_locked)] = weights[ix(Door::epic)] = 0;
        if (s.has(Gem::spying)) {
            weights[ix(Door::unlocked)] += weights[ix(Door::locked)] * p.spying_unlock_fraction;
            weights[ix(Door::locked)] *= 1 - p.spying_unlock_fraction;
        } else weights[ix(Door::unlocked)] = 0;
        if (s.has(Gem::lodestone)) weights[ix(Door::double_locked)] += weights[ix(Door::locked)] * .5;
        else weights[ix(Door::double_locked)] = 0;
        if (s.has(Gem::devil)) weights[ix(Door::epic)] += 8;
        else weights[ix(Door::epic)] = 0;
        if (s.has(Gem::greasy)) weights[ix(Door::epic)] = 0;
        if (s.has(Gem::blood)) weights[ix(Door::sacrifice)] *= 2;
        if (s.has(Gem::hick)) weights[ix(Door::sacrifice)] *= .5;
        if (s.has(Gem::thirsty)) weights[ix(Door::cursed)] *= 2;
        if (s.has(Gem::misadventurer)) weights[ix(Door::cursed)] *= .5;
        constexpr std::array physical{Door::locked, Door::double_locked, Door::unlocked, Door::epic, Door::cursed,
            Door::sacrifice, Door::blessing, Door::destiny, Door::wood, Door::stone, Door::souls, Door::metal,
            Door::arcane, Door::hourglasses, Door::wall};
        constexpr std::array<std::array<Door, 2>, 8> pairs{{{Door::locked, Door::mystery}, {Door::locked, Door::monster},
            {Door::mystery, Door::mystery}, {Door::locked, Door::golden}, {Door::monster, Door::mystery},
            {Door::golden, Door::mystery}, {Door::golden, Door::monster}, {Door::golden, Door::golden}}};
        // Legacy independent-door profiles use an explicitly coarse family mix;
        // calibrated profiles use their measured pair-family distribution.
        auto pair_weights = p.door_pair_weights.value_or(std::array<double, 8>{3, 3, 1, 1, 3, 1, 1, .2});
        double physical_total = 0, baseline_physical = 0;
        for (auto d : physical) {
            physical_total += weights[ix(d)];
            if (d != Door::unlocked && d != Door::double_locked && d != Door::epic) baseline_physical += p.door_weights[ix(d)];
        }
        const double physical_factor = s.has(Gem::pendant) ? 1 : (baseline_physical > 0 ? physical_total / baseline_physical : 1);
        double cost = 0, total = 0;
        for (std::size_t i = 0; i < pairs.size(); ++i) {
            auto [a, b] = pairs[i];
            const bool has_physical = a == Door::locked;
            if (s.has(Gem::pendant) && !has_physical) continue;
            double weight = pair_weights[i];
            if (has_physical) weight *= physical_factor;
            const double mystery_modifier = (s.has(Gem::greed) ? 2 : 1) * (s.has(Gem::explorer) ? .5 : 1);
            if (a == Door::mystery) weight *= mystery_modifier;
            if (b == Door::mystery) weight *= mystery_modifier;
            double pair_cost = 0;
            if (has_physical && physical_total > 0) {
                for (auto d : physical) if (weights[ix(d)] > 0)
                    pair_cost += weights[ix(d)] / physical_total * std::min(door(d, b), door(b, d));
            } else if (has_physical) pair_cost = door(b, Door::wall);
            else pair_cost = std::min(door(a, b), door(b, a));
            cost += weight * pair_cost; total += weight;
        }
        return total > 0 ? cost / total : 0;
    }
    double score() {
        const int remaining = std::max(0, 101 - s.room);
        double ordinary_loss = 0, boss_loss = 0;
        int stages = 0;
        for (stage = static_cast<std::size_t>(std::clamp((s.room - 1) / 25, 0, 3)); stage < 4; ++stage) {
            const int start = std::max(s.room, static_cast<int>(stage) * 25 + 1);
            const int rooms = std::max(0, static_cast<int>(stage + 1) * 25 - start);
            ++stages;
            escape = std::clamp(p.escape_chance + (s.has(Gem::rabbit) ? .4 : 0) + (s.has(Gem::moonstone) ? .2 : 0) -
                (s.has(Gem::bull) ? .3 : 0), 0.0, 1.0);
            fight_multiplier = std::max(0.0, 1 + (s.has(Gem::rabbit) ? .25 : 0) +
                (s.has(Gem::devil) ? .25 : 0) - (s.has(Gem::hero) ? .2 : 0) - (s.has(Gem::deceit) ? .2 : 0) - (s.has(Gem::bull) ? .2 : 0));
            flee_multiplier = std::max(0.0, fight_multiplier + (s.has(Gem::hick) ? .3 : 0) - (s.has(Gem::pearl) ? .4 : 0));
            fight_damage = mean(p.monster_damage[stage]) * fight_multiplier;
            flee_damage = mean(p.escape_damage[stage]) * flee_multiplier;
            key_price = (stage < 2 ? .10 : .065) / (1 + .08 * std::max(0, s.keys - 4));
            one_hit_fraction = 0;
            if (const auto* e = s.effect(EffectKind::one_hit))
                one_hit_fraction = std::clamp(static_cast<double>(e->remaining - (start - s.room)) / std::max(1, rooms), 0.0, 1.0);
            blessing = random_effect(false); curse = random_effect(true);
            for (std::size_t i = 0; i < encounter_costs.size(); ++i) encounter_costs[i] = encounter(static_cast<Encounter>(i));
            ordinary_loss += rooms * ordinary_cost();
            boss_loss += mean(p.boss_damage[stage]); // Boss damage ignores stone multipliers.
            if (!s.shops_seen[stage]) {
                // One bought blessing per remaining shop. Actual offer selection
                // and rerolls remain the normal policy's job, not an oracle here.
                double shop_benefit = 0, total = 0;
                for (std::size_t i = 0; i < p.shop_blessing_weights.size(); ++i) {
                    const auto kind = static_cast<EffectKind>(i);
                    shop_benefit += p.shop_blessing_weights[i] * ((1 - p.shop_strong_effect) * effect_value(effect_template(kind, false)) +
                        p.shop_strong_effect * effect_value(effect_template(kind, true)));
                    total += p.shop_blessing_weights[i];
                }
                if (total > 0) ordinary_loss -= .65 * shop_benefit / total;
            }
        }
        int budget = std::max(0, game.limits().budget - s.mushrooms);
        budget = std::max(0, budget - std::max(0, policy.shop_mushroom_reserve - s.reroll_mushrooms));
        if (policy.recovery_budget >= 0) budget = std::min(budget, std::max(0, policy.recovery_budget - s.recovery_mushrooms));
        const double paid_coverage = std::clamp(budget / std::max(1.0, p.full_heal_cost_per_fraction * (ordinary_loss + boss_loss)), 0.0, 1.0);
        // Free recovery tends to reenter near the minimum; paid play can buy HP
        // before dying. Greasy therefore matters much less when recovery is funded.
        const double revive = std::clamp(policy.revive_hp, .2, 1.0);
        const double useful_rooms = std::min(1.0, remaining / 12.0);
        const double revive_heal = s.has(Gem::greasy) ? .30 * useful_rooms * (s.has(Gem::time_traveler) ? 4.0 / 3 : 1) * (s.has(Gem::rusty) ? 1.4 : 1) : 0;
        const double revive_poison = s.has(Gem::rusty) ? .05 * (s.has(Gem::moonstone) ? 6 : 5) * useful_rooms : 0;
        const double free_attrition = std::max(0.0, ordinary_loss - std::max(0.0, s.hp - boss_loss / std::max(1, stages)));
        const double deaths = free_attrition / std::max(.08, revive + revive_heal - revive_poison);
        ordinary_loss -= deaths * (revive_heal - revive_poison) * (1 - paid_coverage);
        return -(ordinary_loss + boss_loss);
    }
};
}

Action Policy::choose_gem_adaptive(const Game& game) const {
    if (game.state().phase != Phase::gems) throw std::invalid_argument("Adaptive stone selection needs a gem offer");
    int best = -1;
    double value = -std::numeric_limits<double>::infinity();
    for (int i = 0; i < 3; ++i) {
        if (!game.legal({ActionKind::choose_gem, i})) continue;
        const auto gem = game.state().gem_offers[static_cast<std::size_t>(i)];
        if (preferred_gem == gem) return {ActionKind::choose_gem, i};
        GemHorizon horizon{*this, game, game.profile(), game.state()};
        horizon.s.gems[ix(gem)] = true;
        const double score = horizon.score();
        if (score > value) { value = score; best = i; }
    }
    if (best < 0) throw std::logic_error("No legal stone offer");
    return {ActionKind::choose_gem, best};
}
}
