# Rulesets

A ruleset defines the "standard conditions" a lap must meet to enter the
shared leaderboard, so times on the board are comparable. Since we can't make
the game load a session with given settings, laps are driven freely and checked
afterwards.

Rulesets live in [`rulesets/`](../rulesets/) as YAML. Example:
[`standard-v1.yaml`](../rulesets/standard-v1.yaml).

## Fields

| Key               | Meaning |
|-------------------|---------|
| `id`              | Unique, stable id. Stored with every accepted lap. |
| `name`            | Display name. |
| `description`     | Free text. |
| `top_n`           | Laps kept per leaderboard (default 10), or per `top_n_per` group. |
| `leaderboard_key` | Lap fields that must match for laps to compete (default `track, layout, car, game_version`). |
| `top_n_per`       | Optional, e.g. `[car]`: keep `top_n` laps per car within each leaderboard (a class leaderboard with every car's best drivers). Each driver keeps one lap per group. |
| `on_unknown`      | `warn` (default) or `reject`: what to do when a recording lacks a field a rule needs. |
| `rules`           | Map of lap field → rule. |

## Rules

Each key under `rules` is a field of `LapFacts`
([`src/lmu_core/lapfacts.py`](../src/lmu_core/lapfacts.py)). A rule has one or
more constraints, all of which must hold:

| Constraint   | Example                  | Notes |
|--------------|--------------------------|-------|
| `equals`     | `equals: true`           | |
| `one_of`     | `one_of: [Practice]`     | Strings compare ignoring case and surrounding spaces. |
| `min`, `max` | `min: 20` / `max: 35`    | Inclusive; numbers only. |
| `on_unknown` | `on_unknown: reject`     | Overrides the ruleset default for this rule. |

Unknown keys and unknown fields are errors, so typos fail loudly.

## Results

`Ruleset.check(lap)` returns a report with one result per rule: `pass`,
`fail`, or `unknown`. A lap is accepted when nothing rejects it. `fail` always
rejects; `unknown` rejects only if `on_unknown` is `reject`. Warnings are kept
with the lap, so the UI can flag "accepted, but weather couldn't be verified".

## Changing a ruleset

Don't edit a ruleset once laps have been accepted under it. Copy it to a new
file with a new `id` (e.g. `standard-v2`). Leaderboards are per ruleset, so
old and new times never mix.
