"""The library and the Dash app, against the samples."""

from pathlib import Path

import pytest

from lmu_core.library import load_lap, load_references, save_reference, scan_folder

ROOT = Path(__file__).parent.parent
SAMPLES = ROOT / "samples"
REFERENCES = ROOT / "references"

pytestmark = pytest.mark.skipif(
    not any(SAMPLES.glob("*.duckdb")), reason="no sample recordings present"
)


def test_scan_folder_lists_recordings_and_laps():
    infos, errors = scan_folder(SAMPLES)
    assert errors == []
    algarve = next(i for i in infos if i.track == "Algarve International Circuit")
    assert [l.number for l in algarve.laps] == [1, 2, 3]
    assert algarve.best_lap.number == 3


def test_scan_reports_unreadable_files(tmp_path):
    (tmp_path / "broken.duckdb").write_bytes(b"not a database")
    infos, errors = scan_folder(tmp_path)
    assert infos == [] and len(errors) == 1


def test_save_reference_round_trip(tmp_path):
    info = scan_folder(SAMPLES)[0][0]
    path = save_reference(load_lap(info.path, info.best_lap.number), tmp_path)
    [ref], _ = load_references([tmp_path])
    assert ref.path == str(path)
    assert ref.lap_time_s == pytest.approx(info.best_lap.lap_time_s)


def test_app_builds_and_serves_layout(tmp_path):
    from webapp.app import create_app

    app = create_app([REFERENCES], tmp_path / "refs", initial_folder=str(SAMPLES))
    client = app.server.test_client()
    assert client.get("/").status_code == 200
    assert client.get("/_dash-layout").status_code == 200


def test_results_against_expert_lap(tmp_path):
    """Run the results callback directly: lap 2 against lap 3 saved as an expert lap."""
    from webapp import app as webapp

    infos, _ = scan_folder(SAMPLES)
    recordings = [webapp._info_dict(i) for i in infos]
    info = next(i for i in infos if i.track == "Algarve International Circuit")
    rows = webapp._my_laps(recordings, info.track, info.car)
    lap2 = next(i for i, r in enumerate(rows) if r["lap"] == 2)
    save_reference(load_lap(info.path, 3), tmp_path / "refs")
    [ref] = load_references([tmp_path / "refs"])[0]

    app = webapp.create_app([REFERENCES], tmp_path / "refs")
    results = app.callback_map["results.children"]["callback"]
    view = results.__wrapped__([lap2], f"ref:{ref.path}", rows, recordings, info.track, info.car)
    text = str(view)
    assert "Where you lose time" in text
    assert "+0.816 s" in text


def test_conditions_in_one_line():
    from webapp.app import _fmt_conditions

    assert _fmt_conditions({"weather": "Clear", "ambient_temp_c": 18.3, "track_temp_c": 20.8,
                            "fuel_at_start_l": 76.8}) == "Clear · air 18° · track 21° · 77 L fuel"
    assert _fmt_conditions({"weather": None, "track_temp_c": 25.2}) == "track 25°"
    assert _fmt_conditions({"weather": "Clear", "setup_fixed": 1}) == "Clear · fixed setup"
    assert _fmt_conditions({"setup_fixed": False}) == "open setup"
    assert _fmt_conditions({}) == ""
