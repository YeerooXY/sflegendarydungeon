#include "sfld/rule_policy.hpp"
#include "sfld/damage_risk.hpp"
#include <algorithm>
#include <charconv>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <limits>
#include <set>
#include <sstream>
#include <stdexcept>

namespace sfld {
namespace {
std::size_t stage_of(const State& s) { return static_cast<std::size_t>(std::clamp((s.room - 1) / 25, 0, 3)); }
double escape_chance(const Game& game) {
    const auto& s = game.state();
    double chance = game.profile().escape_chance + (s.has(Gem::rabbit) ? .4 : 0) +
        (s.has(Gem::moonstone) ? .2 : 0) - (s.has(Gem::bull) ? .3 : 0);
    if (const auto* effect = s.effect(EffectKind::escape)) chance += effect->magnitude;
    if (const auto* effect = s.effect(EffectKind::clumsy)) chance -= effect->magnitude;
    return std::clamp(chance, 0.0, 1.0);
}
DamageRisk damage(const Game& game, Encounter enemy, bool flee) {
    const auto& s = game.state(); const auto& p = game.profile(); const auto stage = stage_of(s);
    if (enemy == Encounter::boss) return {p.boss_damage[stage], p.damage_distribution};
    if (!flee && s.effect(EffectKind::one_hit)) return {{0, 0, 0}, p.damage_distribution};
    auto range = flee ? p.escape_damage[stage] : p.monster_damage[stage];
    double multiplier = 1;
    if (enemy == Encounter::undead || enemy == Encounter::tube || enemy == Encounter::beta) multiplier *= .5;
    if (enemy == Encounter::trial_monster)
        multiplier *= p.trial_multipliers[static_cast<std::size_t>(std::clamp(s.trial_depth, 0, 4))];
    if (enemy == Encounter::shakes) range = {.3, .3, .3};
    if (enemy == Encounter::valaraukar) range = {.6, .6, .6};
    if (enemy == Encounter::pig) range = {.4, .4, .4};
    multiplier *= game.battle_multiplier(flee);
    return {{range.low * multiplier, range.high * multiplier, range.mean * multiplier}, p.damage_distribution};
}
DamageRisk::RoomResult after_combat(const Game& game, Encounter enemy, bool flee, bool successful_escape = false) {
    const auto& s = game.state();
    const auto law = successful_escape ? DamageRisk{{0, 0, 0}, game.profile().damage_distribution} : damage(game, enemy, flee);
    if (enemy == Encounter::boss) return law.after_room(s.hp);
    const auto* poison = s.effect(EffectKind::poison); const auto* recovery = s.effect(EffectKind::recovery);
    const double healing = s.has(Gem::rusty) ? 1.4 : 1;
    return law.after_room(s.hp, poison ? poison->magnitude : 0, recovery ? recovery->magnitude * healing : 0,
        enemy == Encounter::pig && !flee ? .6 * healing : 0);
}
double death_risk(const Game& game, Encounter enemy, bool flee) {
    const double failed = 1 - after_combat(game, enemy, flee).room_survival;
    if (!flee) return failed;
    const double escaped = escape_chance(game);
    return (1 - escaped) * failed + escaped * (1 - after_combat(game, enemy, true, true).room_survival);
}
bool fight_for_keys(const RulePolicy& rule, const Game& game, Encounter enemy) {
    const auto& s = game.state();
    if (enemy == Encounter::boss || s.effect(EffectKind::one_hit)) return true;
    if (s.hp < rule.min_fight_hp ||
        death_risk(game, enemy, false) > rule.max_fight_death_risk + 1e-12) return false;
    if (rule.fight_when_cheaper) {
        const double escaped = escape_chance(game);
        const double flee_hp = (1 - escaped) * after_combat(game, enemy, true).expected_hp +
            escaped * after_combat(game, enemy, true, true).expected_hp;
        if (after_combat(game, enemy, false).expected_hp > flee_hp + 1e-12) return true;
    }
    if (escape_chance(game) >= rule.fight_escape_cutoff) return false;
    return s.keys < rule.fight_keys[stage_of(s)];
}
int keys_to_keep(const RulePolicy& rule, const Game& game) {
    const auto& s = game.state(); const auto& p = game.profile(); const auto stage = stage_of(s);
    int reserve = rule.door_reserve[stage];
    if (rule.shop_allowance <= 0 || game.limits().budget <= s.mushrooms || s.shops_seen[stage]) return reserve;
    const int earliest = stage == 0 && s.run_number == 1 ? 5 :
        p.shop_windows ? static_cast<int>((*p.shop_windows)[stage].low) : std::max(6, static_cast<int>(stage * 25 + 1));
    if (s.room + rule.shop_lookahead >= earliest) reserve = std::max(reserve, rule.shop_key_reserve);
    return reserve;
}
double immediate_door_harm(const RulePolicy& rule, const Game& game, int side) {
    const auto& s = game.state(); const auto& p = game.profile(); const auto door = s.doors[static_cast<std::size_t>(side)];
    double harm = door.trap && !door.cursed_trap && !s.has(Gem::gambler) && !s.effect(EffectKind::disarm)
        ? .1 * (s.has(Gem::masochist) ? .8 : 1) : 0;
    if (door.kind == Door::sacrifice) harm += .12 * (s.has(Gem::blood) ? .6 : 1);
    const auto enemy_harm = [&](Encounter enemy) {
        const bool flee = enemy != Encounter::boss && !fight_for_keys(rule, game, enemy);
        return damage(game, enemy, flee).mean() * (flee ? 1 - escape_chance(game) : 1);
    };
    if (door.kind == Door::monster) return harm + enemy_harm(Encounter::monster);
    if (door.kind == Door::boss) return harm + enemy_harm(Encounter::boss);
    if (door.kind == Door::blessing) return harm + enemy_harm(Encounter::monster);
    const auto* pool = door.kind == Door::mystery ? &p.mystery_for_alternative(s.doors[static_cast<std::size_t>(1-side)].kind) :
        door.kind == Door::unlocked ? &p.mystery_weights : door.kind == Door::destiny ? &p.destiny_weights : nullptr;
    if (pool) {
        const bool exclude = door.kind == Door::mystery && s.doors[static_cast<std::size_t>(1-side)].kind == Door::monster;
        double total = 0, loss = 0;
        for (std::size_t i = 0; i < pool->size(); ++i) {
            const auto encounter = static_cast<Encounter>(i);
            if (exclude && is_enemy(encounter)) continue;
            total += (*pool)[i];
            if (is_enemy(encounter) && !is_optional(encounter)) loss += (*pool)[i] * enemy_harm(encounter);
        }
        if (total > 0) harm += loss / total;
    }
    if (s.has(Gem::deceit) && (door.kind == Door::locked || door.kind == Door::double_locked || door.kind == Door::epic))
        harm += p.hidden_monster_chance * enemy_harm(Encounter::monster);
    return harm;
}
Action choose_doors(const RulePolicy& rule, const Game& game) {
    const auto& s = game.state();
    int best = -1; double best_score = -std::numeric_limits<double>::infinity();
    const int reserve = keys_to_keep(rule, game);
    for (int side = 0; side < 2; ++side) if (game.legal({ActionKind::choose_door, side})) {
        const auto door = s.doors[static_cast<std::size_t>(side)];
        double score = 0;
        switch (door.kind) {
        case Door::shop: case Door::boss: case Door::exit_trial: score = 1000; break;
        case Door::golden: score = 100; break;
        case Door::unlocked: score = 85; break;
        case Door::locked: case Door::epic: score = 80; break;
        case Door::double_locked: score = 75; break;
        case Door::blessing: score = 80; break;
        case Door::destiny: score = 70; break;
        case Door::mystery: score = 60; break;
        case Door::monster: score = s.effect(EffectKind::one_hit) ? 110 : fight_for_keys(rule, game, Encounter::monster) ? 95 : 30; break;
        case Door::cursed: score = 40; break;
        case Door::sacrifice: score = 35; break;
        case Door::trial: score = -100; break;
        default: score = 65; break; // Hungry doors preserve HP but consume an external resource.
        }
        if (rule.guaranteed_escape_door_preference && door.kind == Door::monster && !door.trap &&
            !s.effect(EffectKind::one_hit) && escape_chance(game) >= 1)
            score = 90; // Below golden/safe mystery, above costly doors; no hidden room information.
        const int cost = game.door_cost(door.kind);
        if (cost > 0 && s.keys - cost < reserve) score -= rule.door_mode == 1 ? 100 : rule.door_mode == 2 ? 15 : 70;
        else if (cost > 0 && rule.door_mode == 1) score -= 30;
        const bool safe_mystery = door.kind == Door::mystery && s.doors[static_cast<std::size_t>(1-side)].kind == Door::monster;
        const bool damaging_trap = door.trap && !door.cursed_trap && !s.has(Gem::gambler) && !s.effect(EffectKind::disarm);
        if (safe_mystery && !s.effect(EffectKind::one_hit) && !damaging_trap) score = 200;
        const double harm = immediate_door_harm(rule, game, side);
        if (rule.door_mode == 2) score -= 300 * harm;
        if (door.trap && !s.effect(EffectKind::disarm) && game.legal({ActionKind::choose_door, 1-side})) {
            const auto other = s.doors[static_cast<std::size_t>(1-side)];
            if (!other.trap && immediate_door_harm(rule, game, 1-side) <= harm + 1e-12)
                score -= s.hp <= rule.trap_hp ? 150 : 40;
            if (damaging_trap && .1 * (s.has(Gem::masochist) ? .8 : 1) >= s.hp)
                score -= 500; // A certain lethal entry is worse than a possible escape from an enemy.
        }
        if (door.kind == Door::sacrifice && harm >= s.hp) score -= 500;
        if (score > best_score) { best_score = score; best = side; }
    }
    if (best < 0) throw std::logic_error("Human rules found no legal door");
    return {ActionKind::choose_door, best};
}
int healing_budget(const RulePolicy& rule, const Game& game) {
    if (rule.paid_refill_mode == 0) return 0;
    const auto& s = game.state();
    const int reserved = static_cast<int>(std::ceil(rule.shop_reserve_fraction * game.limits().budget));
    return std::max(0, game.limits().budget - s.mushrooms - std::max(0, reserved - s.reroll_mushrooms));
}
Action paid_heal(const RulePolicy& rule, const Game& game, double target) {
    const auto& s = game.state(); const int budget = healing_budget(rule, game);
    const bool full = game.legal({ActionKind::heal_full}) && game.full_price() <= budget;
    const bool step = game.legal({ActionKind::heal_step}) && game.step_price() <= budget;
    if (rule.paid_refill_mode == 3) {
        // Do not buy HP that cannot be used before the event ends. Include the
        // visible recovery destination: choosing a door is not yet an encounter.
        int actions = 2; // Heal, then resolve the current encounter.
        if (s.phase == Phase::recovery) {
            actions = 3; // Heal, reenter, then resolve the resumed encounter.
            if (game.recovery_destination() == Phase::doors) ++actions;
            if (game.recovery_destination() == Phase::gems) actions += 2;
        }
        if (actions * game.profile().action_seconds / 3600 > game.limits().deadline_hours - s.elapsed_hours + 1e-12)
            return {ActionKind::skip};
        // The unused HP disappears after room 100. Prefer a cheaper partial
        // refill when it already guarantees survival against that final boss.
        const bool final_boss = s.room == 100 && (!game.limits().repeat_runs || game.limits().restart_health != "carry") &&
            (s.phase == Phase::recovery || (s.phase == Phase::encounter && s.encounter == Encounter::boss));
        const double boss_target = std::max(s.phase == Phase::recovery ? .2 : 0.0, game.damage_ceiling(true) + .001);
        if (step && final_boss && std::min(1.0, s.hp + .2) >= boss_target && (!full || game.step_price() < game.full_price()))
            return {ActionKind::heal_step};
        const double missing = 1 - s.hp;
        if (full && (!step || game.full_price() * std::min(.2, missing) <= game.step_price() * missing))
            return {ActionKind::heal_full};
        if (step) return {ActionKind::heal_step};
        if (full) return {ActionKind::heal_full};
        return {ActionKind::skip};
    }
    if (step && s.hp + .2 >= target && (!full || game.step_price() < game.full_price())) return {ActionKind::heal_step};
    if (full && (!step || game.full_price() / (1 - s.hp) <= game.step_price() / std::min(.2, 1 - s.hp)))
        return {ActionKind::heal_full};
    if (step) return {ActionKind::heal_step};
    if (full) return {ActionKind::heal_full};
    return {ActionKind::skip}; // No affordable paid recovery; the caller continues its rule list.
}
int useful_rooms(const State& s, const Effect& effect) {
    if (effect.clock != Clock::room) return std::min(effect.remaining, 100 - s.room);
    int rooms = 0;
    for (int room = s.room + 1; room <= std::min(100, s.room + effect.remaining); ++room) if (room % 25 != 0) ++rooms;
    return rooms;
}
double offer_score(const RulePolicy& rule, const Game& game, const Offer& offer) {
    const auto& s = game.state(); const auto& e = offer.effect;
    const int rooms = useful_rooms(s, e);
    if (e.kind != EffectKind::elixir && rooms == 0) return 0;
    if (const auto* existing = s.effect(e.kind))
        if (existing->remaining >= e.remaining && existing->magnitude >= e.magnitude) return 0;
    // Never overwrite a still-useful One Hit/escape blessing just to receive a
    // lower-ranked effect. A potion is instant and does not occupy a slot.
    if (s.blessings.size == 3 && e.kind != EffectKind::elixir && !s.effect(e.kind)) {
        const auto& oldest = s.blessings.slots[0];
        if ((oldest.kind == EffectKind::one_hit || oldest.kind == EffectKind::escape) && oldest.remaining > 1 &&
            e.kind != EffectKind::one_hit && !(e.kind == EffectKind::recovery && s.hp < .3)) return 0;
    }
    double score = 0;
    switch (e.kind) {
    case EffectKind::one_hit: score = 70 + 5 * std::min(8, rooms); break;
    case EffectKind::escape:
        if (escape_chance(game) >= 1 && !s.effect(EffectKind::escape)) return 0;
        score = 60 + 3 * std::min(10, rooms); break;
    case EffectKind::recovery: score = 65 + 4 * std::min(6, rooms) + (e.magnitude >= .2 ? 10 : 0); break;
    case EffectKind::elixir:
        if (s.hp >= 1) return 0;
        score = s.hp < .3 ? 95 : s.hp < .6 ? 75 : s.hp < .85 ? 35 : 10;
        if (s.has(Gem::time_traveler) && game.profile().time_traveler_elixir_full_heal) score += 10;
        break;
    case EffectKind::key_moment: score = s.keys < rule.fight_keys[stage_of(s)] ? 80 : 45; break;
    case EffectKind::lockpick: score = s.keys < 2 ? 80 : 60; break;
    case EffectKind::disarm: score = 55 + std::min(8, e.remaining); break;
    default: return 0; // Gold-only blessings do not advance the completion objective.
    }
    if (rule.shop_mode == 1 && (e.kind == EffectKind::recovery || (e.kind == EffectKind::elixir && s.hp < .6))) score += 25;
    if (rule.shop_mode == 2 && e.kind == EffectKind::escape) score += 25;
    return score;
}
Action choose_shop(const RulePolicy& rule, const Game& game) {
    const auto& s = game.state();
    int best = -1; double score = 0;
    for (int i = 0; i < 2; ++i) if (game.legal({ActionKind::buy, i})) {
        const auto& offer = s.offers[static_cast<std::size_t>(i)];
        const double candidate = offer_score(rule, game, offer);
        // Apply the same threshold to every paid effect, including potions;
        // their existing HP-sensitive score supplies the urgency adjustment.
        if (offer.keys > 0 && candidate < rule.shop_purchase_floor) continue;
        if (candidate > score || (candidate == score && candidate > 0 && best >= 0 &&
            s.offers[static_cast<std::size_t>(i)].keys < s.offers[static_cast<std::size_t>(best)].keys)) {
            score = candidate; best = i;
        }
    }
    const auto credit = static_cast<std::int64_t>(rule.shop_allowance) * s.reroll_shop_visits;
    const auto left = std::max<std::int64_t>(0, credit - s.reroll_bank_used);
    const double entry_credit = static_cast<double>(left + s.shop_rerolls);
    const double fraction = entry_credit > 0 ? s.shop_rerolls / entry_credit : 1;
    // Search for a top-ranked offer initially; accept progressively weaker
    // useful effects as this visit consumes its available (including saved) credit.
    const double threshold = std::max(rule.shop_purchase_floor, 100 * (1 - std::clamp(fraction, 0.0, 1.0)));
    if (left > 0 && s.keys >= rule.shop_key_reserve && game.legal({ActionKind::reroll}) && score < threshold)
        return {ActionKind::reroll};
    return best >= 0 ? Action{ActionKind::buy, best} : Action{ActionKind::skip};
}
std::string trim(std::string_view value) {
    const auto first = value.find_first_not_of(" \t\r\n");
    if (first == std::string_view::npos) return {};
    return std::string(value.substr(first, value.find_last_not_of(" \t\r\n") - first + 1));
}
double number(const std::string& value) {
    double parsed = 0;
    const auto [end, error] = std::from_chars(value.data(), value.data() + value.size(), parsed);
    if (error != std::errc{} || end != value.data() + value.size() || !std::isfinite(parsed))
        throw std::invalid_argument("Expected finite rule number: " + value);
    return parsed;
}
void integer(int& destination, const std::string& value) {
    const double parsed = number(value);
    if (std::floor(parsed) != parsed || parsed < 0 || parsed > 1000000)
        throw std::invalid_argument("Expected nonnegative rule integer");
    destination = static_cast<int>(parsed);
}
void boolean(bool& destination, const std::string& value) {
    if (value != "0" && value != "1") throw std::invalid_argument("Expected rule boolean 0 or 1");
    destination = value == "1";
}
void numbers(std::array<int, 4>& destination, const std::string& value) {
    std::istringstream input(value); std::string part; std::size_t i = 0;
    while (std::getline(input, part, ',')) {
        if (i == destination.size()) throw std::invalid_argument("Four stage values required");
        integer(destination[i++], trim(part));
    }
    if (i != destination.size() || value.ends_with(',')) throw std::invalid_argument("Four stage values required");
}
}

Action RulePolicy::choose(const Game& game) const {
    const auto& s = game.state();
    switch (s.phase) {
    case Phase::doors: return choose_doors(*this, game);
    case Phase::shop: return choose_shop(*this, game);
    case Phase::curse_shop:
        if (s.keys < std::max(shop_key_reserve, fight_keys[stage_of(s)]))
            for (int i = 0; i < 2; ++i) if (game.legal({ActionKind::buy, i}) &&
                s.offers[static_cast<std::size_t>(i)].effect.kind == EffectKind::gold_hangover) return {ActionKind::buy, i};
        return {ActionKind::skip};
    case Phase::gems: return choose_rule_gem(game, gem_rules);
    case Phase::recovery: {
        const double time_left = game.limits().deadline_hours - s.elapsed_hours;
        if (time_left > 0 && time_left < game.profile().action_seconds / 3600)
            return {ActionKind::wait, 0, time_left};
        double target = revive_hp;
        if (s.room % 25 == 0) target = std::max(target, std::min(1.0, game.damage_ceiling(true) + .001));
        // Mode 3 preserves only enough HP to finish the final boss when surplus
        // refill HP cannot carry into another run.
        if (paid_refill_mode == 3 && s.room == 100 && (!game.limits().repeat_runs || game.limits().restart_health != "carry"))
            target = std::max(.2, std::min(1.0, game.damage_ceiling(true) + .001));
        if (s.hp >= target - 1e-12 && game.legal({ActionKind::reenter})) return {ActionKind::reenter};
        const auto heal = paid_heal(*this, game, target);
        if (heal.kind != ActionKind::skip) return heal;
        double hours = std::max(1e-9, (target - s.hp) * game.profile().recovery_hours);
        if (login_interval_hours > 0)
            hours = std::ceil((s.elapsed_hours + hours) / login_interval_hours) * login_interval_hours - s.elapsed_hours;
        hours = std::min(hours, game.limits().deadline_hours - s.elapsed_hours);
        if (game.legal({ActionKind::wait, 0, hours})) return {ActionKind::wait, 0, hours};
        break;
    }
    case Phase::complete: if (game.legal({ActionKind::restart})) return {ActionKind::restart}; break;
    case Phase::encounter:
        if (is_enemy(s.encounter)) {
            if (s.encounter == Encounter::mimic && !s.effect(EffectKind::one_hit)) return {ActionKind::skip};
            bool fight = !game.legal({ActionKind::flee}) || fight_for_keys(*this, game, s.encounter);
            if (s.encounter == Encounter::pig) {
                const auto outcome = after_combat(game, s.encounter, false);
                if (1 - outcome.room_survival > max_fight_death_risk || outcome.expected_hp < s.hp - 1e-12) return {ActionKind::skip};
                fight = true;
            }
            const bool danger = death_risk(game, s.encounter, !fight) > max_fight_death_risk + 1e-12;
            if (danger || (paid_refill_mode == 2 && s.hp < revive_hp)) {
                const double target = std::max(revive_hp, std::min(1.0, game.damage_ceiling(s.encounter == Encounter::boss) + .001));
                const auto heal = paid_heal(*this, game, target);
                if (heal.kind != ActionKind::skip) return heal;
            }
            return {fight ? ActionKind::fight : ActionKind::flee};
        }
        if (s.encounter == Encounter::barrel) {
            const double chance = barrel_blessing_chance(game.profile(), s);
            const bool protected_blessing = s.effect(EffectKind::one_hit) || s.effect(EffectKind::escape);
            const bool open = chance > 0 && (barrel_mode == 2 || (barrel_mode == 1 &&
                ((chance >= .75 && !protected_blessing) || (s.hp <= barrel_hp && !protected_blessing))));
            return {open ? ActionKind::interact : ActionKind::skip};
        }
        if (s.encounter == Encounter::skeleton) {
            const bool search = s.effect(EffectKind::one_hit) || (s.hp >= min_fight_hp &&
                death_risk(game, Encounter::monster, true) <= max_fight_death_risk &&
                (s.has(Gem::greed) || s.keys < fight_keys[stage_of(s)]));
            return {search ? ActionKind::interact : ActionKind::skip};
        }
        if (s.encounter == Encounter::spider_legs)
            return {s.keys < std::max(shop_key_reserve, fight_keys[stage_of(s)]) && s.hp > trap_hp ? ActionKind::interact : ActionKind::skip};
        if (s.encounter == Encounter::spider_head || s.encounter == Encounter::spider_full || s.encounter == Encounter::rps ||
            s.encounter == Encounter::wheel || s.encounter == Encounter::rainbow || s.encounter == Encounter::cursed_chest ||
            s.encounter == Encounter::sacrifice_chest) return {ActionKind::skip};
        if (game.legal({ActionKind::interact})) return {ActionKind::interact};
        if (game.legal({ActionKind::skip})) return {ActionKind::skip};
        break;
    }
    // Only forced/unhandled-state safety uses the old policy. Ordinary door,
    // combat, shop, gem and recovery decisions above never invoke Planner.
    Policy fallback; fallback.revive_hp = revive_hp; fallback.login_interval_hours = login_interval_hours;
    const auto action = fallback.choose(game);
    if (!game.legal(action)) throw std::logic_error("No legal human-rule action at this state");
    return action;
}
void RulePolicy::validate() const {
    if (id.empty() || id.size() > 120 || id.find_first_of("\r\n=#") != std::string::npos)
        throw std::invalid_argument("Invalid human-rule id");
    for (const auto* values : {&fight_keys, &door_reserve}) for (int value : *values)
        if (value < 0 || value > 30) throw std::invalid_argument("Rule stage key targets must be in [0,30]");
    for (double value : {max_fight_death_risk, min_fight_hp, fight_escape_cutoff, trap_hp, barrel_hp, shop_reserve_fraction})
        if (!std::isfinite(value) || value < 0 || value > 1) throw std::invalid_argument("Rule fractions must be in [0,1]");
    if (!std::isfinite(revive_hp) || revive_hp < .2 || revive_hp > 1 || !std::isfinite(login_interval_hours) ||
        login_interval_hours < 0 || login_interval_hours > 168 || shop_lookahead < 0 || shop_lookahead > 24 ||
        shop_key_reserve < 0 || shop_key_reserve > 30 || shop_allowance < 0 || shop_allowance > 20)
        throw std::invalid_argument("Invalid human-rule recovery/shop setting");
    if (!std::isfinite(shop_purchase_floor) || shop_purchase_floor < 0 || shop_purchase_floor > 150)
        throw std::invalid_argument("Human-rule shop purchase floor must be in [0,150]");
    for (int mode : {door_mode, shop_mode, barrel_mode})
        if (mode < 0 || mode > 2) throw std::invalid_argument("Human-rule modes must be 0, 1 or 2");
    if (paid_refill_mode < 0 || paid_refill_mode > 3)
        throw std::invalid_argument("Human-rule paid refill mode must be 0, 1, 2 or 3");
    gem_rules.validate();
}
std::string RulePolicy::encode() const {
    validate();
    std::ostringstream out; out << std::setprecision(std::numeric_limits<double>::max_digits10);
    out << "schema=1\nid=" << id << '\n';
    const auto stage_values = [&](const char* key, const std::array<int, 4>& values) {
        out << key << '=';
        for (std::size_t i = 0; i < values.size(); ++i) out << (i ? "," : "") << values[i];
        out << '\n';
    };
    stage_values("fight_keys", fight_keys); stage_values("door_reserve", door_reserve);
    out << "max_fight_death_risk=" << max_fight_death_risk << "\nmin_fight_hp=" << min_fight_hp
        << "\nfight_escape_cutoff=" << fight_escape_cutoff << "\ndoor_mode=" << door_mode
        << "\nfight_when_cheaper=" << fight_when_cheaper
        << "\nguaranteed_escape_door_preference=" << guaranteed_escape_door_preference
        << "\nshop_lookahead=" << shop_lookahead << "\nshop_key_reserve=" << shop_key_reserve
        << "\nshop_allowance=" << shop_allowance << "\nshop_mode=" << shop_mode << "\nshop_purchase_floor=" << shop_purchase_floor
        << "\nbarrel_mode=" << barrel_mode
        << "\npaid_refill_mode=" << paid_refill_mode << "\ntrap_hp=" << trap_hp << "\nbarrel_hp=" << barrel_hp
        << "\nrevive_hp=" << revive_hp << "\nshop_reserve_fraction=" << shop_reserve_fraction
        << "\nlogin_interval_hours=" << login_interval_hours << '\n' << serialize_rule_gems(gem_rules);
    return out.str();
}
void RulePolicy::save(const std::string& path) const {
    const auto contents = encode();
    std::ofstream output(path, std::ios::binary);
    if (!output || !(output << contents)) throw std::runtime_error("Cannot save human rules: " + path);
}
RulePolicy RulePolicy::load(const std::string& path) {
    std::ifstream input(path, std::ios::binary | std::ios::ate);
    if (!input || input.tellg() > 65536) throw std::runtime_error("Cannot read human rules, or file exceeds 64 KiB: " + path);
    input.seekg(0); RulePolicy result; std::set<std::string> seen; std::string line;
    while (std::getline(input, line)) {
        line = trim(line.substr(0, line.find('#')));
        if (line.empty()) continue;
        const auto equals = line.find('=');
        if (equals == std::string::npos) throw std::invalid_argument("Human rules expect key=value");
        const auto key = trim(line.substr(0, equals)); const auto value = trim(line.substr(equals + 1));
        if (!seen.insert(key).second) throw std::invalid_argument("Duplicate human-rule field: " + key);
        if (key == "schema") { if (value != "1") throw std::invalid_argument("Unsupported human-rule schema"); }
        else if (key == "id") result.id = value;
        else if (key == "fight_keys") numbers(result.fight_keys, value);
        else if (key == "door_reserve") numbers(result.door_reserve, value);
        else if (key == "max_fight_death_risk") result.max_fight_death_risk = number(value);
        else if (key == "min_fight_hp") result.min_fight_hp = number(value);
        else if (key == "fight_escape_cutoff") result.fight_escape_cutoff = number(value);
        else if (key == "fight_when_cheaper") boolean(result.fight_when_cheaper, value);
        else if (key == "door_mode") integer(result.door_mode, value);
        else if (key == "guaranteed_escape_door_preference") boolean(result.guaranteed_escape_door_preference, value);
        else if (key == "shop_lookahead") integer(result.shop_lookahead, value);
        else if (key == "shop_key_reserve") integer(result.shop_key_reserve, value);
        else if (key == "shop_allowance") integer(result.shop_allowance, value);
        else if (key == "shop_mode") integer(result.shop_mode, value);
        else if (key == "shop_purchase_floor") result.shop_purchase_floor = number(value);
        else if (key == "barrel_mode") integer(result.barrel_mode, value);
        else if (key == "paid_refill_mode") integer(result.paid_refill_mode, value);
        else if (key == "trap_hp") result.trap_hp = number(value);
        else if (key == "barrel_hp") result.barrel_hp = number(value);
        else if (key == "revive_hp") result.revive_hp = number(value);
        else if (key == "shop_reserve_fraction") result.shop_reserve_fraction = number(value);
        else if (key == "login_interval_hours") result.login_interval_hours = number(value);
        else if (!parse_rule_gem_setting(result.gem_rules, key, number(value))) throw std::invalid_argument("Unknown human-rule field: " + key);
    }
    if (!seen.contains("schema")) throw std::invalid_argument("Missing human-rule schema");
    result.validate(); return result;
}
}
