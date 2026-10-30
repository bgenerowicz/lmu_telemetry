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
   giving the *processed lap*: Parquet with one row per metre (time, speed,
   inputs, gear, rpm, g-forces, x/y), with the `LapFacts` as JSON in the
   Parquet schema metadata. This is what gets uploaded and shared. Resampled
   lap times match the official ones to within 0.01 s.
4. **Segment** the track into corners and straights, once per track (stored
   on the server, editable), so every lap is cut at the same distances.
5. **Compare** a lap with a reference (`compare.py`): `Δt(d) = t_lap(d) − t_ref(d)`.
   Time lost in a segment is Δt at its exit minus Δt at its entry.
6. **Advise** from per-segment metrics: braking point and pressure, entry,
   minimum and exit speed, throttle pickup, full-throttle point; on straights,
   speed carried in from the previous corner exit.

`scripts/compare_laps.py` plots a lap against a reference as an HTML page.

## What the recordings contain

Checked against `samples/` (LMU, October 2026):

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
