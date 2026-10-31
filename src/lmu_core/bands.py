"""Bands of conditions that laps are kept and filtered by.

Leaderboards keep each driver's best lap per band (ruleset ``top_n_per``), so
filtering by setup, track temperature and fuel shows each driver's best lap in
those conditions, not only their overall best.
"""

from __future__ import annotations

import math

# LapFacts fields that are bands (with setup_fixed, which is one already).
BAND_FIELDS = ("setup_fixed", "track_temp_band", "fuel_band")
TEMP_BAND_C = 5
# Fuel at the start of the lap: (upper limit in litres, band).
FUEL_BANDS = ((10.0, "0-10"), (30.0, "10-30"), (60.0, "30-60"), (math.inf, "60+"))
FUEL_LABELS = {"0-10": "≤10 L", "10-30": "10–30 L", "30-60": "30–60 L", "60+": "60+ L"}


def track_temp_band(track_temp_c: float | None) -> int | None:
    """The lower end of the track temperature's band: 20 for 20 to 25 °C."""
    if track_temp_c is None:
        return None
    return int(math.floor(track_temp_c / TEMP_BAND_C) * TEMP_BAND_C)


def fuel_band(fuel_l: float | None) -> str | None:
    if fuel_l is None:
        return None
    return next(band for limit, band in FUEL_BANDS if fuel_l <= limit)


def describe(setup_fixed: bool | None, temp_band: int | None, fuel: str | None) -> str:
    """E.g. "open setup, track 20–25°, ≤10 L fuel"; whatever is known."""
    parts = []
    if setup_fixed is not None:
        parts.append("fixed setup" if setup_fixed else "open setup")
    if temp_band is not None:
        parts.append(f"track {temp_band}–{temp_band + TEMP_BAND_C}°")
    if fuel is not None:
        parts.append(f"{FUEL_LABELS.get(fuel, fuel)} fuel")
    return ", ".join(parts)
