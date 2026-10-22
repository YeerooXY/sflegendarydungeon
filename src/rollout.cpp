#include "sfld/rollout.hpp"
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>

namespace sfld {
namespace {
bool same_action(Action a, Action b) {
    return a.kind == b.kind && a.index == b.index && a.value == b.value;
}
std::pair<double, double> moments(const std::vector<double>& values) {
    const double n = static_cast<double>(values.size());
    const double mean = std::accumulate(values.begin(), values.end(), 0.0) / n;
    double sum = 0;
    for (double value : values) sum += (value - mean) * (value - mean);
    return {mean, std::sqrt(sum / (n * (n - 1)))};
}
}
RolloutReport evaluate_rollouts(const Game& game, const Controller& continuation, const RolloutOptions& options) {
    if (options.samples < 2 || options.samples > 4096 || options.max_total_actions == 0 || options.max_total_actions > 100000000)
        throw std::invalid_argument("Invalid rollout sample count or action-work limit");
    if (!continuation || game.finished()) throw std::invalid_argument("Rollouts need a controller and a nonterminal decision");
    const auto snapshot = game.planning_snapshot();
    auto actions = legal_actions(game);
    Game reference_game(game.profile(), Random::mix(options.seed ^ UINT64_C(0x91523ec764ab80df)), snapshot);
    const auto reference = continuation(reference_game);
    if (!game.legal(reference)) throw std::invalid_argument("Rollout reference action is illegal");
    auto found = std::find_if(actions.begin(), actions.end(), [&](Action action) { return same_action(action, reference); });
    const auto reference_index = found == actions.end() ? actions.size() : static_cast<std::size_t>(found - actions.begin());
    if (found == actions.end()) actions.push_back(reference);
    if (actions.empty() || actions.size() > 128) throw std::invalid_argument("Invalid rollout action menu");
    RolloutReport report; report.samples = options.samples; report.reference_index = reference_index;
    report.best_index = reference_index;
    std::vector<std::vector<double>> values(actions.size(), std::vector<double>(options.samples));
    const auto planning_master = Random::mix(options.seed ^ UINT64_C(0xa247c18d385ba091));
    for (std::size_t sample = 0; sample < options.samples; ++sample) {
        const auto planning_seed = Random::mix(planning_master ^ static_cast<std::uint64_t>(sample));
        for (std::size_t first = 0; first < actions.size(); ++first) {
            Game branch(game.profile(), planning_seed, snapshot);
            const auto step = [&](Action action) {
                if (report.simulated_actions >= options.max_total_actions) throw std::runtime_error("Rollout action-work limit reached; comparison discarded");
                if (!branch.legal(action)) throw std::runtime_error("Rollout continuation selected an illegal action");
                branch.step(action); ++report.simulated_actions;
            };
            step(actions[first]);
            while (!branch.finished()) step(continuation(branch));
            if (branch.state().actions >= branch.limits().max_actions)
                throw std::runtime_error("Rollout reached the event action safety limit; comparison discarded");
            if (branch.state().mushrooms > snapshot.limits.budget || branch.state().elapsed_hours > snapshot.limits.deadline_hours + 1e-9 ||
                branch.state().completed_runs < snapshot.state.completed_runs)
                throw std::logic_error("Rollout violated event limits or completion accounting");
            values[first][sample] = branch.state().completed_runs - snapshot.state.completed_runs;
        }
    }
    for (std::size_t first = 0; first < actions.size(); ++first) {
        const auto [mean, se] = moments(values[first]);
        std::vector<double> paired(options.samples);
        for (std::size_t sample = 0; sample < options.samples; ++sample)
            paired[sample] = values[first][sample] - values[reference_index][sample];
        const auto [advantage, paired_se] = moments(paired);
        report.actions.push_back({actions[first], mean, se, advantage, paired_se});
    }
    // Equal estimated values retain the continuation's decision. Otherwise
    // retain menu order among equal improvements; no extra tie-breaking reward.
    for (std::size_t first = 0; first < actions.size(); ++first)
        if (report.actions[first].mean > report.actions[report.best_index].mean) report.best_index = first;
    return report;
}
}
