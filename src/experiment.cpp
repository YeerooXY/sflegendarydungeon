#include "sfld/experiment.hpp"
#include "sfld/damage_risk.hpp"
#include <algorithm>
#include <atomic>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <numeric>
#include <random>
#include <sstream>
#include <stdexcept>
#include <thread>

namespace sfld {
std::vector<Action> legal_actions(const Game& game) {
    std::vector<Action> result;
    if (game.finished()) return result;
    const auto add = [&](Action a) { if (game.legal(a)) result.push_back(a); };
    for (auto k : {ActionKind::choose_door, ActionKind::buy, ActionKind::choose_gem})
        for (int i = 0; i < (k == ActionKind::choose_gem ? 3 : 2); ++i) add({k, i});
    for (auto k : {ActionKind::fight, ActionKind::flee, ActionKind::interact, ActionKind::skip, ActionKind::reroll,
                   ActionKind::heal_step, ActionKind::heal_full, ActionKind::reenter, ActionKind::restart}) add({k});
    if (game.state().phase == Phase::recovery) {
        std::vector<double> targets{.2, .4, .6, .8, 1.0};
        if (game.state().room % 25 == 0) targets.push_back(std::clamp(game.damage_ceiling(true) + .001, .2, 1.0));
        std::sort(targets.begin(), targets.end());
        std::vector<double> waits;
        const auto wait = [&](double hours) {
            if (hours > 1e-12 && std::none_of(waits.begin(), waits.end(), [&](double old) { return std::abs(old - hours) <= 1e-12; })) {
                add({ActionKind::wait, 0, hours}); waits.push_back(hours);
            }
        };
        for (double target : targets) {
            const double hours = std::min((target - game.state().hp) * game.profile().recovery_hours,
                                         game.limits().deadline_hours - game.state().elapsed_hours);
            wait(hours);
        }
        wait(game.limits().deadline_hours - game.state().elapsed_hours);
        // If less than one ordinary action fits, allow the clock to expire.
        if (result.empty()) add({ActionKind::wait, 0, game.limits().deadline_hours - game.state().elapsed_hours});
    }
    return result;
}
namespace {
void effect_json(std::ostream& out, const Effect& e) {
    constexpr std::string_view clocks[]{"room", "trap", "door", "fight"};
    out << "{\"kind\":" << json_string(name(e.kind)) << ",\"magnitude\":" << e.magnitude
        << ",\"remaining\":" << e.remaining << ",\"clock\":" << json_string(clocks[ix(e.clock)])
        << ",\"starts_at\":" << e.starts_at << '}';
}
void action_json(std::ostream& out, Action a) {
    out << "{\"kind\":" << json_string(name(a.kind)) << ",\"index\":" << a.index << ",\"value\":" << a.value << '}';
}
// Public-state forecast of the hit in Game::fight. It never samples damage or
// predicts newly rolled curses, healing rewards, or room-exit effect ticks.
DamageRisk combat_damage(const Game& game, bool fleeing, Encounter encounter) {
    const auto& s = game.state(); const auto& p = game.profile();
    const auto stage = static_cast<std::size_t>(std::clamp((s.room - 1) / 25, 0, 3));
    if (encounter == Encounter::boss) return {p.boss_damage[stage], p.damage_distribution};
    if (!fleeing && s.effect(EffectKind::one_hit)) return {{0, 0, 0}, p.damage_distribution};
    auto range = fleeing ? p.escape_damage[stage] : p.monster_damage[stage];
    double multiplier = game.battle_multiplier(fleeing);
    if (encounter == Encounter::undead || encounter == Encounter::tube || encounter == Encounter::beta) multiplier *= .5;
    if (encounter == Encounter::trial_monster) multiplier *= p.trial_multipliers.at(static_cast<std::size_t>(s.trial_depth));
    if (encounter == Encounter::shakes) range = {.3, .3, .3};
    if (encounter == Encounter::valaraukar) range = {.6, .6, .6};
    if (encounter == Encounter::pig) range = {.4, .4, .4};
    return {{range.low * multiplier, range.high * multiplier, range.mean * multiplier}, p.damage_distribution};
}
void combat_forecast_json(std::ostream& out, const Game& game) {
    if (!game.legal({ActionKind::fight}) && !game.legal({ActionKind::flee})) return;
    out << ",\"combat_forecast\":{\"scope\":\"hit_only\",\"distribution\":" << json_string(game.profile().damage_distribution);
    for (bool fleeing : {false, true}) {
        if (!game.legal({fleeing ? ActionKind::flee : ActionKind::fight})) continue;
        const auto damage = combat_damage(game, fleeing, game.state().encounter);
        const double escape = fleeing ? game.flee_probability() : 0;
        const double survival = damage.below(game.state().hp - 1e-12);
        out << (fleeing ? ",\"flee\":" : ",\"fight\":")
            << "{\"max_damage\":" << (escape >= 1 ? 0 : damage.maximum())
            << ",\"mean_damage\":" << (1 - escape) * damage.mean()
            << ",\"hit_survival\":" << survival << ",\"escape_chance\":" << escape
            << ",\"survival\":" << escape + (1 - escape) * survival << '}';
    }
    out << '}';
}
// Marginals of the configured entry tables, not a peek at a sampled room.
// Corpses waking and optional mimic fights are separate from a live monster.
void door_forecast_json(std::ostream& out, const Game& game) {
    const auto& s = game.state(); const auto& p = game.profile();
    if (s.phase != Phase::doors) return;
    out << ",\"door_forecast\":[";
    for (std::size_t i = 0; i < s.doors.size(); ++i) {
        const auto d = s.doors[i];
        std::array<double, ix(Encounter::count)> weights{};
        switch (d.kind) {
        case Door::monster: case Door::blessing: weights[ix(Encounter::monster)] = 1; break;
        case Door::boss: weights[ix(Encounter::boss)] = 1; break;
        case Door::trial: weights[ix(Encounter::trial_monster)] = 1; break;
        case Door::mystery: {
            const auto other = s.doors[1 - i].kind;
            weights = p.mystery_for_alternative(other);
            if (other == Door::monster)
                for (std::size_t j = 0; j < weights.size(); ++j)
                    if (is_enemy(static_cast<Encounter>(j))) weights[j] = 0;
            break;
        }
        case Door::unlocked: weights = p.mystery_weights; break;
        case Door::locked: case Door::double_locked: weights = p.locked_weights; break;
        case Door::epic: weights[ix(Encounter::epic)] = 1; break;
        case Door::destiny: weights = p.destiny_weights; break;
        case Door::golden:
            weights = p.golden_weights;
            if (s.room < 90 || s.room > 98) weights[ix(Encounter::armory)] = 0;
            break;
        default: break;
        }
        const double total = std::accumulate(weights.begin(), weights.end(), 0.0);
        double monster = 0, mimic = total > 0 ? weights[ix(Encounter::mimic)] / total : 0;
        if (total > 0) for (std::size_t j = 0; j < weights.size(); ++j)
            if (is_enemy(static_cast<Encounter>(j)) && static_cast<Encounter>(j) != Encounter::mimic)
                monster += weights[j] / total;
        if (d.kind == Door::locked || d.kind == Door::double_locked || d.kind == Door::epic) {
            const double retained = (s.has(Gem::old_sacrifice) ? .8 : 1) * (s.has(Gem::kidney) ? .8 : 1);
            const double hidden = s.has(Gem::deceit) ? std::clamp(p.hidden_monster_chance, 0.0, 1.0) : 0;
            monster = monster * retained * (1 - hidden) + hidden;
            mimic *= retained * (1 - hidden);
        }
        double entry_damage = d.kind == Door::sacrifice ? .12 * (s.has(Gem::blood) ? .6 : 1) : 0;
        double entry_curse = d.kind == Door::cursed ? 1 : 0;
        if (d.kind == Door::destiny) {
            const double sum = std::accumulate(p.destiny_reward_weights.begin(), p.destiny_reward_weights.end(), 0.0);
            entry_curse = sum > 0 ? p.destiny_reward_weights[5] / sum : 0;
        }
        if (s.room > 5 && d.trap && d.kind != Door::golden && !s.effect(EffectKind::disarm)) {
            if (d.cursed_trap || s.has(Gem::gambler)) entry_curse = 1;
            else entry_damage += .1 * (s.has(Gem::masochist) ? .8 : 1);
        }
        const double hp = s.hp - entry_damage;
        const auto encounter = d.kind == Door::boss ? Encounter::boss : d.kind == Door::trial ? Encounter::trial_monster : Encounter::monster;
        if (i) out << ',';
        out << "{\"index\":" << i << ",\"monster_chance\":" << monster << ",\"mimic_chance\":" << mimic
            << ",\"entry_damage\":" << entry_damage << ",\"entry_curse_chance\":" << entry_curse;
        for (const bool fleeing : {false, true}) {
            const auto damage = combat_damage(game, fleeing, encounter);
            const double escape = fleeing && encounter != Encounter::boss ? game.flee_probability() : 0;
            out << (fleeing ? ",\"monster_flee\":{" : ",\"monster_fight\":{")
                << "\"max_damage\":" << (escape >= 1 ? 0 : damage.maximum())
                << ",\"mean_damage\":" << (1 - escape) * damage.mean()
                << ",\"survival\":" << (hp > 1e-12 ? escape + (1 - escape) * damage.below(hp - 1e-12) : 0) << '}';
        }
        out << '}';
    }
    out << ']';
}
}
std::string observation_json(const Game& game) {
    const auto& s = game.state(); const auto& l = game.limits();
    const auto phase = s.phase == Phase::recovery ? game.recovery_destination() : s.phase;
    std::ostringstream out; out << std::setprecision(17) << std::boolalpha;
    out << "{\"schema\":1,\"engine\":" << json_string(engine_version) << ",\"synthetic\":true,\"terminal\":" << game.finished()
        << ",\"action_limit\":" << (s.actions >= l.max_actions) << ",\"phase\":" << json_string(name(s.phase))
        << ",\"max_actions\":" << l.max_actions
        << ",\"resume_phase\":" << json_string(name(phase)) << ",\"room\":" << s.room << ",\"run_number\":" << s.run_number
        << ",\"completed_runs\":" << s.completed_runs << ",\"hp\":" << s.hp << ",\"keys\":" << s.keys
        << ",\"turn\":" << s.turn << ",\"elapsed_hours\":" << s.elapsed_hours
        << ",\"local_hour\":" << game.local_hour() << ",\"event_start_hour\":" << l.event_start_hour << ",\"sleep_until_hour\":" << l.sleep_until_hour
        << ",\"run_elapsed_hours\":" << s.elapsed_hours - s.run_start_hours
        << ",\"run_mushrooms_spent\":" << s.mushrooms - s.run_start_mushrooms
        << ",\"epics\":" << s.epics << ",\"legendaries\":" << s.legendaries
        << ",\"hours_left\":" << std::max(0.0, l.deadline_hours - s.elapsed_hours)
        << ",\"mushrooms_spent\":" << s.mushrooms << ",\"mushrooms_left\":" << l.budget - s.mushrooms
        << ",\"mushrooms_found\":" << s.mushrooms_found
        << ",\"gold_reward_units\":" << s.gold_units << ",\"lucky_coins\":" << s.lucky_coins
        << ",\"recovery_mushrooms\":" << s.recovery_mushrooms << ",\"reroll_mushrooms\":" << s.reroll_mushrooms
        << ",\"heal_step_price\":" << game.step_price() << ",\"heal_full_price\":" << (s.hp < 1 ? game.full_price() : 0)
        << ",\"boss_damage_max\":" << game.damage_ceiling(true)
        << ",\"paid_heals\":" << s.paid_steps << ",\"shop_rerolls\":" << s.shop_rerolls
        << ",\"reroll_shop_visits\":" << s.reroll_shop_visits << ",\"reroll_bank_used\":" << s.reroll_bank_used
        << ",\"trial_depth\":" << s.trial_depth << ",\"trial_seen\":" << s.trial_seen << ",\"armory_room\":" << s.armory_room
        << ",\"pending_trial_reward\":" << s.pending_trial_reward
        << ",\"donated_resource\":" << (phase == Phase::encounter && s.encounter == Encounter::sated_chest ? s.donated_resource : -1)
        << ",\"actions\":" << s.actions << ",\"deaths\":" << s.deaths
        << ",\"first_completion_hours\":";
    if (s.first_completion_hours < 0) out << "null"; else out << s.first_completion_hours;
    out << ",\"shops_seen\":[";
    for (std::size_t i = 0; i < s.shops_seen.size(); ++i) { if (i) out << ','; out << s.shops_seen[i]; }
    out << "],\"resources\":[";
    for (std::size_t i = 0; i < s.resources.size(); ++i) { if (i) out << ','; out << s.resources[i]; }
    out << "],\"gems\":["; bool comma = false;
    for (std::size_t i = 0; i < s.gems.size(); ++i) if (s.gems[i]) {
        if (comma) out << ',';
        comma = true; out << json_string(name(static_cast<Gem>(i)));
    }
    out << "],\"offered_gems\":["; comma = false;
    for (std::size_t i = 0; i < s.offered_gems.size(); ++i) if (s.offered_gems[i]) {
        if (comma) out << ',';
        comma = true; out << json_string(name(static_cast<Gem>(i)));
    }
    out << ']';
    for (const auto& [label, effects] : {std::pair{"blessings", &s.blessings}, std::pair{"curses", &s.curses}}) {
        out << ',' << json_string(label) << ":[";
        for (int i = 0; i < effects->size; ++i) { if (i) out << ','; effect_json(out, effects->slots[static_cast<std::size_t>(i)]); }
        out << ']';
    }
    if (phase == Phase::doors) {
        out << ",\"doors\":[";
        for (std::size_t i = 0; i < 2; ++i) {
            if (i) out << ',';
            const auto& d = s.doors[i];
            out << "{\"kind\":" << json_string(name(d.kind)) << ",\"trap\":" << d.trap
                << ",\"cursed_trap\":" << d.cursed_trap << ",\"key_cost\":" << game.door_cost(d.kind)
                << ",\"available\":" << game.available(d) << '}';
        }
        out << ']';
    } else if (phase == Phase::encounter) {
        out << ",\"encounter\":" << json_string(name(s.encounter));
        if (s.encounter == Encounter::flooded) out << ",\"flooded_seconds\":" << s.flooded_seconds;
    } else if (phase == Phase::shop || phase == Phase::curse_shop) {
        out << ",\"offers\":[";
        for (std::size_t i = 0; i < 2; ++i) {
            if (i) out << ',';
            out << "{\"keys\":" << s.offers[i].keys << ",\"effect\":";
            effect_json(out, s.offers[i].effect); out << '}';
        }
        out << ']';
    } else if (phase == Phase::gems) {
        out << ",\"gem_offers\":[";
        for (std::size_t i = 0; i < 3; ++i) { if (i) out << ','; out << json_string(name(s.gem_offers[i])); }
        out << ']';
    }
    out << ",\"outcomes\":["; comma = false;
    for (const auto& event : game.outcomes()) {
        if (comma) out << ','; comma = true;
        out << "{\"kind\":" << json_string(event.kind) << ",\"detail\":" << json_string(event.detail)
            << ",\"amount\":" << event.amount << ",\"duration\":" << event.duration << '}';
    }
    out << "],\"legal_actions\":["; comma = false;
    for (auto a : legal_actions(game)) { if (comma) out << ','; comma = true; action_json(out, a); }
    out << "]}"; return out.str();
}
std::string ui_observation_json(const Game& game, const LearnedPolicy* learned, const Policy* settings) {
    std::string base = observation_json(game); base.pop_back();
    std::ostringstream out; out << std::setprecision(17) << base << ",\"suggestions\":[";
    bool comma = false;
    const auto add = [&](std::string_view label, Action action) {
        if (action.kind == ActionKind::wait)
            action.value = std::min(action.value, game.limits().deadline_hours - game.state().elapsed_hours);
        if (!game.legal(action)) return;
        if (comma) out << ','; comma = true;
        out << "{\"strategy\":" << json_string(label) << ",\"action\":";
        action_json(out, action); out << '}';
    };
    if (!game.finished()) {
        for (auto strategy : {Strategy::baseline, Strategy::strategic, Strategy::key_stockpile}) {
            Policy policy = settings ? *settings : Policy{};
            policy.strategy = strategy; add(name(strategy), policy.choose(game));
        }
        if (learned) add("learned", learned->choose(game));
    }
    const auto& p = game.profile();
    out << ']';
    combat_forecast_json(out, game);
    door_forecast_json(out, game);
    out << ",\"scenario\":{\"id\":" << json_string(p.id) << ",\"fingerprint\":" << json_string(p.fingerprint)
        << ",\"shop_schedule\":" << json_string(p.shop_schedule) << ",\"armory_schedule\":" << json_string(p.armory_schedule) << ",\"escape_chance\":" << p.escape_chance
        << ",\"key_chance\":" << p.key_chance << ",\"recovery_hours\":" << p.recovery_hours
        << ",\"combat_curse_chance\":" << p.combat_curse_chance
        << ",\"floor_specific_curses\":" << (p.floor_specific_curses ? "true" : "false")
        << ",\"barrel_blessing\":" << p.barrel_blessing
        << ",\"current_barrel_blessing\":" << barrel_blessing_chance(p, game.state())
        << ",\"time_traveler_barrel_curse_multiplier\":" << p.time_traveler_barrel_curse_multiplier
        << ",\"later_stage_one_shop\":" << (p.later_stage_one_shop ? "true" : "false")
        << ",\"exclude_offered_gems\":" << (p.exclude_offered_gems ? "true" : "false")
        << ",\"time_traveler_elixir_full_heal\":" << (p.time_traveler_elixir_full_heal ? "true" : "false")
        << ",\"spider_success\":[" << p.spider_success[0] << ',' << p.spider_success[1] << ',' << p.spider_success[2]
        << "],\"spider_bite_damage\":" << p.spider_bite_damage << "}}";
    return out.str();
}
EventResult run_event(const Profile& profile, const Controller& choose, std::uint64_t seed, Limits limits, const StartState* start) {
    limits.repeat_runs = true;
    Game game = start ? Game(profile, seed, *start, limits) : Game(profile, seed, limits);
    while (!game.finished()) {
        auto action = choose(game);
        if (action.kind == ActionKind::wait) action.value = std::min(action.value, limits.deadline_hours - game.state().elapsed_hours);
        game.step(action);
    }
    const auto& s = game.state();
    return {s.completed_runs, s.mushrooms, s.recovery_mushrooms, s.reroll_mushrooms, s.room, s.deaths,
            s.first_completion_hours, s.elapsed_hours, s.actions, s.actions >= limits.max_actions, s.mushrooms_found,
            s.reroll_shop_visits, s.shop_purchases, s.strong_one_hit_purchases};
}
std::vector<EventResult> event_batch(const Profile& profile, const Controller& choose, const BatchOptions& options) {
    if (options.runs < 1 || options.runs > 10000000 || options.threads < 1 || options.threads > 256)
        throw std::invalid_argument("Invalid event count or thread count");
    std::vector<EventResult> results(options.runs);
    std::atomic<std::size_t> next{0}; std::atomic<bool> failed{false};
    std::exception_ptr failure; std::mutex mutex;
    const auto worker = [&] {
        try {
            while (!failed.load()) {
                const auto i = next.fetch_add(1);
                if (i >= results.size()) break;
                results[i] = run_event(profile, choose, Random::mix(options.seed ^ static_cast<std::uint64_t>(i)), options.limits,
                                       options.start ? &*options.start : nullptr);
            }
        } catch (...) { failed = true; std::lock_guard lock(mutex); if (!failure) failure = std::current_exception(); }
    };
    std::vector<std::jthread> workers;
    for (std::size_t i = 1; i < std::min<std::size_t>(options.threads, options.runs); ++i) workers.emplace_back(worker);
    worker(); for (auto& thread : workers) thread.join(); if (failure) std::rethrow_exception(failure);
    return results;
}
std::string event_report(const Profile& profile, const BatchOptions& options, const std::vector<EventResult>& results, std::string_view label) {
    if (results.empty()) throw std::invalid_argument("No event results");
    double total = 0, squares = 0, spent = 0, healing = 0, rerolls = 0, first = 0, restricted = 0, mushrooms_found = 0;
    double shop_visits = 0, purchases = 0, strong_one_hit = 0;
    std::size_t completed_events = 0, invalid = 0; std::uint64_t actions = 0;
    for (const auto& r : results) {
        total += r.completions; squares += static_cast<double>(r.completions) * r.completions;
        spent += r.mushrooms; healing += r.recovery_mushrooms; rerolls += r.reroll_mushrooms; actions += r.actions;
        mushrooms_found += r.mushrooms_found;
        shop_visits += r.reroll_shop_visits; purchases += r.shop_purchases; strong_one_hit += r.strong_one_hit_purchases;
        invalid += r.action_limit ? 1 : 0;
        if (r.first_completion_hours >= 0) { ++completed_events; first += r.first_completion_hours; restricted += r.first_completion_hours; }
        else restricted += options.limits.deadline_hours;
    }
    const auto n = static_cast<double>(results.size());
    const double mean = total / n, se = n > 1 ? std::sqrt(std::max(0.0, (squares - n * mean * mean) / (n - 1)) / n) : 0;
    std::ostringstream out; out << std::setprecision(12);
    out << "{\"schema\":1,\"engine\":" << json_string(engine_version) << ",\"evidence\":\"synthetic\",\"profile\":" << json_string(profile.id)
        << ",\"profile_fingerprint\":" << json_string(profile.fingerprint) << ",\"policy\":" << json_string(label)
        << ",\"events\":" << results.size() << ",\"master_seed\":" << options.seed << ",\"budget_per_event\":" << options.limits.budget
        << ",\"deadline_hours\":" << options.limits.deadline_hours << ",\"restart_health\":" << json_string(options.limits.restart_health)
        << ",\"sleep_until_hour\":" << options.limits.sleep_until_hour << ",\"event_start_hour\":" << options.limits.event_start_hour
        << ",\"initial_run_number\":" << (options.start ? options.start->state.run_number : options.limits.run_number)
        << ",\"start_fingerprint\":" << (options.start ? json_string(options.start->fingerprint()) : "null")
        << ",\"damage_distribution\":" << json_string(profile.damage_distribution)
        << ",\"valid\":" << (invalid == 0 ? "true" : "false") << ",\"action_limit_events\":" << invalid
        << ",\"mean_completions\":" << mean << ",\"mean_completions_mc_se\":";
    if (n > 1) out << se; else out << "null";
    out << ",\"mean_mushrooms\":" << spent / n << ",\"mean_recovery_mushrooms\":" << healing / n
        << ",\"mean_mushrooms_found\":" << mushrooms_found / n
        << ",\"mushroom_budget_basis\":\"gross spend; rewards do not raise the cap\""
        << ",\"mean_reroll_shop_visits\":" << shop_visits / n << ",\"mean_shop_purchases\":" << purchases / n
        << ",\"mean_strong_one_hit_purchases\":" << strong_one_hit / n
        << ",\"mean_reroll_mushrooms\":" << rerolls / n << ",\"mushrooms_per_completion\":";
    if (total > 0) out << spent / total; else out << "null";
    out << ",\"probability_at_least_one_completion\":" << static_cast<double>(completed_events) / n
        << ",\"conditional_first_completion_hours\":";
    if (completed_events) out << first / static_cast<double>(completed_events); else out << "null";
    out << ",\"restricted_first_completion_hours\":" << restricted / n << ",\"actions\":" << actions
        << ",\"uncertainty\":\"Monte Carlo error only; model uncertainty is not included\"}";
    return out.str();
}
namespace {
template<class Add> void features(const Game& game, Action a, Add add) {
    const auto& s = game.state(); const auto& l = game.limits();
    const std::array<double, 12> context{1, s.hp, s.hp < .3 ? 1.0 : 0.0, std::min(s.keys, 20) / 20.0,
        (s.room - 1) / 100.0, (l.deadline_hours - s.elapsed_hours) / l.deadline_hours,
        l.budget ? static_cast<double>(l.budget - s.mushrooms) / l.budget : 0,
        s.effect(EffectKind::one_hit) ? 1.0 : 0.0, s.effect(EffectKind::poison) ? 1.0 : 0.0,
        s.effect(EffectKind::recovery) ? 1.0 : 0.0, s.room % 25 == 0 ? 1.0 : 0.0,
        a.kind == ActionKind::wait ? a.value / game.profile().recovery_hours : s.shop_rerolls / 20.0};
    for (std::size_t j = 0; j < context.size(); ++j) add(ix(a.kind) * 12 + j, context[j]);
    constexpr std::size_t door_offset = 15 * 12, encounter_offset = door_offset + ix(Door::count) * 6;
    constexpr std::size_t gem_offset = encounter_offset + ix(Encounter::count) * 2;
    constexpr std::size_t offer_offset = gem_offset + ix(Gem::count);
    if (a.kind == ActionKind::choose_door) {
        const auto& d = s.doors[static_cast<std::size_t>(a.index)]; const auto base = door_offset + ix(d.kind) * 6;
        for (std::size_t j = 0; j < 5; ++j) add(base + j, context[j]);
        add(base + 5, d.trap && !s.effect(EffectKind::disarm) ? 1 : 0);
    }
    if (s.phase == Phase::encounter) {
        add(encounter_offset + ix(s.encounter) * 2, a.kind == ActionKind::interact || a.kind == ActionKind::fight || a.kind == ActionKind::rps ? 1 : 0);
        add(encounter_offset + ix(s.encounter) * 2 + 1, a.kind == ActionKind::skip || a.kind == ActionKind::flee ? 1 : 0);
    }
    if (a.kind == ActionKind::choose_gem) add(gem_offset + ix(s.gem_offers[static_cast<std::size_t>(a.index)]), 1);
    if (a.kind == ActionKind::buy) {
        const auto& o = s.offers[static_cast<std::size_t>(a.index)]; const auto base = offer_offset + ix(o.effect.kind) * 4;
        add(base, 1); add(base + 1, o.effect.remaining / 10.0); add(base + 2, o.effect.magnitude * (1 - s.hp));
        add(base + 3, o.keys / 6.0);
    }
}
}
Action LearnedPolicy::choose(const Game& game) const {
    const auto actions = legal_actions(game);
    if (actions.empty()) throw std::logic_error("No action available to learned policy");
    Action best = actions.front(); double best_score = -1e300;
    for (auto a : actions) {
        double score = 0; features(game, a, [&](std::size_t i, double value) { score += weights.at(i) * value; });
        if (score > best_score) { best_score = score; best = a; }
    }
    return best;
}
void LearnedPolicy::save(const std::string& path) const {
    std::ofstream out(path); if (!out) throw std::runtime_error("Cannot write learned policy");
    out.exceptions(std::ios::badbit | std::ios::failbit);
    out << "sfld-linear-v1 " << engine_version << ' ' << dimensions << '\n' << std::setprecision(17);
    for (double w : weights) out << w << '\n';
    out.close();
}
LearnedPolicy LearnedPolicy::load(const std::string& path) {
    std::ifstream in(path); std::string tag, version; std::size_t count = 0;
    if (!(in >> tag >> version >> count) || tag != "sfld-linear-v1" || version != engine_version || count != dimensions)
        throw std::invalid_argument("Invalid/incompatible learned policy header");
    LearnedPolicy policy;
    for (auto& w : policy.weights) if (!(in >> w) || !std::isfinite(w) || std::abs(w) > 1e6)
        throw std::invalid_argument("Invalid learned policy coefficient");
    std::string extra; if (in >> extra) throw std::invalid_argument("Unexpected data after policy");
    return policy;
}
LearnedPolicy train_policy(const Profile& profile, const BatchOptions& options, TrainingOptions config, std::ostream& progress) {
    if (config.generations < 1 || config.generations > 10000 || config.population < 4 || config.population > 1024 ||
        config.episodes < 1 || config.episodes > 100000) throw std::invalid_argument("Invalid training size");
    std::mt19937_64 random(options.seed); std::normal_distribution<double> normal;
    LearnedPolicy mean, best; std::array<double, LearnedPolicy::dimensions> deviation; deviation.fill(1);
    for (unsigned generation = 0; generation < config.generations; ++generation) {
        struct Candidate { LearnedPolicy policy; long long completions = 0, progress = 0, spent = 0; };
        std::vector<Candidate> candidates(config.population);
        auto training = options; training.runs = config.episodes;
        // A new training stream each generation; all candidates face the same set.
        training.seed = Random::mix(options.seed ^ UINT64_C(0x747261696e) ^ generation);
        for (unsigned c = 0; c < config.population; ++c) {
            auto& candidate = candidates[c];
            candidate.policy = c == 0 && generation > 0 ? best : mean;
            if (c != 0 || generation == 0)
                for (std::size_t w = 0; w < mean.weights.size(); ++w) candidate.policy.weights[w] += deviation[w] * normal(random);
            const auto results = event_batch(profile, [&](const Game& g) { return candidate.policy.choose(g); }, training);
            for (const auto& r : results) {
                if (r.action_limit) throw std::runtime_error("Training reached the action cap; increase --max-actions");
                candidate.completions += r.completions; candidate.progress += r.room; candidate.spent += r.mushrooms;
            }
        }
        std::stable_sort(candidates.begin(), candidates.end(), [](const Candidate& a, const Candidate& b) {
            if (a.completions != b.completions) return a.completions > b.completions;
            if (a.progress != b.progress) return a.progress > b.progress;
            return a.spent < b.spent;
        });
        best = candidates.front().policy;
        const auto elite = std::max(2U, config.population / 4);
        for (std::size_t w = 0; w < mean.weights.size(); ++w) {
            double average = 0, variance = 0;
            for (unsigned c = 0; c < elite; ++c) average += candidates[c].policy.weights[w] / elite;
            for (unsigned c = 0; c < elite; ++c) variance += std::pow(candidates[c].policy.weights[w] - average, 2) / elite;
            mean.weights[w] = .3 * mean.weights[w] + .7 * average;
            deviation[w] = std::max(.15, .3 * deviation[w] + .7 * std::sqrt(variance));
        }
        progress << "Generation " << generation + 1 << '/' << config.generations << ": training completions/event "
                 << static_cast<double>(candidates.front().completions) / static_cast<double>(config.episodes) << '\n';
    }
    return best;
}
}
