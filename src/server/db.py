"""SQLite storage: users, sign-in tokens, laps. Lap files live next to the database."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    steam_id    TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    banned      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS laps (
    id              TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ruleset_id      TEXT NOT NULL,
    track           TEXT NOT NULL,
    layout          TEXT,
    car             TEXT NOT NULL,
    car_class       TEXT,
    lap_time_s      REAL NOT NULL,
    track_length_m  REAL NOT NULL,
    driver_name     TEXT,
    recorded_at     TEXT,
    uploaded_at     REAL NOT NULL,
    -- Identifies the lap itself (recording time, lap number, time, track, car).
    file_hash       TEXT UNIQUE NOT NULL,
    report_json     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS laps_board ON laps (ruleset_id, track, layout, car, lap_time_s);
CREATE TABLE IF NOT EXISTS upload_attempts (
    user_id     INTEGER NOT NULL,
    at          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS used_nonces (
    nonce       TEXT PRIMARY KEY,
    at          REAL NOT NULL
);
"""


# Conditions the lap was driven in (LapFacts fields), shown next to it. Added to
# databases made before they were stored.
CONDITION_COLUMNS = {
    "session_type": "TEXT", "weather": "TEXT", "ambient_temp_c": "REAL",
    "track_temp_c": "REAL", "fuel_at_start_l": "REAL", "setup_fixed": "INTEGER",
}
# Columns added since the first version, filled from each lap's LapFacts.
ADDED_COLUMNS = {**CONDITION_COLUMNS, "car_model": "TEXT", "track_temp_band": "INTEGER",
                 "fuel_band": "TEXT"}
# Columns added to users since the first version, with their defaults.
USER_COLUMNS = {
    # Whether the user's Steam name is shown with their laps; else "Anonymous #<id>".
    "show_name": "INTEGER NOT NULL DEFAULT 1",
}
# The name others see for a user (``users`` in the query), in SQL and in Python.
PUBLIC_NAME = "CASE WHEN users.show_name THEN users.name ELSE 'Anonymous #' || users.id END"


def public_name(user: sqlite3.Row) -> str:
    return user["name"] if user["show_name"] else f"Anonymous #{user['id']}"


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    # Each request has its own connection, but FastAPI may open and close it on
    # different worker threads, which sqlite3 refuses unless told otherwise.
    con = sqlite3.connect(data_dir / "lmu.sqlite3", timeout=10, isolation_level=None,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init(data_dir: Path) -> list[str]:
    """Create or update the database; returns the columns it had to add."""
    con = connect(data_dir)
    try:
        con.executescript(SCHEMA)
        have = {r["name"] for r in con.execute("PRAGMA table_info(laps)")}
        added = [c for c in ADDED_COLUMNS if c not in have]
        for c in added:
            con.execute(f"ALTER TABLE laps ADD COLUMN {c} {ADDED_COLUMNS[c]}")
        have_user = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
        for c, kind in USER_COLUMNS.items():
            if c not in have_user:
                con.execute(f"ALTER TABLE users ADD COLUMN {c} {kind}")
    finally:
        con.close()
    (data_dir / "laps").mkdir(parents=True, exist_ok=True)
    return added


def upgrade(data_dir: Path, version: int) -> bool:
    """Mark the database as at ``version`` (SQLite's user_version); True if it was
    older, so the caller runs that version's one-off migration."""
    con = connect(data_dir)
    try:
        if con.execute("PRAGMA user_version").fetchone()[0] >= version:
            return False
        con.execute(f"PRAGMA user_version = {int(version)}")
        return True
    finally:
        con.close()


def lap_files(data_dir: Path, lap_id: str) -> tuple[Path, Path]:
    """The uploaded extract (kept for audits) and the processed lap (served to apps)."""
    folder = data_dir / "laps"
    return folder / f"{lap_id}.duckdb.gz", folder / f"{lap_id}.lap.npz"


def upsert_user(con: sqlite3.Connection, steam_id: str, name: str) -> sqlite3.Row:
    con.execute(
        "INSERT INTO users (steam_id, name, created_at) VALUES (?, ?, ?) "
        "ON CONFLICT(steam_id) DO UPDATE SET name = excluded.name",
        (steam_id, name, time.time()),
    )
    return con.execute("SELECT * FROM users WHERE steam_id = ?", (steam_id,)).fetchone()


def new_token(con: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO tokens VALUES (?, ?, ?)", (_hash(token), user_id, time.time()))
    return token


def user_for_token(con: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT users.* FROM tokens JOIN users ON users.id = tokens.user_id "
        "WHERE token_hash = ?", (_hash(token),),
    ).fetchone()


def revoke_token(con: sqlite3.Connection, token: str) -> None:
    con.execute("DELETE FROM tokens WHERE token_hash = ?", (_hash(token),))


def use_nonce(con: sqlite3.Connection, nonce: str) -> bool:
    """Record a Steam sign-in nonce; False if it was already used (a replay)."""
    try:
        con.execute("INSERT INTO used_nonces VALUES (?, ?)", (nonce, time.time()))
        return True
    except sqlite3.IntegrityError:
        return False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
