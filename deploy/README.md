# Deploying the leaderboard server

The server runs as one container (`lmu-server`) behind a reverse proxy that
provides HTTPS. Data (SQLite database and lap files) lives in `deploy/data/`
on the host.

Below, `HOST` is the server's public hostname and `SSH` the SSH login for it
(e.g. `debian@HOST`). The public server's address is the app's default,
`DEFAULT_SERVER_URL` in [`src/desktop/paths.py`](../src/desktop/paths.py).

The setup here assumes a Caddy container that already serves other projects
on `HOST`:
- It runs in host network mode, so it reaches `lmu-server` on
  `127.0.0.1:8000` (published on loopback only).
- Its Caddyfile has one site block for `HOST`, which imports `/srv/*.caddy`
  (`~/www/*.caddy` on the host). Each project adds a path there; this one is
  `/lmu/`. A separate hostname would need a change to that Caddyfile.

With any other proxy, forward `https://HOST/lmu/` to `127.0.0.1:8000` with the
`/lmu` prefix stripped (see [`lmu.caddy`](lmu.caddy)), allowing request bodies
up to 12 MB.

## One-time setup

1. Copy the code from a checkout of `main` (`core.autocrlf=false` keeps
   Windows line endings out; cloning on the server works too):
   ```bash
   git -c core.autocrlf=false archive main | ssh SSH 'mkdir -p ~/lmu-telemetry && tar -x -C ~/lmu-telemetry'
   ```
   Then on the server:
   ```bash
   cd ~/lmu-telemetry/deploy
   cp .env.example .env    # then edit: LMU_PUBLIC_URL, STEAM_API_KEY (optional)
   mkdir -p data           # the container runs as 1000:1000
   ```
2. Start the server:
   ```bash
   docker compose up -d --build
   curl -s http://127.0.0.1:8000/api/health
   ```
3. Add the site to Caddy, validate, then reload (Caddy also serves other
   projects, so validate first; `CADDY` is its container's name):
   ```bash
   cp lmu.caddy ~/www/lmu.caddy
   docker exec CADDY caddy validate --config /etc/caddy/Caddyfile
   docker exec CADDY caddy reload --config /etc/caddy/Caddyfile
   curl -s https://HOST/lmu/api/health
   ```

## Updating

From the checkout of `main`, copy the code over again and rebuild (this
keeps `deploy/.env` and `deploy/data/`):
```bash
git -c core.autocrlf=false archive main | ssh SSH 'tar -x -C ~/lmu-telemetry'
```
```bash
ssh SSH 'cd ~/lmu-telemetry/deploy && docker compose up -d --build'
```

Files deleted from the repository stay on the server until removed by hand. If
`lmu.caddy` changed, copy it again and validate/reload as above.

## Moderation

```bash
docker compose exec lmu-server python -m server.admin users
docker compose exec lmu-server python -m server.admin laps --track "Algarve International Circuit"
docker compose exec lmu-server python -m server.admin delete-lap LAP_ID
docker compose exec lmu-server python -m server.admin ban STEAM_ID
```

Names come from Steam at sign-in (the Web API with `STEAM_API_KEY`, otherwise
the public profile page). To pick up name changes without waiting for sign-in:
```bash
docker compose exec lmu-server python -m server.admin refresh-names
```

To add laps for a driver who sent their telemetry instead of uploading (same
checks as an upload, shown under NAME, no Steam account), copy lap extracts
into `data/import/` and (`sh -c` so the `*` is expanded inside the container):
```bash
docker compose exec lmu-server sh -c 'python -m server.admin import-lap --as "NAME" /data/import/*.duckdb.gz'
```

## Backups

Everything is in `deploy/data/`. SQLite is in WAL mode; copy it safely with:
```bash
sqlite3 data/lmu.sqlite3 ".backup data/backup.sqlite3"
```
