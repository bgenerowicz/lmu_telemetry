"""Corner-by-corner analysis of a session's laps.

Without a faster driver's lap, the references are the driver's own laps: for
each segment, the lap that was fastest through it. That gives a theoretical
best lap (all best segments added up) and, per lap and segment, how much time
was lost and what the faster lap did differently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from lmu_core.resample import ProcessedLap
from lmu_core.segments import Segment, find_segments

BRAKE_ON_PCT = 10.0
FULL_THROTTLE_PCT = 95.0

# Differences smaller than these aren't worth mentioning.
BRAKE_POINT_M = 5.0
SPEED_KPH = 2.0
THROTTLE_POINT_M = 10.0
MIN_LOSS_S = 0.03


@dataclass(frozen=True)
class SegmentStats:
    time_s: float
    entry_kph: float
    min_kph: float
    min_at_m: float
    exit_kph: float
    top_kph: float
    brake_at_m: float | None
    brake_peak_pct: float
    full_throttle_at_m: float | None
    part_throttle_m: float


@dataclass(frozen=True)
class Finding:
    segment: Segment
    lap: str
    ref_lap: str
    time_lost_s: float
    advice: list[str]


@dataclass
class SessionAnalysis:
    laps: dict[str, ProcessedLap]
    segments: list[Segment]
    stats: dict[str, list[SegmentStats]]
    best_lap: str
    best_segment_lap: list[str] = field(default_factory=list)

    @property
    def lap_times(self) -> dict[str, float]:
        return {n: float(p.time_s[-1]) for n, p in self.laps.items()}

    @property
    def theoretical_best_s(self) -> float:
        return sum(self.stats[n][i].time_s for i, n in enumerate(self.best_segment_lap))

    def segment_spread_s(self, i: int) -> float:
        """Slowest minus fastest time through segment ``i``: how consistent it is."""
        times = [s[i].time_s for s in self.stats.values()]
        return max(times) - min(times)

    def findings(self, lap: str) -> list[Finding]:
        """Where ``lap`` lost time to the fastest lap in each segment, biggest first."""
        out = []
        for i, seg in enumerate(self.segments):
            ref = self.best_segment_lap[i]
            lost = self.stats[lap][i].time_s - self.stats[ref][i].time_s
            if ref == lap or lost < MIN_LOSS_S:
                continue
            advice = advise(seg, self.stats[lap][i], self.stats[ref][i], self.segments)
            out.append(Finding(seg, lap, ref, lost, advice))
        return sorted(out, key=lambda f: f.time_lost_s, reverse=True)


def analyse(laps: dict[str, ProcessedLap]) -> SessionAnalysis:
    """Analyse flying laps of one track and car, keyed by a display label."""
    if not laps:
        raise ValueError("no laps to analyse")
    best = min(laps, key=lambda n: laps[n].time_s[-1])
    ref = laps[best]
    aligned = {n: _on_grid(p, ref.distance_m) for n, p in laps.items()}
    segments = find_segments(_median_lap(list(aligned.values())))
    stats = {n: [segment_stats(p, s) for s in segments] for n, p in aligned.items()}
    best_segment_lap = [
        min(stats, key=lambda n: stats[n][i].time_s) for i in range(len(segments))
    ]
    return SessionAnalysis(aligned, segments, stats, best, best_segment_lap)


def segment_stats(lap: ProcessedLap, seg: Segment) -> SegmentStats:
    d = lap.distance_m
    m = (d >= seg.start_m) & (d <= seg.end_m)
    dist, t, v = d[m], lap.time_s[m], lap.data["speed_kph"][m]
    brake, throttle = lap.data["brake_pct"][m], lap.data["throttle_pct"][m]
    step = float(np.median(np.diff(d)))

    imin = int(np.argmin(v))
    braking = np.flatnonzero(brake[: imin + 1] >= BRAKE_ON_PCT)
    after_apex = np.flatnonzero(throttle[imin:] >= FULL_THROTTLE_PCT)
    return SegmentStats(
        time_s=float(t[-1] - t[0]),
        entry_kph=float(v[0]),
        min_kph=float(v[imin]),
        min_at_m=float(dist[imin]),
        exit_kph=float(v[-1]),
        top_kph=float(v.max()),
        brake_at_m=float(dist[braking[0]]) if len(braking) else None,
        brake_peak_pct=float(brake.max()),
        full_throttle_at_m=float(dist[imin + after_apex[0]]) if len(after_apex) else None,
        part_throttle_m=float(((throttle < FULL_THROTTLE_PCT) & (brake < BRAKE_ON_PCT)).sum() * step),
    )


def advise(
    seg: Segment,
    lap: SegmentStats,
    ref: SegmentStats,
    segments: list[Segment],
) -> list[str]:
    """Plain-language differences between a lap and the faster reference."""
    tips = []
    if seg.kind == "straight":
        entry_diff = lap.entry_kph - ref.entry_kph
        if entry_diff <= -SPEED_KPH:
            # The straight across the line follows the lap's last corner.
            corners = [s.name for s in segments if s.kind == "corner"]
            source = seg.name.split("–")[0]
            if source == "Start" and corners:
                source = corners[-1]
            tips.append(
                f"You started this straight {-entry_diff:.0f} km/h slower "
                f"({lap.entry_kph:.0f} vs {ref.entry_kph:.0f}). The time is lost at the "
                f"exit of {source}, not on the straight."
            )
        if lap.part_throttle_m - ref.part_throttle_m >= THROTTLE_POINT_M:
            tips.append(
                f"{lap.part_throttle_m - ref.part_throttle_m:.0f} m more off full throttle "
                f"without braking ({lap.part_throttle_m:.0f} m vs {ref.part_throttle_m:.0f} m)."
            )
        if lap.brake_at_m is not None and ref.brake_at_m is not None:
            _brake_tip(tips, lap, ref)
        if not tips:
            tips.append(_speed_summary(lap, ref))
        return tips

    if lap.brake_at_m is not None and ref.brake_at_m is None:
        tips.append("Your faster lap didn't brake here, only lifted.")
    elif lap.brake_at_m is None and ref.brake_at_m is not None:
        tips.append(f"Your faster lap braked at {ref.brake_at_m:.0f} m; you only lifted.")
    elif lap.brake_at_m is not None and ref.brake_at_m is not None:
        _brake_tip(tips, lap, ref)

    min_diff = lap.min_kph - ref.min_kph
    if min_diff <= -SPEED_KPH:
        tip = (
            f"Carry more minimum speed: {lap.min_kph:.0f} vs {ref.min_kph:.0f} km/h."
        )
        if lap.brake_at_m is not None and ref.brake_at_m is not None and (
            lap.brake_at_m - ref.brake_at_m >= BRAKE_POINT_M
        ):
            tip += " You braked later but came out slower: likely overdriving the entry."
        tips.append(tip)
    elif min_diff >= SPEED_KPH:
        tips.append(
            f"You carried {min_diff:.0f} km/h more minimum speed but it didn't pay off; "
            "check the line and the exit."
        )

    if lap.full_throttle_at_m is not None and ref.full_throttle_at_m is not None:
        late = lap.full_throttle_at_m - ref.full_throttle_at_m
        if late >= THROTTLE_POINT_M:
            tips.append(
                f"Full throttle {late:.0f} m later than your faster lap "
                f"({lap.full_throttle_at_m:.0f} m vs {ref.full_throttle_at_m:.0f} m)."
            )
    exit_diff = lap.exit_kph - ref.exit_kph
    if exit_diff <= -SPEED_KPH:
        tips.append(
            f"Exit {-exit_diff:.0f} km/h slower ({lap.exit_kph:.0f} vs {ref.exit_kph:.0f}); "
            "this also costs time on the next straight."
        )
    if not tips:
        tips.append(_speed_summary(lap, ref))
    return tips


def _brake_tip(tips: list[str], lap: SegmentStats, ref: SegmentStats) -> None:
    diff = lap.brake_at_m - ref.brake_at_m
    if diff <= -BRAKE_POINT_M:
        tips.append(
            f"Brake {-diff:.0f} m later: you braked at {lap.brake_at_m:.0f} m, "
            f"your faster lap at {ref.brake_at_m:.0f} m."
        )
    if ref.brake_peak_pct - lap.brake_peak_pct >= 10:
        tips.append(
            f"Brake harder: peak {lap.brake_peak_pct:.0f}% vs {ref.brake_peak_pct:.0f}%."
        )


def _speed_summary(lap: SegmentStats, ref: SegmentStats) -> str:
    return (
        f"No single clear cause. Speeds (entry / min / exit): "
        f"{lap.entry_kph:.0f} / {lap.min_kph:.0f} / {lap.exit_kph:.0f} km/h vs "
        f"{ref.entry_kph:.0f} / {ref.min_kph:.0f} / {ref.exit_kph:.0f} km/h."
    )


def _on_grid(lap: ProcessedLap, grid: np.ndarray) -> ProcessedLap:
    if len(lap.distance_m) == len(grid) and np.allclose(lap.distance_m, grid):
        return lap
    data = {
        k: np.interp(grid, lap.distance_m, v) for k, v in lap.data.items() if k != "distance_m"
    }
    return ProcessedLap(lap.facts, {"distance_m": grid, **data})


def _median_lap(laps: list[ProcessedLap]) -> ProcessedLap:
    """A typical lap for finding segments, less sensitive to one-off moments."""
    data = {k: np.median([p.data[k] for p in laps], axis=0) for k in laps[0].data}
    return ProcessedLap(laps[0].facts, data)
