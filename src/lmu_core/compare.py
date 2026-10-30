"""Comparing a lap with a reference lap on a shared distance grid."""

from __future__ import annotations

import numpy as np

from lmu_core.resample import ProcessedLap


def delta_time(lap: ProcessedLap, ref: ProcessedLap) -> tuple[np.ndarray, np.ndarray]:
    """Distance and time gap to the reference (positive = slower than reference).

    Both laps are put on the reference's grid, so laps from recordings with
    slightly different track-length estimates still line up.
    """
    d = ref.distance_m
    # Scale for small differences in the recordings' track-length estimates.
    lap_d = lap.distance_m * (d[-1] / lap.distance_m[-1])
    lap_time = np.interp(d, lap_d, lap.time_s)
    return d, lap_time - ref.time_s
