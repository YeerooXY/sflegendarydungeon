#include "sfld/policy.hpp"
#include "sfld/damage_risk.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>

namespace sfld {
namespace {
// Approximate health-equivalent utilities, not exact Bellman values. The planner
// reads only visible state and the declared scenario, never Game::step or RNG.
struct Planner {
    const Policy& policy;
    const Game& game;
    const State& s;
    const Profile& p;
    std::size_t stage;
    int shop_reserve() const {
        return policy.shop_key_reserve > 0 ? policy.shop_key_reserve :
            (policy.strategy == Strategy::key_stockpile ? 4 : 0);
    }
    double key_value() const {
        // The reserve exists to buy useful blessings, not to reject them once
        // the shop is reached. Keep the stockpile's shopping value separate.
        if (s.phase == Phase::shop && policy.strategy == Strategy::key_stockpile) return .045;
        double reserve_value = 0;
        const auto damage = combat_damage(Encounter::monster, false);
        const auto* poison = s.effect(EffectKind::poison);
        const auto* recovery = s.effect(EffectKind::recovery);
        const bool healthy = s.hp > damage.maximum() && damage.after_room(s.hp,
            poison ? poison->magnitude : 0,
            recovery ? recovery->magnitude * (s.has(Gem::rusty) ? 1.4 : 1) : 0).room_survival >= 1;
        const int early_target = policy.early_key_target > 0 ? policy.early_key_target :
            (policy.strategy == Strategy::key_stockpile ? 10 : 0);
        if (early_target > 0 && stage < 2 && healthy) {
            const int target = stage == 0 ? early_target : (early_target + 1) / 2;
            if (s.keys < target) reserve_value = stage == 0 ? .22 : .16;
        }
        if (healthy && !s.shops_seen[stage] && s.keys < shop_reserve())
            reserve_value = std::max(reserve_value, .20);
        if (policy.strategy == Strategy::key_stockpile) {
            // Candidate strategy: save/earn keys while fights are cheap, then
            // spend more readily later. These utilities are untrained heuristics.
            constexpr double stage_value[]{.18, .12, .06, .035};
            return std::max(reserve_value, stage_value[stage] / (1 + .10 * s.keys));
        }
        return std::max(reserve_value, .065 / (1 + .35 * s.keys));
    }
    double permanent_escape() const {
        return std::clamp(p.escape_chance + (s.has(Gem::rabbit) ? .4 : 0) +
            (s.has(Gem::moonstone) ? .2 : 0) - (s.has(Gem::bull) ? .3 : 0), 0.0, 1.0);
    }
    double escape() const {
        double chance = p.escape_chance + (s.has(Gem::rabbit) ? .4 : 0) +
            (s.has(Gem::moonstone) ? .2 : 0) - (s.has(Gem::bull) ? .3 : 0);
        if (const auto* e = s.effect(EffectKind::escape)) chance += e->magnitude;
        if (const auto* e = s.effect(EffectKind::clumsy)) chance -= e->magnitude;
        return std::clamp(chance, 0.0, 1.0);
    }
    double mean(Range range) const {
        if (p.damage_distribution == "low") return range.low;
        if (p.damage_distribution == "high") return range.high;
        if (p.damage_distribution == "triangular_mean") return range.mean;
        return (range.low + range.high) / 2;
    }
    double magnitude(Encounter encounter, bool fleeing) const {
        if (encounter == Encounter::boss) return mean(p.boss_damage[stage]);
        if (!fleeing && s.effect(EffectKind::one_hit)) return 0;
        double damage = mean(fleeing ? p.escape_damage[stage] : p.monster_damage[stage]);
        if (encounter == Encounter::undead || encounter == Encounter::tube || encounter == Encounter::beta) damage *= .5;
        if (encounter == Encounter::shakes) damage = .3;
        if (encounter == Encounter::valaraukar) damage = .6;
        if (encounter == Encounter::pig) damage = .4;
        if (encounter == Encounter::trial_monster) damage *= p.trial_multipliers[static_cast<std::size_t>(std::clamp(s.trial_depth, 0, 4))];
        return damage * game.battle_multiplier(fleeing);
    }
    double effect_value(const Effect& e) const {
        int rooms = std::min(e.remaining, 100 - s.room);
        if (e.clock == Clock::room) for (int n = s.room + 1; n <= std::min(100, s.room + e.remaining); ++n)
            if (n % 25 == 0) --rooms;
        const double damage = mean(p.escape_damage[stage]);
        switch (e.kind) {
        case EffectKind::elixir: return p.time_traveler_elixir_full_heal && s.has(Gem::time_traveler)
            ? 1 - s.hp : std::min(1 - s.hp, e.magnitude * (s.has(Gem::rusty) ? 1.4 : 1));
        case EffectKind::recovery: return std::min(1 - s.hp + .04 * rooms, e.magnitude * rooms * (s.has(Gem::rusty) ? 1.4 : 1));
        case EffectKind::one_hit: return rooms * (.45 * damage * (1 - permanent_escape()) + .2 * key_value());
        case EffectKind::escape: return rooms * .45 * damage * std::min(e.magnitude, 1 - permanent_escape());
        case EffectKind::disarm: return .1 * std::min(static_cast<double>(e.remaining), (100 - s.room) * .07);
        case EffectKind::lockpick: return key_value() * std::min(static_cast<double>(e.remaining), (100 - s.room) * .18);
        case EffectKind::key_moment: return key_value() * std::max(0.0, 2 * e.magnitude - p.key_chance) *
            std::min(static_cast<double>(e.remaining), (100 - s.room) * .3);
        case EffectKind::poison: return e.magnitude * rooms;
        case EffectKind::broken_armor: return e.magnitude * damage * .45 * rooms;
        case EffectKind::clumsy: return std::min(e.magnitude, permanent_escape()) * damage * .45 * rooms;
        case EffectKind::hard_lock: return key_value() * .15 * rooms;
        default: return 0; // Gold and items do not count as completed runs.
        }
    }
    double marginal(Effect e, bool extend = false) const {
        if (extend && e.kind != EffectKind::elixir && s.has(is_curse(e.kind) ? Gem::moonstone : Gem::time_traveler)) ++e.remaining;
        double value = effect_value(e);
        const auto& effects = is_curse(e.kind) ? s.curses : s.blessings;
        if (const auto* active = s.effect(e.kind)) value -= effect_value(*active);
        else if (e.kind != EffectKind::elixir && effects.size == 3) value -= effect_value(effects.slots[0]);
        return value;
    }
    double random_effect_value(bool curse, bool barrel = false) const {
        double total = 0, sum = 0;
        const auto count = curse ? p.curse_weights.size() : p.blessing_weights.size();
        const double strong = std::clamp(p.strong_effect + (curse && s.has(Gem::explorer) ? .2 : 0) +
            (!curse && barrel && s.has(Gem::thirsty) ? .4 : 0), 0.0, 1.0);
        for (std::size_t i = 0; i < count; ++i) {
            const double weight = curse ? p.curse_weights[i] : p.blessing_weights[i];
            const auto kind = static_cast<EffectKind>(i + (curse ? 8 : 0));
            total += weight;
            sum += weight * ((1 - strong) * marginal(effect_template(kind, false), true) +
                strong * marginal(effect_template(kind, true), true));
        }
        return sum / total;
    }
    double injury_cost(double damage) const {
        // A coarse survival penalty also discourages lethal optional encounters.
        return damage + (damage >= s.hp ? .25 : 0);
    }
    DamageRisk combat_damage(Encounter encounter, bool fleeing) const {
        if (encounter == Encounter::boss) return {p.boss_damage[stage], p.damage_distribution};
        if (!fleeing && s.effect(EffectKind::one_hit)) return {{0, 0, 0}, p.damage_distribution};
        Range range = fleeing ? p.escape_damage[stage] : p.monster_damage[stage];
        double factor = 1;
        if (encounter == Encounter::undead || encounter == Encounter::tube || encounter == Encounter::beta) factor = .5;
        if (encounter == Encounter::shakes) range = {.3, .3, .3};
        if (encounter == Encounter::valaraukar) range = {.6, .6, .6};
        if (encounter == Encounter::pig) range = {.4, .4, .4};
        if (encounter == Encounter::trial_monster)
            factor *= p.trial_multipliers[static_cast<std::size_t>(std::clamp(s.trial_depth, 0, 4))];
        factor *= game.battle_multiplier(fleeing);
        return {{range.low * factor, range.high * factor, range.mean * factor}, p.damage_distribution};
    }
    double survival_combat(Encounter encounter, bool fleeing) const {
        const bool boss = encounter == Encounter::boss;
        const double rusty = s.has(Gem::rusty) ? 1.4 : 1;
        const auto* poison = boss ? nullptr : s.effect(EffectKind::poison);
        const auto* recovery = boss ? nullptr : s.effect(EffectKind::recovery);
        const auto assess = [&](const DamageRisk& damage, double immediate_heal = 0) {
            return damage.after_room(s.hp, poison ? poison->magnitude : 0,
                recovery ? recovery->magnitude * rusty : 0, immediate_heal);
        };
        const auto loss = [&](const DamageRisk::RoomResult& risk) {
            return s.hp - risk.expected_hp + .25 * (1 - risk.room_survival);
        };
        const auto risk = assess(combat_damage(encounter, fleeing), !fleeing && encounter == Encounter::pig ? .6 * rusty : 0);
        if (boss) return -loss(risk);
        const auto no_hit = assess(DamageRisk{{0, 0, 0}, p.damage_distribution});
        // Every ordinary exit gets the same ongoing healing/poison. Measure
        // combat against that safe exit, not against the unhealed current HP,
        // or Recovery would make a lethal gamble look better than a safe door.
        const double exit_baseline = loss(no_hit);
        if (fleeing) {
            const double escaped = escape();
            return exit_baseline - (1 - escaped) * loss(risk) - escaped * loss(no_hit) +
                (s.has(Gem::pendant) ? escaped * .4 * key_value() : 0) -
                (s.has(Gem::pearl) ? escaped * no_hit.room_survival * .2 * random_effect_value(true) : 0);
        }
        double keys = std::clamp(p.key_chance + (s.has(Gem::lodestone) ? .3 : 0) -
            (s.has(Gem::spying) ? .15 : 0), 0.0, 1.0);
        if (const auto* e = s.effect(EffectKind::key_moment)) keys = 2 * e->magnitude;
        // Keys are awarded after surviving the hit and persist even if the exit
        // poison kills. Persistent effects only retain utility while alive.
        double value = exit_baseline - loss(risk) + risk.hit_survival * keys * key_value();
        if (s.room > 25 && !s.effect(EffectKind::one_hit)) {
            double curse_value = random_effect_value(true);
            if (p.floor_specific_curses) {
                constexpr EffectKind kinds[]{EffectKind::gold_hangover, EffectKind::poison, EffectKind::broken_armor};
                const auto kind = kinds[stage - 1];
                const double strong = std::clamp(p.strong_effect + (s.has(Gem::explorer) ? .2 : 0), 0.0, 1.0);
                curse_value = (1 - strong) * marginal(effect_template(kind, false), true) +
                    strong * marginal(effect_template(kind, true), true);
            }
            value -= risk.room_survival * std::clamp(p.combat_curse_chance +
                (s.has(Gem::misadventurer) ? .1 : 0), 0.0, 1.0) * curse_value;
        }
        // Random new effects still use marginal utilities. In particular, an
        // instant random heal or refresh that displaces active poison/recovery
        // is not expanded into every possible exit-tick branch here.
        if (encounter == Encounter::shakes) value += risk.hit_survival * random_effect_value(false);
        if (s.has(Gem::kidney)) value += risk.hit_survival * .1 * random_effect_value(false);
        if (encounter == Encounter::valaraukar)
            value += risk.room_survival * marginal({EffectKind::recovery, .1, 3, Clock::room}, true);
        return value;
    }
    double combat(Encounter encounter, bool fleeing) const {
        if (policy.survival_aware || s.effect(EffectKind::recovery) || s.effect(EffectKind::poison))
            return survival_combat(encounter, fleeing);
        const double damage = magnitude(encounter, fleeing);
        if (encounter == Encounter::boss) return -injury_cost(damage);
        if (fleeing) return -(1 - escape()) * injury_cost(damage) +
            (s.has(Gem::pendant) ? escape() * .4 * key_value() : 0) -
            (s.has(Gem::pearl) ? escape() * .2 * random_effect_value(true) : 0);
        double keys = std::clamp(p.key_chance + (s.has(Gem::lodestone) ? .3 : 0) - (s.has(Gem::spying) ? .15 : 0), 0.0, 1.0);
        if (const auto* e = s.effect(EffectKind::key_moment)) keys = 2 * e->magnitude;
        double value = -injury_cost(damage);
        if (damage >= s.hp) return value;
        value += keys * key_value();
        if (s.room > 25 && !s.effect(EffectKind::one_hit)) value -= std::min(1.0, p.combat_curse_chance + (s.has(Gem::misadventurer) ? .1 : 0)) * random_effect_value(true);
        if (encounter == Encounter::pig) value += std::min(.6, 1 - s.hp + damage);
        if (encounter == Encounter::shakes) value += random_effect_value(false);
        if (encounter == Encounter::valaraukar) value += marginal({EffectKind::recovery, .1, 3, Clock::room}, true);
        return value;
    }
    bool recovery_covers_fight(Encounter encounter) const {
        const auto* recovery = s.effect(EffectKind::recovery);
        if (!recovery || encounter == Encounter::boss) return false;
        const auto damage = combat_damage(encounter, false);
        const auto* poison = s.effect(EffectKind::poison);
        const double healing = recovery->magnitude * (s.has(Gem::rusty) ? 1.4 : 1);
        // Spend healing on earning keys when it covers the expected loss, but
        // never rely on an exit heal to survive the incoming hit or poison.
        return s.hp > damage.maximum() &&
            damage.mean() + (poison ? poison->magnitude : 0) <= healing &&
            damage.after_room(s.hp, poison ? poison->magnitude : 0, healing).room_survival >= 1;
    }
    bool safe_free_escape() const {
        const auto* poison = s.effect(EffectKind::poison);
        const auto* recovery = s.effect(EffectKind::recovery);
        return escape() >= 1 && DamageRisk{{0, 0, 0}, p.damage_distribution}.after_room(
            s.hp, poison ? poison->magnitude : 0,
            recovery ? recovery->magnitude * (s.has(Gem::rusty) ? 1.4 : 1) : 0).room_survival >= 1;
    }
    double barrel_value() const {
        const double blessing = barrel_blessing_chance(p, s);
        return blessing * random_effect_value(false, true) - (1 - blessing) * random_effect_value(true, true);
    }
    double container_blessing_value(bool ordinary) const {
        if (ordinary && p.container_joint_weights) {
            const auto& weights = s.has(Gem::greed) && p.container_joint_greed_weights ?
                *p.container_joint_greed_weights : *p.container_joint_weights;
            double blessing = 0;
            for (std::size_t i = 1; i < weights.size(); i += 2) blessing += weights[i];
            return blessing / std::accumulate(weights.begin(), weights.end(), 0.0) * random_effect_value(false);
        }
        return (s.has(Gem::greed) || !p.container_blessing_requires_greed) ?
            p.container_weights[0] / std::accumulate(p.container_weights.begin(), p.container_weights.end(), 0.0) * random_effect_value(false) : 0;
    }
    double encounter_value(Encounter e) const {
        if (is_enemy(e)) {
            const double value = e == Encounter::boss ? combat(e, false) : std::max(combat(e, false), combat(e, true));
            return is_optional(e) ? std::max(0.0, value) : value;
        }
        switch (e) {
        case Encounter::skeleton: return std::max(0.0, p.skeleton_wakes * std::max(combat(Encounter::monster, false), combat(Encounter::monster, true)) +
            (1 - p.skeleton_wakes) * container_blessing_value(false));
        case Encounter::crate: case Encounter::silver: case Encounter::bronze:
            return container_blessing_value(true);
        case Encounter::barrel: return std::max(0.0, barrel_value());
        case Encounter::fountain: return std::min(1 - s.hp, .25 * (s.has(Gem::rusty) ? 1.4 : 1));
        case Encounter::cleansing_fountain: {
            double value = encounter_value(Encounter::fountain);
            for (int i = 0; i < s.curses.size; ++i) value += effect_value(s.curses.slots[static_cast<std::size_t>(i)]);
            return value;
        }
        case Encounter::narrator: return std::max(0.0, encounter_value(Encounter::fountain) + random_effect_value(false));
        case Encounter::lava: return -injury_cost(.1);
        case Encounter::wishing_well: return std::max(0.0, .5 * random_effect_value(false));
        case Encounter::rps: return std::max(0.0, (random_effect_value(false) - injury_cost(.1) - random_effect_value(true)) / 3);
        case Encounter::wheel: return std::max(0.0, (random_effect_value(false) - random_effect_value(true)) / 5);
        case Encounter::spider_legs: case Encounter::spider_head: case Encounter::spider_full: {
            const auto i = ix(e) - ix(Encounter::spider_legs);
            const double reward = i == 0 ? 1 : (i == 1 ? 2 : 5);
            return std::max(0.0, p.spider_success[i] * reward * key_value() -
                (1 - p.spider_success[i]) * (p.spider_bite_damage + marginal({EffectKind::poison, .05, 5, Clock::room}, true)));
        }
        case Encounter::rainbow: return std::max(0.0, random_effect_value(false) - injury_cost(.2));
        default: return 0;
        }
    }
    double contents(const std::array<double, ix(Encounter::count)>& weights, bool exclude_enemies = false) const {
        double sum = 0, total = 0;
        for (std::size_t i = 0; i < weights.size(); ++i) if (weights[i] > 0 && !(exclude_enemies && is_enemy(static_cast<Encounter>(i)))) {
            total += weights[i]; sum += weights[i] * encounter_value(static_cast<Encounter>(i));
        }
        return total > 0 ? sum / total : 0;
    }
    double shop_value() const {
        double sum = 0, total = 0;
        const bool free = p.free_first_level_shops && s.run_number == 1 && s.room < 25;
        constexpr int weak[]{1,2,3,2,1,1,1,1};
        for (std::size_t i = 0; i < 8; ++i) for (bool strong : {false, true}) {
            const int price = free ? 0 : (strong ? (i >= 6 ? 3 : weak[i] * 2) : weak[i]);
            const double weight = p.shop_blessing_weights[i] * (strong ? p.shop_strong_effect : 1 - p.shop_strong_effect);
            total += weight;
            if (s.keys >= price) sum += weight * std::max(0.0, marginal(effect_template(static_cast<EffectKind>(i), strong), true) - price * key_value());
        }
        return total > 0 ? 1.4 * sum / total : 0; // Approximate value of the better of two offers.
    }
    double door_value(int index) const {
        const auto d = s.doors[static_cast<std::size_t>(index)];
        double value = 0;
        switch (d.kind) {
        case Door::boss: return combat(Encounter::boss, false);
        case Door::monster: value = encounter_value(Encounter::monster); break;
        case Door::mystery: {
            const auto other = s.doors[static_cast<std::size_t>(1 - index)].kind;
            value = contents(p.mystery_for_alternative(other), other == Door::monster);
            break;
        }
        case Door::unlocked: value = contents(p.mystery_weights); break;
        case Door::locked: case Door::double_locked: value = contents(p.locked_weights); break;
        case Door::epic: break;
        case Door::golden: value = contents(p.golden_weights); break;
        case Door::shop: value = shop_value(); break;
        case Door::blessing: value = random_effect_value(false) + encounter_value(Encounter::monster); break;
        case Door::cursed: value = -random_effect_value(true); break;
        case Door::sacrifice: value = -injury_cost(.12 * (s.has(Gem::blood) ? .6 : 1)); break;
        case Door::destiny: {
            const double total = std::accumulate(p.destiny_reward_weights.begin(), p.destiny_reward_weights.end(), 0.0);
            value = contents(p.destiny_weights) + (p.destiny_reward_weights[0] * random_effect_value(false) -
                p.destiny_reward_weights[5] * random_effect_value(true)) / total;
            break;
        }
        case Door::trial: value = encounter_value(Encounter::trial_monster); break;
        case Door::exit_trial: break;
        default: value = -.002; break; // Preserve external resource stock when equally safe.
        }
        if ((d.kind == Door::locked || d.kind == Door::double_locked || d.kind == Door::epic) && s.has(Gem::deceit))
            value = (1 - p.hidden_monster_chance) * value + p.hidden_monster_chance * encounter_value(Encounter::monster);
        value -= game.door_cost(d.kind) * key_value();
        if (policy.practical_tactics && game.door_cost(d.kind) > 0) {
            // Spend when the health saved justifies it; these are preferences,
            // never a prohibition on buying a safer route.
            if (stage < 2) value -= .035 * game.door_cost(d.kind);
            if (s.effect(EffectKind::hard_lock)) value -= .025 * game.door_cost(d.kind);
        }
        if (!s.shops_seen[stage] && game.door_cost(d.kind) > 0 &&
            s.hp > combat_damage(Encounter::monster, false).maximum()) {
            const int spent_below_reserve = std::max(0, shop_reserve() - (s.keys - game.door_cost(d.kind))) -
                std::max(0, shop_reserve() - s.keys);
            value -= .12 * spent_below_reserve;
        }
        if (d.trap && !s.effect(EffectKind::disarm)) value -= d.cursed_trap || s.has(Gem::gambler) ? random_effect_value(true) : injury_cost(.1);
        return value;
    }
    Action heal(double target, bool must_reach = false) const {
        int budget = game.limits().budget - s.mushrooms;
        budget -= std::max(0, std::min(policy.shop_mushroom_reserve, game.limits().budget) - s.reroll_mushrooms);
        if (policy.recovery_budget >= 0) budget = std::min(budget, std::max(0, policy.recovery_budget - s.recovery_mushrooms));
        const bool full = game.legal({ActionKind::heal_full}) && game.full_price() <= budget;
        bool step = game.legal({ActionKind::heal_step}) && game.step_price() <= budget;
        if (must_reach) {
            const int count = std::max(0, static_cast<int>(std::ceil((target - s.hp - 1e-12) / .2)));
            int cost = 0;
            for (int i = 0; i < count; ++i) cost += 10 + 5 * (s.paid_steps + i);
            if (cost > budget) step = false;
        }
        if (s.room >= 99 && step && s.hp + .2 >= target && (!full || game.step_price() < game.full_price())) return {ActionKind::heal_step};
        if (full && (!step || game.full_price() / (1 - s.hp) <= game.step_price() / std::min(.2, 1 - s.hp))) return {ActionKind::heal_full};
        if (step) return {ActionKind::heal_step};
        if (full) return {ActionKind::heal_full};
        return {ActionKind::skip}; // Sentinel: caller falls back to recovery/encounter action.
    }
    bool trial_is_worth_attempting() const {
        // Assess the NEXT tier only. Effects tick after every arena enemy, so
        // a temporary escape bonus must be reconsidered before continuing.
        const auto* one_hit = s.effect(EffectKind::one_hit);
        if (one_hit && safe_exit_after(0)) return true;
        if (safe_free_escape()) return true;
        if (!(s.has(Gem::rabbit) || s.effect(EffectKind::escape))) return false;
        const auto damage = combat_damage(Encounter::trial_monster, true);
        return escape() >= .8 - 1e-12 && s.hp > damage.maximum() && safe_exit_after(damage.maximum());
    }
    bool safe_exit_after(double damage) const {
        const auto* recovery = s.effect(EffectKind::recovery);
        const auto* poison = s.effect(EffectKind::poison);
        return s.hp > damage && std::min(1.0, s.hp - damage +
            (recovery ? recovery->magnitude * (s.has(Gem::rusty) ? 1.4 : 1) : 0)) >
            (poison ? poison->magnitude : 0) + 1e-12;
    }
    double curse_trade_value(const Offer& offer) const {
        const auto& curse = offer.effect;
        const int duration = curse.remaining + (s.has(Gem::moonstone) ? 1 : 0);
        double harm = std::max(0.0, marginal(curse, true));
        const auto* one_hit = s.effect(EffectKind::one_hit);
        const auto* recovery = s.effect(EffectKind::recovery);
        if (curse.kind == EffectKind::clumsy || curse.kind == EffectKind::broken_armor) {
            // Clumsy affects escape, not fighting. One Hit cancels ordinary
            // attacks; Recovery can cover them only if the hit is survivable.
            int covered = one_hit ? std::min(duration, std::max(0, one_hit->remaining - 1)) : 0;
            const double hit = combat_damage(Encounter::monster, false).maximum() +
                (curse.kind == EffectKind::broken_armor ? p.monster_damage[stage].high * curse.magnitude : 0);
            const double heal = recovery ? recovery->magnitude * (s.has(Gem::rusty) ? 1.4 : 1) : 0;
            if (recovery && s.hp > hit && heal >= hit + (s.effect(EffectKind::poison) ? s.effect(EffectKind::poison)->magnitude : 0))
                covered = std::max(covered, std::min(duration, std::max(0, recovery->remaining - 1)));
            harm *= 1.0 - static_cast<double>(covered) / std::max(1, duration);
        }
        if (curse.kind == EffectKind::poison) {
            const double heal = recovery ? recovery->magnitude * (s.has(Gem::rusty) ? 1.4 : 1) : 0;
            if (std::min(1.0, s.hp + heal + (recovery && recovery->remaining > 1 ? heal : 0)) <= curse.magnitude + 1e-12) {
                // New poison starts in the following room. Even a safe exit
                // there would kill without another heal, clear effects and keep keys.
                // Charge the recovery time and lost blessings instead of the
                // whole poison duration, but do not spend the event's last hours.
                if (game.limits().deadline_hours - s.elapsed_hours <= policy.revive_hp * p.recovery_hours + 1)
                    return -1;
                harm = policy.revive_hp;
                for (int i = 0; i < s.blessings.size; ++i) harm += effect_value(s.blessings.slots[static_cast<std::size_t>(i)]);
            }
        }
        return offer.keys * key_value() - harm;
    }
};
}
Action Policy::choose_strategic(const Game& game) const {
    const auto& s = game.state();
    const Planner plan{*this, game, s, game.profile(), static_cast<std::size_t>(std::clamp((s.room - 1) / 25, 0, 3))};
    Policy fallback = *this; fallback.strategy = Strategy::baseline;
    // A fallback must remain the legacy handler, rather than dispatching back
    // into this planner because baseline combat/door modifiers are enabled.
    fallback.survival_aware = false; fallback.early_key_target = 0;
    fallback.practical_tactics = false; fallback.patient_boss_hp = 0;
    if (s.phase == Phase::doors) {
        if (practical_tactics) for (int i = 0; i < 2; ++i) {
            const auto& d = s.doors[static_cast<std::size_t>(i)];
            if (!game.legal({ActionKind::choose_door, i})) continue;
            if (d.kind == Door::trial && !d.trap && plan.trial_is_worth_attempting())
                return {ActionKind::choose_door, i};
            // At room 99, keys have no later shop to fund. A normal locked
            // room avoids compulsory lava damage if the boss HP is ready.
            if (s.room == 99 && d.kind == Door::locked && !d.trap && !s.has(Gem::deceit) &&
                s.doors[static_cast<std::size_t>(1-i)].kind == Door::golden &&
                !s.effect(EffectKind::hard_lock) && s.hp > game.profile().boss_damage[3].high +
                    (s.effect(EffectKind::poison) ? s.effect(EffectKind::poison)->magnitude : 0))
                return {ActionKind::choose_door, i};
        }
        for (int i = 0; i < 2; ++i) {
            const auto& door = s.doors[static_cast<std::size_t>(i)];
            const auto& other = s.doors[static_cast<std::size_t>(1 - i)];
            if (door.kind != Door::monster || !game.legal({ActionKind::choose_door, i})) continue;
            const bool safe_entry = !door.trap || s.effect(EffectKind::disarm);
            const bool covered = plan.recovery_covers_fight(Encounter::monster);
            if (safe_entry && game.door_cost(other.kind) > 0 &&
                (covered || s.effect(EffectKind::one_hit) || plan.safe_free_escape()))
                return {ActionKind::choose_door, i};
            if (other.kind == Door::mystery && game.legal({ActionKind::choose_door, 1 - i})) {
                if (safe_entry && (covered || (other.trap && plan.safe_free_escape())))
                    return {ActionKind::choose_door, i};
                if (safe_entry && other.trap && plan.stage == 0 &&
                    s.hp > plan.combat_damage(Encounter::monster, false).maximum())
                    return {ActionKind::choose_door, i};
                if (!s.effect(EffectKind::one_hit) && !other.trap) return {ActionKind::choose_door, 1 - i};
            }
        }
        int best = -1; double value = -std::numeric_limits<double>::infinity();
        for (int i = 0; i < 2; ++i) if (game.legal({ActionKind::choose_door, i})) {
            const double candidate = plan.door_value(i);
            if (candidate > value) { best = i; value = candidate; }
        }
        if (best >= 0) return {ActionKind::choose_door, best};
    } else if (s.phase == Phase::encounter) {
        if (is_enemy(s.encounter)) {
            if (practical_tactics && s.encounter == Encounter::trial_monster &&
                !s.effect(EffectKind::one_hit) && plan.safe_free_escape()) return {ActionKind::flee};
            const bool fight = !game.legal({ActionKind::flee}) || s.effect(EffectKind::one_hit) ||
                plan.recovery_covers_fight(s.encounter) ||
                (plan.escape() < 1 && plan.combat(s.encounter, false) >= plan.combat(s.encounter, true));
            const double value = plan.combat(s.encounter, !fight);
            if (is_optional(s.encounter) && value <= 0) return {ActionKind::skip};
            if (game.damage_ceiling(s.encounter == Encounter::boss) >= s.hp && (!fight ? plan.escape() < .9 : plan.magnitude(s.encounter, false) > 0)) {
                const auto heal_action = plan.heal(std::min(1.0, game.damage_ceiling(s.encounter == Encounter::boss) + .001), true);
                if (heal_action.kind != ActionKind::skip) return heal_action;
            }
            return {fight ? ActionKind::fight : ActionKind::flee};
        }
        if (s.encounter == Encounter::barrel && barrels != BarrelPolicy::adaptive) return fallback.choose(game);
        if (s.encounter == Encounter::barrel) return {plan.barrel_value() > 0 ? ActionKind::interact : ActionKind::skip};
        if (s.encounter == Encounter::rps) return {plan.encounter_value(s.encounter) > 0 ? ActionKind::interact : ActionKind::skip};
        if (s.encounter == Encounter::skeleton && (plan.safe_free_escape() ||
            s.effect(EffectKind::one_hit) || plan.recovery_covers_fight(Encounter::monster)))
            return {ActionKind::interact};
        if (s.encounter == Encounter::skeleton || s.encounter == Encounter::spider_legs || s.encounter == Encounter::spider_head ||
            s.encounter == Encounter::spider_full || s.encounter == Encounter::wheel || s.encounter == Encounter::rainbow)
            return {plan.encounter_value(s.encounter) > 0 ? ActionKind::interact : ActionKind::skip};
    } else if (s.phase == Phase::curse_shop && practical_tactics) {
        int best = -1; double value = 0;
        for (int i = 0; i < 2; ++i) if (game.legal({ActionKind::buy, i})) {
            const double gain = plan.curse_trade_value(s.offers[static_cast<std::size_t>(i)]);
            if (gain > value) { value = gain; best = i; }
        }
        return best >= 0 ? Action{ActionKind::buy, best} : Action{ActionKind::skip};
    } else if (s.phase == Phase::shop && shop == ShopPolicy::adaptive) {
        int best = -1; double value = 0;
        for (int i = 0; i < 2; ++i) if (game.legal({ActionKind::buy, i})) {
            const auto& offer = s.offers[static_cast<std::size_t>(i)];
            const double gain = plan.marginal(offer.effect, true) - offer.keys * plan.key_value();
            if (gain > value) { best = i; value = gain; }
        }
        if (practical_tactics && best >= 0 && s.room < 98) {
            const auto& e = s.offers[static_cast<std::size_t>(best)].effect;
            if (e.kind == EffectKind::one_hit || e.kind == EffectKind::recovery ||
                (e.kind == EffectKind::escape && plan.escape() < 1) ||
                (e.kind == EffectKind::lockpick && e.remaining >= 4 && s.hp >= .5))
                return {ActionKind::buy, best};
        }
        if (shop_allowance > 0) {
            const auto earned = static_cast<std::int64_t>(shop_allowance) * s.reroll_shop_visits;
            const auto bank_remaining = std::max<std::int64_t>(0, earned - s.reroll_bank_used);
            const double allowance_at_entry = static_cast<double>(bank_remaining + s.shop_rerolls);
            const double used_fraction = allowance_at_entry > 0 ? s.shop_rerolls / allowance_at_entry : 1;
            // Start selective, then accept progressively smaller positive gains.
            // A useful current offer may end the visit before the allowance is spent.
            const double target = std::max(.025, 2 * plan.shop_value()) * (1 - std::clamp(used_fraction, 0.0, 1.0));
            if (bank_remaining > 0 && s.keys >= plan.shop_reserve() && game.legal({ActionKind::reroll}) && value < target)
                return {ActionKind::reroll};
            return best >= 0 ? Action{ActionKind::buy, best} : Action{ActionKind::skip};
        }
        if (s.shop_rerolls < reroll_limit && game.legal({ActionKind::reroll}) && plan.shop_value() > value + .04)
            return {ActionKind::reroll};
        return best >= 0 ? Action{ActionKind::buy, best} : Action{ActionKind::skip};
    } else if (s.phase == Phase::recovery) {
        if (patient_boss_hp > 0 && s.room == 100 && s.paid_steps >= 2) {
            const double wait = std::max(0.0, patient_boss_hp - s.hp) * game.profile().recovery_hours;
            if (wait + 2 * game.profile().action_seconds / 3600 < game.limits().deadline_hours - s.elapsed_hours)
                return s.hp >= patient_boss_hp - 1e-12 ? Action{ActionKind::reenter} : Action{ActionKind::wait, 0, wait};
        }
        double target = revive_hp;
        if (safe_boss_reentry && s.room % 25 == 0) target = std::max(target, std::min(1.0, game.damage_ceiling(true) + .001));
        if (safe_boss_reentry && s.room >= 97 && s.room < 100) {
            const int rooms = std::min(2, 100 - s.room);
            const double ordinary = std::max(.1, plan.combat_damage(Encounter::monster, false).maximum());
            target = std::max(target, std::min(1.0, game.profile().boss_damage[3].high + rooms * ordinary + .001));
        }
        fallback.revive_hp = target;
        if (s.hp < target - 1e-12) {
            const auto action = plan.heal(target);
            if (action.kind != ActionKind::skip) return action;
        }
    }
    auto action = fallback.choose(game);
    if (action.kind == ActionKind::wait)
        action.value = std::min(action.value, game.limits().deadline_hours - s.elapsed_hours);
    return action;
}
}
