#pragma once
#include "sfld/policy.hpp"
#include <iosfwd>

namespace sfld {
struct StageActivity {
    int nonboss_fights = 0, flee_actions = 0;
    int keys_spent_on_doors = 0, keys_spent_in_shops = 0;
    int free_shop_purchases = 0, keys_at_exit = 0;
    bool completed = false;
};
struct Result {
    bool complete = false;
    bool action_limit = false;
    double hours = 0;
    double active_hours = 0;
    int mushrooms = 0;
    int mushrooms_found = 0;
    int recovery_mushrooms = 0;
    int reroll_mushrooms = 0;
    int deaths = 0;
    int reentries = 0;
    double recovery_wait_hours = 0;
    std::array<StageActivity, 4> stages{};
    int room = 0;
    int barrels_opened = 0;
    int barrels_skipped = 0;
    double gold_units = 0;
    int lucky_coins = 0;
    int epics = 0;
    int legendaries = 0;
    int shop_purchases = 0;
    int strong_one_hit_purchases = 0;
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
