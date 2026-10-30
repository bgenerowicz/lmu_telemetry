# LMU Telemetry

Compare your Le Mans Ultimate laps against fast reference laps, and see
corner by corner (and straight by straight) where you lose time and why.

Status: early. The **LMU Lap Analyser** desktop app works on your own laps:
pick one or more recordings and it opens a report in your browser showing,
corner by corner and straight by straight, where each lap lost time to your
fastest time through that section, with advice. It also shows your
theoretical best lap and where you're least consistent.

## LMU Lap Analyser (Windows)

1. Download `lmu-analyser.exe`: open the latest successful
   [Build Windows app](https://github.com/bgenerowicz/lmu_telemetry/actions/workflows/build-windows.yml) run and
   download the `lmu-analyser-windows` artifact (you need to be signed in to
   GitHub), or take it from a [release](https://github.com/bgenerowicz/lmu_telemetry/releases) if there is one.
2. In LMU, turn on telemetry recording (Settings → Gameplay) and drive a
   session with a few timed laps.
3. Run `lmu-analyser.exe`. It opens LMU's `UserData\Telemetry` folder (found
   through Steam); pick one or more recordings of the same track and car. You
   can also drop recordings onto the exe.
4. The report opens in your browser and is saved in
   `Documents\LMU Lap Analyser`.

Windows may warn about an unknown publisher, since the exe isn't signed:
choose "More info" → "Run anyway".

From source, on any OS:

```bash
python -m desktop "samples/Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb"
```

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
src/lmu_core/   analysis shared by everything (ruleset, reader, laps, segments, analysis)
src/desktop/    LMU Lap Analyser: desktop app producing the HTML report
packaging/      PyInstaller entry point (built by .github/workflows/build-windows.yml)
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
.venv/bin/pip install -e ".[dev,webapp]"
```

```bash
.venv/bin/pytest
```

## License

MIT
