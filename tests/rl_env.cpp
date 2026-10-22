#include "sfld/rl_env.hpp"
#include "sfld/experiment.hpp"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void check(bool value, const char* text, int line) {
    if (!value) throw std::runtime_error(std::to_string(line) + ": " + text + "; " + sfld_rl_last_error());
}
#define CHECK(x) check(bool(x), #x, __LINE__)
std::vector<std::string> schema_names(const std::string& field) {
    const std::string json = sfld_rl_schema_json();
    auto p = json.find("\"" + field + "\":["); CHECK(p != std::string::npos);
    p = json.find('[', p) + 1; const auto end = json.find(']', p);
    std::vector<std::string> result;
    while ((p = json.find('"', p)) < end) {
        const auto close = json.find('"', p + 1); result.push_back(json.substr(p + 1, close - p - 1)); p = close + 1;
    }
    return result;
}
size_t index(const std::vector<std::string>& names, const std::string& name) {
    const auto it = std::find(names.begin(), names.end(), name); CHECK(it != names.end());
    return static_cast<size_t>(it - names.begin());
}
struct Env {
    sfld_rl_env* handle = nullptr;
    size_t n, d = static_cast<size_t>(sfld_rl_observation_count()), a = static_cast<size_t>(sfld_rl_action_count()), m = static_cast<size_t>(sfld_rl_metric_count());
    std::vector<float> obs, reward, final_obs;
    std::vector<uint8_t> mask, done, truncated, final_mask;
    std::vector<double> metrics;
    explicit Env(size_t count = 1, double hours = 168, uint64_t cap = 100000, bool automatic = false, uint8_t seed_namespace = 0xa1)
        : n(count), obs(n*d), reward(n), final_obs(n*d), mask(n*a), done(n), truncated(n), final_mask(n*a), metrics(n*m) {
        const std::string path = std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile";
        const char* profiles[]{path.c_str()}; const int32_t budgets[]{0, 20, 50, 100, 500};
        handle = sfld_rl_create(profiles, 1, budgets, 5, static_cast<int32_t>(n), hours, cap, seed_namespace, automatic ? 1 : 0);
        CHECK(handle != nullptr);
    }
    ~Env() { sfld_rl_destroy(handle); }
    void reset(uint64_t seed = 748219, uint64_t episode = 0) {
        CHECK(sfld_rl_reset(handle, seed, episode, obs.data(), mask.data()) == 0);
        CHECK(sfld_rl_episode_info(handle, metrics.data()) == 0);
    }
    std::vector<int32_t> teacher() const {
        std::vector<int32_t> result(n); CHECK(sfld_rl_teacher_actions(handle, 1, nullptr, result.data()) == 0); return result;
    }
    int step(const std::vector<int32_t>& action) {
        return sfld_rl_step(handle, action.data(), obs.data(), mask.data(), reward.data(), done.data(), truncated.data(),
            metrics.data(), final_obs.data(), final_mask.data());
    }
    int custom_wait(const std::vector<int32_t>& action, const std::vector<double>& targets) {
        return sfld_rl_step_wait_targets(handle, action.data(), targets.data(), obs.data(), mask.data(), reward.data(), done.data(), truncated.data(),
            metrics.data(), final_obs.data(), final_mask.data());
    }
};
void schema_and_reproducibility() {
    const auto obs_names = schema_names("observation_names"), actions = schema_names("action_names");
    CHECK(obs_names.size() == static_cast<size_t>(sfld_rl_observation_count()));
    CHECK(actions.size() == 23); CHECK(std::find(actions.begin(), actions.end(), "linger") == actions.end());
    for (const auto* name : {"owned_gem.rabbit", "curses.2.remaining_div10", "blessings.0.kind.one_hit", "donated_resource.wood",
        "offers.1.effect.magnitude", "doors.1.kind.mystery", "heal_step_price_div100", "shops_seen.4", "resume_phase.doors"})
        CHECK(index(obs_names, name) < obs_names.size());
    for (const auto& name : obs_names) CHECK(name.find("seed") == std::string::npos && name.find("episode_index") == std::string::npos);
    Env first(16), second(16), other(16, 168, 100000, false, 0xa2);
    first.reset(); second.reset(); other.reset();
    CHECK(first.obs == second.obs && first.mask == second.mask); CHECK(first.obs != other.obs);
    CHECK(sfld_rl_next_episode_index(first.handle) == 16);
    const auto old = first.obs;
    CHECK(sfld_rl_observe(first.handle, first.obs.data(), first.mask.data()) == 0); CHECK(old == first.obs);
    for (int step = 0; step < 12; ++step) {
        const auto a = first.teacher(); CHECK(a == first.teacher()); CHECK(a == second.teacher());
        CHECK(first.step(a) == 0 && second.step(a) == 0);
        CHECK(first.obs == second.obs && first.mask == second.mask && first.metrics == second.metrics);
    }
}
void illegal_batch_is_atomic() {
    Env first(2), reference(2); first.reset(); reference.reset();
    const auto old = first.obs; auto actions = first.teacher(); actions[1] = 2; // Fight is illegal at initial doors.
    CHECK(first.step(actions) == -1);
    CHECK(sfld_rl_observe(first.handle, first.obs.data(), first.mask.data()) == 0); CHECK(old == first.obs);
    actions = first.teacher(); CHECK(first.step(actions) == 0 && reference.step(actions) == 0);
    CHECK(first.obs == reference.obs && first.metrics == reference.metrics);
    const int32_t bad_reset[]{0};
    CHECK(sfld_rl_reset_done(first.handle, bad_reset, 1, first.obs.data(), first.mask.data()) == -1);
}
void custom_waits_preserve_control_and_reach_exact_hp() {
    Env control(2), parameterized(2); control.reset(); parameterized.reset();
    auto actions = control.teacher();
    CHECK(parameterized.custom_wait(actions, {-1, .21}) == -1);
    CHECK(sfld_rl_observe(parameterized.handle, parameterized.obs.data(), parameterized.mask.data()) == 0);
    CHECK(control.obs == parameterized.obs);
    CHECK(control.step(actions) == 0 && parameterized.custom_wait(actions, {-1, -1}) == 0);
    CHECK(control.obs == parameterized.obs && control.metrics == parameterized.metrics);
    const auto names = schema_names("observation_names"), metrics = schema_names("metric_names");
    Env one(1); one.reset();
    for (int step = 0; step < 5000 && !one.obs[index(names, "phase.recovery")]; ++step) CHECK(one.step(one.teacher()) == 0);
    CHECK(one.obs[index(names, "phase.recovery")] == 1);
    const double before_hp = one.obs[index(names, "hp")], before_hours = one.metrics[index(metrics, "elapsed_hours")];
    CHECK(before_hp < .21);
    // Even a deduplicated menu wait can carry an explicit legal custom target.
    CHECK(one.custom_wait({22}, {.21}) == 0);
    CHECK(std::abs(one.obs[index(names, "hp")] - .21) < 1e-7);
    CHECK(std::abs(one.metrics[index(metrics, "elapsed_hours")] - before_hours - (.21 - before_hp) * 24) < 1e-6);
    CHECK(one.custom_wait({22}, {.2}) == -1);
}
void terminal_autoreset_and_truncation_are_distinct() {
    const auto names = schema_names("observation_names"), metrics = schema_names("metric_names");
    const auto terminal = index(names, "terminal"), episode = index(metrics, "episode_index");
    Env automatic(2, 2.0/3600, 100000, true); automatic.reset();
    CHECK(automatic.step(automatic.teacher()) == 0);
    for (size_t i = 0; i < 2; ++i) {
        CHECK(automatic.done[i] == 1 && automatic.truncated[i] == 0);
        CHECK(automatic.final_obs[i*automatic.d + terminal] == 1);
        CHECK(automatic.obs[i*automatic.d + terminal] == 0);
        CHECK(automatic.metrics[i*automatic.m + episode] == static_cast<double>(i));
    }
    CHECK(sfld_rl_next_episode_index(automatic.handle) == 4);
    Env limited(2, 168, 1, true); limited.reset();
    CHECK(limited.step(limited.teacher()) == 0);
    CHECK(limited.truncated[0] && !limited.done[0]); CHECK(limited.truncated[1] && !limited.done[1]);
    CHECK(sfld_rl_next_episode_index(limited.handle) == 2); // Safety truncations never auto-reset.
    const auto retained = std::vector<float>(limited.obs.begin() + static_cast<ptrdiff_t>(limited.d), limited.obs.end());
    const int32_t one[]{0};
    CHECK(sfld_rl_reset_done(limited.handle, one, 1, limited.obs.data(), limited.mask.data()) == 0);
    CHECK(std::equal(retained.begin(), retained.end(), limited.obs.begin() + static_cast<ptrdiff_t>(limited.d)));
    CHECK(sfld_rl_next_episode_index(limited.handle) == 3);
}
void full_event_and_phase_visibility() {
    Env env; env.reset();
    const auto names = schema_names("observation_names"), metrics = schema_names("metric_names");
    double rewards = 0; bool saw_gems = false, saw_recovery = false;
    for (int step = 0; step < 20000; ++step) {
        CHECK(std::all_of(env.obs.begin(), env.obs.end(), [](float x) { return std::isfinite(x); }));
        const bool doors = env.obs[index(names, "resume_phase.doors")] != 0;
        const bool shop = env.obs[index(names, "resume_phase.shop")] != 0 || env.obs[index(names, "resume_phase.curse_shop")] != 0;
        const bool gems = env.obs[index(names, "resume_phase.gems")] != 0;
        saw_gems |= gems; saw_recovery |= env.obs[index(names, "phase.recovery")] != 0;
        for (size_t i = 0; i < names.size(); ++i) {
            if ((!doors && names[i].starts_with("doors.")) || (!shop && names[i].starts_with("offers.")) || (!gems && names[i].starts_with("gem_offers."))) CHECK(env.obs[i] == 0);
        }
        const auto actions = env.teacher(); CHECK(actions[0] >= 0); CHECK(env.mask[static_cast<size_t>(actions[0])]);
        CHECK(env.step(actions) == 0); rewards += env.reward[0]; CHECK(!env.truncated[0]);
        if (env.done[0]) break;
    }
    CHECK(env.done[0] && saw_gems && saw_recovery);
    CHECK(rewards == env.metrics[index(metrics, "completions")]); CHECK(rewards > 0);
    CHECK(env.metrics[index(metrics, "mushrooms")] <= env.metrics[index(metrics, "budget")]);
    CHECK(std::none_of(env.mask.begin(), env.mask.end(), [](uint8_t x) { return x != 0; }));
}
void exact_recovery_wait_and_donation_observation() {
    using namespace sfld;
    auto profile = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    State s; s.room = 100; s.turn = 99; s.phase = Phase::recovery; s.encounter = Encounter::boss; s.hp = 0;
    Game game(profile, 881, s);
    const double target = std::clamp(game.damage_ceiling(true) + .001, .2, 1.0);
    const auto actions = legal_actions(game);
    CHECK(std::any_of(actions.begin(), actions.end(), [&](Action a) { return a.kind == ActionKind::wait && std::abs(a.value - target * profile.recovery_hours) < 1e-10; }));
    s.room = 8; s.turn = 7; s.phase = Phase::encounter; s.encounter = Encounter::sated_chest; s.hp = .9; s.donated_resource = 3;
    Game donation(profile, 32, s);
    CHECK(observation_json(donation).find("\"donated_resource\":3") != std::string::npos);
}
void offered_history_is_public_and_resets_per_run() {
    Env env; env.reset();
    const auto names = schema_names("observation_names");
    CHECK(names.size() == 460); CHECK(names[437] == "offered_gem.rabbit");
    std::vector<float> expected(23, 0);
    int menus = 0, deaths_with_history = 0, completions = 0;
    bool previous_menu = false;
    for (int step = 0; step < 20000; ++step) {
        const bool menu = env.obs[index(names, "resume_phase.gems")] != 0;
        if (menu && !previous_menu) ++menus;
        for (size_t gem = 0; gem < expected.size(); ++gem) {
            const auto label = names[437 + gem].substr(std::string("offered_gem.").size());
            for (int slot = 0; slot < 3; ++slot)
                if (env.obs[index(names, "gem_offers." + std::to_string(slot) + "." + label)]) expected[gem] = 1;
            CHECK(env.obs[437 + gem] == expected[gem]);
        }
        if (env.obs[index(names, "phase.recovery")] && std::count(expected.begin(), expected.end(), 1.f)) ++deaths_with_history;
        previous_menu = menu;
        CHECK(env.step(env.teacher()) == 0); CHECK(!env.truncated[0]);
        if (env.reward[0] > 0) { ++completions; std::fill(expected.begin(), expected.end(), 0.f); }
        if (env.done[0]) break;
    }
    CHECK(menus >= 3 && deaths_with_history > 0 && completions > 0);
    env.reset();
    for (size_t gem = 0; gem < expected.size(); ++gem) CHECK(env.obs[437 + gem] == 0);
}
}
int main() {
    try {
        schema_and_reproducibility(); illegal_batch_is_atomic(); terminal_autoreset_and_truncation_are_distinct();
        custom_waits_preserve_control_and_reach_exact_hp();
        full_event_and_phase_visibility(); exact_recovery_wait_and_donation_observation();
        offered_history_is_public_and_resets_per_run();
        std::cout << "RL vector environment: 7 test groups passed\n"; return 0;
    } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
