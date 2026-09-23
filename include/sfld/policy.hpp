#pragma once
#include "sfld/engine.hpp"

namespace sfld {
enum class BarrelPolicy { skip, always, low_hp, adaptive };
struct Policy {
    std::string label = "adaptive";
    BarrelPolicy barrels = BarrelPolicy::adaptive;
    double barrel_hp = .35;
    double revive_hp = .2;
    double login_interval_hours = 0;
    int reroll_limit = 0;
    std::optional<Gem> preferred_gem;
    bool safe_boss_reentry = true;
    Action choose(const Game&) const;
    double effect_value(const State&, const Effect&) const;
    void validate() const;
};
BarrelPolicy barrel_policy_from(std::string_view);
std::string_view name(BarrelPolicy);
}
