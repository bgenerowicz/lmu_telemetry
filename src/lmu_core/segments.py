"""Splitting a lap into corners and straights.

Corners are found from the speed trace of a reference lap: every clear dip in
speed is a corner. A corner runs from the speed peak before it (where braking
or lifting starts) to the end of the curve after the slowest point, found from
the curvature of the driven line. Whatever lies between corners is a straight.
Flat-out kinks without a speed dip stay part of their straight.

Segments are distance ranges, so every lap on the same track can be cut at the
same places. Corner numbers count detected corners and won't always match the
track's official turn numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from lmu_core.resample import ProcessedLap

# A dip in speed counts as a corner when it is at least this deep.
MIN_DIP_KPH = 8.0
# The slowest point must be the slowest within this many metres either side.
MIN_SPACING_M = 60
# The driven line is curved where its radius is below this.
CURVE_RADIUS_M = 250.0
# Gaps shorter than this between corners are absorbed into the corner before.
MIN_STRAIGHT_M = 30


@dataclass(frozen=True)
class Segment:
    name: str
    kind: Literal["corner", "straight"]
    start_m: float
    end_m: float
    apex_m: float | None = None
    direction: Literal["L", "R"] | None = None


def find_segments(ref: ProcessedLap) -> list[Segment]:
    d = ref.distance_m
    step = float(np.median(np.diff(d)))
    speed = _smooth(ref.data["speed_kph"], int(15 / step))
    radius, curvature = _radius(ref, step)

    apexes = _speed_minima(speed, int(MIN_SPACING_M / step))
    corners = []
    for n, apex in enumerate(apexes):
        prev_apex = apexes[n - 1] if n else 0
        entry = prev_apex + int(np.argmax(speed[prev_apex : apex + 1]))
        exit_ = apex + int(30 / step)
        if radius is not None:
            j = apex
            # Walk to the end of the curve, bridging short straight bits.
            gap = int(20 / step)
            while j < len(d) - 1 and (radius[j] < CURVE_RADIUS_M or (radius[j : j + gap] < CURVE_RADIUS_M).any()):
                j += 1
            exit_ = max(exit_, j + int(20 / step))
        corners.append([entry, min(exit_, len(d) - 1), apex])

    # Corners may not overlap; a short straight between two is absorbed.
    for a, b in zip(corners, corners[1:]):
        if a[1] > b[0] or (b[0] - a[1]) * step < MIN_STRAIGHT_M:
            a[1] = b[0]

    segments = []
    pos = 0
    for n, (entry, exit_, apex) in enumerate(corners, start=1):
        if (entry - pos) * step >= MIN_STRAIGHT_M:
            prev = "Start" if n == 1 else f"T{n - 1}"
            segments.append(Segment(f"{prev}–T{n}", "straight", d[pos], d[entry]))
        elif segments:
            entry = pos
        else:
            entry = 0
        direction = None
        if curvature is not None:
            direction = "L" if curvature[apex] > 0 else "R"
        segments.append(Segment(f"T{n}", "corner", d[entry], d[exit_], d[apex], direction))
        pos = exit_
    if pos < len(d) - 1:
        last = f"T{len(corners)}" if corners else "Start"
        segments.append(Segment(f"{last}–Finish", "straight", d[pos], d[-1]))
    return segments


def _speed_minima(speed: np.ndarray, spacing: int) -> list[int]:
    n = len(speed)
    candidates = [
        i
        for i in range(1, n - 1)
        if speed[i] == speed[max(0, i - spacing) : i + spacing + 1].min()
        and speed[i] < speed[i - 1] + 1e-9
        and speed[i] < speed[i + 1]
    ]
    kept = []
    for k, i in enumerate(candidates):
        left = speed[(candidates[k - 1] if k else 0) : i + 1].max()
        right = speed[i : (candidates[k + 1] if k + 1 < len(candidates) else n - 1) + 1].max()
        if min(left, right) - speed[i] >= MIN_DIP_KPH:
            kept.append(i)
    return kept


def _radius(lap: ProcessedLap, step: float) -> tuple[np.ndarray | None, np.ndarray | None]:
    if "x_m" not in lap.data or "y_m" not in lap.data:
        return None, None
    x = _smooth(lap.data["x_m"], int(15 / step))
    y = _smooth(lap.data["y_m"], int(15 / step))
    heading = np.unwrap(np.arctan2(np.gradient(y), np.gradient(x)))
    curvature = _smooth(np.gradient(heading) / step, int(25 / step))
    return 1 / np.maximum(np.abs(curvature), 1e-6), curvature


def _smooth(a: np.ndarray, window: int) -> np.ndarray:
    if window < 2:
        return a.astype(float)
    padded = np.pad(a.astype(float), (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")
