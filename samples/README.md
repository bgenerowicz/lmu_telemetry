# Sample recordings

Raw LMU telemetry recordings used to develop and test the reader.

LMU writes them to `<LMU install>\UserData\Telemetry\` as
`<Track>_<SessionType initial>_<date>.duckdb`.

## Most useful to start

- Two or more recordings at the **same track and car**, one with a clearly
  faster lap, so the comparison has something to find.
- At least one session with a lap you know was **invalid** (track limits), so
  we can find how invalid laps are flagged.
- Ideally a short one (3–5 laps) to keep the repo small.

## Adding them

Put the files in this folder as they are and commit them. If you know anything
about a recording (e.g. "lap 3 is my best, lap 5 cut T1"), add it to the table:

| File | Track | Car | Notes |
|------|-------|-----|-------|
| `Algarve International Circuit_P_2026-10-30T12_56_57Z.duckdb` | Algarve | Garage 59 #10 (GT3) | Practice: out lap, flying laps 1–3 (114.425 / 112.418 / 111.602), partial lap 4 |

GitHub rejects files over 100 MB. If a recording is bigger, use Git LFS
(`git lfs track "samples/*.duckdb"`) or share it some other way.
