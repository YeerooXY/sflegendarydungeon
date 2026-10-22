#pragma once
#include "sfld/engine.hpp"

namespace sfld {
enum class BarrelPolicy { skip, always, low_hp, adaptive };
enum class ShopPolicy { adaptive, one_hit, one_hit_8 };
enum class Strategy { baseline, strategic, key_stockpile };
struct Policy {
    std::string label = "adaptive";
    Strategy strategy = Strategy::baseline;
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
    bool survival_aware = false;
    bool adaptive_gems = false;
    bool practical_tactics = false; // Visible-state key reserve, protected curse trades and trial decisions.
    double patient_boss_hp = 0; // After two step refills, wait naturally at room 100 if time permits.
    int early_key_target = 0;
    int shop_allowance = 0; // Accrues at each rerollable shop; unused credit carries across runs.
    int shop_key_reserve = 0;
    int shop_mushroom_reserve = 0; // Part of the shared budget protected from recovery spending.
    Action choose(const Game&) const;
    Action choose_strategic(const Game&) const;
    Action choose_gem_adaptive(const Game&) const;
    double effect_value(const State&, const Effect&, const Profile&) const;
    void validate() const;
};
BarrelPolicy barrel_policy_from(std::string_view);
std::string_view name(BarrelPolicy);
ShopPolicy shop_policy_from(std::string_view);
std::string_view name(ShopPolicy);
Strategy strategy_from(std::string_view);
std::string_view name(Strategy);
}
