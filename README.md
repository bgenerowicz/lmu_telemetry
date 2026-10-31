# LMU Telemetry

Compare your Le Mans Ultimate laps against fast reference laps, and see
corner by corner (and straight by straight) where you lose time and why.

Status: early, but usable. **LMU Lap Analyser** is a local app: pick a track and car,
point it at your telemetry folder, choose one of your laps and a lap
to compare against (from the online leaderboard, or your own), and it shows corner by corner (and straight by straight)
where you lose time and why, with traces and a track map.

## LMU Lap Analyser (Windows)

1. In LMU, turn on telemetry recording (Settings → Gameplay) and drive a
   session with a few timed laps.
2. Start the app, either way:
   - **The exe:** download [`lmu-analyser.exe`](https://github.com/bgenerowicz/lmu_telemetry/releases/latest/download/lmu-analyser.exe)
     from the [latest release](https://github.com/bgenerowicz/lmu_telemetry/releases/latest)
     and run it. It is built from this repository by
     [GitHub Actions](.github/workflows/build-windows.yml) on every push, but
     it isn't signed, so Windows warns about an unknown publisher: choose
     "More info" → "Run anyway".
   - **From source**, if you'd rather not run an unsigned exe: see
     [Running from source](#running-from-source).

   The app opens in your browser. Keep its console window (or terminal) open;
   closing it quits the app.
3. In the app:
   1. **Track and car**: from your recordings and the online leaderboard.
   2. **Telemetry folder**: found through Steam if possible; otherwise
      **Browse…** to LMU's `UserData\Telemetry`. It's remembered.
   3. **Your lap**: your timed laps for that track and car, fastest first.
   4. **Compare against**: a lap from the online leaderboard, your own best
      lap, or your fastest time through each section (a theoretical best
      lap). Saved expert laps (★) appear here too.

   The comparison shows traces (time gap, speed, pedals, steering, line, gear)
   next to a track map with both driving lines and the track edges. Hover the
   traces to see both cars on the map; drag across the traces, or click one of
   the "where you lose time" sections, to zoom the map to that stretch. Scroll
   on the map to zoom, drag to move.

**Online leaderboard**: anyone can see it; to add your laps, **Sign in with
Steam**. The app lists your laps that would make the leaderboard; tick the
ones you want and click **Upload selected laps**. The server checks the
lap itself (see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#uploads)) and
keeps one leaderboard per class (e.g. GT3) at each track, with each car model's top 5
drivers, under the current [ruleset](rulesets/loose-v5.yaml), with the
conditions each lap was driven in (weather, air and track temperature, fuel at
the start of the lap, fixed or open setup). Laps are kept per band of
conditions (setup type, 5° of track temperature, fuel ≤10 / 10–30 / 30–60 /
60+ L; see [`lmu_core/bands.py`](src/lmu_core/bands.py)), so the **Filters**
section (car, setup, track temperature, fuel; folded away, with the active
filters in its header) shows each driver's best lap in those conditions. Laps on the leaderboard appear under **Compare against** as
"(online)", and clicking one picks it; other cars of the same class come with
a warning.

Expert laps (`.lap.npz`) in `Documents\LMU Lap Analyser\references` or in
[`references/`](references/) (shipped with the app) are picked up too.

### Running from source

This runs the code in this repository as it is, with nothing prebuilt.

1. Install [Python](https://www.python.org/downloads/) 3.11 or newer (on
   Windows, tick "Add python.exe to PATH" in the installer).
2. Get the code: **Code → Download ZIP** on GitHub and unzip it, or
   `git clone https://github.com/bgenerowicz/lmu_telemetry.git`.
3. Open a terminal in that folder (in Explorer: right-click the folder →
   "Open in Terminal") and install the app into its own environment, once:

   ```powershell
   python -m venv .venv
   ```

   ```powershell
   .venv\Scripts\pip install -e ".[webapp]"
   ```

4. Start it (this is also how you start it next time):

   ```powershell
   .venv\Scripts\python -m desktop
   ```

To update, download the code again (or `git pull`) and repeat step 3.

On Linux or macOS (for recordings copied from a Windows PC), use
`.venv/bin/pip` and `.venv/bin/python` instead.

## Leaderboard server

`src/server`: FastAPI + SQLite, Sign in with Steam. Deployment:
[deploy/README.md](deploy/README.md). To try it locally with the app, start a
server that allows sign-in without Steam:

```bash
LMU_DEV_LOGIN=1 python -m server --port 8000
```

then start the app pointed at it (instead of the public server), signed in as
a made-up user:

```bash
LMU_SERVER_URL=http://127.0.0.1:8000 LMU_DEV_LOGIN_NAME=Alice python -m desktop
```

## How it fits together

1. **Drive.** LMU records telemetry to `UserData\Telemetry\*.duckdb`.
2. **Upload.** The app splits recordings into laps, checks them against the
   shared [ruleset](docs/RULESETS.md) ("standard conditions"), and lists the
   laps that would make the leaderboard for you to upload. The server checks
   each lap again.
3. **Review.** The app compares your lap with a reference from the
   leaderboard: time lost per corner and straight, with advice, traces and a
   track map.

The server can only check that an uploaded lap looks genuine (it stays on
track, meets the ruleset); the telemetry comes from the player's own PC, so
the leaderboard relies on moderation (see [deploy/README.md](deploy/README.md#moderation)).

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for details.

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

## Contributing

Issues and pull requests are welcome. Run the tests before sending a change;
most of them need the sample recording in [`samples/`](samples/).

## License

[MIT](LICENSE). Not affiliated with Le Mans Ultimate or its developers.
