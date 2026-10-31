"""Driving lines and track edges, for drawing laps on a map.

Positions come from two channels rather than GPS alone:

- ``Path Lateral``: the car's sideways distance from LMU's track reference
  path (positive = right). It is smooth and the same reference for every
  recording, so laps from different sessions line up.
- ``Track Edge``: the sideways position of the track edge on the side the car
  is on (same sign convention). Combined over several laps it gives both edges.

GPS (``x_m``/``y_m``, quantised to ~0.4 m) is only used once, to place the
reference path itself: centre = reference lap position - its lateral offset.
Checked on the Algarve sample: lines rebuilt this way match each lap's own GPS
position to ~0.1 m (median).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np

from lmu_core.resample import ProcessedLap

# Track Edge reads ~30 m in the pit lane; real edges are well inside this.
MAX_EDGE_M = 20.0
# Sector changes this close to the line are the lap starting, not a sector.
SECTOR_LINE_M = 50.0


@dataclass(frozen=True)
class TrackGeometry:
    distance_m: np.ndarray
    centre_x: np.ndarray
    centre_y: np.ndarray
    # Unit normal pointing to the right of the direction of travel.
    normal_x: np.ndarray
    normal_y: np.ndarray
    # Lateral offsets of the edges (left negative), or None if not recorded.
    left_edge_m: np.ndarray | None
    right_edge_m: np.ndarray | None

    def at(self, lateral_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Map positions of a lateral offset at every grid distance."""
        return self.centre_x + lateral_m * self.normal_x, self.centre_y + lateral_m * self.normal_y

    def line(self, lap: ProcessedLap) -> tuple[np.ndarray, np.ndarray]:
        """A lap's driving line (the lap must be on this geometry's distance grid)."""
        if "path_lateral_m" in lap.data:
            return self.at(lap.data["path_lateral_m"])
        return lap.data["x_m"], lap.data["y_m"]


def track_geometry(ref: ProcessedLap, laps: list[ProcessedLap]) -> TrackGeometry | None:
    """Geometry from the reference lap's position; edges from all ``laps``.

    All laps must be on the reference's distance grid.
    """
    if "x_m" not in ref.data or "y_m" not in ref.data:
        return None
    x, y = _smooth(ref.data["x_m"], 9), _smooth(ref.data["y_m"], 9)
    lateral = ref.data.get("path_lateral_m", np.zeros_like(x))

    # The normal of the driven line is close to the path's; one refinement
    # step with the normal of the derived centre line makes them agree.
    nx, ny = _right_normal(x, y)
    cx, cy = x - lateral * nx, y - lateral * ny
    nx, ny = _right_normal(_smooth(cx, 15), _smooth(cy, 15))
    cx, cy = x - lateral * nx, y - lateral * ny

    left, right = _edges(laps)
    return TrackGeometry(ref.distance_m, cx, cy, nx, ny, left, right)


def sector_starts(laps: list[ProcessedLap]) -> list[float]:
    """Where sectors 2, 3, ... start (m), from the first lap that recorded its
    sector; empty if none did (laps saved before sectors were kept)."""
    for p in laps:
        if "sector" in p.data:
            d, sector = p.distance_m, p.data["sector"]
            starts = []
            # A lap put on another lap's grid has in-between values at a change,
            # so changes a few metres apart are one.
            for i in np.flatnonzero(np.diff(sector) != 0) + 1:
                if SECTOR_LINE_M < d[i] < d[-1] - SECTOR_LINE_M and (
                        not starts or d[i] - starts[-1] > SECTOR_LINE_M):
                    starts.append(float(d[i]))
            return starts
    return []


def _edges(laps: list[ProcessedLap]) -> tuple[np.ndarray | None, np.ndarray | None]:
    samples = [p.data["track_edge_m"] for p in laps if "track_edge_m" in p.data]
    if not samples:
        return None, None
    e = np.array(samples)
    e = np.where(np.abs(e) < MAX_EDGE_M, e, np.nan)
    with warnings.catch_warnings():
        # Distances where no lap was on that side give all-NaN slices.
        warnings.simplefilter("ignore", RuntimeWarning)
        left = np.nanmedian(np.where(e < 0, e, np.nan), axis=0)
        right = np.nanmedian(np.where(e > 0, e, np.nan), axis=0)
    return _fill(left), _fill(right)


def _fill(a: np.ndarray) -> np.ndarray | None:
    """Interpolate gaps where no lap was on that side of the track."""
    ok = np.isfinite(a)
    if ok.sum() < 2:
        return None
    i = np.arange(len(a))
    return _smooth(np.interp(i, i[ok], a[ok]), 15)


def _right_normal(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    dx, dy = np.gradient(x), np.gradient(y)
    n = np.hypot(dx, dy)
    n[n == 0] = 1.0
    return dy / n, -dx / n


def _smooth(a: np.ndarray, window: int) -> np.ndarray:
    padded = np.pad(a.astype(float), (window // 2, window - 1 - window // 2), mode="edge")
    return np.convolve(padded, np.ones(window) / window, mode="valid")

