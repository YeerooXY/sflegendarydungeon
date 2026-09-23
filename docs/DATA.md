# Collecting observations

The next research step is an openly inspectable set of transitions and runs. No
live-game dataset is included, and the simulator's TSV trace is not a replacement
for observed data. We have not implemented a game-log importer yet.

Manual notes or recordings from ordinary play are sufficient to begin. Keep the
game version, event/theme, run number and timestamps. Assign random anonymous run
IDs so a sequence can be reconstructed without an account identity. Preserve raw
displayed quantities as well as normalized HP; rounding can conceal small changes.

## Minimum record

Use one JSON object per line, representing one decision and its observed result.
This is a proposed collection format, not an accepted engine input schema:

```json
{
  "schema_version": 1,
  "source": "manual_observation",
  "anonymous_run_id": "random-non-account-id",
  "game_version": "record-exact-client-version",
  "event": "record-event-and-theme",
  "run_number": 1,
  "sequence": 1,
  "timestamp_utc": "2026-09-23T12:00:00Z",
  "before": {
    "room": 1,
    "phase": "doors",
    "hp_display": "100%",
    "keys": 0,
    "gems": [],
    "blessings": [],
    "curses": [],
    "offers": ["record-left-door-and-trap", "record-right-door-and-trap"]
  },
  "action": {"kind": "choose_door", "side": 0},
  "after": {"room": 1, "phase": "encounter", "encounter": "record-revealed-content"},
  "mushroom_quote": null,
  "mushrooms_deducted": null,
  "notes": "Illustrative fields only; this is not a real observation."
}
```

For usable data, include complete before/after HP, keys, gems and **ordered** effect
slots on every relevant transition, not just the shortened example. Effects need
identity, strength, remaining count, counter type and source. Record both offered
choices, the chosen action, special-room sub-choices, and the actual post-action
state. An unknown field is `null`, never an invented value or an empty list that
incorrectly means none.

For paid actions, record the displayed quote and actual deduction independently,
plus earlier purchases in that run. Include unsuccessful actions and deaths. If a
run is abandoned or the event ends, record the last observed time and reason.

## Most valuable gaps

| Question | Needed observations |
| --- | --- |
| Average time / best gem | Entire runs, all three gem offers, chosen policies, elapsed time and availability |
| Barrel value | Every encountered barrel, decision, HP/effects/gems, resulting identity/strength/duration |
| Fight versus flee | Attempt type, success, precise HP loss, encounter/floor, modifiers, key drops |
| Shop reroll value | Every offer before/after reroll, cost, purchase/skip, resulting effects |
| Recovery spending | Timers, current HP, every quote/deduction, purchase history, re-entry state |
| Exact timing | Short sequences near effect expiration, replacement, a boss and lethal poison |

Door frequencies must be conditioned on floor, theme and gems. Unchosen hidden
contents are unobserved. Do not count them as empty or infer them from the chosen
door. Logging only interesting barrels biases the estimated outcome distribution.
Lethal damage displayed as zero remaining HP is censored: it says damage was at
least the prior HP, not that damage equaled it.

## Acceptance before live-game claims

1. Validate deterministic transitions against observations, including boundary
   cases. Keep disagreements visible with a minimal reproducing sequence.
2. Fit outcome distributions on training runs, estimating uncertainty and checking
   dependence on state instead of assuming independent uniform draws.
3. Hold out **whole runs** for validation. Splitting neighboring actions between
   training and validation can leak run-specific information.
4. Check room frequencies, damage, deaths, recovery, spend and deadline completion
   together, with uncertainty and version/theme scope.
5. Re-run policy comparisons across plausible parameter ranges. If a ranking
   flips within those ranges, publish that sensitivity rather than a single winner.

Contributors should submit only data they are entitled to share. Strip credentials,
session tokens, names and account/server identifiers. Keep game documentation and
third-party datasets under their original terms; this project's MIT license does
not relicense them.
