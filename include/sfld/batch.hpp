#pragma once
#include "sfld/policy.hpp"
#include <iosfwd>

namespace sfld {
struct Result {
    bool complete = false;
    bool action_limit = false;
    double hours = 0;
    double active_hours = 0;
    int mushrooms = 0;
    int recovery_mushrooms = 0;
    int reroll_mushrooms = 0;
    int deaths = 0;
    int room = 0;
    int barrels_opened = 0;
    int barrels_skipped = 0;
    std::uint64_t actions = 0;
    std::array<bool, ix(Gem::count)> gems{};
};
struct BatchOptions {
    std::size_t runs = 10000;
    unsigned threads = 1;
    std::uint64_t seed = 42;
    Limits limits;
    std::optional<StartState> start;
};
Result run_one(const Profile&, const Policy&, std::uint64_t, Limits, std::ostream* trace = nullptr, const StartState* start = nullptr);
std::vector<Result> run_batch(const Profile&, const Policy&, const BatchOptions&);
std::string report_json(const Profile&, const Policy&, const BatchOptions&, const std::vector<Result>&);
std::size_t replay(const Profile&, const std::string& trace_path);
}
