"""The leaderboard server, end to end with real lap extracts (skipped without samples).

Several made-up users upload laps 1-3 of the Algarve sample: lap 1 went off
track, lap 2 is 1:52.418, lap 3 is 1:51.602.
"""

import gzip
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ALGARVE = Path(__file__).parent.parent / "samples" / (
    "Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
)
ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(not ALGARVE.exists(), reason="sample recording not present")

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from lmu_core.extract import extract_lap  # noqa: E402
from lmu_core.resample import ProcessedLap  # noqa: E402
from server.app import create_app  # noqa: E402
from server.config import Settings  # noqa: E402


@pytest.fixture(scope="module")
def extracts(tmp_path_factory):
    d = tmp_path_factory.mktemp("extracts")
    return {n: extract_lap(ALGARVE, n, d / f"lap{n}.duckdb.gz").read_bytes() for n in (1, 2, 3)}


def make_client(tmp_path, ruleset="loose-v1.yaml", steam_verify=None, **overrides):
    settings = Settings(
        data_dir=tmp_path / "data", public_url="https://lmu.example.com",
        ruleset_path=ROOT / "rulesets" / ruleset, dev_login=True, **overrides,
    )
    kwargs = {"steam_verify": steam_verify} if steam_verify else {}
    return TestClient(create_app(settings, **kwargs), follow_redirects=False), settings


def sign_in(client, name):
    r = client.get("/auth/dev", params={"name": name, "return_to": "http://127.0.0.1:5000/cb"})
    assert r.status_code in (302, 307)
    token = parse_qs(urlparse(r.headers["location"]).query)["token"][0]
    return {"Authorization": f"Bearer {token}"}


def upload(client, auth, data):
    return client.post("/api/laps", headers=auth, files={"extract": ("lap.duckdb.gz", data)})


def test_health_and_ruleset(tmp_path):
    client, _ = make_client(tmp_path)
    assert client.get("/api/health").json() == {"ok": True, "ruleset": "loose-v1"}
    assert client.get("/api/ruleset").json()["top_n"] == 10
    assert client.get("/").status_code == 200
    assert client.get("/privacy").status_code == 200


def test_upload_needs_sign_in(tmp_path, extracts):
    client, _ = make_client(tmp_path)
    assert upload(client, {}, extracts[3]).status_code == 401
    assert upload(client, {"Authorization": "Bearer nope"}, extracts[3]).status_code == 401


def test_tokens_only_go_to_the_local_app(tmp_path):
    client, _ = make_client(tmp_path)
    for bad in ("https://evil.example.com/steal", "http://192.168.1.5/cb", "ftp://127.0.0.1/"):
        assert client.get("/auth/dev", params={"name": "x", "return_to": bad}).status_code == 400
        assert client.get("/auth/steam/login", params={"return_to": bad}).status_code == 400


def test_steam_login_redirects_to_steam(tmp_path):
    client, _ = make_client(tmp_path)
    r = client.get("/auth/steam/login", params={"return_to": "http://127.0.0.1:5000/cb"})
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert url.netloc == "steamcommunity.com"
    assert q["openid.return_to"][0].startswith("https://lmu.example.com/auth/steam/callback")
    assert q["openid.realm"] == ["https://lmu.example.com/"]


def test_steam_callback_signs_in_once(tmp_path):
    seen = []

    def fake_verify(params, expected):
        seen.append(expected)
        return "76561198000000001" if params.get("openid.sig") == "good" else None

    client, _ = make_client(tmp_path, steam_verify=fake_verify)
    params = {"openid.sig": "good", "openid.response_nonce": "n1",
              "return_to": "http://127.0.0.1:5000/cb"}
    r = client.get("/auth/steam/callback", params=params)
    assert r.status_code in (302, 307)
    token = parse_qs(urlparse(r.headers["location"]).query)["token"][0]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["steam_id"] == "76561198000000001"
    assert seen == ["https://lmu.example.com/auth/steam/callback"]
    # The same assertion can't be replayed, and a bad one is refused.
    assert client.get("/auth/steam/callback", params=params).status_code == 403
    bad = {**params, "openid.sig": "forged", "openid.response_nonce": "n2"}
    assert client.get("/auth/steam/callback", params=bad).status_code == 403


def test_upload_flow(tmp_path, extracts):
    client, settings = make_client(tmp_path)
    alice, bob = sign_in(client, "Alice"), sign_in(client, "Bob")

    r = upload(client, alice, extracts[2])
    assert r.status_code == 201, r.json()
    assert r.json()["rank"] == 1
    alice_lap = r.json()["lap_id"]

    # The same lap again, even re-packed so the file differs, is a duplicate.
    repacked = gzip.compress(gzip.decompress(extracts[2]), mtime=12345)
    assert repacked != extracts[2]
    assert upload(client, bob, repacked).status_code == 409

    # Bob's faster lap goes above Alice's.
    r = upload(client, bob, extracts[3])
    assert r.status_code == 201 and r.json()["rank"] == 1
    board = client.get("/api/leaderboard", params={
        "track": "Algarve International Circuit", "car": "Garage 59 2026 #10:LM"}).json()
    assert [(e["rank"], e["driver"], round(e["lap_time_s"], 3)) for e in board] == [
        (1, "Bob", 111.602), (2, "Alice", 112.418)]

    # The processed lap downloads and loads like any reference lap.
    path = tmp_path / "ref.lap.npz"
    path.write_bytes(client.get(f"/api/laps/{board[0]['id']}/lap.npz").content)
    ref = ProcessedLap.load(path)
    assert ref.facts.is_valid is True
    assert ref.time_s[-1] == pytest.approx(111.602, abs=0.02)
    assert client.get("/api/leaderboards").json()[0]["laps"] == 2

    # Only the owner can delete a lap.
    assert client.delete(f"/api/laps/{alice_lap}", headers=bob).status_code == 403
    assert client.delete(f"/api/laps/{alice_lap}", headers=alice).status_code == 200
    raw, processed = (settings.data_dir / "laps" / f"{alice_lap}{ext}"
                      for ext in (".duckdb.gz", ".lap.npz"))
    assert not raw.exists() and not processed.exists()


def test_only_each_drivers_best_lap_counts(tmp_path, extracts):
    client, _ = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    assert upload(client, alice, extracts[3]).status_code == 201
    r = upload(client, alice, extracts[2])
    assert r.status_code == 200 and not r.json()["accepted"]
    assert "already have a faster lap" in r.json()["reason"]


def test_faster_lap_replaces_own_slower_one(tmp_path, extracts):
    client, settings = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    slow = upload(client, alice, extracts[2]).json()["lap_id"]
    assert upload(client, alice, extracts[3]).status_code == 201
    board = client.get("/api/leaderboard", params={
        "track": "Algarve International Circuit", "car": "Garage 59 2026 #10:LM"}).json()
    assert len(board) == 1 and round(board[0]["lap_time_s"], 3) == 111.602
    assert not (settings.data_dir / "laps" / f"{slow}.lap.npz").exists()


def test_top_n_pushes_out_slowest(tmp_path, extracts):
    ruleset = tmp_path / "top1.yaml"
    ruleset.write_text((ROOT / "rulesets" / "loose-v1.yaml").read_text().replace(
        "top_n: 10", "top_n: 1").replace("id: loose-v1", "id: top1"))
    client, settings = make_client(tmp_path, ruleset=ruleset)
    alice, bob, carol = (sign_in(client, n) for n in ("Alice", "Bob", "Carol"))
    first = upload(client, alice, extracts[2]).json()["lap_id"]
    assert upload(client, bob, extracts[3]).json()["rank"] == 1
    assert not (settings.data_dir / "laps" / f"{first}.lap.npz").exists()
    # Not fast enough for a top-1 board.
    carol_repack = gzip.compress(gzip.decompress(extracts[2]), mtime=1)
    r = upload(client, carol, carol_repack)
    assert r.status_code == 200 and "Not fast enough" in r.json()["reason"]


def test_off_track_lap_rejected_by_rules(tmp_path, extracts):
    client, _ = make_client(tmp_path)
    r = upload(client, sign_in(client, "Alice"), extracts[1])
    assert r.status_code == 422
    assert r.json()["reason"] == (
        "Not added: it went off track (4.3 m beyond the track edge at 2771 m).")


def test_tampered_and_garbage_uploads_rejected(tmp_path, extracts):
    import duckdb

    client, _ = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    db = tmp_path / "t.duckdb"
    db.write_bytes(gzip.decompress(extracts[3]))
    con = duckdb.connect(str(db))
    con.execute('UPDATE "Lap Time" SET value = value - 2 WHERE value > 0')
    con.close()
    r = upload(client, alice, gzip.compress(db.read_bytes()))
    assert r.status_code == 422 and r.json()["reason"] == "The lap failed verification."
    assert upload(client, alice, b"not a lap").status_code == 400
    assert upload(client, alice, gzip.compress(b"not a database")).status_code == 400


def test_upload_limits(tmp_path, extracts):
    client, _ = make_client(tmp_path, max_unpacked_mb=1)
    alice = sign_in(client, "Alice")
    assert upload(client, alice, extracts[3]).status_code == 400  # unpacks beyond 1 MB
    client, _ = make_client(tmp_path / "b", uploads_per_hour=2)
    bob = sign_in(client, "Bob")
    assert upload(client, bob, b"x").status_code == 400
    assert upload(client, bob, b"x").status_code == 400
    assert upload(client, bob, b"x").status_code == 429


def test_delete_account_removes_laps(tmp_path, extracts):
    client, settings = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    lap = upload(client, alice, extracts[3]).json()["lap_id"]
    assert client.delete("/api/me", headers=alice).json() == {"deleted": True}
    assert client.get(f"/api/laps/{lap}").status_code == 404
    assert not (settings.data_dir / "laps" / f"{lap}.lap.npz").exists()
    assert client.get("/api/me", headers=alice).status_code == 401


def test_admin_ban_removes_laps_and_access(tmp_path, extracts, monkeypatch):
    from server import admin

    client, settings = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    upload(client, alice, extracts[3])
    steam_id = client.get("/api/me", headers=alice).json()["steam_id"]
    monkeypatch.setenv("LMU_DATA_DIR", str(settings.data_dir))
    assert admin.main(["ban", steam_id]) == 0
    assert client.get("/api/leaderboards").json() == []
    assert client.get("/api/me", headers=alice).status_code == 401


def test_admin_import_lap_for_a_driver_without_an_account(tmp_path, extracts, monkeypatch):
    from server import admin

    client, settings = make_client(tmp_path)
    for n in (1, 3):
        (tmp_path / f"lap{n}.duckdb.gz").write_bytes(extracts[n])
    monkeypatch.setenv("LMU_DATA_DIR", str(settings.data_dir))
    files = [str(tmp_path / "lap1.duckdb.gz"), str(tmp_path / "lap3.duckdb.gz")]
    assert admin.main(["import-lap", "--as", "Daniel", *files]) == 0
    [board] = client.get("/api/leaderboards").json()
    [entry] = client.get("/api/leaderboard", params={"track": board["track"],
                                                     "car": board["car"]}).json()
    assert entry["driver"] == "Daniel" and entry["lap_time_s"] == pytest.approx(111.602, abs=0.01)
    # Same checks as an upload: the same lap again is a duplicate.
    assert admin.main(["import-lap", "--as", "Daniel", files[1]]) == 0
    assert len(client.get("/api/leaderboard", params={"track": board["track"],
                                                      "car": board["car"]}).json()) == 1
