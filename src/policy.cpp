#include "sfld/policy.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace sfld {
std::string_view name(BarrelPolicy policy) {
    switch (policy) {
    case BarrelPolicy::skip: return "skip";
    case BarrelPolicy::always: return "always";
    case BarrelPolicy::low_hp: return "low-hp";
    case BarrelPolicy::adaptive: return "adaptive";
    }
    return "invalid";
}
BarrelPolicy barrel_policy_from(std::string_view value) {
    for (int i = 0; i < 4; ++i) if (name(static_cast<BarrelPolicy>(i)) == value) return static_cast<BarrelPolicy>(i);
    throw std::invalid_argument("Unknown barrel policy: " + std::string(value));
}
void Policy::validate() const {
    if (!std::isfinite(barrel_hp) || barrel_hp < 0 || barrel_hp > 1 || !std::isfinite(revive_hp) || revive_hp < .2 || revive_hp > 1 ||
        !std::isfinite(login_interval_hours) || login_interval_hours < 0 || login_interval_hours > 1000000 ||
        reroll_limit < 0 || reroll_limit > 1000000)
        throw std::invalid_argument("Invalid policy thresholds");
}
double Policy::effect_value(const State& s, const Effect& e) const {
    const double rooms = static_cast<double>(std::min(e.remaining, 100 - s.room));
    switch (e.kind) {
    case EffectKind::elixir: return std::min(1 - s.hp, e.magnitude);
    case EffectKind::recovery: return std::min(1.0, e.magnitude * rooms) * (s.hp < .8 ? 1 : .45);
    case EffectKind::one_hit: return .1 * rooms;
    case EffectKind::escape: return .055 * rooms;
    case EffectKind::disarm: return .065 * rooms;
    case EffectKind::lockpick: return .09 * rooms;
    case EffectKind::key_moment: return s.room < 25 ? .04 * rooms : .015 * rooms;
    default: return 0;
    }
}
Action Policy::choose(const Game& game) const {
    const auto& s = game.state();
    switch (s.phase) {
    case Phase::doors: {
        int best = -1;
        double score = -std::numeric_limits<double>::infinity();
        for (int i = 0; i < 2; ++i) {
            const auto door = s.doors[static_cast<std::size_t>(i)];
            if (!game.available(door)) continue;
            double value = 0;
            switch (door.kind) {
            case Door::boss: value = 100; break;
            case Door::exit_trial: value = 110; break;
            case Door::golden: value = 90; break;
            case Door::shop: value = s.keys > 0 || (s.run_number == 1 && s.room < 25) ? 85 : 50; break;
            case Door::blessing: value = 75; break;
            case Door::epic: case Door::locked: case Door::double_locked: value = 70 - 6 * game.door_cost(door.kind); break;
            case Door::unlocked: value = 60; break;
            case Door::monster: value = (s.room < 25 && s.keys < 10) || s.effect(EffectKind::one_hit) ? 80 : 10; break;
            case Door::mystery: value = 40; break;
            case Door::cursed: value = s.hp < .3 ? 40 : 20; break;
            case Door::sacrifice: value = s.hp > .2 ? 25 : -100; break;
            case Door::destiny: value = s.hp < .4 ? 55 : 35; break;
            case Door::trial: value = -100; break;
            default: value = 65; break;
            }
            if (door.trap && !s.effect(EffectKind::disarm)) value -= s.has(Gem::gambler) || door.cursed_trap ? 15 : 50;
            if (value > score) { score = value; best = i; }
        }
        if (best < 0) throw std::logic_error("No legal door at room " + std::to_string(s.room) +
            " (" + std::string(name(s.doors[0].kind)) + ", " + std::string(name(s.doors[1].kind)) +
            "; keys=" + std::to_string(s.keys) + ")");
        return {ActionKind::choose_door, best};
    }
    case Phase::encounter:
        if (s.encounter == Encounter::mimic && !s.effect(EffectKind::one_hit)) return {ActionKind::skip};
        if (is_enemy(s.encounter)) {
            const bool fight = s.encounter == Encounter::boss || s.effect(EffectKind::one_hit) ||
                (s.room < 25 && s.keys < 10) || s.has(Gem::bull) || s.encounter == Encounter::pig;
            return {fight ? ActionKind::fight : ActionKind::flee};
        }
        if (s.encounter == Encounter::barrel) {
            bool open = barrels == BarrelPolicy::always;
            if (barrels == BarrelPolicy::low_hp) open = s.hp <= barrel_hp && !s.has(Gem::time_traveler);
            if (barrels == BarrelPolicy::adaptive)
                open = !s.has(Gem::time_traveler) && !s.effect(EffectKind::one_hit) &&
                    (s.hp <= barrel_hp || (s.has(Gem::gambler) && s.hp < .8));
            return {open ? ActionKind::interact : ActionKind::skip};
        }
        if (s.encounter == Encounter::sacrifice_chest || s.encounter == Encounter::cursed_chest ||
            s.encounter == Encounter::rainbow || s.encounter == Encounter::spider_full ||
            (s.encounter == Encounter::skeleton && (s.room > 25 || s.keys >= 10)) ||
            (s.encounter == Encounter::locked_sarcophagus && !game.legal({ActionKind::interact})))
            return {ActionKind::skip};
        if (s.encounter == Encounter::rps) return {s.hp <= barrel_hp ? ActionKind::rps : ActionKind::skip, 0};
        if (s.encounter == Encounter::wheel && s.hp > barrel_hp) return {ActionKind::skip};
        return {ActionKind::interact};
    case Phase::shop: {
        int best = -1;
        double value = 0;
        for (int i = 0; i < 2; ++i) {
            const auto& offer = s.offers[static_cast<std::size_t>(i)];
            if (!game.legal({ActionKind::buy, i})) continue;
            double score = effect_value(s, offer.effect) - .025 * offer.keys;
            if (const auto* active = s.effect(offer.effect.kind)) score -= effect_value(s, *active);
            if (score > value) { best = i; value = score; }
        }
        if (best >= 0) return {ActionKind::buy, best};
        if (s.shop_rerolls < reroll_limit && game.legal({ActionKind::reroll})) return {ActionKind::reroll};
        return {ActionKind::skip};
    }
    case Phase::curse_shop:
        if (s.keys < 2) for (int i = 0; i < 2; ++i)
            if (s.offers[static_cast<std::size_t>(i)].effect.kind == EffectKind::gold_hangover) return {ActionKind::buy, i};
        return {ActionKind::skip};
    case Phase::gems: {
        // Baseline heuristic; this ordering is not a statistically validated optimum.
        constexpr std::array priority{Gem::rabbit, Gem::greasy, Gem::moonstone, Gem::pendant, Gem::spying,
            Gem::gambler, Gem::greed, Gem::hero, Gem::time_traveler, Gem::thirsty, Gem::pearl, Gem::blood,
            Gem::explorer, Gem::misadventurer, Gem::devil, Gem::deceit, Gem::lodestone, Gem::bull,
            Gem::hick, Gem::old_sacrifice, Gem::kidney, Gem::masochist, Gem::rusty};
        int best = 0;
        std::ptrdiff_t rank = 1000;
        for (int i = 0; i < 3; ++i) {
            const auto gem = s.gem_offers[static_cast<std::size_t>(i)];
            const auto r = preferred_gem == gem ? -1 : std::find(priority.begin(), priority.end(), gem) - priority.begin();
            if (r < rank) { rank = r; best = i; }
        }
        return {ActionKind::choose_gem, best};
    }
    case Phase::recovery: {
        double target = revive_hp;
        if (safe_boss_reentry && s.room % 25 == 0) target = std::max(target, std::min(1.0, game.damage_ceiling(true) + .001));
        if (s.hp >= target - 1e-12) return {ActionKind::reenter};
        const int remaining_budget = game.limits().budget - s.mushrooms;
        const int steps = static_cast<int>(std::ceil((target - s.hp - 1e-12) / .2));
        int cost = 0;
        for (int n = 0; n < steps; ++n) cost += s.paid_steps + n == 0 ? 10 : (s.paid_steps + n == 1 ? 15 : 20);
        if (game.full_price() <= remaining_budget && game.full_price() <= cost) return {ActionKind::heal_full};
        if (game.legal({ActionKind::heal_step})) return {ActionKind::heal_step};
        if (game.legal({ActionKind::heal_full})) return {ActionKind::heal_full};
        double wait = (target - s.hp) * game.profile().recovery_hours;
        if (login_interval_hours > 0)
            wait = std::ceil((s.elapsed_hours + wait) / login_interval_hours) * login_interval_hours - s.elapsed_hours;
        return {ActionKind::wait, 0, std::max(wait, 1e-9)};
    }
    case Phase::complete: throw std::logic_error("Complete run has no next action");
    }
    throw std::logic_error("Unhandled phase");
}
}
