# LMU Telemetry

Compare your Le Mans Ultimate laps against fast reference laps, and see
corner by corner (and straight by straight) where you lose time and why.

Status: early. The ruleset format is in place. The reader for LMU's DuckDB
recordings comes next, built against real files in [`samples/`](samples/).

## How it fits together

1. **Drive.** LMU records telemetry to `UserData\Telemetry\*.duckdb`.
2. **Upload.** A small watcher on your PC splits recordings into laps, checks
   them against the shared [ruleset](docs/RULESETS.md) ("standard conditions"),
   and uploads laps that would make the top N for their track and car.
3. **Review.** A web app compares your lap with a reference from the
   leaderboard: time lost per corner and straight, with advice, charts, a
   track map and a 2D ghost.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

## Layout

```
src/lmu_core/   analysis shared by everything (ruleset, reader, laps, comparison)
src/uploader/   folder watcher + upload           (not started)
src/server/     FastAPI leaderboard server        (not started)
src/webapp/     Dash web app                      (not started)
rulesets/       standard-conditions rulesets (YAML)
samples/        raw LMU recordings for development
docs/           architecture and format docs
tests/
```

## Development

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest
```

## License

MIT
