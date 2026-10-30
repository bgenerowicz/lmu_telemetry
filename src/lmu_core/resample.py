"""Resampling a lap onto a fixed distance grid.

Laps are compared by position on track, so every channel is resampled against
``Lap Dist`` (distance along the track's reference line, reset at the line).
``Lap Dist`` is only logged at 10 Hz but is smooth, so time at each grid point
is interpolated from it, and every other channel is then read at those times.

The result, a :class:`ProcessedLap`, is what gets stored and shared: one row
per metre, with the lap's :class:`LapFacts` in the Parquet schema metadata.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from lmu_core.lapfacts import LapFacts
from lmu_core.laps import Lap, lap_facts
from lmu_core.recording import Recording

# Output column -> recording channel. Columns are named with their units.
CHANNELS = {
    "speed_kph": "Ground Speed",
    "throttle_pct": "Throttle Pos",
    "brake_pct": "Brake Pos",
    "steering_pct": "Steering Pos",
    "rpm": "Engine RPM",
    "g_lat": "G Force Lat",
    "g_long": "G Force Long",
    "path_lateral_m": "Path Lateral",
}
EVENTS = {"gear": "Gear"}

_EARTH_M_PER_DEG = 111_320.0
_FACTS_KEY = b"lmu_telemetry.facts"


@dataclass
class ProcessedLap:
    facts: LapFacts
    data: dict[str, np.ndarray]

    @property
    def distance_m(self) -> np.ndarray:
        return self.data["distance_m"]

    @property
    def time_s(self) -> np.ndarray:
        return self.data["time_s"]

    def to_parquet(self, path: str | Path) -> None:
        table = pa.table(self.data)
        meta = {_FACTS_KEY: json.dumps(dataclasses.asdict(self.facts)).encode()}
        pq.write_table(table.replace_schema_metadata(meta), path)

    @classmethod
    def from_parquet(cls, path: str | Path) -> ProcessedLap:
        table = pq.read_table(path)
        facts = LapFacts(**json.loads(table.schema.metadata[_FACTS_KEY]))
        return cls(facts, {name: table[name].to_numpy() for name in table.column_names})


def track_length(rec: Recording) -> float:
    """Track length in metres, from where ``Lap Dist`` resets to zero.

    At each reset, length ~= distance before + one sample step - distance after,
    with the step taken from the samples either side of the reset.
    """
    _, d = rec.channel("Lap Dist")
    steps = np.diff(d)
    resets = np.flatnonzero(steps < -0.5 * d.max())
    resets = resets[(resets >= 1) & (resets + 2 < len(d))]
    if len(resets) == 0:
        return float(d.max())
    local_step = (steps[resets - 1] + steps[resets + 1]) / 2
    return float(np.median(d[resets] + local_step - d[resets + 1]))


def resample_lap(rec: Recording, lap: Lap, step_m: float = 1.0) -> ProcessedLap:
    if not lap.complete:
        raise ValueError(f"lap {lap.number} has no end")
    length = track_length(rec)
    t_dist, dist = _unwrapped_lap_dist(rec, lap, length)

    grid = np.append(np.arange(0.0, length, step_m), length)
    t_grid = np.interp(grid, dist, t_dist)
    data = {"distance_m": grid, "time_s": t_grid - t_grid[0]}

    for column, name in CHANNELS.items():
        if name in rec.channels:
            ts, values = rec.channel(name)
            data[column] = np.interp(t_grid, ts, values)
    for column, name in EVENTS.items():
        if name in rec.events:
            ts, values = rec.event(name)
            i = np.searchsorted(ts, t_grid, side="right") - 1
            data[column] = values[np.clip(i, 0, len(values) - 1)]

    if "GPS Latitude" in rec.channels and "GPS Longitude" in rec.channels:
        ts, lat = rec.channel("GPS Latitude")
        _, lon = rec.channel("GPS Longitude")
        # Local flat projection; the coordinates are only used to draw the map.
        lat0, lon0 = lat[0], lon[0]
        x = (lon - lon0) * _EARTH_M_PER_DEG * np.cos(np.radians(lat0))
        y = (lat - lat0) * _EARTH_M_PER_DEG
        data["x_m"] = np.interp(t_grid, ts, x)
        data["y_m"] = np.interp(t_grid, ts, y)

    return ProcessedLap(lap_facts(rec, lap), data)


def _unwrapped_lap_dist(rec: Recording, lap: Lap, length: float) -> tuple[np.ndarray, np.ndarray]:
    """Lap Dist over the lap as a continuous, increasing distance.

    Includes a second either side of the lap so the grid ends are covered.
    Samples just before the line read ~length and samples just after read ~0;
    each is shifted by whole laps to be nearest to the expected progress.
    """
    ts, d = rec.channel("Lap Dist")
    keep = (ts >= lap.start_ts - 1.0) & (ts <= lap.end_ts + 1.0)
    ts, d = ts[keep], d[keep]
    expected = (ts - lap.start_ts) / (lap.end_ts - lap.start_ts) * length
    d = d + np.round((expected - d) / length) * length
    # np.interp needs increasing x; the car never goes backwards on a flying lap,
    # but guard against small jitter.
    d = np.maximum.accumulate(d)
    d, first = np.unique(d, return_index=True)
    return ts[first], d
