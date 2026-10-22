#pragma once
#include "sfld/engine.hpp"

namespace sfld {
// Human-readable stone chart: base points plus a small set of named conditions.
// Points express preference, not a probability or a measured stone effect.
struct GemRules {
    GemRules();
    std::array<double, ix(Gem::count)> scores{};
    double escape_pair_bonus = 0;
    double greasy_traveler_bonus = 0;
    double gambler_traveler_adjustment = 0;
    double greasy_with_perfect_escape_adjustment = 0;
    double greasy_second_pick_adjustment = 0;
    double greasy_third_pick_adjustment = 0;
    double spying_pendant_adjustment = 0;
    double gambler_moonstone_adjustment = 0;
    double pearl_with_perfect_escape_adjustment = 0;
    void validate() const;
};

// Pair adjustments apply when the offered stone completes that pair. Greasy's
// and Pearl's perfect-escape adjustments use permanent chance before selecting.
// Second/third pick refer to the choices after rooms 50/75, respectively.
double score_rule_gem(const Game&, const GemRules&, Gem);
Action choose_rule_gem(const Game&, const GemRules&);

// Settings use gem.rabbit, gem.greasy, ... and gem.<adjustment field name>.
// Unknown keys return false. Invalid known values throw before changing rules.
bool parse_rule_gem_setting(GemRules&, std::string_view key, double value);
std::string serialize_rule_gems(const GemRules&);
}
