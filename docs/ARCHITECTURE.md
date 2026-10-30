# Architecture

## Components

```
 Gaming PC (Windows)                      Server                      Browser
┌──────────────────────┐   processed   ┌──────────────────┐         ┌─────────────┐
│ LMU → *.duckdb       │     laps      │ FastAPI          │  HTTP   │ Dash web app│
│ uploader (watcher)   │ ────────────▶ │ leaderboards     │ ◀─────▶ │             │
│  lmu_core            │               │ lap store        │         │             │
└──────────────────────┘               │  lmu_core        │         └─────────────┘
                                       └──────────────────┘
```

All three use `lmu_core`, so the reader, ruleset checks and analysis are the
same everywhere. A Godot ghost viewer could later be another client of the
server, reading the same processed lap format.

## lmu_core pipeline

1. **Read** a recording (`recording.py`): `metadata` table plus one table per
   channel. Continuous channels have no timestamp: `t = start + rowid / frequency`,
   with `start` being the first `GPS Time` value. Event channels (`Lap`,
   `Gear`, …) have a `ts` column.
2. **Split into laps** (`laps.py`) and build `LapFacts` for each (what rulesets
   check). The `Lap` event marks each lap start; the `Lap Time` event at the
   next lap start gives the official time. A lap is *flying* if it has an end,
   an official time, and didn't start in the pits.
3. **Resample by distance** (`resample.py`) onto a 1 m grid of `Lap Dist`,
   giving the *processed lap*: one value per metre for time, speed, inputs,
   gear, rpm, g-forces and x/y, plus the `LapFacts`, saved as a compressed
   NumPy archive (`.lap.npz`, ~350 KB). This is what gets shared as a
   reference lap and, later, uploaded. Resampled lap times match the official
   ones to within 0.01 s.
4. **Segment** (`segments.py`) the track into corners and straights. Every
   clear dip in speed (at least 8 km/h) is a corner, running from the speed
   peak before it to the end of the curve after the slowest point (from the
   curvature of the driven line). Between corners are straights. Segments are
   found from the median of the session's laps, so one-off moments don't
   create or hide corners. Later: store them per track on the server, editable.
5. **Compare** (`compare.py`, `analysis.py`): `Δt(d) = t_lap(d) − t_ref(d)`.
   The reference is either one lap (an expert lap, whose corners then define
   the segments) or, per segment, the driver's own fastest lap through it;
   adding those up gives the theoretical best lap.
6. **Advise** (`analysis.py`) from per-segment metrics: braking point and
   peak pressure, entry, minimum and exit speed, full-throttle point, metres
   off throttle. Losses on a straight that come from a slower start are blamed
   on the exit of the corner before it.

`library.py` finds laps: it scans a telemetry folder (metadata and lap events
only, cached per file) and loads reference laps from `.lap.npz` folders: the
ones bundled in `references/` and the user's own in
`Documents/LMU Lap Analyser/references`.

The app (`src/webapp`, Plotly Dash) is the UI; `src/desktop` runs it on a
local port, opens the browser, remembers the telemetry folder, and offers a
native folder picker. `.github/workflows/build-windows.yml` packages it as a
Windows exe with PyInstaller, bundling `references/`.

## What the recordings contain

Checked against `samples/` (LMU, October 2026):

- Pedals: `Throttle Pos`/`Brake Pos` include traction-control and shift cuts;
  the `... Unfiltered` channels are the driver's actual inputs and are used.
- ~65 continuous channels (inputs and `Ground Speed`/`Engine RPM`/`Steering Pos`
  at 50–100 Hz; `Lap Dist`, `GPS Latitude`/`Longitude`, G-forces at 10 Hz;
  `Fuel Level` 20 Hz; `Track Temperature` 1 Hz) and ~40 event channels.
- `GPS Latitude/Longitude` are not real-world coordinates (they sit around
  60°N 0°E) but give a correct track shape, which is all the map needs.
- `Lap Dist` resets to ~0 at the line; the track length isn't stored, so it is
  estimated from the jump at each reset (Algarve: ~4635 m).
- `metadata` has `TrackName`, `TrackLayout`, `CarName`, `CarClass`,
  `SessionType`, `WeatherConditions`, `DriverName` and `CarSetup` (JSON, 170+
  entries, each with `value`, `stringValue` and `lastSavedStringValue`).

## Open questions

- **Invalid laps:** no track-limits flag found yet. Need a recording with a
  known invalid lap to see whether `Lap Time` is missing, negative, or flagged
  elsewhere. Until then `is_valid` is unknown, which `standard-v1` rejects.
- **Default setup:** the setup JSON doesn't say whether it is the default.
  Options: compare against a stored default per car, or rule on a setup hash.
- **Game version:** not in the recording; the uploader could read it from the
  LMU install.
- **Tyre compound:** only an index (`TyresCompound` 0..n) is recorded.
- **Car model:** only the entry name is recorded (`CarName`, e.g.
  "Garage 59 2026 #10:LM"), not the model, so expert laps match on the exact
  entry name; other cars in the same class are offered as "other car".
- **Corner names:** detected per reference, so numbering can differ between
  references. A fixed, editable corner list per track would fix this.
