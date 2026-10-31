"""The app's server client against a real server on a local port (skipped without samples)."""

import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)
ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")
uvicorn = pytest.importorskip("uvicorn")
httpx = pytest.importorskip("httpx")

from lmu_core.resample import ProcessedLap  # noqa: E402
from server.app import create_app  # noqa: E402
from server.config import Settings  # noqa: E402
from webapp.server_client import ServerClient, ServerError  # noqa: E402


@pytest.fixture(scope="module")
def server_url(tmp_path_factory):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = Settings(data_dir=tmp_path_factory.mktemp("srv"), public_url=f"http://127.0.0.1:{port}",
                        ruleset_path=ROOT / "rulesets" / "loose-v1.yaml", dev_login=True)
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port,
                                           log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def token_for(url, name):
    r = httpx.get(f"{url}/auth/dev", params={"name": name, "return_to": "http://127.0.0.1:1/cb"})
    return parse_qs(urlparse(r.headers["location"]).query)["token"][0]


def test_upload_compare_download(server_url, tmp_path):
    client = ServerClient(server_url, token_for(server_url, "Alice"))
    assert client.health()["ok"]
    assert client.me()["name"] == "Alice"

    result = client.upload_lap(ALGARVE, 3)
    assert result["accepted"] and result["rank"] == 1
    again = client.upload_lap(ALGARVE, 3)
    assert not again["accepted"] and "already uploaded" in again["reason"]
    off_track = client.upload_lap(ALGARVE, 1)
    assert not off_track["accepted"] and "off track" in off_track["reason"]

    [entry] = client.leaderboard("Algarve International Circuit", "McLaren 720S GT3")
    path = client.download_lap(entry["id"], tmp_path)
    assert ProcessedLap.load(path).facts.lap_number == 3
    assert client.download_lap(entry["id"], tmp_path) == path  # cached


def test_app_shows_leaderboard_without_sign_in(server_url, tmp_path):
    from webapp.app import create_app as create_webapp

    ServerClient(server_url, token_for(server_url, "Alice")).upload_lap(ALGARVE, 3)
    app = create_webapp([], tmp_path / "refs", settings={"server_url": server_url})
    [key] = [k for k in app.callback_map if "board.data" in k]
    any_conditions = ("any", [0, 60], [])

    def board(*args):
        return app.callback_map[key]["callback"].__wrapped__(*args, *any_conditions)

    rows, style, note, picker, options, view = board(
        "Algarve International Circuit", "McLaren 720S GT3", [], 0, "class")
    assert [(r["rank"], r["driver"], r["selected"]) for r in rows] == [(1, "Alice", 1)]
    assert style == {} and "GT3 at Algarve" in str(note) and picker != {"display": "none"}
    assert [o["value"] for o in options] == ["class", "McLaren 720S GT3"] and view == "class"
    rows, style, note, *_ = board("Algarve International Circuit", "Some other car", [], 0, "class")
    assert rows == [] and "No laps" in str(note)

    # One car of the class: the same lap, not highlighted (it isn't this user's).
    rows, style, note, _, options, view = board(
        "Algarve International Circuit", "Another GT3 #1",
        [{"track": "Algarve International Circuit", "car": "Another GT3 #1", "car_class": "GT3",
          "laps": []}], 0, "McLaren 720S GT3")
    assert [(r["driver"], r["selected"]) for r in rows] == [("Alice", 0)]
    assert "McLaren 720S GT3 at Algarve" in str(note) and view == "McLaren 720S GT3"
    assert [o["value"] for o in options] == ["class", "Another GT3 #1", "McLaren 720S GT3"]
    # A car that isn't there (e.g. from another track) falls back to the whole class.
    *_, view = board("Algarve International Circuit", "McLaren 720S GT3", [], 0, "Gone GT3")
    assert view == "class"

    # Filters: the lap was on an open setup, track ~21°, ~77 L fuel.
    filtered = app.callback_map[key]["callback"].__wrapped__
    args = ("Algarve International Circuit", "McLaren 720S GT3", [], 0, "class")
    assert len(filtered(*args, "open", [20, 25], ["60+"])[0]) == 1
    rows, _, note, *_ = filtered(*args, "fixed", [0, 60], [])
    assert rows == [] and "match these filters" in str(note)
    assert filtered(*args, "any", [25, 60], [])[0] == []
    assert filtered(*args, "any", [0, 60], ["0-10"])[0] == []


def test_app_offers_online_laps_of_other_cars_in_the_class(server_url, tmp_path):
    from webapp.app import create_app as create_webapp

    ServerClient(server_url, token_for(server_url, "Alice")).upload_lap(ALGARVE, 3)
    app = create_webapp([], tmp_path / "refs", settings={"server_url": server_url})
    options = app.callback_map["..reference.options...reference.value...reference-empty.children.."]
    track = "Algarve International Circuit"
    mine = [{"track": track, "car": "Another GT3 #1", "car_class": "GT3", "laps": []}]
    labels = [o["label"] for o in options["callback"].__wrapped__(track, "Another GT3 #1",
                                                                  mine, 0, 0, None, None, None)[0]]
    assert any("Alice" in l and "other car: McLaren 720S GT3" in l for l in labels)
    other_class = [{**mine[0], "car_class": "Hypercar"}]
    labels = [o["label"] for o in options["callback"].__wrapped__(track, "Another GT3 #1",
                                                                  other_class, 0, 0, None, None, None)[0]]
    assert labels == []


def test_candidates_are_laps_that_would_make_the_leaderboard(server_url):
    from lmu_core.library import scan_folder
    from lmu_core.ruleset import Ruleset
    from webapp.app import _info_dict
    from webapp.candidates import find_candidates

    alice = ServerClient(server_url, token_for(server_url, "Alice"))
    alice.upload_lap(ALGARVE, 3)  # (or already there from an earlier test)
    recordings = [_info_dict(i) for i in scan_folder(ALGARVE.parent)[0]]
    ruleset = Ruleset.model_validate(alice.ruleset())

    def entries(client):
        return lambda track, car, car_class, band: client.leaderboard(track, car)

    # Alice's best lap is on the board: nothing to add.
    assert find_candidates(recordings, ruleset, entries(alice)) == []
    # For Bob, lap 3 is already there (Alice's upload of it); lap 2 would be #2.
    # Lap 1 is never suggested: it went off track.
    bob = ServerClient(server_url, token_for(server_url, "Bob"))
    [c] = find_candidates(recordings, ruleset, entries(bob))
    assert (c.lap, c.rank) == (2, 2)
    assert find_candidates(recordings, ruleset, entries(bob), skip={c.key}) == []
    # Signed out, the board doesn't say whose laps are whose: lap 3 there is one
    # of this folder's laps, so taken to be yours, and lap 2 is slower.
    signed_out = ServerClient(server_url)
    assert find_candidates(recordings, ruleset, entries(signed_out), signed_in=False) == []


def test_app_lists_laps_to_upload_and_uploads_only_picked_ones(server_url, tmp_path):
    from dash._callback_context import context_value
    from dash._utils import AttributeDict

    from webapp.app import _info_dict
    from webapp.app import create_app as create_webapp
    from lmu_core.library import scan_folder

    ServerClient(server_url, token_for(server_url, "Alice")).upload_lap(ALGARVE, 3)
    recordings = [_info_dict(i) for i in scan_folder(ALGARVE.parent)[0]]
    app = create_webapp([], tmp_path / "refs", settings={
        "server_url": server_url, "server_token": token_for(server_url, "Carol")})
    [key] = [k for k in app.callback_map if "candidate-pick.options" in k]
    candidates = app.callback_map[key]["callback"].__wrapped__

    # Listed and ticked, nothing uploaded yet.
    context_value.set(AttributeDict(triggered_inputs=[
        {"prop_id": "recordings.data", "value": recordings}]))
    _, style, options, picked, status, version = candidates(recordings, 0, None, [])
    assert style == {} and [o["value"] for o in options] == picked == [f"{ALGARVE}|2"]
    assert "would be #2" in options[0]["label"]
    assert ServerClient(server_url, token_for(server_url, "Carol")).leaderboard(
        "Algarve International Circuit", "McLaren 720S GT3")[-1]["driver"] != "Carol"
    # Unticked, it stays unticked, and the button uploads nothing.
    context_value.set(AttributeDict(triggered_inputs=[
        {"prop_id": "upload-candidates.n_clicks", "value": 1}]))
    _, _, _, picked, status, version = candidates(recordings, 0, 1, [])
    assert picked == [] and "Pick the laps" in str(status)


def test_errors_are_readable(server_url):
    with pytest.raises(ServerError, match="Sign in"):
        ServerClient(server_url).upload_lap(ALGARVE, 3)
    with pytest.raises(ServerError, match="Not signed in"):
        ServerClient(server_url, "bad-token").me()
    with pytest.raises(ServerError, match="Can't reach"):
        ServerClient("http://127.0.0.1:9").health()


def test_clicking_a_leaderboard_row_picks_that_lap(server_url, tmp_path):
    from dash._callback_context import context_value
    from dash._utils import AttributeDict

    from webapp.app import create_app as create_webapp

    ServerClient(server_url, token_for(server_url, "Alice")).upload_lap(ALGARVE, 3)
    client = ServerClient(server_url, token_for(server_url, "Bob"))
    client.upload_lap(ALGARVE, 2)
    track, car = "Algarve International Circuit", "McLaren 720S GT3"
    # Runs last: Bob's upload would change the candidates above.
    # Bob's lap: not the car's fastest, so only offered once picked.
    lap_id = next(e["id"] for e in client.leaderboard(track, car) if e["driver"] == "Bob")
    app = create_webapp([], tmp_path / "refs", settings={"server_url": server_url})
    options = app.callback_map["..reference.options...reference.value...reference-empty.children.."]
    mine = [{"track": track, "car": "Another GT3 #1", "car_class": "GT3", "laps": []}]
    clicked = {"row": 0, "column": 1, "column_id": "driver", "row_id": lap_id}
    context_value.set(AttributeDict(triggered_inputs=[
        {"prop_id": "board.active_cell", "value": clicked}]))
    offered, value, _ = options["callback"].__wrapped__(track, "Another GT3 #1", mine, 0, 0,
                                                        clicked, None, None)
    assert value == f"srv:{lap_id}"
    assert any(o["value"] == value and o["label"].startswith("#2 Bob") for o in offered)



def test_candidates_per_band():
    from lmu_core.library import scan_folder
    from lmu_core.ruleset import Ruleset
    from webapp.app import _info_dict
    from webapp.candidates import find_candidates

    [rec] = [_info_dict(i) for i in scan_folder(ALGARVE.parent)[0]]
    ruleset = Ruleset.load(ROOT / "rulesets" / "loose-v5.yaml")
    # Laps 2 and 3 are in the same conditions: only the faster one is suggested...
    asked = []

    def entries_for(track, car, car_class, band):
        asked.append(band)
        return []

    assert [c.lap for c in find_candidates([rec], ruleset, entries_for)] == [3]
    assert asked == [(False, 20, "60+")]
    # ...but in different fuel bands, each is the best of its band.
    rec["lap_bands"]["2"] = [20, "30-60"]
    found = find_candidates([rec], ruleset, lambda *_: [])
    assert sorted((c.lap, c.band) for c in found) == [
        (2, "open setup, track 20–25°, 30–60 L fuel"), (3, "open setup, track 20–25°, 60+ L fuel")]
