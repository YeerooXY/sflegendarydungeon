#pragma once
#include "sfld/engine.hpp"

namespace sfld {
enum class BarrelPolicy { skip, always, low_hp, adaptive };
enum class ShopPolicy { adaptive, one_hit, one_hit_8 };
struct Policy {
    std::string label = "adaptive";
    BarrelPolicy barrels = BarrelPolicy::adaptive;
    double barrel_hp = .35;
    double revive_hp = .2;
    double login_interval_hours = 0;
    int reroll_limit = 0;
    ShopPolicy shop = ShopPolicy::adaptive;
    // -1 uses the shared run budget. Zero reserves all mushrooms for rerolls.
    int recovery_budget = -1;
    std::optional<Gem> preferred_gem;
    bool safe_boss_reentry = true;
    Action choose(const Game&) const;
    double effect_value(const State&, const Effect&) const;
    void validate() const;
};
BarrelPolicy barrel_policy_from(std::string_view);
std::string_view name(BarrelPolicy);
ShopPolicy shop_policy_from(std::string_view);
std::string_view name(ShopPolicy);
}
