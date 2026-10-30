"""Lap extracts and upload checks, against the Algarve sample (skipped if absent).

Each tampering test edits a genuine extract the way a cheat might and checks
that verification catches it.
"""

import gzip
import shutil
from pathlib import Path

import duckdb
import pytest

from lmu_core.extract import extract_lap, open_extract
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.verify import verify_lap

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)
LENGTH = 4635

pytestmark = pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")


@pytest.fixture(scope="module")
def extract(tmp_path_factory):
    return extract_lap(ALGARVE, 3, tmp_path_factory.mktemp("x") / "lap3.duckdb.gz")


def tampered(extract, tmp_path, *sql):
    """Unpack the extract, run SQL on it, and open the result."""
    db = tmp_path / "t.duckdb"
    with gzip.open(extract, "rb") as g, open(db, "wb") as f:
        shutil.copyfileobj(g, f)
    con = duckdb.connect(str(db))
    for s in sql:
        con.execute(s)
    con.close()
    return Recording(db)


def failed(v):
    return {c.name for c in v.problems}


def test_extract_is_small_and_holds_only_that_lap(extract, tmp_path):
    assert extract.stat().st_size < 3_000_000
    rec = open_extract(extract, tmp_path)
    assert rec.metadata["ExtractedLap"] == "3"
    timed = [l.number for l in split_laps(rec) if l.is_flying]
    assert timed == [3]


def test_genuine_laps_pass(extract, tmp_path):
    with Recording(ALGARVE) as rec:
        for n in (1, 2, 3):
            assert verify_lap(rec, n, LENGTH).ok
    v = verify_lap(open_extract(extract, tmp_path), 3, LENGTH)
    assert v.ok and v.facts.is_valid
    assert v.processed.time_s[-1] == pytest.approx(111.602, abs=0.02)


def test_off_track_lap_is_marked_invalid():
    with Recording(ALGARVE) as rec:
        v = verify_lap(rec, 1, LENGTH)
    assert v.ok
    assert v.facts.is_valid is False


def test_out_lap_and_unfinished_lap_rejected():
    with Recording(ALGARVE) as rec:
        assert failed(verify_lap(rec, 0)) == {"timed lap"}
        assert failed(verify_lap(rec, 4)) == {"timed lap"}
        assert failed(verify_lap(rec, 9)) == {"lap present"}


def test_edited_lap_time_caught(extract, tmp_path):
    rec = tampered(extract, tmp_path, 'UPDATE "Lap Time" SET value = value - 1.5 WHERE value > 0')
    assert "lap time matches timeline" in failed(verify_lap(rec, 3, LENGTH))


def test_edited_sector_time_caught(extract, tmp_path):
    rec = tampered(extract, tmp_path, 'UPDATE "Current Sector1" SET value = value - 0.5')
    assert "sector times consistent" in failed(verify_lap(rec, 3, LENGTH))


def test_faster_speed_trace_caught(extract, tmp_path):
    rec = tampered(extract, tmp_path, 'UPDATE "Ground Speed" SET value = value * 1.15')
    assert "speed matches distance" in failed(verify_lap(rec, 3, LENGTH))


def test_shortcut_through_distance_caught(extract, tmp_path):
    # Skip 300 m of track: distance jumps forward mid-lap.
    rec = tampered(extract, tmp_path,
                   'UPDATE "Lap Dist" SET value = value + 300 WHERE value > 2000 AND value < 4300')
    v = verify_lap(rec, 3, LENGTH)
    assert not v.ok


def test_teleport_caught(extract, tmp_path):
    rec = tampered(extract, tmp_path,
                   'UPDATE "GPS Latitude" SET value = value + 0.001 WHERE rowid = 500')
    assert "no position jumps" in failed(verify_lap(rec, 3, LENGTH))


def test_wrong_track_length_caught(extract, tmp_path):
    v = verify_lap(open_extract(extract, tmp_path), 3, track_length_m=5000)
    assert "distance covers the lap" in failed(v)


def test_known_edge_data_errors_are_skipped(monkeypatch):
    import numpy as np

    from lmu_core import verify
    from lmu_core.lapfacts import LapFacts
    from lmu_core.resample import ProcessedLap

    distance = np.arange(1000.0)
    lateral = np.where((distance > 600) & (distance < 700), 9.0, 2.0)
    data = {"distance_m": distance, "track_edge_m": np.full(1000, 5.0), "path_lateral_m": lateral}
    lap = ProcessedLap(LapFacts(track="Somewhere", car="Car", layout="Long"), data)
    assert verify._off_track(lap) == (4.0, 601.0)
    monkeypatch.setitem(verify.EDGE_DATA_ERRORS, ("Somewhere", "Long"), [(590.0, 710.0)])
    assert verify._off_track(lap) == (-3.0, 0.0)
