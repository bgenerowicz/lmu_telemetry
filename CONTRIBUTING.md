# Contributing

Issues and pull requests are welcome. This page is for working on the code;
for using the app, see the [README](README.md).

## Setup

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev,webapp,server]"
```

(On Windows: `.venv\Scripts\pip` and `.venv\Scripts\python`.)

Run the tests before sending a change. Most of them need the sample recording
in [`samples/`](samples/) and skip themselves without it.

```bash
.venv/bin/pytest
```

## How it fits together

1. **Drive.** LMU records telemetry to `UserData\Telemetry\*.duckdb`.
2. **Upload.** The app splits recordings into laps, checks them against the
   shared [ruleset](docs/RULESETS.md) ("standard conditions"), and lists the
   laps that would make the leaderboard for you to upload. The server checks
   each lap again (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#uploads)).
3. **Review.** The app compares your lap with a reference from the
   leaderboard: time lost per corner and straight, with advice, traces and a
   track map.

The server can only check that an uploaded lap looks genuine (it stays on
track, meets the ruleset); the telemetry comes from the player's own PC, so
the leaderboard relies on moderation (see [deploy/README.md](deploy/README.md#moderation)).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details, and
[docs/RULESETS.md](docs/RULESETS.md) for rulesets.

## Layout

```
src/lmu_core/   analysis shared by everything (ruleset, reader, laps, segments,
                analysis, library of recordings and reference laps)
src/webapp/     the Dash app: track/car, folder, laps, reference, results
src/desktop/    launcher: settings, finding LMU's folder, runs the app locally
src/server/     leaderboard server (Steam sign-in, verified uploads)
deploy/         Docker, Caddy and .env files for the server
references/     expert reference laps shipped with the app (.lap.npz)
packaging/      PyInstaller entry point and exe smoke test
rulesets/       standard-conditions rulesets (YAML)
samples/        raw LMU recordings for development
docs/           architecture and format docs
tests/
```

## Running the leaderboard server locally

`src/server`: FastAPI + SQLite, Sign in with Steam. To try it with the app,
start a server that allows sign-in without Steam:

```bash
LMU_DEV_LOGIN=1 python -m server --port 8000
```

then start the app pointed at it (instead of the public server), signed in as
a made-up user:

```bash
LMU_SERVER_URL=http://127.0.0.1:8000 LMU_DEV_LOGIN_NAME=Alice python -m desktop
```

Deploying the server: [deploy/README.md](deploy/README.md).

## The Windows exe

Every push builds `lmu-analyser.exe` with PyInstaller
([workflow](.github/workflows/build-windows.yml)) after the tests pass;
pushes to the default branch publish it as the `latest` release.
