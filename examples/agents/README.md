# Frozen independent controllers

`player_a.py` and `player_b.py` expose `choose(observation)` and return a member
of the engine's legal-action menu. They were authored by fresh AI agents using
only the rules, scenario profile and revealed states. They are research baselines,
not recommended live-game strategies. Their source is covered by the repository
MIT license.

The [pilot report](../../docs/EVENT_PILOT.md) records practice limits, model changes,
controller hashes, results and reproduction commands. Neither controller calls
an LLM per decision. The C++ engine remains responsible for all transitions,
time, budget and legality.
