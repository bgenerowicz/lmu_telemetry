"""Reading LMU's DuckDB telemetry recordings.

A recording has one table per channel plus three metadata tables:

- ``metadata`` (key, value): driver, track, car, session type, weather, and the
  setup as a JSON blob under ``CarSetup``.
- ``channelsList`` (channelName, frequency, unit): continuous channels. These
  have no timestamp column; sample ``i`` is at ``start + i / frequency``, where
  ``start`` is the first ``GPS Time`` value.
- ``eventsList`` (eventName, unit): event channels, logged only on change, with
  a ``ts`` column in seconds on the same clock.

Channels with four values (one per wheel) have ``value1..value4`` columns.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

import duckdb
import numpy as np


class Recording:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._con = duckdb.connect(str(self.path), read_only=True)
        self.metadata: dict[str, str] = dict(
            self._con.execute("select key, value from metadata").fetchall()
        )
        self.channels: dict[str, tuple[int, str]] = {
            name: (freq, unit)
            for name, freq, unit in self._con.execute(
                'select channelName, frequency, unit from "channelsList"'
            ).fetchall()
        }
        self.events: dict[str, str] = dict(
            self._con.execute('select eventName, unit from "eventsList"').fetchall()
        )

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Recording:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @cached_property
    def start_ts(self) -> float:
        return self._con.execute('select value from "GPS Time" order by rowid limit 1').fetchone()[0]

    @cached_property
    def end_ts(self) -> float:
        freq, _ = self.channels["GPS Time"]
        return self.start_ts + (self._count("GPS Time") - 1) / freq

    @cached_property
    def setup(self) -> dict:
        return json.loads(self.metadata.get("CarSetup", "{}"))

    def channel(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        """Timestamps and values of a continuous channel.

        Values are 1-D, or shape (n, 4) for per-wheel channels.
        """
        if name not in self.channels:
            raise KeyError(f"no continuous channel {name!r}")
        freq, _ = self.channels[name]
        values = self._values(name)
        ts = self.start_ts + np.arange(len(values)) / freq
        return ts, values

    def event(self, name: str) -> tuple[np.ndarray, np.ndarray]:
        """Timestamps and values of an event channel, one entry per change."""
        if name not in self.events:
            raise KeyError(f"no event channel {name!r}")
        ts = np.array(
            [r[0] for r in self._con.execute(f'select ts from "{name}" order by rowid').fetchall()],
            dtype=float,
        )
        return ts, self._values(name)

    def _count(self, name: str) -> int:
        return self._con.execute(f'select count(*) from "{name}"').fetchone()[0]

    def _values(self, name: str) -> np.ndarray:
        # Names are checked against channelsList/eventsList before reaching here.
        cols = [
            c
            for (c,) in self._con.execute(
                "select column_name from information_schema.columns "
                "where table_name = ? order by ordinal_position",
                [name],
            ).fetchall()
            if c != "ts"
        ]
        select = ", ".join(f'"{c}"' for c in cols)
        rows = self._con.execute(f'select {select} from "{name}" order by rowid').fetchall()
        values = np.array(rows, dtype=float).reshape(-1, len(cols))
        return values[:, 0] if len(cols) == 1 else values
