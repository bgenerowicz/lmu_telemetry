"""Splitting a recording into laps.

What the recordings tell us (checked against samples/):

- The ``Lap`` event fires as a lap starts: value ``n`` at ``ts`` means lap
  ``n`` begins at ``ts``. Lap 0 starts with the recording, usually in the pits.
- The ``Lap Time`` event fires as a lap ends, at the same ``ts`` as the next
  ``Lap`` event, with the official time of the lap that just ended. It starts
  with a 0.0 placeholder.
- The last lap usually runs until the recording stops and has no end.
- ``In Pits`` is 1 while in the pit lane.

How invalid laps (track limits) are flagged is still unknown.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from lmu_core.carmodel import car_model
from lmu_core.lapfacts import LapFacts
from lmu_core.recording import Recording

# Event timestamps line up to the sample, but allow for float noise.
_TS_TOLERANCE = 0.05


@dataclass(frozen=True)
class Lap:
    number: int
    start_ts: float
    end_ts: float | None
    lap_time_s: float | None
    starts_in_pits: bool

    @property
    def complete(self) -> bool:
        return self.end_ts is not None

    @property
    def duration_s(self) -> float | None:
        return None if self.end_ts is None else self.end_ts - self.start_ts

    @property
    def is_flying(self) -> bool:
        return self.complete and self.lap_time_s is not None and not self.starts_in_pits


def split_laps(rec: Recording) -> list[Lap]:
    lap_ts, lap_no = rec.event("Lap")
    time_ts, time_val = rec.event("Lap Time")
    pits_ts, pits_val = rec.event("In Pits")

    laps = []
    for i, (start, number) in enumerate(zip(lap_ts, lap_no)):
        end = lap_ts[i + 1] if i + 1 < len(lap_ts) else None
        lap_time = None
        if end is not None:
            match = np.flatnonzero((np.abs(time_ts - end) < _TS_TOLERANCE) & (time_val > 0))
            if len(match):
                lap_time = float(time_val[match[0]])
        laps.append(
            Lap(
                number=int(number),
                start_ts=float(start),
                end_ts=None if end is None else float(end),
                lap_time_s=lap_time,
                starts_in_pits=bool(_value_at(pits_ts, pits_val, start)),
            )
        )
    return laps


def lap_facts(rec: Recording, lap: Lap) -> LapFacts:
    meta = rec.metadata
    return LapFacts(
        track=meta.get("TrackName", ""),
        layout=meta.get("TrackLayout"),
        car=meta.get("CarName", ""),
        car_class=meta.get("CarClass"),
        car_model=car_model(meta.get("CarName", ""), meta.get("CarSetup")),
        # Not in the recording; the uploader will have to get it elsewhere.
        game_version=None,
        session_type=meta.get("SessionType"),
        weather=meta.get("WeatherConditions"),
        track_temp_c=_channel_at(rec, "Track Temperature", lap.start_ts),
        ambient_temp_c=_channel_at(rec, "Ambient Temperature", lap.start_ts),
        rain_pct=_channel_at(rec, "Rain", lap.start_ts),
        track_wetness_pct=_event_at(rec, "Minimum Path Wetness", lap.start_ts),
        # The setup JSON doesn't say whether it is the default setup.
        setup_is_default=None,
        fuel_at_start_l=_channel_at(rec, "Fuel Level", lap.start_ts),
        # Only a compound index is recorded; its name depends on the car.
        tyre_compound=None,
        lap_time_s=lap.lap_time_s,
        is_valid=None,
        is_flying=lap.is_flying,
        driver=meta.get("DriverName"),
        recorded_at=meta.get("RecordingTime"),
        lap_number=lap.number,
    )


def _value_at(ts: np.ndarray, values: np.ndarray, t: float):
    """Value of an event channel at time ``t`` (last change at or before it)."""
    i = np.searchsorted(ts, t + _TS_TOLERANCE, side="right") - 1
    return values[max(i, 0)]


def _event_at(rec: Recording, name: str, t: float) -> float | None:
    if name not in rec.events:
        return None
    ts, values = rec.event(name)
    return float(_value_at(ts, values, t)) if len(ts) else None


def _channel_at(rec: Recording, name: str, t: float) -> float | None:
    if name not in rec.channels:
        return None
    ts, values = rec.channel(name)
    if len(ts) == 0:
        return None
    return float(np.interp(t, ts, values))
