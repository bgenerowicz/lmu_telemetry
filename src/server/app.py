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
import sqlite3
import statistics
import tempfile
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from lmu_core.bands import BAND_FIELDS, FUEL_LABELS, describe, track_temp_band
from lmu_core.laps import lap_facts, split_laps
from lmu_core.recording import Recording
from lmu_core.resample import ProcessedLap, resample_lap
from lmu_core.ruleset import Ruleset
from lmu_core.verify import verify_lap
from server import db, steam
from server.config import Settings

# Track length is checked against earlier accepted laps once there are this many.
KNOWN_LENGTH_MIN_LAPS = 3
# Lap fields stored as columns, which leaderboards can be keyed by.
STORED_FIELDS = {"track", "layout", "car", "car_class", "car_model", *BAND_FIELDS}
# Not served: the in-game driver name (often a real name; laps show the Steam
# name instead), and internals.
_PRIVATE_LAP_COLUMNS = ("report_json", "user_id", "file_hash", "driver_name")
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def create_app(
    settings: Settings | None = None,
    steam_verify: Callable[[dict[str, str], str], str | None] = steam.verify,
    steam_name: Callable[[str, str | None], str | None] = steam.display_name,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if db.init(settings.data_dir):
        _fill_added_columns(settings)
    if db.upgrade(settings.data_dir, 1):
        _hide_driver_names(settings)
    # 2: setups and sectors; 3: each setting's step as well as its text.
    if any([db.upgrade(settings.data_dir, 2), db.upgrade(settings.data_dir, 3)]):
        _reprocess_laps(settings)
    ruleset = Ruleset.load(settings.ruleset_path)
    unstored = set(ruleset.leaderboard_key + ruleset.top_n_per) - STORED_FIELDS
    if unstored:
        raise ValueError(f"ruleset {ruleset.id}: laps don't store {sorted(unstored)}, "
                         "so the server can't build leaderboards by them")
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
        name = steam_name(steam_id, settings.steam_api_key)
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

    @app.post("/api/sign-out")
    def sign_out(authorization: str | None = Header(default=None),
                 con: sqlite3.Connection = Depends(get_con)) -> dict:
        """Revoke this sign-in's token (the app forgets it too)."""
        if authorization and authorization.startswith("Bearer "):
            db.revoke_token(con, authorization.removeprefix("Bearer ").strip())
        return {"signed_out": True}

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
        # One row per car model (or entry, where the model isn't known), with "laps"
        # counting drivers: laps are kept per band, but each driver shows once.
        rows = con.execute(
            "SELECT track, layout, MIN(car) AS car, car_class, car_model, "
            "COUNT(DISTINCT user_id) AS laps, MIN(lap_time_s) AS best FROM laps "
            "WHERE ruleset_id = ? GROUP BY track, layout, car_class, COALESCE(car_model, car) "
            "ORDER BY track, COALESCE(car_model, car)",
            (ruleset.id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @app.get("/api/leaderboard")
    def leaderboard(track: str, car: str | None = None, car_class: str | None = None,
                    layout: str | None = None, car_model: str | None = None,
                    setup: str | None = None, track_temp_min: float | None = None,
                    track_temp_max: float | None = None,
                    fuel: list[str] | None = Query(default=None),
                    con=Depends(get_con), authorization: str | None = Header(default=None)):
        """Laps at a track, fastest first: one car's (entry or model), one class's, or
        all; optionally only laps on a ``fixed`` or ``open`` setup, with the track
        temperature in a range, and in some fuel bands. Each driver's best matching
        lap per car, top N per car. Anyone can read it; for a signed-in app, ``mine``
        marks the user's own laps."""
        me = None
        if authorization and authorization.startswith("Bearer "):
            me = db.user_for_token(con, authorization.removeprefix("Bearer ").strip())
        bands, band_args = [], []
        if setup in ("fixed", "open"):
            bands.append("setup_fixed = ?")
            band_args.append(int(setup == "fixed"))
        if track_temp_min is not None:
            bands.append("track_temp_band >= ?")
            band_args.append(track_temp_band(track_temp_min))
        if track_temp_max is not None:
            bands.append("track_temp_band < ?")
            band_args.append(track_temp_max)
        if fuel:
            fuel = sorted(set(fuel))
            if not set(fuel) <= set(FUEL_LABELS):
                raise HTTPException(422, f"fuel must be among {sorted(FUEL_LABELS)}.")
            bands.append(f"fuel_band IN ({', '.join('?' * len(fuel))})")
            band_args += fuel
        rows = con.execute(
            "SELECT laps.id, laps.user_id, users.name AS driver, "
            "laps.lap_time_s, laps.track, laps.car, laps.car_class, laps.layout, "
            "laps.recorded_at, laps.uploaded_at, " + ", ".join(f"laps.{c}" for c in db.ADDED_COLUMNS) +
            " FROM laps JOIN users ON users.id = laps.user_id "
            "WHERE ruleset_id = ? AND track = ? AND (? IS NULL OR car = ?) "
            "AND (? IS NULL OR car_class = ?) AND (? IS NULL OR layout = ?) "
            "AND (? IS NULL OR car_model = ?) AND users.banned = 0 "
            + "".join(f"AND {b} " for b in bands) + "ORDER BY lap_time_s",
            (ruleset.id, track, car, car, car_class, car_class, layout, layout,
             car_model, car_model, *band_args),
        ).fetchall()
        # Laps are kept per band; show each driver's best, and the top N, per car.
        slot = [c for c in (*ruleset.leaderboard_key, *ruleset.top_n_per)
                if c not in BAND_FIELDS]
        seen, per_slot, shown = set(), defaultdict(int), []
        for r in rows:
            where = tuple(r[c] for c in slot)
            if (where, r["user_id"]) in seen or per_slot[where] >= ruleset.top_n:
                continue
            seen.add((where, r["user_id"]))
            per_slot[where] += 1
            shown.append(r)
        return [{"rank": i, **{k: r[k] for k in r.keys() if k != "user_id"},
                 "mine": me is not None and r["user_id"] == me["id"]}
                for i, r in enumerate(shown, start=1)]

    @app.get("/api/laps/{lap_id}")
    def lap_info(lap_id: str, con=Depends(get_con)) -> dict:
        row = _lap_row(con, lap_id)
        info = {k: row[k] for k in row.keys() if k not in _PRIVATE_LAP_COLUMNS}
        driver = con.execute("SELECT name FROM users WHERE id = ?", (row["user_id"],)).fetchone()
        return {**info, "driver": driver["name"], "report": json.loads(row["report_json"])}

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
            f"<tr><td>{html.escape(b['track'])}</td>"
            f"<td>{html.escape(b['car_model'] or b['car'])}</td>"
            f"<td>{b['laps']}</td><td>{_fmt_time(b['best'])}</td></tr>" for b in boards
        ) or "<tr><td colspan=4>No laps yet.</td></tr>"
        return _page("LMU Lap Analyser", (
            f"<p>Leaderboards for <b>{html.escape(ruleset.name)}</b>: "
            f"{html.escape(ruleset.description)}</p>"
            "<table><tr><th>Track</th><th>Car</th><th>Drivers</th><th>Best</th></tr>"
            f"{rows}</table><p><a href=privacy>Privacy</a></p>"))

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy() -> str:
        return _page("Privacy", (
            "<p>When you sign in with Steam we store your Steam ID and Steam display name. "
            "When you upload a lap we store the lap's telemetry (one lap cut from your LMU "
            "recording, including the in-game driver name and car setup) and show your "
            "display name, lap time, the lap's telemetry and its car setup on the "
            "leaderboard; the in-game driver name is not shown. Laps that drop out of the top "
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
    # Per ruleset, so a lap can enter the leaderboards of a new ruleset too.
    f = v.facts
    fingerprint = hashlib.sha256(
        f"{ruleset.id}|{f.recorded_at}|{f.lap_number}|{f.lap_time_s:.4f}|{f.track}|{f.car}".encode()
    ).hexdigest()
    if con.execute("SELECT 1 FROM laps WHERE file_hash = ?", (fingerprint,)).fetchone():
        return 409, _rejected("This lap was already uploaded.")
    report = ruleset.check(v.facts)
    rules = [dataclasses.asdict(r) for r in report.results]
    if not report.accepted:
        limits = next((c.detail for c in v.checks if c.name.startswith("track limits")), "")
        reasons = [_rule_reason(r, limits) for r in report.problems if r.rejects]
        return 422, _rejected("Not added: " + "; ".join(reasons) + ".", checks=checks, rules=rules)

    # The laps competing for top_n places: the leaderboard, or one car on it
    # (top_n_per). Each driver keeps one lap there.
    def where(fields: list[str]) -> tuple[str, tuple]:
        sql = " AND ".join(["ruleset_id = ?", *(f"{c} IS ?" for c in fields)])
        return sql, (ruleset.id, *(getattr(f, c) for c in fields))

    board, key = where([*ruleset.leaderboard_key, *ruleset.top_n_per])
    whole_board, whole_key = where(ruleset.leaderboard_key)
    # The car laps compete within: the entry under top_n_per [car], else the model;
    # and the conditions, when laps are kept per band.
    car_name = f.car if "car" in ruleset.top_n_per else (f.car_model or f.car)
    within = f" in the {car_name}" if {"car", "car_model"} & set(ruleset.top_n_per) else ""
    if set(BAND_FIELDS) & set(ruleset.top_n_per):
        within += f", {describe(f.setup_fixed, f.track_temp_band, f.fuel_band)}"
    con.execute("BEGIN IMMEDIATE")
    try:
        own = con.execute(f"SELECT id, lap_time_s FROM laps WHERE {board} AND user_id = ?",
                          (*key, user["id"])).fetchone()
        if own and own["lap_time_s"] <= f.lap_time_s:
            con.execute("ROLLBACK")
            return 200, _rejected(
                f"You already have a faster lap here{within} ({_fmt_time(own['lap_time_s'])}).",
                checks=checks, rules=rules)
        faster = con.execute(
            f"SELECT COUNT(*) FROM laps WHERE {board} AND user_id != ? AND lap_time_s < ?",
            (*key, user["id"], f.lap_time_s)).fetchone()[0]
        rank = faster + 1
        if rank > ruleset.top_n:
            con.execute("ROLLBACK")
            return 200, _rejected(
                f"Not fast enough for the top {ruleset.top_n}{within} (it would be #{rank}).",
                checks=checks, rules=rules)
        # Overall: drivers with a faster lap anywhere on the leaderboard.
        overall = 1 + con.execute(
            f"SELECT COUNT(DISTINCT user_id) FROM laps WHERE {whole_board} AND user_id != ? "
            "AND lap_time_s < ?", (*whole_key, user["id"], f.lap_time_s)).fetchone()[0]

        lap_id = uuid.uuid4().hex
        raw, processed = db.lap_files(settings.data_dir, lap_id)
        raw.write_bytes(data)
        _served(v.processed, user["name"]).save(processed)
        row = {
            "id": lap_id, "user_id": user["id"], "ruleset_id": ruleset.id, "track": f.track,
            "layout": f.layout, "car": f.car, "car_class": f.car_class,
            "lap_time_s": f.lap_time_s, "track_length_m": float(v.processed.distance_m[-1]),
            "driver_name": f.driver, "recorded_at": f.recorded_at, "uploaded_at": time.time(),
            "file_hash": fingerprint, "report_json": json.dumps({"checks": checks, "rules": rules}),
            **{c: getattr(f, c) for c in db.ADDED_COLUMNS},
        }
        con.execute(f"INSERT INTO laps ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                    tuple(row.values()))
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
    overall_note = ""
    if ruleset.top_n_per:
        overall_note = f" (#{overall} overall" + (f" in {f.car_class})" if f.car_class else ")")
    return 201, {
        "accepted": True, "lap_id": lap_id, "rank": rank, "overall_rank": overall,
        "lap_time_s": f.lap_time_s,
        "message": f"Accepted: #{rank} on {f.track}{within or ' in the ' + car_name}"
                   f"{overall_note}.",
        "checks": checks, "rules": rules,
    }


def _fill_added_columns(settings: Settings) -> None:
    """Columns for laps stored before they existed, read again from each lap's
    extract; the processed lap's facts get them too."""
    con = db.connect(settings.data_dir)
    try:
        for (lap_id,) in con.execute("SELECT id FROM laps").fetchall():
            raw, processed = db.lap_files(settings.data_dir, lap_id)
            try:
                fresh = _facts_from_extract(raw, int(settings.max_unpacked_mb * 1e6))
                p = ProcessedLap.load(processed)
            except Exception:  # noqa: BLE001 - a lap that can't be read keeps empty columns
                continue
            added = {c: getattr(fresh, c) for c in db.ADDED_COLUMNS}
            con.execute("UPDATE laps SET " + ", ".join(f"{c} = ?" for c in added) +
                        " WHERE id = ?", (*added.values(), lap_id))
            dataclasses.replace(p, facts=dataclasses.replace(p.facts, **added)).save(processed)
    finally:
        con.close()


def _served(lap: ProcessedLap, name: str) -> ProcessedLap:
    """The processed lap as anyone may download it: with the driver's leaderboard
    name, not the in-game one."""
    return dataclasses.replace(lap, facts=dataclasses.replace(lap.facts, driver=name))


def _hide_driver_names(settings: Settings) -> None:
    """Processed laps stored with the in-game driver name get the leaderboard name."""
    con = db.connect(settings.data_dir)
    try:
        for lap_id, name in con.execute(
                "SELECT laps.id, users.name FROM laps JOIN users ON users.id = laps.user_id"):
            _, processed = db.lap_files(settings.data_dir, lap_id)
            try:
                _served(ProcessedLap.load(processed), name).save(processed)
            except Exception:  # noqa: BLE001 - a lap that can't be read isn't served either
                continue
    finally:
        con.close()


def _reprocess_laps(settings: Settings) -> None:
    """Processed laps stored before setups and sectors were kept are made again
    from their extract; their facts (as served) stay."""
    def resampled(rec: Recording) -> ProcessedLap:
        number = int(rec.metadata["ExtractedLap"])
        return resample_lap(rec, next(l for l in split_laps(rec) if l.number == number))

    con = db.connect(settings.data_dir)
    try:
        for (lap_id,) in con.execute("SELECT id FROM laps").fetchall():
            raw, processed = db.lap_files(settings.data_dir, lap_id)
            try:
                fresh = _from_extract(raw, int(settings.max_unpacked_mb * 1e6), resampled)
                facts = ProcessedLap.load(processed).facts
            except Exception:  # noqa: BLE001 - a lap that can't be read stays as it is
                continue
            dataclasses.replace(fresh, facts=facts).save(processed)
    finally:
        con.close()


def _facts_from_extract(path: Path, limit: int):
    def facts(rec: Recording):
        number = int(rec.metadata["ExtractedLap"])
        lap = next(l for l in split_laps(rec) if l.number == number)
        return lap_facts(rec, lap)

    return _from_extract(path, limit, facts)


def _from_extract(path: Path, limit: int, read: Callable[[Recording], object]):
    with tempfile.TemporaryDirectory() as tmp:
        unpacked = Path(tmp) / "lap.duckdb"
        _gunzip(path.read_bytes(), unpacked, limit)
        with Recording(unpacked) as rec:
            return read(rec)


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
