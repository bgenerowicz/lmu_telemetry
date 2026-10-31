# Sample recordings

Raw LMU telemetry recordings used to develop and test the reader. Most tests
skip themselves when the sample is missing.

LMU writes them to `<LMU install>\UserData\Telemetry\` as
`<Track>_<SessionType initial>_<date>.duckdb`. A recording includes the
driver's name as set in the game.

| File | Track | Car | Notes |
|------|-------|-----|-------|
| `Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb` | Algarve | Garage 59 #10 (GT3) | Practice: out lap, flying laps 1–3 (114.425 / 112.418 / 111.602; lap 1 goes off track), partial lap 4 |

To add one, keep it short (3–5 laps) to keep the repo small, and describe it
in the table. GitHub rejects files over 100 MB.
