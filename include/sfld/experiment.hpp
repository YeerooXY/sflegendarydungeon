#pragma once
#include "sfld/batch.hpp"
#include <functional>

namespace sfld {
// Every controller uses this finite menu. The interactive protocol also accepts
// arbitrary legal wait durations; no menu entry reveals a future draw.
std::vector<Action> legal_actions(const Game&);
std::string observation_json(const Game&);
using Controller = std::function<Action(const Game&)>;
struct EventResult {
    int completions = 0, mushrooms = 0, recovery_mushrooms = 0, reroll_mushrooms = 0;
    int room = 1, deaths = 0;
    double first_completion_hours = -1, elapsed_hours = 0;
    std::uint64_t actions = 0;
    bool action_limit = false;
};
EventResult run_event(const Profile&, const Controller&, std::uint64_t, Limits, const StartState* = nullptr);
std::vector<EventResult> event_batch(const Profile&, const Controller&, const BatchOptions&);
std::string event_report(const Profile&, const BatchOptions&, const std::vector<EventResult>&, std::string_view label);

// A policy learned by population search over visible-state features. No calls to
// the hand-written Policy, Game::step, or random streams during action selection.
struct LearnedPolicy {
    static constexpr std::size_t dimensions = 15 * 12 + ix(Door::count) * 6 + ix(Encounter::count) * 2 + ix(Gem::count) + ix(EffectKind::count) * 4;
    std::array<double, dimensions> weights{};
    Action choose(const Game&) const;
    void save(const std::string&) const;
    static LearnedPolicy load(const std::string&);
};
struct TrainingOptions { unsigned generations = 12, population = 32; std::size_t episodes = 24; };
LearnedPolicy train_policy(const Profile&, const BatchOptions&, TrainingOptions, std::ostream& progress);
int experiment_cli(int argc, char** argv);
}
