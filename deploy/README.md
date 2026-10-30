# Deploying the leaderboard server

The server runs as one container (`lmu-server`) next to an existing Caddy
container on the VPS, which provides HTTPS. Data (SQLite database and lap
files) lives in `deploy/data/` on the host.

Draft: to be checked against the VPS (Caddy's network name, how it reloads).

## One-time setup

1. DNS: an `A` record for the chosen hostname (e.g. `lmu.example.com`)
   pointing at the VPS.
2. On the VPS:
   ```bash
   git clone https://github.com/bgenerowicz/lmu_telemetry.git ~/lmu-telemetry
   cd ~/lmu-telemetry/deploy
   cp .env.example .env    # then edit: LMU_PUBLIC_URL, STEAM_API_KEY, CADDY_NETWORK
   mkdir -p data && sudo chown 1000:1000 data
   ```
3. Find the Caddy container's network and put it in `.env` as `CADDY_NETWORK`:
   ```bash
   docker inspect substorm-https --format '{{json .NetworkSettings.Networks}}'
   ```
4. Start the server:
   ```bash
   docker compose up -d --build
   ```
5. Add the site to Caddy: copy `lmu.caddy` to `/home/debian/www/`, set the
   hostname, validate, then reload Caddy (this also serves other sites, so
   validate first):
   ```bash
   docker exec substorm-https caddy validate --config /etc/caddy/Caddyfile
   docker exec substorm-https caddy reload --config /etc/caddy/Caddyfile
   ```

## Updating

```bash
cd ~/lmu-telemetry && git pull && cd deploy && docker compose up -d --build
```

## Moderation

```bash
docker compose exec lmu-server python -m server.admin users
docker compose exec lmu-server python -m server.admin laps --track "Algarve International Circuit"
docker compose exec lmu-server python -m server.admin delete-lap LAP_ID
docker compose exec lmu-server python -m server.admin ban STEAM_ID
```

## Backups

Everything is in `deploy/data/`. SQLite is in WAL mode; copy it safely with:
```bash
sqlite3 data/lmu.sqlite3 ".backup data/backup.sqlite3"
```
