#pragma once
#include "sfld/experiment.hpp"

namespace sfld {
struct RolloutOptions {
    std::size_t samples = 32; // 2..4096 fresh futures per first action.
    std::uint64_t seed = 42; // Planning seed only; unrelated to the live game seed.
    std::uint64_t max_total_actions = 10000000; // Hard guard; incomplete comparisons throw.
};
struct RolloutActionEstimate {
    Action action;
    double mean = 0; // Additional completions before the existing event deadline.
    double standard_error = 0;
    double paired_advantage = 0; // This action minus the reference action, using shared sampled futures.
    double paired_standard_error = 0;
};
struct RolloutReport {
    std::vector<RolloutActionEstimate> actions;
    std::size_t samples = 0;
    std::size_t best_index = 0;
    std::size_t reference_index = 0; // The supplied continuation's initial decision.
    std::uint64_t simulated_actions = 0;
};

// Compare every finite-menu legal first action, plus the continuation's initial
// action if it is legal and not already in that menu. Thereafter follow a
// deterministic controller using only visible state. Never clone the live RNG.
// Shared fresh planning seeds provide paired comparisons; they are not a peek
// at the environment's actual future. Preserve its budget, deadline and restart
// convention. Terminal states, illegal continuation actions, action-limit
// exhaustion or an exceeded work guard throw rather than return partial scores.
RolloutReport evaluate_rollouts(const Game&, const Controller& continuation, const RolloutOptions& = {});
}
