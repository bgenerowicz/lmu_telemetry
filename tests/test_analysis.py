"""Segmentation and analysis against the Algarve sample (skipped if absent)."""

from pathlib import Path

import pytest

from desktop.report import render
from lmu_core.analysis import analyse
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import resample_lap

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)

pytestmark = pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")


@pytest.fixture(scope="module")
def analysis():
    with Recording(ALGARVE) as rec:
        laps = {str(l.number): resample_lap(rec, l) for l in split_laps(rec) if l.is_flying}
    return analyse(laps)


def test_segments_cover_the_lap_without_gaps(analysis):
    segs = analysis.segments
    assert segs[0].start_m == 0
    assert segs[-1].end_m == pytest.approx(analysis.laps["3"].distance_m[-1])
    for a, b in zip(segs, segs[1:]):
        assert a.end_m == b.start_m
        assert a.end_m > a.start_m


def test_corners_found(analysis):
    corners = [s for s in analysis.segments if s.kind == "corner"]
    # Algarve has ~15 official turns; fast kinks and linked turns merge.
    assert 8 <= len(corners) <= 15
    assert all(c.start_m <= c.apex_m <= c.end_m for c in corners)


def test_segment_times_add_up_to_lap_time(analysis):
    for n, stats in analysis.stats.items():
        assert sum(s.time_s for s in stats) == pytest.approx(analysis.lap_times[n], abs=1e-6)


def test_best_lap_and_theoretical_best(analysis):
    assert analysis.best_lap == "3"
    assert analysis.theoretical_best_s < analysis.lap_times["3"]


def test_findings_explain_lap_2_mistake(analysis):
    # Lap 2 ran wide/slow through the corner around 1.9 km: its biggest loss.
    top = analysis.findings("2")[0]
    assert top.segment.kind == "corner"
    assert top.segment.start_m < 1950 < top.segment.end_m
    assert top.time_lost_s > 0.4
    assert any("minimum speed" in tip for tip in top.advice)


def test_findings_sorted_and_positive(analysis):
    for lap in analysis.laps:
        losses = [f.time_lost_s for f in analysis.findings(lap)]
        assert losses == sorted(losses, reverse=True)
        assert all(x > 0 for x in losses)


def test_report_renders(analysis):
    page = render(analysis, [ALGARVE.name])
    assert "Where your best lap loses time" in page
    assert "Theoretical best" in page


def test_desktop_combines_recordings_with_labels():
    from desktop.main import load_laps

    laps, sources = load_laps([ALGARVE, ALGARVE])
    assert sorted(laps) == ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3"]
    assert sources == [ALGARVE.name, ALGARVE.name]
