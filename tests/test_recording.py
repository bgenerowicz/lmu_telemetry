"""Tests against the real recordings in samples/ (skipped if there are none)."""

from pathlib import Path

import numpy as np
import pytest

from lmu_core.compare import delta_time
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import ProcessedLap, resample_lap, track_length

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)

pytestmark = pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")


@pytest.fixture(scope="module")
def rec():
    with Recording(ALGARVE) as r:
        yield r


def test_metadata(rec):
    assert rec.metadata["TrackName"] == "Algarve International Circuit"
    assert rec.metadata["SessionType"] == "Practice"
    assert "VM_BRAKE_BALANCE" in rec.setup


def test_continuous_channel_timeline(rec):
    ts, values = rec.channel("GPS Time")
    # GPS Time is the clock itself, so reconstructed timestamps must equal it.
    np.testing.assert_allclose(ts, values, atol=1e-6)


def test_per_wheel_channel_shape(rec):
    _, values = rec.channel("TyresPressure")
    assert values.shape[1] == 4


def test_unknown_channel_raises(rec):
    with pytest.raises(KeyError):
        rec.channel('Brake Pos"; drop table metadata; --')


def test_split_laps(rec):
    laps = split_laps(rec)
    assert [lap.number for lap in laps] == [0, 1, 2, 3, 4]
    assert [lap.is_flying for lap in laps] == [False, True, True, True, False]
    assert laps[0].starts_in_pits
    assert not laps[-1].complete
    assert [round(lap.lap_time_s, 3) for lap in laps[1:4]] == [114.425, 112.418, 111.602]


def test_track_length(rec):
    # Algarve is ~4.65 km; Lap Dist runs along LMU's own reference line.
    assert track_length(rec) == pytest.approx(4635, abs=2)


@pytest.mark.parametrize("index", [1, 2, 3])
def test_resampled_time_matches_official_lap_time(rec, index):
    lap = split_laps(rec)[index]
    processed = resample_lap(rec, lap)
    assert processed.time_s[0] == 0
    assert np.all(np.diff(processed.time_s) > 0)
    assert processed.time_s[-1] == pytest.approx(lap.lap_time_s, abs=0.02)


def test_delta_time_ends_at_lap_time_difference(rec):
    laps = split_laps(rec)
    lap, ref = resample_lap(rec, laps[2]), resample_lap(rec, laps[3])
    _, dt = delta_time(lap, ref)
    assert dt[0] == 0
    assert dt[-1] == pytest.approx(laps[2].lap_time_s - laps[3].lap_time_s, abs=0.02)


def test_save_load_round_trip(rec, tmp_path):
    processed = resample_lap(rec, split_laps(rec)[3])
    processed.save(tmp_path / "lap.lap.npz")
    loaded = ProcessedLap.load(tmp_path / "lap.lap.npz")
    assert loaded.facts == processed.facts
    assert loaded.facts.driver == "Bas Generowicz"
    assert loaded.data.keys() == processed.data.keys()
    np.testing.assert_array_equal(loaded.time_s, processed.time_s)


def test_steering_given_as_a_fraction_is_read_as_percent(rec, monkeypatch):
    lap = split_laps(rec)[3]
    expected = resample_lap(rec, lap).data["steering_pct"]
    # As recordings up to about April 2026 have it: -1 to 1, no unit.
    name = "Steering Pos Unfiltered"
    freq, _ = rec.channels[name]
    channel = rec.channel

    def old_channel(n):
        ts, values = channel(n)
        return ts, values / 100 if n == name else values

    monkeypatch.setitem(rec.channels, name, (freq, ""))
    monkeypatch.setattr(rec, "channel", old_channel)
    np.testing.assert_allclose(resample_lap(rec, lap).data["steering_pct"], expected)
