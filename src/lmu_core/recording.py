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

Recordings can come from anyone (uploads to the server), so they are opened
without access to files, the network or extensions, and with no views: a view
could run any query when one of the tables above is read.
"""

from __future__ import annotations

import json
from functools import cached_property
from pathlib import Path

import duckdb
import numpy as np

# Nothing outside the recording itself: no files, network or extensions, and
# settings that can't be changed back from inside a query.
_LOCKED_DOWN = {
    "enable_external_access": False,
    "autoload_known_extensions": False,
    "autoinstall_known_extensions": False,
    "lock_configuration": True,
}


class UnsafeRecording(ValueError):
    """The file has things a real LMU recording never has (views)."""


class Recording:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._con = duckdb.connect(str(self.path), read_only=True, config=_LOCKED_DOWN)
        try:
            views = self._con.execute(
                "select count(*) from duckdb_views() where not internal").fetchone()[0]
            if views:
                raise UnsafeRecording(f"{self.path.name} has views; not an LMU recording")
        except BaseException:
            self._con.close()
            raise
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
        return self._con.execute(f'select value from {_table("GPS Time")} order by rowid '
                                 'limit 1').fetchone()[0]

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
            [r[0] for r in self._con.execute(
                f"select ts from {_table(name)} order by rowid").fetchall()],
            dtype=float,
        )
        return ts, self._values(name)

    def _count(self, name: str) -> int:
        return self._con.execute(f"select count(*) from {_table(name)}").fetchone()[0]

    def _values(self, name: str) -> np.ndarray:
        cols = [
            c
            for (c,) in self._con.execute(
                "select column_name from information_schema.columns "
                "where table_name = ? order by ordinal_position",
                [name],
            ).fetchall()
            if c != "ts"
        ]
        select = ", ".join(_table(c) for c in cols)
        rows = self._con.execute(f"select {select} from {_table(name)} order by rowid").fetchall()
        values = np.array(rows, dtype=float).reshape(-1, len(cols))
        return values[:, 0] if len(cols) == 1 else values


def _table(name: str) -> str:
    """A quoted identifier. Names come from the file, so quotes in them are escaped."""
    return '"' + name.replace('"', '""') + '"'
