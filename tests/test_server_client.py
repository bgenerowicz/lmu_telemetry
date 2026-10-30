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

    [entry] = client.leaderboard("Algarve International Circuit", "Garage 59 2026 #10:LM")
    path = client.download_lap(entry["id"], tmp_path)
    assert ProcessedLap.load(path).facts.lap_number == 3
    assert client.download_lap(entry["id"], tmp_path) == path  # cached


def test_app_shows_leaderboard_without_sign_in(server_url, tmp_path):
    from webapp.app import create_app as create_webapp

    ServerClient(server_url, token_for(server_url, "Alice")).upload_lap(ALGARVE, 3)
    app = create_webapp([], tmp_path / "refs", settings={"server_url": server_url})
    [key] = [k for k in app.callback_map if "board.data" in k]
    board = app.callback_map[key]["callback"].__wrapped__
    rows, style, note = board("Algarve International Circuit", "Garage 59 2026 #10:LM", 0)
    assert [(r["rank"], r["driver"]) for r in rows] == [(1, "Alice")]
    assert style == {} and "Top laps" in str(note)
    rows, style, note = board("Algarve International Circuit", "Some other car", 0)
    assert rows == [] and "No laps" in str(note)


def test_app_offers_online_laps_of_other_cars_in_the_class(server_url, tmp_path):
    from webapp.app import create_app as create_webapp

    ServerClient(server_url, token_for(server_url, "Alice")).upload_lap(ALGARVE, 3)
    app = create_webapp([], tmp_path / "refs", settings={"server_url": server_url})
    options = app.callback_map["..reference.options...reference.value...reference-empty.children.."]
    track = "Algarve International Circuit"
    mine = [{"track": track, "car": "Another GT3 #1", "car_class": "GT3", "laps": []}]
    labels = [o["label"] for o in options["callback"].__wrapped__(track, "Another GT3 #1",
                                                                  mine, 0, 0, None)[0]]
    assert any("Alice" in l and "other car: Garage 59 2026 #10:LM" in l for l in labels)
    other_class = [{**mine[0], "car_class": "Hypercar"}]
    labels = [o["label"] for o in options["callback"].__wrapped__(track, "Another GT3 #1",
                                                                  other_class, 0, 0, None)[0]]
    assert labels == []


def test_errors_are_readable(server_url):
    with pytest.raises(ServerError, match="Sign in"):
        ServerClient(server_url).upload_lap(ALGARVE, 3)
    with pytest.raises(ServerError, match="Not signed in"):
        ServerClient(server_url, "bad-token").me()
    with pytest.raises(ServerError, match="Can't reach"):
        ServerClient("http://127.0.0.1:9").health()
