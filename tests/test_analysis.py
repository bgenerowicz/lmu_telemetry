"""Segmentation and analysis against the Algarve sample (skipped if absent)."""

from pathlib import Path

import numpy as np
import pytest

from lmu_core.analysis import advise, analyse, segment_stats
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import ProcessedLap, resample_lap
from lmu_core.segments import Segment

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


def test_fixed_reference_compares_every_section_with_it(analysis):
    laps = analysis.laps
    a = analyse({"you": laps["2"], "ref": laps["3"]}, reference="ref")
    assert a.best_segment_lap == ["ref"] * len(a.segments)
    assert sum(f.time_lost_s for f in a.findings("you")) >= a.lap_times["you"] - a.lap_times["ref"]
    assert a.findings("ref") == []


def made_up_lap(throttle, brake, speed_kph=100.0):
    """A stretch of road, one value per metre, at a steady speed but for the slowest point."""
    n = len(throttle)
    speed = np.full(n, float(speed_kph))
    speed[n - 1] -= 20  # the last metre, so all braking comes before it
    return ProcessedLap(None, {
        "distance_m": np.arange(n, dtype=float), "time_s": np.arange(n) / (speed_kph / 3.6),
        "throttle_pct": np.array(throttle, dtype=float), "brake_pct": np.array(brake, dtype=float),
        "speed_kph": speed,
    })


def stats_and_tips(lap, ref, kind="corner"):
    seg = Segment("T1", kind, 0.0, float(len(ref.distance_m) - 1), 50.0)
    stats = segment_stats(lap, seg)
    return stats, advise(seg, stats, segment_stats(ref, seg), [seg])


def pedal(*parts):
    """Pedal values per metre, from (metres, value) pairs."""
    return [value for metres, value in parts for _ in range(metres)]


REF_THROTTLE = pedal((30, 100), (70, 0))
REF_BRAKE = pedal((30, 0), (40, 100), (30, 0))


def test_coasting_before_the_brakes():
    ref = made_up_lap(REF_THROTTLE, REF_BRAKE)
    stats, tips = stats_and_tips(made_up_lap(pedal((5, 100), (95, 0)), REF_BRAKE), ref)
    assert stats.coast_before_brake_m == pytest.approx(25)
    assert any("coasted 25 m before braking" in t for t in tips)
    # A small difference isn't mentioned.
    _, tips = stats_and_tips(made_up_lap(pedal((15, 100), (85, 0)), REF_BRAKE), ref)
    assert not any("coasted" in t for t in tips)


def test_rolling_slowly_is_not_coasting():
    lap = made_up_lap(pedal((5, 100), (95, 0)), REF_BRAKE, speed_kph=30)
    assert stats_and_tips(lap, lap)[0].coast_m == 0


def test_coasting_on_a_straight():
    no_brake = [0] * 100
    _, tips = stats_and_tips(made_up_lap(pedal((60, 100), (40, 0)), no_brake),
                             made_up_lap([100] * 100, no_brake), kind="straight")
    assert any("coasted 40 m with neither pedal" in t for t in tips)


def test_pedal_overlap():
    ref = made_up_lap(REF_THROTTLE, REF_BRAKE)
    # On the throttle 15 m into the braking zone.
    stats, tips = stats_and_tips(made_up_lap(pedal((45, 100), (55, 0)), REF_BRAKE), ref)
    assert stats.pedal_overlap_s == pytest.approx(15 / (100 / 3.6), abs=0.05)
    assert any("Brake and throttle on together" in t for t in tips)
    # 2 m of it is under the limit.
    _, tips = stats_and_tips(made_up_lap(pedal((32, 100), (68, 0)), REF_BRAKE), ref)
    assert not any("together" in t for t in tips)


def test_sudden_brake_release():
    sudden = made_up_lap(REF_THROTTLE, REF_BRAKE)
    gradual = made_up_lap(REF_THROTTLE, pedal((30, 0), (30, 100), (10, 60), (10, 40), (10, 20), (10, 0)))
    assert any("Ease off the brake" in t for t in stats_and_tips(sudden, gradual)[1])
    # The other way round, the driver is the more gradual one.
    assert not any("Ease off the brake" in t for t in stats_and_tips(gradual, sudden)[1])


def test_brake_release_needs_hard_braking_and_a_release():
    light = made_up_lap(REF_THROTTLE, pedal((30, 0), (40, 30), (30, 0)))
    assert stats_and_tips(light, light)[0].brake_release_s is None
    held = made_up_lap(REF_THROTTLE, pedal((30, 0), (70, 100)))
    assert stats_and_tips(held, held)[0].brake_release_s is None

