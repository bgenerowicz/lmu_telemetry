"""Moderation from the server's command line.

    python -m server.admin users
    python -m server.admin laps [--track NAME]
    python -m server.admin delete-lap LAP_ID
    python -m server.admin ban STEAM_ID        (also deletes their laps)
    python -m server.admin unban STEAM_ID
    python -m server.admin make-admin STEAM_ID
    python -m server.admin import-lap --as NAME EXTRACT...
    python -m server.admin refresh-names      (Steam display names, for all users)

In Docker: docker compose exec lmu-server python -m server.admin users

import-lap adds lap extracts (``lmu_core.extract``) for a driver who hasn't
signed in, e.g. telemetry they sent: each goes through the same checks as an
upload, filed under a placeholder account with that name (not a Steam account,
so nobody can sign in as it).
"""

from __future__ import annotations

import argparse
import sys
from types import SimpleNamespace

from lmu_core.ruleset import Ruleset
from server import db, steam
from server.app import _delete_lap_files, _process_upload
from server.config import Settings

# Steam IDs are numbers, so these can't clash with a real account.
IMPORTED_PREFIX = "imported:"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="server.admin")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("users")
    sub.add_parser("refresh-names")
    laps = sub.add_parser("laps")
    laps.add_argument("--track")
    for name in ("delete-lap", "ban", "unban", "make-admin"):
        sub.add_parser(name).add_argument("target")
    imp = sub.add_parser("import-lap")
    imp.add_argument("--as", dest="name", required=True, help="driver name to show")
    imp.add_argument("files", nargs="+", help="lap extracts (.duckdb.gz)")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    db.init(settings.data_dir)
    con = db.connect(settings.data_dir)
    try:
        if args.command == "users":
            for u in con.execute("SELECT * FROM users ORDER BY created_at"):
                flags = " ".join(f for f, on in (("admin", u["is_admin"]), ("BANNED", u["banned"])) if on)
                print(f"{u['steam_id']}  {u['name']}  {flags}")
        elif args.command == "laps":
            for lap in con.execute(
                "SELECT laps.*, users.name FROM laps JOIN users ON users.id = laps.user_id "
                "WHERE ? IS NULL OR track = ? ORDER BY track, car, lap_time_s",
                (args.track, args.track),
            ):
                print(f"{lap['id']}  {lap['track']} / {lap['car']}  {lap['lap_time_s']:.3f}  {lap['name']}")
        elif args.command == "refresh-names":
            for u in con.execute("SELECT * FROM users WHERE steam_id NOT LIKE ?",
                                 (IMPORTED_PREFIX + "%",)).fetchall():
                name = steam.display_name(u["steam_id"], settings.steam_api_key)
                if name and name != u["name"]:
                    con.execute("UPDATE users SET name = ? WHERE id = ?", (name, u["id"]))
                print(f"{u['steam_id']}  {u['name']} -> {name or '(unchanged)'}")
        elif args.command == "delete-lap":
            _delete_lap_files(settings.data_dir, args.target)
            n = con.execute("DELETE FROM laps WHERE id = ?", (args.target,)).rowcount
            print(f"deleted {n} lap(s)")
        elif args.command == "import-lap":
            ruleset = Ruleset.load(settings.ruleset_path)
            user = db.upsert_user(con, f"{IMPORTED_PREFIX}{args.name}", args.name)
            for path in args.files:
                with open(path, "rb") as f:
                    _, body = _process_upload(settings, ruleset, con, user, SimpleNamespace(file=f))
                print(f"{path}: {body.get('message') or body['reason']}")
        else:
            user = con.execute("SELECT * FROM users WHERE steam_id = ?", (args.target,)).fetchone()
            if user is None:
                print(f"no user with Steam ID {args.target}", file=sys.stderr)
                return 1
            if args.command == "ban":
                for lap in con.execute("SELECT id FROM laps WHERE user_id = ?", (user["id"],)).fetchall():
                    _delete_lap_files(settings.data_dir, lap["id"])
                con.execute("DELETE FROM laps WHERE user_id = ?", (user["id"],))
                con.execute("DELETE FROM tokens WHERE user_id = ?", (user["id"],))
                con.execute("UPDATE users SET banned = 1 WHERE id = ?", (user["id"],))
            elif args.command == "unban":
                con.execute("UPDATE users SET banned = 0 WHERE id = ?", (user["id"],))
            else:
                con.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))
            print(f"{args.command}: {user['name']} ({user['steam_id']})")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
