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

1. **Read** a recording: `metadata` table plus one table per channel.
   Continuous channels have no timestamp: `t = start + rowid / frequency`,
   with `start` being the first `GPS Time` value. Event channels (`Lap`,
   `Gear`, …) have a `ts` column.
2. **Split into laps** and build `LapFacts` for each (what rulesets check).
3. **Resample by distance** onto a fixed grid (1 m), giving the *processed
   lap*: Parquet with one row per metre, plus a JSON sidecar with the
   `LapFacts`. This is what gets uploaded and shared.
4. **Segment** the track into corners and straights, once per track (stored
   on the server, editable), so every lap is cut at the same distances.
5. **Compare** a lap with a reference: `Δt(d) = t_lap(d) − t_ref(d)`. Time
   lost in a segment is Δt at its exit minus Δt at its entry.
6. **Advise** from per-segment metrics: braking point and pressure, entry,
   minimum and exit speed, throttle pickup, full-throttle point; on straights,
   speed carried in from the previous corner exit.

## Open questions (need sample recordings)

- How lap boundaries are marked, and how to tell out/in laps apart.
- Whether position channels (x/y/z or lat/long) exist for the track map and ghost.
- Whether the lap-invalid flag, track temperature and fuel level are logged,
  i.e. which `LapFacts` fields we can actually fill.
- How to tell a default setup from the `CarSetup` JSON in `metadata`.
- Where the game version comes from.
