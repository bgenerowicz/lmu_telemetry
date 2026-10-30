"""Facts about a single lap that rulesets are checked against.

The reader fills these in from a LMU DuckDB recording (metadata table plus
channels). Any field the recording doesn't provide stays ``None``; the ruleset
decides whether an unknown value rejects the lap or only warns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LapFacts:
    # Identity: these make up the leaderboard key.
    track: str
    car: str
    layout: str | None = None
    car_class: str | None = None
    game_version: str | None = None

    # Session and conditions.
    session_type: str | None = None
    weather: str | None = None
    track_temp_c: float | None = None
    ambient_temp_c: float | None = None
    rain_pct: float | None = None
    track_wetness_pct: float | None = None

    # Car state.
    setup_is_default: bool | None = None
    fuel_at_start_l: float | None = None
    tyre_compound: str | None = None

    # The lap itself.
    lap_time_s: float | None = None
    is_valid: bool | None = None
    is_flying: bool | None = None

    # Who and when; not used by rulesets.
    driver: str | None = None
    recorded_at: str | None = None
    lap_number: int | None = None
