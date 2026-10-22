#include "sfld/rl_env.hpp"
#include "sfld/experiment.hpp"
#include "sfld/planning_wire.hpp"
#include "sfld/rule_policy.hpp"
#include <algorithm>
#include <array>
#include <cmath>
#include <limits>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

using namespace sfld;
namespace {
thread_local std::string last_error;
constexpr uint64_t episode_limit = UINT64_C(1) << 24;
constexpr std::array<const char*, 23> action_names{
    "door_0", "door_1", "fight", "flee", "interact", "skip", "buy_0", "buy_1", "reroll",
    "gem_0", "gem_1", "gem_2", "heal_step", "heal_full", "reenter", "restart",
    "wait_hp_20", "wait_hp_40", "wait_hp_60", "wait_hp_80", "wait_hp_100", "wait_boss_safe", "wait_deadline"};
constexpr std::array<const char*, 16> metric_names{
    "completions", "mushrooms", "recovery_mushrooms", "reroll_mushrooms", "elapsed_hours",
    "budget", "episode_index", "profile_index", "actions", "invalid_actions", "hp", "room",
    "deaths", "mushrooms_found", "first_completion_hours", "legendaries"};

template<class F> int32_t checked(F&& fn) {
    try { last_error.clear(); fn(); return 0; }
    catch (const std::exception& e) { last_error = e.what(); return -1; }
    catch (...) { last_error = "Unknown RL environment error"; return -1; }
}
void require(bool value, const char* message) { if (!value) throw std::invalid_argument(message); }

struct Features {
    float* values;
    std::vector<std::string>* names;
    size_t offset = 0;
    void add(double value) {
        if (values) values[offset] = static_cast<float>(value);
        ++offset;
    }
    template<class MakeName> void named(MakeName&& make_name, double value) {
        if (names) names->push_back(make_name());
        add(value);
    }
    template<class E> void categorical(const std::string& prefix, E selected, size_t count, bool visible = true) {
        if (names) {
            for (size_t i = 0; i < count; ++i)
                names->push_back(prefix + "." + std::string(name(static_cast<E>(i))));
        }
        if (values) {
            std::fill_n(values + offset, count, 0.0F);
            if (visible && ix(selected) < count) values[offset + ix(selected)] = 1.0F;
        }
        offset += count;
    }
    void effect(const std::string& prefix, const Effect* e, int turn) {
        const auto field = [&](const char* suffix, double value) {
            if (names) names->push_back(prefix + suffix);
            add(value);
        };
        field(".present", e != nullptr);
        categorical(names ? prefix + ".kind" : std::string{}, e ? e->kind : EffectKind::raider, ix(EffectKind::count), e != nullptr);
        field(".magnitude", e ? e->magnitude : 0);
        field(".remaining_div10", e ? e->remaining / 10.0 : 0);
        field(".starts_in_turns_div10", e ? (e->starts_at - turn) / 10.0 : 0);
        field(".active", e && e->remaining > 0 && e->starts_at <= turn);
        constexpr std::array clocks{"room", "trap", "door", "fight"};
        for (size_t i = 0; i < clocks.size(); ++i) {
            if (names) names->push_back(prefix + ".clock." + clocks[i]);
            add(e && ix(e->clock) == i);
        }
    }
};
size_t encode(const Game* game, float* output, std::vector<std::string>* names = nullptr) {
    const State empty; const Limits no_limits;
    const auto& s = game ? game->state() : empty;
    const auto& l = game ? game->limits() : no_limits;
    const Phase visible_phase = game && s.phase == Phase::recovery ? game->recovery_destination() : s.phase;
    Features f{output, names};
#define SFLD_FEATURE(key, value) f.named([&] { return std::string(key); }, (value))
    f.categorical("phase", s.phase, 7);
    f.categorical("resume_phase", visible_phase, 7);
    SFLD_FEATURE("terminal", game && game->finished());
    SFLD_FEATURE("hp", s.hp); SFLD_FEATURE("keys_div20", s.keys / 20.0); SFLD_FEATURE("room_div100", s.room / 100.0);
    for (int i = 0; i < 4; ++i) SFLD_FEATURE("stage." + std::to_string(i + 1), std::clamp((s.room - 1) / 25, 0, 3) == i);
    SFLD_FEATURE("first_event_run", s.run_number == 1); SFLD_FEATURE("run_number_div10", s.run_number / 10.0);
    SFLD_FEATURE("completed_runs_div10", s.completed_runs / 10.0); SFLD_FEATURE("turn_div100", s.turn / 100.0);
    SFLD_FEATURE("elapsed_hours_div168", s.elapsed_hours / 168.0);
    SFLD_FEATURE("hours_left_div168", std::max(0.0, l.deadline_hours - s.elapsed_hours) / 168.0);
    SFLD_FEATURE("deadline_hours_div168", l.deadline_hours / 168.0);
    SFLD_FEATURE("run_elapsed_hours_div24", (s.elapsed_hours - s.run_start_hours) / 24.0);
    SFLD_FEATURE("budget_div500", l.budget / 500.0); SFLD_FEATURE("mushrooms_left_div500", (l.budget - s.mushrooms) / 500.0);
    SFLD_FEATURE("mushrooms_spent_div500", s.mushrooms / 500.0);
    SFLD_FEATURE("run_mushrooms_spent_div100", (s.mushrooms - s.run_start_mushrooms) / 100.0);
    SFLD_FEATURE("mushrooms_found_div100", s.mushrooms_found / 100.0);
    SFLD_FEATURE("recovery_mushrooms_div500", s.recovery_mushrooms / 500.0); SFLD_FEATURE("reroll_mushrooms_div100", s.reroll_mushrooms / 100.0);
    SFLD_FEATURE("heal_step_price_div100", game ? game->step_price() / 100.0 : 0);
    SFLD_FEATURE("heal_full_price_div100", game && s.hp < 1 ? game->full_price() / 100.0 : 0);
    SFLD_FEATURE("paid_heals_div20", s.paid_steps / 20.0);
    SFLD_FEATURE("shop_rerolls_div20", visible_phase == Phase::shop || visible_phase == Phase::curse_shop ? s.shop_rerolls / 20.0 : 0);
    SFLD_FEATURE("reroll_shop_visits_div20", s.reroll_shop_visits / 20.0); SFLD_FEATURE("reroll_bank_used_div100", s.reroll_bank_used / 100.0);
    SFLD_FEATURE("trial_depth_div5", s.trial_depth / 5.0); SFLD_FEATURE("trial_seen", s.trial_seen);
    SFLD_FEATURE("pending_trial_reward_div5", s.pending_trial_reward / 5.0);
    SFLD_FEATURE("flooded_seconds_div10", visible_phase == Phase::encounter && s.encounter == Encounter::flooded ? s.flooded_seconds / 10 : 0);
    SFLD_FEATURE("deaths_div100", s.deaths / 100.0); SFLD_FEATURE("actions_div10000", s.actions / 10000.0);
    SFLD_FEATURE("action_limit_left_fraction", l.max_actions ? static_cast<double>(l.max_actions - std::min(l.max_actions, s.actions)) / l.max_actions : 0);
    SFLD_FEATURE("epics_div100", s.epics / 100.0); SFLD_FEATURE("legendaries_div10", s.legendaries / 10.0);
    SFLD_FEATURE("gold_reward_units_div100", s.gold_units / 100.0); SFLD_FEATURE("lucky_coins_div100", s.lucky_coins / 100.0);
    for (size_t i = 0; i < s.shops_seen.size(); ++i) SFLD_FEATURE("shops_seen." + std::to_string(i + 1), s.shops_seen[i]);
    constexpr std::array resources{"wood", "stone", "souls", "metal", "arcane", "hourglasses"};
    for (size_t i = 0; i < resources.size(); ++i) SFLD_FEATURE("resources." + std::string(resources[i]) + "_div100", s.resources[i] / 100.0);
    const bool donation_visible = visible_phase == Phase::encounter && s.encounter == Encounter::sated_chest;
    SFLD_FEATURE("donated_resource.none", donation_visible && s.donated_resource < 0);
    for (size_t i = 0; i < resources.size(); ++i) SFLD_FEATURE("donated_resource." + std::string(resources[i]), donation_visible && s.donated_resource == static_cast<int>(i));
    for (size_t i = 0; i < s.gems.size(); ++i) SFLD_FEATURE("owned_gem." + std::string(name(static_cast<Gem>(i))), s.gems[i]);
    for (const auto& [label, effects] : {std::pair{"blessings", &s.blessings}, std::pair{"curses", &s.curses}})
        for (size_t i = 0; i < 3; ++i) f.effect(names ? std::string(label) + "." + std::to_string(i) : std::string{}, i < static_cast<size_t>(effects->size) ? &effects->slots[i] : nullptr, s.turn);
    for (size_t i = 0; i < 2; ++i) {
        const auto& door = s.doors[i]; const auto prefix = names ? "doors." + std::to_string(i) : std::string{};
        const bool visible = visible_phase == Phase::doors;
        f.categorical(names ? prefix + ".kind" : std::string{}, door.kind, ix(Door::count), visible);
        SFLD_FEATURE(prefix + ".trap", visible && door.trap); SFLD_FEATURE(prefix + ".cursed_trap", visible && door.cursed_trap);
        SFLD_FEATURE(prefix + ".key_cost_div6", visible && game ? game->door_cost(door.kind) / 6.0 : 0);
        SFLD_FEATURE(prefix + ".available", visible && game && game->available(door));
    }
    f.categorical("encounter", s.encounter, ix(Encounter::count), visible_phase == Phase::encounter);
    for (size_t i = 0; i < 2; ++i) {
        const bool visible = visible_phase == Phase::shop || visible_phase == Phase::curse_shop;
        const auto prefix = names ? "offers." + std::to_string(i) : std::string{};
        SFLD_FEATURE(prefix + ".keys_div6", visible ? s.offers[i].keys / 6.0 : 0);
        f.effect(names ? prefix + ".effect" : std::string{}, visible ? &s.offers[i].effect : nullptr, s.turn);
    }
    for (size_t i = 0; i < 3; ++i)
        f.categorical(names ? "gem_offers." + std::to_string(i) : std::string{}, s.gem_offers[i], ix(Gem::count), visible_phase == Phase::gems);
    // Append public, already-seen history; never expose future offers. Keeping
    // the original prefix permits an explicit zero-column checkpoint transfer.
    for (size_t i = 0; i < s.offered_gems.size(); ++i)
        SFLD_FEATURE("offered_gem." + std::string(name(static_cast<Gem>(i))), s.offered_gems[i]);
    return f.offset;
#undef SFLD_FEATURE
}
const std::vector<std::string>& observation_names() {
    static const auto result = [] { std::vector<std::string> names; encode(nullptr, nullptr, &names); return names; }();
    return result;
}
Action menu_action(const Game& game, int32_t index) {
    switch (index) {
    case 0: case 1: return {ActionKind::choose_door, index};
    case 2: return {ActionKind::fight}; case 3: return {ActionKind::flee};
    case 4: return {ActionKind::interact}; case 5: return {ActionKind::skip};
    case 6: case 7: return {ActionKind::buy, index - 6}; case 8: return {ActionKind::reroll};
    case 9: case 10: case 11: return {ActionKind::choose_gem, index - 9};
    case 12: return {ActionKind::heal_step}; case 13: return {ActionKind::heal_full};
    case 14: return {ActionKind::reenter}; case 15: return {ActionKind::restart};
    default: {
        require(index >= 16 && index < static_cast<int32_t>(action_names.size()), "RL action index out of range");
        const auto& s = game.state(); const double left = std::max(0.0, game.limits().deadline_hours - s.elapsed_hours);
        if (index == 22) return {ActionKind::wait, 0, left};
        if (index == 21 && s.room % 25 != 0) return {ActionKind::wait, 0, 0};
        const double target = index == 21 ? std::clamp(game.damage_ceiling(true) + .001, .2, 1.0) : (index - 15) * .2;
        return {ActionKind::wait, 0, std::min(left, std::max(0.0, target - s.hp) * game.profile().recovery_hours)};
    }}
}
bool same_action(Action a, Action b) {
    return a.kind == b.kind && a.index == b.index && std::abs(a.value - b.value) <= 1e-10;
}
std::array<uint8_t, action_names.size()> action_mask(const Game& game) {
    std::array<uint8_t, action_names.size()> result{};
    if (game.finished()) return result;
    // Ordinary menu actions are distinct by kind/index. Only recovery waits
    // can coincide (including waits clipped to the event deadline).
    const auto ordinary = [&](int first, int end) {
        for (int i = first; i < end; ++i)
            result[static_cast<size_t>(i)] = game.legal(menu_action(game, i));
    };
    switch (game.state().phase) {
    case Phase::doors: ordinary(0, 2); break;
    case Phase::encounter: ordinary(2, 6); break;
    case Phase::shop: ordinary(5, 9); break;
    case Phase::curse_shop: ordinary(5, 8); break;
    case Phase::gems: ordinary(9, 12); break;
    case Phase::complete: ordinary(15, 16); break;
    case Phase::recovery: {
        ordinary(14, 15);
        std::array<Action, 7> waits{};
        for (size_t i = 16; i < result.size(); ++i) {
            const auto action = waits[i - 16] = menu_action(game, static_cast<int32_t>(i));
            if (!game.legal(action)) continue;
            bool duplicate = false;
            for (size_t j = 16; j < i; ++j)
                if (result[j] && same_action(action, waits[j - 16])) duplicate = true;
            if (!duplicate) result[i] = 1;
        }
        break;
    }
    }
    ordinary(12, 14); // Paid healing is allowed outside recovery as well.
    require(std::any_of(result.begin(), result.end(), [](uint8_t x) { return x != 0; }), "Nonterminal RL state has no legal menu action");
    return result;
}
struct Slot {
    std::unique_ptr<Game> game;
    uint64_t episode = 0;
    size_t profile = 0;
    int budget = 0;
    mutable std::array<uint8_t, action_names.size()> mask{};
    mutable bool mask_ready = false;
    const auto& action_mask() const {
        if (!mask_ready) { mask = ::action_mask(*game); mask_ready = true; }
        return mask;
    }
};
void episode_metrics(const Slot& slot, double* output) {
    const auto& s = slot.game->state();
    const std::array<double, metric_names.size()> row{static_cast<double>(s.completed_runs), static_cast<double>(s.mushrooms),
        static_cast<double>(s.recovery_mushrooms), static_cast<double>(s.reroll_mushrooms), s.elapsed_hours,
        static_cast<double>(slot.budget), static_cast<double>(slot.episode), static_cast<double>(slot.profile),
        static_cast<double>(s.actions), 0, s.hp, static_cast<double>(s.room), static_cast<double>(s.deaths),
        static_cast<double>(s.mushrooms_found), s.first_completion_hours, static_cast<double>(s.legendaries)};
    std::copy(row.begin(), row.end(), output);
}
}

struct sfld_rl_env {
    std::vector<Profile> profiles;
    std::vector<int32_t> budgets;
    std::vector<Slot> slots;
    std::map<std::string, RulePolicy> teachers;
    double deadline = 168;
    double sleep_until_hour = 0, event_start_hour = 0;
    uint64_t max_actions = 100000, seed = 0, next_episode = 0;
    uint8_t seed_namespace = 0;
    bool auto_reset = true, initialized = false, allow_hold = false;
    void fresh(size_t i) {
        require(next_episode < episode_limit, "RL episode seed namespace exhausted (2^24 episodes)");
        const uint64_t episode = next_episode++;
        const uint64_t master = (static_cast<uint64_t>(seed_namespace) << 56) | ((Random::mix(seed) & UINT64_C(0xffffffff)) << 24);
        const uint64_t episode_seed = Random::mix(master ^ episode);
        auto& slot = slots.at(i); slot.episode = episode;
        slot.profile = Random::mix(episode_seed ^ UINT64_C(0x9f4a7c15b8e2d603)) % profiles.size();
        slot.budget = budgets[Random::mix(episode_seed ^ UINT64_C(0x3610ade97bc4825f)) % budgets.size()];
        Limits limits; limits.deadline_hours = deadline; limits.budget = slot.budget;
        limits.max_actions = max_actions; limits.repeat_runs = true; limits.restart_health = "full";
        limits.sleep_until_hour = sleep_until_hour; limits.event_start_hour = event_start_hour;
        slot.game = std::make_unique<Game>(profiles[slot.profile], episode_seed, limits);
        slot.mask_ready = false;
    }
    void observe(float* observations, uint8_t* masks) const {
        require(initialized && observations && masks, "RL environment needs reset and output buffers");
        for (size_t i = 0; i < slots.size(); ++i) {
            encode(slots[i].game.get(), observations + i * observation_names().size());
            const auto& mask = slots[i].action_mask();
            std::copy(mask.begin(), mask.end(), masks + i * action_names.size());
        }
    }
};

struct sfld_rl_snapshot {
    PlanningSnapshot value;
    std::string profile_fingerprint;
};

extern "C" {
sfld_rl_snapshot* sfld_rl_capture(const sfld_rl_env* env, int32_t index) {
    std::unique_ptr<sfld_rl_snapshot> result;
    if (checked([&] {
        require(env && env->initialized && index >= 0 && static_cast<size_t>(index) < env->slots.size(), "Invalid snapshot source");
        const auto& slot = env->slots[static_cast<size_t>(index)];
        require(!slot.game->finished(), "Cannot capture a finished episode");
        result = std::make_unique<sfld_rl_snapshot>(sfld_rl_snapshot{
            slot.game->planning_snapshot(), env->profiles[slot.profile].fingerprint});
    }) != 0) return nullptr;
    return result.release();
}
sfld_rl_snapshot* sfld_rl_fixture(const sfld_rl_env* env, int32_t profile, int32_t budget, const char* text, uint64_t seed) {
    std::unique_ptr<sfld_rl_snapshot> result;
    if (checked([&] {
        require(env && text && profile >= 0 && static_cast<size_t>(profile) < env->profiles.size(), "Invalid fixture arguments");
        require(std::find(env->budgets.begin(), env->budgets.end(), budget) != env->budgets.end(), "Fixture budget not configured");
        Limits limits; limits.budget = budget; limits.deadline_hours = env->deadline;
        limits.max_actions = env->max_actions; limits.repeat_runs = true; limits.restart_health = "full";
        limits.sleep_until_hour = env->sleep_until_hour; limits.event_start_hour = env->event_start_hour;
        const auto& p = env->profiles[static_cast<size_t>(profile)];
        Game game(p, seed, StartState::parse(text), limits);
        result = std::make_unique<sfld_rl_snapshot>(sfld_rl_snapshot{game.planning_snapshot(), p.fingerprint});
    }) != 0) return nullptr;
    return result.release();
}
sfld_rl_snapshot* sfld_rl_planning_fixture(const sfld_rl_env* env, int32_t profile, const char* text) {
    std::unique_ptr<sfld_rl_snapshot> result;
    if (checked([&] {
        require(env && text && profile >= 0 && static_cast<size_t>(profile) < env->profiles.size(), "Invalid planning fixture arguments");
        const auto& p = env->profiles[static_cast<size_t>(profile)];
        auto value = parse_planning_state(p, text);
        result = std::make_unique<sfld_rl_snapshot>(sfld_rl_snapshot{std::move(value), p.fingerprint});
    }) != 0) return nullptr;
    return result.release();
}
void sfld_rl_snapshot_destroy(sfld_rl_snapshot* snapshot) { delete snapshot; }
int32_t sfld_rl_restore(sfld_rl_env* env, int32_t index, const sfld_rl_snapshot* snapshot, uint64_t independent_seed) {
    return checked([&] {
        require(env && env->initialized && snapshot && index >= 0 && static_cast<size_t>(index) < env->slots.size(), "Invalid snapshot restore");
        auto& slot = env->slots[static_cast<size_t>(index)];
        const auto& limits = snapshot->value.limits;
        require(env->profiles[slot.profile].fingerprint == snapshot->profile_fingerprint, "Snapshot profile mismatch");
        require(slot.budget == limits.budget && env->deadline == limits.deadline_hours && env->max_actions == limits.max_actions &&
            env->sleep_until_hour == limits.sleep_until_hour && env->event_start_hour == limits.event_start_hour,
            "Snapshot budget or limits mismatch");
        auto replacement = std::make_unique<Game>(env->profiles[slot.profile], independent_seed, snapshot->value);
        require(!replacement->finished(), "Cannot restore a finished episode");
        slot.game = std::move(replacement);
        slot.mask_ready = false;
    });
}
int32_t sfld_rl_clone_slot(sfld_rl_env* env, int32_t source, int32_t target) {
    return checked([&] {
        require(env && env->initialized && source >= 0 && target >= 0 && static_cast<size_t>(source) < env->slots.size() &&
            static_cast<size_t>(target) < env->slots.size() && source != target, "Invalid clone slots");
        auto& from = env->slots[static_cast<size_t>(source)];
        auto& to = env->slots[static_cast<size_t>(target)];
        require(from.profile == to.profile && from.budget == to.budget, "Clone requires matching profile and budget");
        to.game = std::make_unique<Game>(*from.game);
        to.mask_ready = false;
    });
}
int32_t sfld_rl_enable_hold(sfld_rl_env* env) {
    return checked([&] { require(env && env->initialized && !env->auto_reset, "Hold requires an initialized env without auto-reset"); env->allow_hold = true; });
}
const char* sfld_rl_last_error(void) { return last_error.c_str(); }
int32_t sfld_rl_observation_count(void) { return static_cast<int32_t>(observation_names().size()); }
int32_t sfld_rl_action_count(void) { return static_cast<int32_t>(action_names.size()); }
int32_t sfld_rl_metric_count(void) { return static_cast<int32_t>(metric_names.size()); }
const char* sfld_rl_schema_json(void) {
    static const auto schema = [] {
        std::ostringstream out; out << "{\"schema\":1,\"engine\":" << json_string(engine_version) << ",\"observation_names\":[";
        for (size_t i = 0; i < observation_names().size(); ++i) { if (i) out << ','; out << json_string(observation_names()[i]); }
        out << "],\"action_names\":[";
        for (size_t i = 0; i < action_names.size(); ++i) { if (i) out << ','; out << json_string(action_names[i]); }
        out << "],\"metric_names\":[";
        for (size_t i = 0; i < metric_names.size(); ++i) { if (i) out << ','; out << json_string(metric_names[i]); }
        out << "],\"reward\":\"completion_increment\",\"water_linger_omitted\":true,\"water_limitation\":\"Engine has drowning timing but no duration-dependent water reward; zero-second linger exit is excluded\",\"seed_index_bits\":24,\"auto_reset_truncations\":false}";
        return out.str();
    }();
    return schema.c_str();
}
sfld_rl_env* sfld_rl_create(const char* const* paths, int32_t profile_count, const int32_t* budgets,
        int32_t budget_count, int32_t env_count, double deadline, uint64_t max_actions, uint8_t seed_namespace, int32_t auto_reset) {
    std::unique_ptr<sfld_rl_env> result;
    const auto status = checked([&] {
        require(paths && budgets && profile_count > 0 && profile_count <= 128 && budget_count > 0 && budget_count <= 128, "Invalid RL profile/budget list");
        require(env_count > 0 && env_count <= 4096 && std::isfinite(deadline) && deadline > 0 && deadline <= 1000000 && max_actions > 0, "Invalid RL environment limits");
        require(auto_reset == 0 || auto_reset == 1, "auto_reset must be 0 or 1");
        result = std::make_unique<sfld_rl_env>(); result->profiles.reserve(static_cast<size_t>(profile_count));
        for (int32_t i = 0; i < profile_count; ++i) {
            require(paths[i] && *paths[i], "Empty RL profile path"); result->profiles.push_back(Profile::load(paths[i]));
            require(deadline >= result->profiles.back().action_seconds / 3600, "RL deadline is shorter than one initial game action");
        }
        for (int32_t i = 0; i < budget_count; ++i) { require(budgets[i] >= 0 && budgets[i] <= 1000000, "Invalid RL mushroom budget"); result->budgets.push_back(budgets[i]); }
        result->slots.resize(static_cast<size_t>(env_count)); result->deadline = deadline; result->max_actions = max_actions;
        result->seed_namespace = seed_namespace; result->auto_reset = auto_reset != 0;
    });
    return status == 0 ? result.release() : nullptr;
}
void sfld_rl_destroy(sfld_rl_env* env) { delete env; }
int32_t sfld_rl_set_daily_schedule(sfld_rl_env* env, double sleep_until_hour, double event_start_hour) {
    return checked([&] {
        require(env && !env->initialized, "Set daily schedule before the first reset");
        require(std::isfinite(sleep_until_hour) && sleep_until_hour >= 0 && sleep_until_hour < 24 &&
            std::isfinite(event_start_hour) && event_start_hour >= 0 && event_start_hour < 24,
            "Daily schedule hours must be finite and in [0,24)");
        env->sleep_until_hour = sleep_until_hour; env->event_start_hour = event_start_hour;
    });
}
int32_t sfld_rl_reset(sfld_rl_env* env, uint64_t seed, uint64_t index, float* obs, uint8_t* masks) {
    return checked([&] {
        require(env && obs && masks, "Null RL reset argument");
        require(index <= episode_limit - env->slots.size(), "RL reset exceeds episode namespace");
        env->seed = seed; env->next_episode = index;
        for (size_t i = 0; i < env->slots.size(); ++i) env->fresh(i);
        env->initialized = true; env->observe(obs, masks);
    });
}
int32_t sfld_rl_reset_done(sfld_rl_env* env, const int32_t* indices, int32_t count, float* obs, uint8_t* masks) {
    return checked([&] {
        require(env && env->initialized && obs && masks && count >= 0 && (count == 0 || indices), "Invalid RL subset reset");
        require(static_cast<uint64_t>(count) <= episode_limit - env->next_episode, "RL reset exceeds episode namespace");
        std::set<int32_t> seen;
        for (int32_t j = 0; j < count; ++j) {
            const int32_t i = indices[j];
            require(i >= 0 && static_cast<size_t>(i) < env->slots.size() && seen.insert(i).second, "Invalid or duplicate RL reset index");
            require(env->slots[static_cast<size_t>(i)].game->finished(), "reset_done may only reset finished environments");
        }
        for (int32_t j = 0; j < count; ++j) env->fresh(static_cast<size_t>(indices[j]));
        env->observe(obs, masks);
    });
}
uint64_t sfld_rl_next_episode_index(const sfld_rl_env* env) {
    if (!env) { last_error = "Null RL environment"; return UINT64_MAX; }
    return env->next_episode;
}
int32_t sfld_rl_observe(const sfld_rl_env* env, float* obs, uint8_t* masks) {
    return checked([&] { require(env != nullptr, "Null RL environment"); env->observe(obs, masks); });
}
const char* sfld_rl_visible_json(const sfld_rl_env* env, int32_t slot) {
    static thread_local std::string result;
    const int status=checked([&] {
        require(env && env->initialized && slot>=0 && static_cast<size_t>(slot)<env->slots.size(), "Invalid visible observation slot");
        result=ui_observation_json(*env->slots[static_cast<size_t>(slot)].game);
    });
    return status==0 ? result.c_str() : nullptr;
}
int32_t sfld_rl_episode_info(const sfld_rl_env* env, double* metrics) {
    return checked([&] {
        require(env && env->initialized && metrics, "RL episode info needs reset and output buffer");
        for (size_t i = 0; i < env->slots.size(); ++i) episode_metrics(env->slots[i], metrics + i * metric_names.size());
    });
}
int32_t sfld_rl_reward_totals(const sfld_rl_env* env, double* totals) {
    return checked([&] {
        require(env && env->initialized && totals, "Null reward totals or missing reset");
        for (size_t i = 0; i < env->slots.size(); ++i) {
            const auto& s = env->slots[i].game->state();
            totals[3*i] = s.gold_units;
            totals[3*i+1] = s.gold_rewards;
            totals[3*i+2] = s.epics;
        }
    });
}
int32_t sfld_rl_step_wait_targets(sfld_rl_env* env, const int32_t* actions, const double* targets, float* obs, uint8_t* masks,
        float* rewards, uint8_t* terminated, uint8_t* truncated, double* metrics, float* terminal_obs, uint8_t* terminal_masks) {
    return checked([&] {
        require(env && env->initialized && actions && obs && masks && rewards && terminated && truncated && metrics && terminal_obs && terminal_masks, "Null RL step argument or missing reset");
        const size_t n = env->slots.size(), d = observation_names().size(), a = action_names.size();
        std::vector<Action> committed(n);
        std::vector<bool> held(n, false);
        // Preflight the entire vector before any simulator mutation.
        for (size_t i = 0; i < n; ++i) {
            if (env->allow_hold && actions[i] == -1) { held[i] = true; continue; }
            require(actions[i] >= 0 && static_cast<size_t>(actions[i]) < a, "RL action index out of range");
            const auto& game = *env->slots[i].game;
            if (targets && targets[i] != -1) {
                require(actions[i] >= 16 && std::isfinite(targets[i]) && targets[i] >= .2 && targets[i] <= 1,
                    "Custom recovery target needs a wait action and HP fraction in [.2,1]");
                const double hours = std::min(game.limits().deadline_hours - game.state().elapsed_hours,
                    (targets[i] - game.state().hp) * game.profile().recovery_hours);
                committed[i] = {ActionKind::wait, 0, hours};
                require(game.legal(committed[i]), "Illegal custom recovery wait; batch unchanged");
            } else {
                const auto& mask = env->slots[i].action_mask();
                require(mask[static_cast<size_t>(actions[i])] != 0, "Illegal RL action; batch unchanged");
                committed[i] = menu_action(game, actions[i]);
            }
        }
        if (env->auto_reset) require(env->next_episode <= episode_limit - n, "Insufficient RL namespace space for auto-reset batch");
        std::fill(terminal_obs, terminal_obs + n * d, 0.0F); std::fill(terminal_masks, terminal_masks + n * a, 0);
        for (size_t i = 0; i < n; ++i) {
            auto& slot = env->slots[i]; auto& game = *slot.game;
            if (held[i]) { rewards[i] = 0; terminated[i] = truncated[i] = 0; episode_metrics(slot, metrics + i * metric_names.size()); continue; }
            const int before = game.state().completed_runs;
            game.step(committed[i]);
            slot.mask_ready = false;
            const auto& s = game.state();
            rewards[i] = static_cast<float>(s.completed_runs - before);
            truncated[i] = s.actions >= game.limits().max_actions;
            terminated[i] = game.finished() && !truncated[i];
            episode_metrics(slot, metrics + i * metric_names.size());
            if (terminated[i] || truncated[i]) {
                encode(&game, terminal_obs + i * d);
                const auto& terminal_mask = slot.action_mask();
                std::copy(terminal_mask.begin(), terminal_mask.end(), terminal_masks + i * a);
            }
        }
        for (size_t i = 0; i < n; ++i) if (terminated[i] && env->auto_reset) env->fresh(i);
        env->observe(obs, masks);
    });
}
int32_t sfld_rl_step(sfld_rl_env* env, const int32_t* actions, float* obs, uint8_t* masks,
        float* rewards, uint8_t* terminated, uint8_t* truncated, double* metrics, float* terminal_obs, uint8_t* terminal_masks) {
    return sfld_rl_step_wait_targets(env, actions, nullptr, obs, masks, rewards, terminated, truncated, metrics, terminal_obs, terminal_masks);
}
int32_t sfld_rl_teacher_actions(sfld_rl_env* env, int32_t kind, const char* path, int32_t* actions) {
    return checked([&] {
        require(env && env->initialized && actions && kind >= 0 && kind <= 2, "Invalid RL teacher arguments");
        const std::string key = path ? path : "";
        require(kind != 2 || !key.empty(), "Human-rules teacher requires a rules path");
        const RulePolicy* rules = nullptr;
        if (!key.empty()) {
            const auto found = env->teachers.find(key);
            if (found == env->teachers.end()) env->teachers.emplace(key, RulePolicy::load(key));
            rules = &env->teachers.at(key);
        }
        for (size_t i = 0; i < env->slots.size(); ++i) {
            const auto& game = *env->slots[i].game; actions[i] = -1;
            if (game.finished()) continue;
            Policy policy; policy.strategy = Strategy::key_stockpile; policy.survival_aware = kind == 1;
            policy.shop_allowance = game.limits().budget ? 5 : 0; policy.shop_key_reserve = game.limits().budget ? 4 : 0;
            const Action action = kind == 2 ? rules->choose(game) :
                rules && game.state().phase == Phase::gems ? choose_rule_gem(game, rules->gem_rules) : policy.choose(game);
            const auto& mask = env->slots[i].action_mask();
            for (size_t j = 0; j < mask.size(); ++j)
                if (mask[j] && same_action(action, menu_action(game, static_cast<int32_t>(j)))) { actions[i] = static_cast<int32_t>(j); break; }
        }
    });
}
}
