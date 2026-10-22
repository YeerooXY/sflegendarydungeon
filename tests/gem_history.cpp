#include "sfld/engine.hpp"
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace sfld;
namespace {
void require(bool value, const char* expression, int line) {
    if (!value) throw std::runtime_error("Line " + std::to_string(line) + ": " + expression);
}
#define CHECK(expression) require(bool(expression), #expression, __LINE__)
template<class F> void rejects(F fn) {
    bool rejected = false;
    try { fn(); } catch (const std::invalid_argument&) { rejected = true; }
    CHECK(rejected);
}
Profile profile() {
    auto p = Profile::load(std::string(SFLD_SOURCE_DIR) + "/profiles/synthetic.profile");
    p.exclude_offered_gems = true;
    p.gem_pool = p.first_run_gem_pool = p.later_run_gem_pool = {
        Gem::rabbit, Gem::moonstone, Gem::spying, Gem::pendant, Gem::greasy,
        Gem::gambler, Gem::greed, Gem::hero, Gem::time_traveler};
    return p;
}
std::string base(int room, const std::string& phase, const std::string& gems) {
    return "schema=1\nroom=" + std::to_string(room) + "\nrun_number=2\nphase=" + phase +
        "\nhp_percent=80\nkeys=4\ngems=" + gems + "\n";
}
void history_roundtrips_without_granting_rejected_effects() {
    const auto p = profile();
    const auto start = StartState::parse(base(42, "doors", "rabbit") +
        "offered_gems=rabbit,moonstone,spying\ndoors=monster,mystery\n");
    start.validate(p);
    const auto restored = StartState::parse(start.encode());
    restored.validate(p);
    CHECK(restored.state.offered_gems == start.state.offered_gems);
    CHECK(restored.state.gems == start.state.gems);
    CHECK(restored.state.has(Gem::rabbit));
    CHECK(!restored.state.has(Gem::moonstone));
    CHECK(!restored.state.has(Gem::spying));
    CHECK(start.fingerprint() == restored.fingerprint());
    CHECK(start.encode().find("missing history") == std::string::npos);
}
void old_progress_preserves_only_known_history_and_reports_the_limit() {
    const auto p = profile();
    const auto old = StartState::parse(base(51, "gems", "rabbit") + "gem_offers=unknown\n");
    old.validate(p);
    CHECK(std::count(old.state.offered_gems.begin(), old.state.offered_gems.end(), true) == 1);
    CHECK(old.state.offered_gems[ix(Gem::rabbit)]);
    CHECK(old.encode().find("missing history cannot be reconstructed") != std::string::npos);
    const auto restored = StartState::parse(old.encode());
    CHECK(restored.state.offered_gems == old.state.offered_gems);
    // We must not fabricate two earlier rejected stones from the current pool.
    bool saw_moonstone = false;
    for (std::uint64_t seed = 0; seed < 64; ++seed) {
        Game game(p, seed, old);
        for (Gem gem : game.state().gem_offers) {
            CHECK(gem != Gem::rabbit);
            saw_moonstone = saw_moonstone || gem == Gem::moonstone;
        }
    }
    CHECK(saw_moonstone);
}
void known_history_excludes_prior_offers_from_an_unknown_menu() {
    const auto p = profile();
    const auto start = StartState::parse(base(51, "gems", "rabbit") +
        "offered_gems=moonstone,spying\ngem_offers=unknown\n");
    CHECK(start.state.offered_gems[ix(Gem::rabbit)]); // Held ownership is sufficient evidence.
    for (std::uint64_t seed = 0; seed < 32; ++seed) {
        Game game(p, seed, start);
        for (Gem gem : game.state().gem_offers) {
            CHECK(!start.state.offered_gems[ix(gem)]);
            CHECK(game.state().offered_gems[ix(gem)]);
            CHECK(!game.state().has(gem));
        }
        CHECK(std::count(game.state().offered_gems.begin(), game.state().offered_gems.end(), true) == 6);
    }
}
void unknown_menu_does_not_record_the_default_rabbit_placeholder() {
    const auto p = profile();
    const auto start = StartState::parse(base(51, "gems", "moonstone") +
        "offered_gems=moonstone,spying,pendant\ngem_offers=unknown\n");
    CHECK(!start.state.offered_gems[ix(Gem::rabbit)]);
    bool saw_rabbit = false;
    for (std::uint64_t seed = 0; seed < 64; ++seed) {
        Game game(p, seed, start);
        const bool offered = std::find(game.state().gem_offers.begin(), game.state().gem_offers.end(), Gem::rabbit) !=
            game.state().gem_offers.end();
        CHECK(game.state().offered_gems[ix(Gem::rabbit)] == offered);
        CHECK(std::count(game.state().offered_gems.begin(), game.state().offered_gems.end(), true) == 6);
        saw_rabbit = saw_rabbit || offered;
    }
    CHECK(saw_rabbit);
}
void explicit_final_menu_survives_full_history_and_remains_selectable() {
    const auto p = profile();
    const auto start = StartState::parse(base(76, "gems", "rabbit,pendant") +
        "offered_gems=rabbit,moonstone,spying,pendant,greasy,gambler,greed,hero,time_traveler\n"
        "gem_offers=greed,hero,time_traveler\n");
    start.validate(p); // No new offer is needed after this final displayed menu.
    const auto roundtrip = StartState::parse(start.encode());
    CHECK(roundtrip.state.offered_gems == start.state.offered_gems);
    for (int choice = 0; choice < 3; ++choice) {
        Game game(p, 500 + static_cast<std::uint64_t>(choice), roundtrip);
        CHECK(game.state().gem_offers == start.state.gem_offers);
        CHECK(game.legal({ActionKind::choose_gem, choice}));
        game.step({ActionKind::choose_gem, choice});
        CHECK(game.state().has(start.state.gem_offers[static_cast<std::size_t>(choice)]));
        CHECK(game.state().offered_gems == start.state.offered_gems);
    }
}
void explicit_menu_is_unioned_even_when_legacy_history_is_missing() {
    const auto p = profile();
    const auto start = StartState::parse(base(51, "gems", "rabbit") +
        "gem_offers=greed,hero,time_traveler\n");
    start.validate(p);
    CHECK(std::count(start.state.offered_gems.begin(), start.state.offered_gems.end(), true) == 4);
    for (Gem gem : start.state.gem_offers) CHECK(start.state.offered_gems[ix(gem)]);
    CHECK(start.encode().find("missing history") != std::string::npos);
    Game game(p, 10, start);
    CHECK(game.state().gem_offers == start.state.gem_offers);
    CHECK(game.state().offered_gems == start.state.offered_gems);
}
void malformed_or_impossible_history_is_rejected() {
    const auto p = profile();
    const auto prefix = base(42, "doors", "rabbit") + "doors=monster,mystery\n";
    for (const std::string value : {"rabbit,rabbit", "not_a_gem", "true", "rabbit,,spying", "none,rabbit"})
        rejects([&] { StartState::parse(prefix + "offered_gems=" + value + "\n"); });
    rejects([&] { StartState::parse(prefix + "offered_gems=rabbit\noffered_gems=spying\n"); });
    rejects([&] {
        StartState::parse(prefix + "offered_gems=rabbit,moonstone,spying,pendant\n").validate(p);
    });
    rejects([&] {
        StartState::parse(base(51, "gems", "rabbit") + "gem_offers=greed,greed,hero\n").validate(p);
    });
    rejects([&] {
        StartState::parse(base(51, "gems", "rabbit") + "gem_offers=rabbit,greed,hero\n").validate(p);
    });
}
void legacy_toggle_retains_history_but_does_not_exclude_rejected_stones() {
    auto p = profile(); p.exclude_offered_gems = false;
    const auto start = StartState::parse(base(51, "gems", "rabbit") +
        "offered_gems=rabbit,moonstone,spying\ngem_offers=unknown\n");
    bool repeated = false;
    for (std::uint64_t seed = 0; seed < 64; ++seed) {
        Game game(p, seed, start);
        for (Gem gem : game.state().gem_offers) {
            CHECK(gem != Gem::rabbit);
            CHECK(game.state().offered_gems[ix(gem)]);
            repeated = repeated || start.state.offered_gems[ix(gem)];
        }
    }
    CHECK(repeated);
}
}

int main() {
    try {
        history_roundtrips_without_granting_rejected_effects();
        old_progress_preserves_only_known_history_and_reports_the_limit();
        known_history_excludes_prior_offers_from_an_unknown_menu();
        unknown_menu_does_not_record_the_default_rabbit_placeholder();
        explicit_final_menu_survives_full_history_and_remains_selectable();
        explicit_menu_is_unioned_even_when_legacy_history_is_missing();
        malformed_or_impossible_history_is_rejected();
        legacy_toggle_retains_history_but_does_not_exclude_rejected_stones();
        std::cout << "8 gem-history progress tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
