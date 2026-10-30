# Deploying the leaderboard server

The server runs as one container (`lmu-server`) on the VPS
(`vps-534a6b72.vps.ovh.net`), behind the Caddy container that is already
there (`substorm-https`, from the Substorm project), which provides HTTPS.
It is served at `https://vps-534a6b72.vps.ovh.net/lmu/`. Data (SQLite
database and lap files) lives in `deploy/data/` on the host.

How the VPS's Caddy is set up (it belongs to Substorm, so don't change it
from here):
- It runs in host network mode, so it reaches `lmu-server` on
  `127.0.0.1:8000` (published on loopback only).
- Its Caddyfile has one site block for the VPS hostname, which imports
  `/srv/*.caddy` (`/home/debian/www/*.caddy` on the host). Each project adds
  a path there (`/kakariko/`, `/play/`, and `/lmu/` for this one). A
  separate hostname would need a change to Substorm's Caddyfile.

## One-time setup

1. On the VPS:
   ```bash
   git clone https://github.com/bgenerowicz/lmu_telemetry.git ~/lmu-telemetry
   cd ~/lmu-telemetry/deploy
   cp .env.example .env    # then edit: STEAM_API_KEY (optional)
   mkdir -p data           # the container runs as 1000:1000, which is debian
   ```
2. Start the server:
   ```bash
   docker compose up -d --build
   curl -s http://127.0.0.1:8000/api/health
   ```
3. Add the site to Caddy, validate, then reload (Caddy also serves other
   projects, so validate first):
   ```bash
   cp lmu.caddy /home/debian/www/lmu.caddy
   docker exec substorm-https caddy validate --config /etc/caddy/Caddyfile
   docker exec substorm-https caddy reload --config /etc/caddy/Caddyfile
   curl -s https://vps-534a6b72.vps.ovh.net/lmu/api/health
   ```

## Updating

```bash
cd ~/lmu-telemetry && git pull && cd deploy && docker compose up -d --build
```

If `lmu.caddy` changed, copy it again and validate/reload as above.

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
