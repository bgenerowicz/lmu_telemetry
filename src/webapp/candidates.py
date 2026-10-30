"""Which of the user's laps would make the online leaderboard.

For each track, layout and car in the telemetry folder, the fastest lap that
beats the user's own entry, would place in the top N, and passes the checks
the server runs (``lmu_core.verify`` and the ruleset). The server checks again
on upload; this only decides what's worth uploading.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from lmu_core.recording import Recording
from lmu_core.ruleset import Ruleset
from lmu_core.verify import verify_lap

# Laps per car checked in full before giving up (each check reads the lap).
MAX_CHECKS_PER_CAR = 5


@dataclass(frozen=True)
class Candidate:
    path: str
    lap: int
    track: str
    layout: str | None
    car: str
    lap_time_s: float
    rank: int  # its place among the laps it competes with

    @property
    def key(self) -> str:
        return f"{self.path}|{self.lap}"


def find_candidates(
    recordings: list[dict],
    ruleset: Ruleset,
    entries_for: Callable[[str, str, str | None], list[dict]],
    skip: set[str] = frozenset(),
) -> list[Candidate]:
    """``recordings``: the app's scanned recordings. ``entries_for(track, car,
    car_class)``: the leaderboard laps a lap of that car competes with, each with
    ``lap_time_s``, ``layout``, ``recorded_at`` and ``mine``. ``skip``: lap keys
    not to suggest again (e.g. already tried)."""
    groups = defaultdict(list)
    for r in recordings:
        for number, seconds in r["laps"]:
            groups[(r["track"], r.get("layout"), r["car"], r.get("car_class"))].append(
                (seconds, r, number))
    found = []
    for (track, layout, car, car_class), laps in sorted(groups.items(), key=str):
        entries = [e for e in entries_for(track, car, car_class) if e.get("layout") == layout]
        mine = min((e["lap_time_s"] for e in entries if e.get("mine")), default=None)
        others = [e for e in entries if not e.get("mine")]
        checks = 0
        for seconds, r, number in sorted(laps, key=lambda x: x[0]):
            if mine is not None and seconds >= mine - 0.0005:
                break
            rank = 1 + sum(e["lap_time_s"] < seconds for e in others)
            if rank > ruleset.top_n:
                break
            if f"{r['path']}|{number}" in skip or _already_there(others, r, seconds):
                continue
            checks += 1
            if checks > MAX_CHECKS_PER_CAR:
                break
            if _passes(r["path"], number, ruleset):
                found.append(Candidate(r["path"], number, track, layout, car, seconds, rank))
                break
    return found


def _already_there(entries: list[dict], recording: dict, seconds: float) -> bool:
    """This very lap is on the board (e.g. uploaded while signed out of this app)."""
    return any(e.get("recorded_at") == recording.get("recorded_at")
               and abs(e["lap_time_s"] - seconds) < 0.0005 for e in entries)


def _passes(path: str, number: int, ruleset: Ruleset) -> bool:
    try:
        with Recording(path) as rec:
            v = verify_lap(rec, number)
    except Exception:  # noqa: BLE001 - an unreadable lap just isn't a candidate
        return False
    return v.ok and ruleset.check(v.facts).accepted
