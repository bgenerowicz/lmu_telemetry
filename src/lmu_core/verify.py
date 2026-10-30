"""Checking that an uploaded lap is genuine and valid.

The server runs these on the raw lap extract, so it never trusts numbers
computed on the uploader's PC. They catch edited or stitched-together data
and laps that went off track; someone determined could still fabricate a
whole recording, so accounts and moderation remain the last line.

Track limits: LMU doesn't mark invalid laps in recordings. ``Track Edge`` is
the edge on the side the car is on, so ``|Path Lateral| - |Track Edge|`` is
how far the car's centre is beyond it. On the Algarve sample, kerb-using laps
peak at ~0.9 m and a real off at 4.3 m; a GT3 or prototype is ~2 m wide, so
beyond ~1 m all four wheels are past the edge. Where the game's edge data is
known to be wrong (``EDGE_DATA_ERRORS``), the check skips that stretch.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from lmu_core.lapfacts import LapFacts
from lmu_core.laps import Lap, lap_facts, split_laps
from lmu_core.recording import Recording
from lmu_core.resample import ProcessedLap, resample_lap

OFF_TRACK_M = 1.2
# Stretches (metres from the line) where ``Track Edge`` is narrower than the
# road, by (track, layout). Long Beach: every lap of a 2026 qualifying session
# was ~4 m "off" for ~115 m before turn 1, at 250 km/h, wheel straight, on a
# street circuit with walls.
EDGE_DATA_ERRORS: dict[tuple[str, str | None], list[tuple[float, float]]] = {
    ("Grand Prix of Long Beach", "Grand Prix of Long Beach"): [(570.0, 710.0)],
}
TIME_TOLERANCE_S = 0.05
# Integrated speed vs. distance along the reference line: different lines and
# wheel-speed noise give a few percent either way.
SPEED_DISTANCE_RATIO = (0.95, 1.05)
# Position may not move further between samples than speed allows, plus this.
MAX_JUMP_M = 5.0


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Verification:
    lap_number: int
    checks: list[Check]
    facts: LapFacts | None = None
    processed: ProcessedLap | None = None

    @property
    def ok(self) -> bool:
        """Genuine-looking data. Track limits are reported in ``facts.is_valid``."""
        return all(c.ok for c in self.checks)

    @property
    def problems(self) -> list[Check]:
        return [c for c in self.checks if not c.ok]


def verify_lap(rec: Recording, lap_number: int, track_length_m: float | None = None) -> Verification:
    """Check one lap. ``track_length_m``: known length of this track, if any."""
    checks: list[Check] = []
    v = Verification(lap_number, checks)
    lap = next((l for l in split_laps(rec) if l.number == lap_number), None)
    if lap is None:
        checks.append(Check("lap present", False, f"no lap {lap_number} in the recording"))
        return v
    if not lap.is_flying:
        why = "has no end" if not lap.complete else (
            "has no official time" if lap.lap_time_s is None else "starts in the pits")
        checks.append(Check("timed lap", False, f"lap {lap_number} {why}"))
        return v
    checks.append(Check("timed lap", True, f"official time {lap.lap_time_s:.3f} s"))

    diff = abs(lap.duration_s - lap.lap_time_s)
    checks.append(Check("lap time matches timeline", diff <= TIME_TOLERANCE_S,
                        f"official {lap.lap_time_s:.3f} s, start to finish {lap.duration_s:.3f} s"))
    checks.append(_sectors(rec, lap))

    try:
        p = resample_lap(rec, lap)
    except Exception as exc:  # noqa: BLE001 - malformed data is a failed check, not a crash
        checks.append(Check("distance covers the lap", False, f"could not process: {exc}"))
        return v
    checks.append(_coverage(rec, lap, p, track_length_m))
    checks.append(_speed_vs_distance(p))
    checks.append(_no_jumps(rec, lap))
    checks.append(_inputs_in_range(p))

    off_m, off_at = _off_track(p)
    valid = off_m <= OFF_TRACK_M
    checks_detail = (f"at most {off_m:.1f} m beyond the track edge" if valid
                     else f"off track: {off_m:.1f} m beyond the track edge at {off_at:.0f} m")
    v.facts = dataclasses.replace(lap_facts(rec, lap), is_valid=valid if off_m >= 0 else None)
    v.processed = dataclasses.replace(p, facts=v.facts)
    checks.append(Check("track limits (informational)", True, checks_detail))
    return v


def _sectors(rec: Recording, lap: Lap) -> Check:
    """Sector split events must sit where the sector changes happen."""
    name = "sector times consistent"
    if not {"Current Sector1", "Current Sector2"} <= rec.events.keys():
        return Check(name, True, "no sector events recorded; skipped")
    problems = []
    splits = []
    for event in ("Current Sector1", "Current Sector2"):
        ts, val = rec.event(event)
        inside = (ts > lap.start_ts + TIME_TOLERANCE_S) & (ts < lap.end_ts + TIME_TOLERANCE_S)
        if not inside.any():
            problems.append(f"{event} missing")
            continue
        t, split = ts[inside][0], val[inside][0]
        splits.append(split)
        if abs((t - lap.start_ts) - split) > TIME_TOLERANCE_S:
            problems.append(f"{event} says {split:.3f} s but came at {t - lap.start_ts:.3f} s")
    if len(splits) == 2 and not (0 < splits[0] < splits[1] < lap.lap_time_s):
        problems.append(f"splits out of order: {splits[0]:.3f}, {splits[1]:.3f}, {lap.lap_time_s:.3f}")
    if problems:
        return Check(name, False, "; ".join(problems))
    return Check(name, True, f"S1 {splits[0]:.3f}, S2 {splits[1]:.3f}, lap {lap.lap_time_s:.3f}")


def _coverage(rec: Recording, lap: Lap, p: ProcessedLap, known: float | None) -> Check:
    ts, d = rec.channel("Lap Dist")
    m = (ts > lap.start_ts + 1) & (ts < lap.end_ts - 1)
    back = float(np.max(-np.diff(d[m]), initial=0))
    length = float(p.distance_m[-1])
    problems = []
    if back > 5:
        problems.append(f"distance goes backwards by {back:.0f} m")
    if known is not None and abs(length - known) > 10:
        problems.append(f"track length {length:.0f} m, expected {known:.0f} m")
    detail = f"track length {length:.0f} m"
    return Check("distance covers the lap", not problems, "; ".join(problems) or detail)


def _speed_vs_distance(p: ProcessedLap) -> Check:
    # Time to cover each metre at the recorded speed, summed, vs. the lap time.
    v = np.maximum(p.data["speed_kph"] / 3.6, 1.0)
    t_from_speed = float(np.sum(np.diff(p.distance_m) / ((v[1:] + v[:-1]) / 2)))
    ratio = t_from_speed / float(p.time_s[-1])
    lo, hi = SPEED_DISTANCE_RATIO
    return Check("speed matches distance", lo <= ratio <= hi,
                 f"time from speed / lap time = {ratio:.3f}")


def _no_jumps(rec: Recording, lap: Lap) -> Check:
    if not {"GPS Latitude", "GPS Longitude", "Ground Speed"} <= rec.channels.keys():
        return Check("no position jumps", True, "no position recorded; skipped")
    ts, lat = rec.channel("GPS Latitude")
    _, lon = rec.channel("GPS Longitude")
    m = (ts >= lap.start_ts) & (ts <= lap.end_ts)
    ts, lat, lon = ts[m], lat[m], lon[m]
    x = lon * 111_320.0 * np.cos(np.radians(lat[0]))
    y = lat * 111_320.0
    step = np.hypot(np.diff(x), np.diff(y))
    st, speed = rec.channel("Ground Speed")
    allowed = np.interp(ts[1:], st, speed) / 3.6 * np.diff(ts) * 1.5 + MAX_JUMP_M
    worst = float(np.max(step - allowed, initial=-np.inf))
    return Check("no position jumps", worst <= 0,
                 "ok" if worst <= 0 else f"position jumps {worst:.0f} m more than speed allows")


def _inputs_in_range(p: ProcessedLap) -> Check:
    bad = [
        name for name, lo, hi in (("throttle_pct", 0, 100), ("brake_pct", 0, 100),
                                  ("steering_pct", -100, 100), ("speed_kph", 0, 500))
        if name in p.data and (p.data[name].min() < lo - 1 or p.data[name].max() > hi + 1)
    ]
    return Check("inputs in range", not bad, "ok" if not bad else "out of range: " + ", ".join(bad))


def _off_track(p: ProcessedLap) -> tuple[float, float]:
    """Furthest the car's centre went beyond the track edge, and where (-1 if unknown)."""
    if "track_edge_m" not in p.data or "path_lateral_m" not in p.data:
        return -1.0, 0.0
    edge = p.data["track_edge_m"]
    beyond = np.where(np.abs(edge) < 20, np.abs(p.data["path_lateral_m"]) - np.abs(edge), -np.inf)
    for start, end in EDGE_DATA_ERRORS.get((p.facts.track, p.facts.layout), []):
        beyond[(p.distance_m >= start) & (p.distance_m <= end)] = -np.inf
    i = int(np.argmax(beyond))
    return float(beyond[i]), float(p.distance_m[i])
