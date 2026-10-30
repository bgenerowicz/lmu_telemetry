"""The leaderboard server (FastAPI).

Sign-in: the app opens ``/auth/steam/login?return_to=<app's local address>``
in the browser; after Steam confirms who the user is, the server redirects
back to the app with an API token. The app then sends the token as
``Authorization: Bearer <token>``. ``return_to`` must be a local (loopback)
address, so a token can never be sent to another website.

Uploads: one lap extract (see ``lmu_core.extract``). The server unpacks it,
runs ``lmu_core.verify`` and the ruleset itself, and keeps each driver's best
lap per leaderboard (ruleset, track, layout, car), top N drivers only.
"""

from __future__ import annotations

import dataclasses
import gzip
import hashlib
import html
import json
import shutil
import sqlite3
import statistics
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from lmu_core.recording import Recording
from lmu_core.ruleset import Ruleset
from lmu_core.verify import verify_lap
from server import db, steam
from server.config import Settings

# Track length is checked against earlier accepted laps once there are this many.
KNOWN_LENGTH_MIN_LAPS = 3
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def create_app(
    settings: Settings | None = None,
    steam_verify: Callable[[dict[str, str], str], str | None] = steam.verify,
) -> FastAPI:
    settings = settings or Settings.from_env()
    db.init(settings.data_dir)
    ruleset = Ruleset.load(settings.ruleset_path)
    callback_url = f"{settings.public_url}/auth/steam/callback"
    app = FastAPI(title="LMU Lap Analyser server", docs_url="/api/docs", redoc_url=None)

    def get_con() -> Iterator[sqlite3.Connection]:
        con = db.connect(settings.data_dir)
        try:
            yield con
        finally:
            con.close()

    def current_user(
        authorization: str | None = Header(default=None),
        con: sqlite3.Connection = Depends(get_con),
    ) -> sqlite3.Row:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Sign in first.")
        user = db.user_for_token(con, authorization.removeprefix("Bearer ").strip())
        if user is None:
            raise HTTPException(401, "Sign-in expired or invalid. Sign in again.")
        if user["banned"]:
            raise HTTPException(403, "This account is banned.")
        return user

    # --- Sign-in -------------------------------------------------------------

    @app.get("/auth/steam/login")
    def steam_login(return_to: str = "") -> RedirectResponse:
        _check_return_to(return_to)
        callback = callback_url + ("?" + urlencode({"return_to": return_to}) if return_to else "")
        return RedirectResponse(steam.login_url(callback, settings.public_url + "/"))

    @app.get("/auth/steam/callback")
    def steam_callback(request: Request, con: sqlite3.Connection = Depends(get_con)):
        params = dict(request.query_params)
        return_to = params.pop("return_to", "")
        _check_return_to(return_to)
        steam_id = steam_verify(params, callback_url)
        if steam_id is None:
            raise HTTPException(403, "Steam could not confirm the sign-in. Try again.")
        if not db.use_nonce(con, params.get("openid.response_nonce", "")):
            raise HTTPException(403, "This sign-in link was already used. Sign in again.")
        name = steam.display_name(steam_id, settings.steam_api_key)
        return _signed_in(con, steam_id, name, return_to)

    if settings.dev_login:
        @app.get("/auth/dev")
        def dev_login(name: str, return_to: str = "", con: sqlite3.Connection = Depends(get_con)):
            """Sign in without Steam, as a made-up user. Development only."""
            _check_return_to(return_to)
            fake_id = "7656119" + str(int(hashlib.sha256(name.encode()).hexdigest(), 16))[:10]
            return _signed_in(con, fake_id, name, return_to)

    def _signed_in(con, steam_id: str, name: str | None, return_to: str):
        existing = con.execute("SELECT name FROM users WHERE steam_id = ?", (steam_id,)).fetchone()
        name = name or (existing["name"] if existing else f"Driver {steam_id[-4:]}")
        user = db.upsert_user(con, steam_id, name)
        if user["banned"]:
            raise HTTPException(403, "This account is banned.")
        token = db.new_token(con, user["id"])
        if return_to:
            sep = "&" if "?" in return_to else "?"
            return RedirectResponse(return_to + sep + urlencode({"token": token, "name": name}))
        return HTMLResponse(_page("Signed in", (
            f"<p>Signed in as <b>{html.escape(name)}</b>. Paste this key into LMU Lap "
            f"Analyser's server settings:</p><pre>{html.escape(token)}</pre>")))

    # --- Account -------------------------------------------------------------

    @app.get("/api/me")
    def me(user: sqlite3.Row = Depends(current_user)) -> dict:
        return {"steam_id": user["steam_id"], "name": user["name"],
                "is_admin": bool(user["is_admin"])}

    @app.delete("/api/me")
    def delete_me(user=Depends(current_user), con=Depends(get_con)) -> dict:
        """Delete the account and all its laps."""
        for row in con.execute("SELECT id FROM laps WHERE user_id = ?", (user["id"],)).fetchall():
            _delete_lap_files(settings.data_dir, row["id"])
        con.execute("DELETE FROM users WHERE id = ?", (user["id"],))
        return {"deleted": True}

    # --- Laps ----------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "ruleset": ruleset.id}

    @app.get("/api/ruleset")
    def get_ruleset() -> dict:
        return ruleset.model_dump()

    @app.post("/api/laps")
    def upload(extract: UploadFile, user=Depends(current_user), con=Depends(get_con)):
        status, body = _process_upload(settings, ruleset, con, user, extract)
        return JSONResponse(body, status_code=status)

    @app.get("/api/leaderboards")
    def leaderboards(con=Depends(get_con)) -> list[dict]:
        rows = con.execute(
            "SELECT track, layout, car, car_class, COUNT(*) AS laps, MIN(lap_time_s) AS best "
            "FROM laps WHERE ruleset_id = ? GROUP BY track, layout, car ORDER BY track, car",
            (ruleset.id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/leaderboard")
    def leaderboard(track: str, car: str, layout: str | None = None, con=Depends(get_con)):
        rows = con.execute(
            "SELECT laps.id, users.name AS driver, laps.driver_name, laps.lap_time_s, "
            "laps.car_class, laps.recorded_at, laps.uploaded_at FROM laps "
            "JOIN users ON users.id = laps.user_id "
            "WHERE ruleset_id = ? AND track = ? AND car = ? AND (? IS NULL OR layout = ?) "
            "AND users.banned = 0 ORDER BY lap_time_s",
            (ruleset.id, track, car, layout, layout),
        ).fetchall()
        return [{"rank": i, **dict(r)} for i, r in enumerate(rows, start=1)]

    @app.get("/api/laps/{lap_id}")
    def lap_info(lap_id: str, con=Depends(get_con)) -> dict:
        row = _lap_row(con, lap_id)
        info = {k: row[k] for k in row.keys() if k not in ("report_json", "user_id", "file_hash")}
        return {**info, "report": json.loads(row["report_json"])}

    @app.get("/api/laps/{lap_id}/lap.npz")
    def lap_file(lap_id: str, con=Depends(get_con)) -> FileResponse:
        _lap_row(con, lap_id)
        _, processed = db.lap_files(settings.data_dir, lap_id)
        return FileResponse(processed, media_type="application/octet-stream",
                            filename=f"{lap_id}.lap.npz")

    @app.delete("/api/laps/{lap_id}")
    def delete_lap(lap_id: str, user=Depends(current_user), con=Depends(get_con)) -> dict:
        row = _lap_row(con, lap_id)
        if row["user_id"] != user["id"] and not user["is_admin"]:
            raise HTTPException(403, "You can only delete your own laps.")
        _delete_lap_files(settings.data_dir, lap_id)
        con.execute("DELETE FROM laps WHERE id = ?", (lap_id,))
        return {"deleted": lap_id}

    # --- Pages ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def home(con=Depends(get_con)) -> str:
        boards = leaderboards(con)
        rows = "".join(
            f"<tr><td>{html.escape(b['track'])}</td><td>{html.escape(b['car'])}</td>"
            f"<td>{b['laps']}</td><td>{_fmt_time(b['best'])}</td></tr>" for b in boards
        ) or "<tr><td colspan=4>No laps yet.</td></tr>"
        return _page("LMU Lap Analyser", (
            f"<p>Leaderboards for <b>{html.escape(ruleset.name)}</b>: "
            f"{html.escape(ruleset.description)}</p>"
            "<table><tr><th>Track</th><th>Car</th><th>Drivers</th><th>Best</th></tr>"
            f"{rows}</table><p><a href=/privacy>Privacy</a></p>"))

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy() -> str:
        return _page("Privacy", (
            "<p>When you sign in with Steam we store your Steam ID and Steam display name. "
            "When you upload a lap we store the lap's telemetry (one lap cut from your LMU "
            "recording, including the in-game driver name and car setup) and show your "
            "display name and lap time on the leaderboard. Laps that drop out of the top "
            "are deleted.</p><p>You can delete your account and all your laps at any time "
            "from the app.</p>"))

    return app


def _process_upload(settings: Settings, ruleset: Ruleset, con: sqlite3.Connection,
                    user: sqlite3.Row, extract: UploadFile) -> tuple[int, dict]:
    hour_ago = time.time() - 3600
    attempts = con.execute(
        "SELECT COUNT(*) FROM upload_attempts WHERE user_id = ? AND at > ?",
        (user["id"], hour_ago),
    ).fetchone()[0]
    if attempts >= settings.uploads_per_hour:
        return 429, _rejected("Too many uploads; try again in an hour.")
    con.execute("INSERT INTO upload_attempts VALUES (?, ?)", (user["id"], time.time()))

    data = extract.file.read(int(settings.max_upload_mb * 1e6) + 1)
    if len(data) > settings.max_upload_mb * 1e6:
        return 413, _rejected(f"File too big (limit {settings.max_upload_mb:.0f} MB).")

    with tempfile.TemporaryDirectory() as tmp:
        unpacked = Path(tmp) / "lap.duckdb"
        try:
            _gunzip(data, unpacked, int(settings.max_unpacked_mb * 1e6))
            rec = Recording(unpacked)
        except Exception:  # noqa: BLE001 - anything unreadable is the uploader's problem
            return 400, _rejected("Not a lap extract from LMU Lap Analyser.")
        try:
            lap_number = int(rec.metadata.get("ExtractedLap", ""))
        except ValueError:
            rec.close()
            return 400, _rejected("Not a lap extract from LMU Lap Analyser.")
        meta = rec.metadata
        known = _known_length(con, meta.get("TrackName"), meta.get("TrackLayout"))
        try:
            v = verify_lap(rec, lap_number, known)
        finally:
            rec.close()

    checks = [dataclasses.asdict(c) for c in v.checks]
    if not v.ok:
        return 422, _rejected("The lap failed verification.", checks=checks)
    # Identify the lap by what it is, not the file's bytes, so re-packing the
    # same lap (e.g. to upload it from a second account) is still a duplicate.
    f = v.facts
    fingerprint = hashlib.sha256(
        f"{f.recorded_at}|{f.lap_number}|{f.lap_time_s:.4f}|{f.track}|{f.car}".encode()
    ).hexdigest()
    if con.execute("SELECT 1 FROM laps WHERE file_hash = ?", (fingerprint,)).fetchone():
        return 409, _rejected("This lap was already uploaded.")
    report = ruleset.check(v.facts)
    rules = [dataclasses.asdict(r) for r in report.results]
    if not report.accepted:
        limits = next((c.detail for c in v.checks if c.name.startswith("track limits")), "")
        reasons = [_rule_reason(r, limits) for r in report.problems if r.rejects]
        return 422, _rejected("Not added: " + "; ".join(reasons) + ".", checks=checks, rules=rules)

    key = (ruleset.id, f.track, f.layout, f.car)
    board = "ruleset_id = ? AND track = ? AND layout IS ? AND car = ?"
    con.execute("BEGIN IMMEDIATE")
    try:
        own = con.execute(f"SELECT id, lap_time_s FROM laps WHERE {board} AND user_id = ?",
                          (*key, user["id"])).fetchone()
        if own and own["lap_time_s"] <= f.lap_time_s:
            con.execute("ROLLBACK")
            return 200, _rejected(
                f"You already have a faster lap here ({_fmt_time(own['lap_time_s'])}).",
                checks=checks, rules=rules)
        faster = con.execute(
            f"SELECT COUNT(*) FROM laps WHERE {board} AND user_id != ? AND lap_time_s < ?",
            (*key, user["id"], f.lap_time_s)).fetchone()[0]
        rank = faster + 1
        if rank > ruleset.top_n:
            con.execute("ROLLBACK")
            return 200, _rejected(
                f"Not fast enough for the top {ruleset.top_n} (it would be #{rank}).",
                checks=checks, rules=rules)

        lap_id = uuid.uuid4().hex
        raw, processed = db.lap_files(settings.data_dir, lap_id)
        raw.write_bytes(data)
        v.processed.save(processed)
        con.execute(
            "INSERT INTO laps VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (lap_id, user["id"], ruleset.id, f.track, f.layout, f.car, f.car_class,
             f.lap_time_s, float(v.processed.distance_m[-1]), f.driver, f.recorded_at,
             time.time(), fingerprint, json.dumps({"checks": checks, "rules": rules})))
        removed = [own["id"]] if own else []
        con.execute("DELETE FROM laps WHERE id IN (%s)" % ",".join("?" * len(removed)), removed)
        # Keep the top N; laps pushed out are deleted.
        pushed_out = [r["id"] for r in con.execute(
            f"SELECT id FROM laps WHERE {board} ORDER BY lap_time_s LIMIT -1 OFFSET ?",
            (*key, ruleset.top_n)).fetchall()]
        con.execute("DELETE FROM laps WHERE id IN (%s)" % ",".join("?" * len(pushed_out)),
                    pushed_out)
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    for old in removed + pushed_out:
        _delete_lap_files(settings.data_dir, old)
    return 201, {
        "accepted": True, "lap_id": lap_id, "rank": rank, "lap_time_s": f.lap_time_s,
        "message": f"Accepted: #{rank} on {f.track} in the {f.car}.",
        "checks": checks, "rules": rules,
    }


def _rule_reason(result, track_limits: str) -> str:
    """A ruleset problem in plain words."""
    if result.field == "is_valid":
        if result.status == "fail":
            return "it went " + track_limits.replace("off track: ", "off track (") + ")"
        return "track limits couldn't be checked"
    if result.field == "is_flying":
        return "it isn't a complete timed lap"
    if result.field == "rain_pct":
        return "it was raining"
    if result.field == "track_wetness_pct":
        return "the track was wet"
    return result.message


def _known_length(con: sqlite3.Connection, track: str | None, layout: str | None) -> float | None:
    lengths = [r[0] for r in con.execute(
        "SELECT track_length_m FROM laps WHERE track = ? AND layout IS ?", (track, layout))]
    return statistics.median(lengths) if len(lengths) >= KNOWN_LENGTH_MIN_LAPS else None


def _gunzip(data: bytes, dest: Path, limit: int) -> None:
    """Unpack, refusing anything that unpacks beyond ``limit`` bytes (zip bombs)."""
    import io

    written = 0
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as g, open(dest, "wb") as out:
        while chunk := g.read(1 << 20):
            written += len(chunk)
            if written > limit:
                raise ValueError("unpacked file too big")
            out.write(chunk)


def _check_return_to(return_to: str) -> None:
    if not return_to:
        return
    url = urlparse(return_to)
    if url.scheme != "http" or url.hostname not in _LOOPBACK:
        raise HTTPException(400, "return_to must be the app's local address.")


def _lap_row(con: sqlite3.Connection, lap_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM laps WHERE id = ?", (lap_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such lap.")
    return row


def _delete_lap_files(data_dir: Path, lap_id: str) -> None:
    for path in db.lap_files(data_dir, lap_id):
        path.unlink(missing_ok=True)


def _rejected(reason: str, **extra) -> dict:
    return {"accepted": False, "reason": reason, **extra}


def _fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>"
        "body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:0 auto;padding:24px 16px;"
        "color:#1d2128;background:#fff}table{border-collapse:collapse}"
        "td,th{padding:4px 12px;border-bottom:1px solid #e2e5ea;text-align:left}"
        "pre{background:#f4f5f7;padding:10px;overflow-x:auto}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{body}</body></html>"
    )
