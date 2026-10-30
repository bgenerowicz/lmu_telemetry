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


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(data_dir / "lmu.sqlite3", timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init(data_dir: Path) -> None:
    con = connect(data_dir)
    try:
        con.executescript(SCHEMA)
    finally:
        con.close()
    (data_dir / "laps").mkdir(parents=True, exist_ok=True)


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


def use_nonce(con: sqlite3.Connection, nonce: str) -> bool:
    """Record a Steam sign-in nonce; False if it was already used (a replay)."""
    try:
        con.execute("INSERT INTO used_nonces VALUES (?, ?)", (nonce, time.time()))
        return True
    except sqlite3.IntegrityError:
        return False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
