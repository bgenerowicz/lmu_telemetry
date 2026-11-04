"""The library and the Dash app, against the samples."""

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
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
    # The same recording: the same setup.
    assert "The setups are the same." in text
    # The map marks the start/finish line and where sectors 2 and 3 start.
    [viz] = [c for c in view.children if getattr(c, "id", None) == "viz"]
    marks = [a.text for a in viz.children[1].children.figure.layout.annotations]
    assert {"Start/finish", "Sector 2", "Sector 3"} <= set(marks)
    # An arrow for each car, shown (and turned) while hovering the traces.
    arrows = [a for a in viz.children[1].children.figure.layout.annotations if a.name]
    assert [(a.name, a.visible, a.arrowside) for a in arrows] == [
        ("ref-pos", False, "start"), ("lap-pos", False, "start")]


def test_traces_include_rpm():
    from lmu_core.analysis import analyse
    from webapp.charts import ROWS, traces_figure

    info = scan_folder(SAMPLES)[0][0]
    a = analyse({"you": load_lap(info.path, 2), "ref": load_lap(info.path, 3)}, reference="ref")
    fig = traces_figure(a.laps["you"], a.laps["ref"], a.segments, "you", "ref")
    titles = [t.text for t in fig.layout.annotations if t.text in {r[0] for r in ROWS}]
    assert "RPM" in titles and titles.index("RPM") == titles.index("Gear") - 1


def test_map_arrows_point_where_the_front_wheels_point():
    from lmu_core.geometry import track_geometry
    from webapp.charts import front_wheel_angles

    info = scan_folder(SAMPLES)[0][0]
    lap = load_lap(info.path, 3)
    x, y = track_geometry(lap, [lap]).line(lap)
    angles = np.array(front_wheel_angles(lap, x, y))
    travel = np.degrees(np.arctan2(np.gradient(x), np.gradient(y))) % 360
    # Turned from the direction of travel by the steering, at most the lock (18.5°).
    turn = (angles - travel + 180) % 360 - 180
    assert np.abs(turn).max() <= 18.5 * 0.65
    steering = lap.data["steering_pct"]
    assert np.all(np.sign(turn[np.abs(steering) > 5]) == np.sign(steering[np.abs(steering) > 5]))
    no_setup = dataclasses.replace(lap, setup=None)
    assert front_wheel_angles(no_setup, x, y) is None


def test_saved_laps_keep_their_setup(tmp_path):
    info = scan_folder(SAMPLES)[0][0]
    lap = load_lap(info.path, info.best_lap.number)
    assert lap.setup["VM_BRAKE_BALANCE"]["text"] == "52.5:47.5"
    path = save_reference(lap, tmp_path)
    from lmu_core.library import load_reference
    assert load_reference(path).setup == lap.setup


def test_conditions_in_one_line():
    from webapp.app import _fmt_conditions

    assert _fmt_conditions({"weather": "Clear", "ambient_temp_c": 18.3, "track_temp_c": 20.8,
                            "fuel_at_start_l": 76.8}) == "Clear · air 18° · track 21° · 77 L fuel"
    assert _fmt_conditions({"weather": None, "track_temp_c": 25.2}) == "track 25°"
    assert _fmt_conditions({"weather": "Clear", "setup_fixed": 1}) == "Clear · fixed setup"
    assert _fmt_conditions({"setup_fixed": False}) == "open setup"
    assert _fmt_conditions({}) == ""


def test_a_finding_says_which_stretch_to_zoom_to():
    from lmu_core.analysis import Finding
    from lmu_core.segments import Segment
    from webapp.app import _finding_view

    seg = Segment("T1", "corner", 2556.7, 2840.2)
    view = _finding_view(Finding(seg, "lap", "ref", 0.5, ["Brake later."]))
    # The browser splits "<id>.n_clicks" at the first dot, so the id can't have one.
    assert view.id == {"type": "focus", "index": "2557:2840"}


def stamp(when):
    """A local time as recordings store it: in UTC."""
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H_%M_%SZ")


NOW = datetime(2026, 11, 4, 12, 0).astimezone()


def test_filter_laps_by_date_uses_local_days():
    from webapp.app import _filter_laps

    rows = [{"lap": name, "session_type": "Race", "recorded_at": stamp(at)} for name, at in {
        "just after midnight": NOW.replace(hour=0, minute=1),
        "just before midnight": NOW.replace(hour=0, minute=0) - timedelta(minutes=1),
        "6 days ago": NOW - timedelta(days=6),
        "20 days ago": NOW - timedelta(days=20),
        "40 days ago": NOW - timedelta(days=40),
    }.items()] + [{"lap": "unknown date", "session_type": "Race", "recorded_at": None}]

    def laps(period):
        return [r["lap"] for r in _filter_laps(rows, period, "any", NOW)]

    assert laps("today") == ["just after midnight"]
    assert laps("week") == ["just after midnight", "just before midnight", "6 days ago"]
    assert laps("month") == laps("week") + ["20 days ago"]
    assert laps("any") == [r["lap"] for r in rows]


def test_filter_laps_by_session():
    from webapp.app import _filter_laps

    rows = [{"lap": 1, "session_type": "Race", "recorded_at": None},
            {"lap": 2, "session_type": "Qualify", "recorded_at": None},
            {"lap": 3, "session_type": None, "recorded_at": None}]
    assert [r["lap"] for r in _filter_laps(rows, "any", "Qualify", NOW)] == [2]
    assert len(_filter_laps(rows, "any", "any", NOW)) == 3
    assert len(_filter_laps(rows, None, None, NOW)) == 3


def test_recording_times_are_shown_in_local_time():
    from webapp.app import _fmt_recorded

    assert _fmt_recorded(stamp(NOW)) == "2026-11-04 12:00"
    assert _fmt_recorded(None) == ""
    # Not the usual format: as it is, rather than nothing.
    assert _fmt_recorded("2026-10-30T12:56") == "2026-10-30 12:56"


def test_lap_filters_keep_the_gap_and_the_selection(tmp_path):
    from webapp import app as webapp

    now = datetime.now().astimezone()
    recordings = [
        {"path": "old.duckdb", "track": "T", "car": "C", "driver": "D", "session_type": "Qualify",
         "recorded_at": stamp(now - timedelta(days=40)), "laps": [[1, 100.0], [2, 103.0]]},
        {"path": "new.duckdb", "track": "T", "car": "C", "driver": "D", "session_type": "Race",
         "recorded_at": stamp(now), "laps": [[1, 101.0], [2, 102.0]]},
    ]
    app = webapp.create_app([REFERENCES], tmp_path / "refs")
    table = next(v["callback"] for k, v in app.callback_map.items() if "laps.page_current" in k)

    everything, selected, page, note = table.__wrapped__("T", "C", recordings, "any", "any", None, None)
    assert [r["seconds"] for r in everything] == [100.0, 101.0, 102.0, 103.0]
    assert (selected, page, note) == ([0], 0, "")

    # Your best lap isn't showing, but the gaps are still to it.
    rows, selected, _, note = table.__wrapped__("T", "C", recordings, "today", "any", everything, [2])
    assert [r["gap"] for r in rows] == ["+1.000", "+2.000"]
    assert note == "Showing 2 of 4 laps."
    # The selected lap stays selected, in its new row.
    assert rows[selected[0]]["seconds"] == 102.0

    # A selected lap that's filtered out gives way to the fastest one showing.
    rows, selected, _, _ = table.__wrapped__("T", "C", recordings, "today", "any", everything, [0])
    assert (rows[selected[0]]["seconds"], selected) == (101.0, [0])

    rows, selected, _, note = table.__wrapped__("T", "C", recordings, "today", "Qualify", everything, [0])
    assert (rows, selected) == ([], [])
    assert note == "None of your 4 laps here match these filters."


def test_active_filters():
    from webapp.app import _active_filters

    assert _active_filters("class", "any", [0, 60], []) == ""
    assert _active_filters(None, None, None, None) == ""
    assert _active_filters("McLaren 720S GT3", "open", [20, 35], ["60+", "0-10"]) == (
        "McLaren 720S GT3 · open setup · track 20–35° · ≤10 L, 60+ L fuel")
    assert _active_filters("class", "fixed", [25, 60], []) == "fixed setup · track ≥25°"
    assert _active_filters("class", "any", [0, 30], []) == "track ≤30°"
