"""Driving lines and track edges against the Algarve sample (skipped if absent)."""

from pathlib import Path

import numpy as np
import pytest

from lmu_core.geometry import track_geometry
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import resample_lap

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)

pytestmark = pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")


@pytest.fixture(scope="module")
def laps():
    with Recording(ALGARVE) as rec:
        return [resample_lap(rec, l) for l in split_laps(rec) if l.is_flying]


def test_lines_match_each_laps_own_position(laps):
    # Lines are built from the reference's position plus each lap's Path Lateral;
    # they should land on each lap's own (independent) GPS position.
    g = track_geometry(laps[2], laps)
    for lap in laps:
        x, y = g.line(lap)
        err = np.hypot(x - lap.data["x_m"], y - lap.data["y_m"])
        assert np.median(err) < 0.3
        assert np.percentile(err, 95) < 1.0


def test_edges_enclose_the_driving_lines(laps):
    g = track_geometry(laps[2], laps)
    assert g.left_edge_m is not None and g.right_edge_m is not None
    width = g.right_edge_m - g.left_edge_m
    assert 6 < np.median(width) < 20
    inside = [
        np.mean((lap.data["path_lateral_m"] >= g.left_edge_m - 1)
                & (lap.data["path_lateral_m"] <= g.right_edge_m + 1))
        for lap in laps
    ]
    assert min(inside) > 0.95


def test_no_edges_without_track_edge_channel(laps):
    from lmu_core.resample import ProcessedLap

    bare = [ProcessedLap(l.facts, {k: v for k, v in l.data.items() if k != "track_edge_m"})
            for l in laps]
    g = track_geometry(bare[2], bare)
    assert g.left_edge_m is None and g.right_edge_m is None
