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


def make_client(tmp_path, ruleset="loose-v1.yaml", steam_verify=None,
                steam_name=lambda steam_id, key: None, **overrides):
    settings = Settings(
        data_dir=tmp_path / "data", public_url="https://lmu.example.com",
        ruleset_path=ROOT / "rulesets" / ruleset, dev_login=True, **overrides,
    )
    # No requests to Steam from tests: names come from steam_name.
    kwargs = {"steam_verify": steam_verify} if steam_verify else {}
    app = create_app(settings, steam_name=steam_name, **kwargs)
    return TestClient(app, follow_redirects=False), settings


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

    client, _ = make_client(tmp_path, steam_verify=fake_verify,
                            steam_name=lambda steam_id, key: "Stazzle")
    params = {"openid.sig": "good", "openid.response_nonce": "n1",
              "return_to": "http://127.0.0.1:5000/cb"}
    r = client.get("/auth/steam/callback", params=params)
    assert r.status_code in (302, 307)
    token = parse_qs(urlparse(r.headers["location"]).query)["token"][0]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["steam_id"] == "76561198000000001" and me["name"] == "Stazzle"
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


def crafted(extract: bytes, *sql: str) -> bytes:
    """The lap extract with some SQL run on it."""
    import tempfile

    import duckdb

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lap.duckdb"
        path.write_bytes(gzip.decompress(extract))
        con = duckdb.connect(str(path))
        for statement in sql:
            con.execute(statement)
        con.close()
        return gzip.compress(path.read_bytes())


def test_crafted_recordings_cant_touch_the_servers_files(tmp_path, extracts):
    client, _ = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET")
    # A view that reads a file on the server, where the metadata table should be.
    reads = crafted(extracts[3], "CREATE TABLE real_metadata AS SELECT * FROM metadata",
                    "DROP TABLE metadata",
                    "CREATE VIEW metadata AS SELECT * FROM real_metadata UNION ALL "
                    f"SELECT 'Secret', content FROM read_text('{secret.as_posix()}')")
    r = upload(client, alice, reads)
    assert r.status_code == 400 and "SECRET" not in r.text
    # A column name that ends the quoted identifier and writes a file.
    written = tmp_path / "written.csv"
    name = (f'value" FROM "Ground Speed"; COPY (SELECT 42) TO \'{written.as_posix()}\'; '
            'SELECT "value')
    sneaky = crafted(extracts[3], f'ALTER TABLE "Ground Speed" ADD COLUMN "{name.replace(chr(34), chr(34) * 2)}" DOUBLE')
    upload(client, alice, sneaky)
    assert not written.exists()


def test_in_game_driver_name_isnt_served(tmp_path, extracts):
    import sqlite3

    client, settings = make_client(tmp_path)
    lap = upload(client, sign_in(client, "Alice"), extracts[3]).json()["lap_id"]
    params = {"track": "Algarve International Circuit"}
    for served in (client.get("/api/leaderboard", params=params).text,
                   client.get(f"/api/laps/{lap}").text):
        assert "Bas Generowicz" not in served and "Alice" in served
    npz = settings.data_dir / "laps" / f"{lap}.lap.npz"
    assert ProcessedLap.load(npz).facts.driver == "Alice"

    # Laps stored before this get the leaderboard name once.
    p = ProcessedLap.load(npz)
    import dataclasses
    dataclasses.replace(p, facts=dataclasses.replace(p.facts, driver="Bas Generowicz")).save(npz)
    con = sqlite3.connect(settings.data_dir / "lmu.sqlite3")
    con.execute("PRAGMA user_version = 0")
    con.close()
    make_client(tmp_path)
    assert ProcessedLap.load(npz).facts.driver == "Alice"


def test_sign_out_revokes_the_token(tmp_path):
    client, _ = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    assert client.get("/api/me", headers=alice).status_code == 200
    assert client.post("/api/sign-out", headers=alice).json() == {"signed_out": True}
    assert client.get("/api/me", headers=alice).status_code == 401
    assert client.post("/api/sign-out").status_code == 200  # signed out already: fine


def test_steam_assertion_must_be_signed():
    from server import steam

    callback = "https://lmu.example.com/auth/steam/callback"
    claimed = "https://steamcommunity.com/openid/id/76561198000000001"
    params = {"openid.mode": "id_res", "openid.op_endpoint": steam.OPENID_URL,
              "openid.claimed_id": claimed, "openid.identity": claimed,
              "openid.return_to": callback, "openid.signed": "op_endpoint,return_to"}
    # Refused before asking Steam (no request is made).
    assert steam.verify(params, callback) is None
    assert steam.verify({**params, "openid.identity": claimed[:-1] + "2",
                         "openid.signed": "op_endpoint,claimed_id,identity,return_to,"
                                          "response_nonce"}, callback) is None


def test_fuel_filter_takes_known_bands_only(tmp_path):
    client, _ = make_client(tmp_path)
    params = {"track": "Algarve International Circuit"}
    assert client.get("/api/leaderboard", params={**params, "fuel": ["0-10", "60+"]}).status_code == 200
    assert client.get("/api/leaderboard", params={**params, "fuel": ["x"] * 5000}).status_code == 422


def test_served_laps_carry_the_setup_and_sectors(tmp_path, extracts):
    import dataclasses
    import sqlite3

    from lmu_core.geometry import sector_starts

    client, settings = make_client(tmp_path)
    lap = upload(client, sign_in(client, "Alice"), extracts[3]).json()["lap_id"]
    npz = settings.data_dir / "laps" / f"{lap}.lap.npz"
    served = ProcessedLap.load(npz)
    assert served.setup["VM_BRAKE_BALANCE"]["text"] == "52.5:47.5"
    assert sector_starts([served]) == [978.0, 2586.0]

    # Laps stored before setups and sectors were kept get them from the extract,
    # once, keeping the served facts.
    old = dataclasses.replace(served, setup=None, data={
        k: v for k, v in served.data.items() if k != "sector"})
    old.save(npz)
    con = sqlite3.connect(settings.data_dir / "lmu.sqlite3")
    con.execute("PRAGMA user_version = 1")
    con.close()
    make_client(tmp_path)
    again = ProcessedLap.load(npz)
    assert again.setup == served.setup and "sector" in again.data
    assert again.facts.driver == "Alice"


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
    monkeypatch.setenv("LMU_RULESET", str(settings.ruleset_path))
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


def as_car(extract: bytes, car: str) -> bytes:
    """The same lap extract, recorded in another car (for class leaderboards)."""
    import tempfile

    import duckdb

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lap.duckdb"
        path.write_bytes(gzip.decompress(extract))
        con = duckdb.connect(str(path))
        con.execute("UPDATE metadata SET value = ? WHERE key = 'CarName'", [car])
        con.close()
        return gzip.compress(path.read_bytes())


def test_class_leaderboard_keeps_top_n_per_car(tmp_path, extracts):
    ruleset = tmp_path / "class.yaml"
    ruleset.write_text((ROOT / "rulesets" / "loose-v2.yaml").read_text().replace(
        "top_n: 3", "top_n: 1"))
    client, _ = make_client(tmp_path, ruleset=ruleset)
    alice, bob, carol = (sign_in(client, n) for n in ("Alice", "Bob", "Carol"))
    mclaren, ford = "Garage 59 2026 #10:LM", "Proton Competition 2026 #88:LM"

    assert upload(client, alice, extracts[2]).json()["rank"] == 1
    # A faster lap in another GT3: #1 for that car, and #1 overall in GT3.
    r = upload(client, bob, as_car(extracts[3], ford)).json()
    assert (r["rank"], r["overall_rank"]) == (1, 1) and "#1 overall in GT3" in r["message"]
    # Top 1 per car: Carol's faster McLaren lap pushes out Alice's, not Bob's Ford.
    assert upload(client, carol, extracts[3]).json()["rank"] == 1
    board = client.get("/api/leaderboard", params={
        "track": "Algarve International Circuit", "car_class": "GT3"}).json()
    # (Same lap, same time: their order is a tie.)
    assert sorted((e["driver"], e["car"]) for e in board) == [("Bob", ford), ("Carol", mclaren)]
    # Drivers keep a lap per car, so Carol's Ford lap competes with Bob's for that car.
    r = upload(client, carol, as_car(extracts[2], ford)).json()
    assert r["reason"] == "Not fast enough for the top 1 in the " + ford + " (it would be #2)."


def test_ruleset_must_key_leaderboards_by_stored_fields(tmp_path):
    ruleset = tmp_path / "bad.yaml"
    ruleset.write_text((ROOT / "rulesets" / "loose-v2.yaml").read_text().replace(
        "top_n_per: [car]", "top_n_per: [weather]"))
    with pytest.raises(ValueError, match="weather"):
        make_client(tmp_path, ruleset=ruleset)


def test_same_lap_can_enter_a_new_ruleset(tmp_path, extracts):
    v1, _ = make_client(tmp_path, ruleset="loose-v1.yaml")
    assert upload(v1, sign_in(v1, "Alice"), extracts[3]).status_code == 201
    v2, _ = make_client(tmp_path, ruleset="loose-v2.yaml")
    alice = sign_in(v2, "Alice")
    assert upload(v2, alice, extracts[3]).status_code == 201
    assert upload(v2, alice, extracts[3]).status_code == 409


def test_leaderboard_marks_your_own_laps(tmp_path, extracts):
    client, _ = make_client(tmp_path)
    alice, bob = sign_in(client, "Alice"), sign_in(client, "Bob")
    upload(client, alice, extracts[3])
    params = {"track": "Algarve International Circuit", "car": "Garage 59 2026 #10:LM"}
    assert [e["mine"] for e in client.get("/api/leaderboard", params=params,
                                          headers=alice).json()] == [True]
    assert [e["mine"] for e in client.get("/api/leaderboard", params=params,
                                          headers=bob).json()] == [False]
    assert [e["mine"] for e in client.get("/api/leaderboard", params=params).json()] == [False]


def test_pages_for_search_engines(tmp_path, extracts):
    client, _ = make_client(tmp_path)
    upload(client, sign_in(client, "Alice"), extracts[3])
    home = client.get("/").text
    assert "<meta name=description" in home and "og:image" in home
    assert "<link rel=canonical href='https://lmu.example.com/'>" in home
    # Each leaderboard has its own page, linked from the home page and the sitemap.
    board = "https://lmu.example.com/leaderboards/Algarve%20International%20Circuit/McLaren%20720S%20GT3"
    assert f"href='{board}'" in home
    page = client.get(board.removeprefix("https://lmu.example.com"))
    assert page.status_code == 200
    assert "McLaren 720S GT3 at Algarve International Circuit: LMU leaderboard" in page.text
    assert "Alice" in page.text and "open setup" in page.text
    assert client.get("/leaderboards/Nowhere/McLaren%20720S%20GT3").status_code == 404
    guide = client.get("/guide/telemetry-files")
    assert guide.status_code == 200 and "UserData\\Telemetry" in guide.text
    assert "href=\"https://lmu.example.com/\"" in guide.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.headers["content-type"].startswith("application/xml")
    assert sitemap.text.count("<loc>") == 3 and board in sitemap.text


def test_steam_profile_name():
    from server.steam import profile_name

    assert profile_name("<profile><steamID><![CDATA[Stazzle]]></steamID></profile>") == "Stazzle"
    assert profile_name("<steamID><![CDATA[  ]]></steamID>") is None
    assert profile_name("<html>Steam is down</html>") is None


def test_admin_refresh_names(tmp_path, monkeypatch):
    from server import admin, steam

    client, settings = make_client(tmp_path)
    alice = sign_in(client, "Alice")
    monkeypatch.setenv("LMU_DATA_DIR", str(settings.data_dir))
    monkeypatch.setattr(steam, "display_name", lambda steam_id, key: "Stazzle")
    assert admin.main(["refresh-names"]) == 0
    assert client.get("/api/me", headers=alice).json()["name"] == "Stazzle"


def test_leaderboard_shows_conditions(tmp_path, extracts):
    import sqlite3

    from server import db

    client, settings = make_client(tmp_path)
    assert upload(client, sign_in(client, "Alice"), extracts[3]).status_code == 201
    params = {"track": "Algarve International Circuit", "car": "Garage 59 2026 #10:LM"}
    [entry] = client.get("/api/leaderboard", params=params).json()
    assert entry["weather"] == "Clear" and entry["session_type"] == "Practice"
    assert entry["ambient_temp_c"] == pytest.approx(18.3, abs=0.5)
    assert entry["track_temp_c"] == pytest.approx(20.8, abs=0.5)
    assert entry["fuel_at_start_l"] > 0 and entry["setup_fixed"] == 0

    # A database from before conditions were stored gets them from the laps' files.
    con = sqlite3.connect(settings.data_dir / "lmu.sqlite3")
    for column in db.CONDITION_COLUMNS:
        con.execute(f"ALTER TABLE laps DROP COLUMN {column}")
    con.commit()
    con.close()
    client, _ = make_client(tmp_path)
    [again] = client.get("/api/leaderboard", params=params).json()
    assert {c: again[c] for c in db.CONDITION_COLUMNS} == {c: entry[c] for c in db.CONDITION_COLUMNS}


def test_leaderboards_group_liveries_of_a_model(tmp_path, extracts):
    ruleset = tmp_path / "model.yaml"
    ruleset.write_text((ROOT / "rulesets" / "loose-v4.yaml").read_text().replace(
        "top_n: 5", "top_n: 1"))
    client, _ = make_client(tmp_path, ruleset=ruleset)
    alice, bob = sign_in(client, "Alice"), sign_in(client, "Bob")
    # Another livery of the same McLaren (same setup ranges, so the same model).
    custom = "McLaren Custom Team 2025 #397"
    assert upload(client, alice, extracts[2]).status_code == 201
    r = upload(client, bob, as_car(extracts[3], custom)).json()
    assert r["message"] == ("Accepted: #1 on Algarve International Circuit in the "
                            "McLaren 720S GT3 (#1 overall in GT3).")
    # Top 1 per model: Bob's lap pushed Alice's out, though their liveries differ.
    board = client.get("/api/leaderboard", params={
        "track": "Algarve International Circuit", "car_model": "McLaren 720S GT3"}).json()
    assert [(e["driver"], e["car"], e["car_model"]) for e in board] == [
        ("Bob", custom, "McLaren 720S GT3")]


def hotter(extract: bytes, degrees: float) -> bytes:
    """The same lap extract, on a hotter track."""
    import tempfile

    import duckdb

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lap.duckdb"
        path.write_bytes(gzip.decompress(extract))
        con = duckdb.connect(str(path))
        con.execute('UPDATE "Track Temperature" SET value = value + ?', [degrees])
        con.close()
        return gzip.compress(path.read_bytes())


def test_laps_are_kept_and_filtered_per_band(tmp_path, extracts):
    client, _ = make_client(tmp_path, ruleset="loose-v5.yaml")
    alice, bob = sign_in(client, "Alice"), sign_in(client, "Bob")
    track = "Algarve International Circuit"
    # The sample: open setup, track ~21° (the 20-25° band), ~77 L fuel.
    r = upload(client, alice, extracts[3]).json()
    assert r["message"] == (f"Accepted: #1 on {track} in the McLaren 720S GT3, open setup, "
                            "track 20–25°, 60+ L fuel (#1 overall in GT3).")
    # A slower lap of Alice's on a hotter track is her best there, so it's kept too.
    r = upload(client, alice, hotter(extracts[2], 10)).json()
    assert r["accepted"] and "track 30–35°" in r["message"]
    # (Another livery, or it would be the same lap as Alice's.)
    bobs = as_car(hotter(extracts[3], 10), "McLaren Custom Team 2025 #397")
    assert upload(client, bob, bobs).json()["rank"] == 1

    def board(**filters):
        laps = client.get("/api/leaderboard", params={"track": track, "car_class": "GT3",
                                                      **filters}).json()
        return [(e["driver"], round(e["lap_time_s"], 3)) for e in laps]

    # Unfiltered, each driver's best lap; filtered, their best in those conditions.
    assert sorted(board()) == [("Alice", 111.602), ("Bob", 111.602)]
    assert board(track_temp_min=30, track_temp_max=40) == [("Bob", 111.602), ("Alice", 112.418)]
    assert board(track_temp_max=25) == [("Alice", 111.602)]
    assert board(setup="fixed") == [] and board(setup="open") != []
    assert board(fuel=["0-10", "10-30"]) == [] and len(board(fuel=["60+"])) == 2
    assert client.get("/api/leaderboards").json()[0]["laps"] == 2
