"""Cutting one lap out of a recording, for uploading.

A full recording is ~13 MB for 9 minutes; one lap with all channels at their
full rate is ~1.8 MB gzipped. The extract is itself an LMU-style DuckDB file
(same tables and columns), so the server reads it with the same code as a
full recording and redoes all processing itself.

Timing: continuous channels have no timestamps (sample i is at start + i/f),
so the cut starts a whole number of seconds after the recording start. Every
channel frequency is a whole number of Hz, so every channel then starts on a
sample at exactly the new start time, and timestamps stay correct.
"""

from __future__ import annotations

import gzip
import math
import shutil
import tempfile
from pathlib import Path

import duckdb

from lmu_core.laps import split_laps
from lmu_core.recording import Recording

PADDING_S = 2.0
# Lap boundary events: only those of the extracted lap are kept, so the extract
# doesn't contain a phantom previous lap.
_LAP_EVENTS = ("Lap", "Lap Time")
_TS_TOLERANCE = 0.05


def extract_lap(source: str | Path, lap_number: int, dest: str | Path) -> Path:
    """Write lap ``lap_number`` of ``source`` to ``dest`` as gzipped DuckDB."""
    with Recording(source) as rec:
        lap = next((l for l in split_laps(rec) if l.number == lap_number), None)
        if lap is None or not lap.complete:
            raise ValueError(f"lap {lap_number} not found or not complete in {Path(source).name}")
        start = rec.start_ts
        t0 = start + max(0, math.floor(lap.start_ts - PADDING_S - start))
        t1 = lap.end_ts + PADDING_S
        channels = dict(rec.channels)
        events = list(rec.events)

    dest = Path(dest)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "lap.duckdb"
        con = duckdb.connect(str(db))
        try:
            con.execute("ATTACH ? AS src (READ_ONLY)", [str(source)])
            for name, (freq, _) in channels.items():
                lo, hi = round((t0 - start) * freq), math.ceil((t1 - start) * freq)
                con.execute(
                    f'CREATE TABLE {_q(name)} AS SELECT * FROM src.{_q(name)} '
                    f"WHERE rowid BETWEEN {lo} AND {hi} ORDER BY rowid"
                )
            for name in events:
                if name in _LAP_EVENTS:
                    lo = lap.start_ts - _TS_TOLERANCE if name == "Lap" else lap.start_ts + _TS_TOLERANCE
                    con.execute(
                        f"CREATE TABLE {_q(name)} AS SELECT * FROM src.{_q(name)} "
                        f"WHERE ts BETWEEN {lo} AND {t1} ORDER BY rowid"
                    )
                    continue
                # Other events: changes inside the window, plus the state at its
                # start (the last change before it, moved to the window start).
                con.execute(
                    f"CREATE TABLE {_q(name)} AS "
                    f"SELECT * REPLACE (greatest(ts, {t0}) AS ts) FROM src.{_q(name)} "
                    f"WHERE ts BETWEEN {t0} AND {t1} OR rowid = "
                    f"(SELECT max(rowid) FROM src.{_q(name)} WHERE ts < {t0}) ORDER BY rowid"
                )
            for table in ("channelsList", "eventsList"):
                con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM src."{table}"')
            con.execute('CREATE TABLE metadata AS SELECT * FROM src.metadata')
            con.execute("INSERT INTO metadata VALUES ('ExtractedLap', ?)", [str(lap_number)])
            con.execute("DETACH src")
        finally:
            con.close()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(db, "rb") as f, gzip.open(dest, "wb", compresslevel=6) as g:
            shutil.copyfileobj(f, g)
    return dest


def open_extract(path: str | Path, workdir: str | Path) -> Recording:
    """Unpack a gzipped extract into ``workdir`` and open it."""
    db = Path(workdir) / "extract.duckdb"
    with gzip.open(path, "rb") as g, open(db, "wb") as f:
        shutil.copyfileobj(g, f)
    return Recording(db)


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'
