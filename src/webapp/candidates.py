"""Which of the user's laps would make the online leaderboard.

For each track, layout and car in the telemetry folder (and band of conditions,
when the ruleset keeps laps per band), the fastest lap that beats the user's own
entry, would place in the top N, and passes the checks the server runs
(``lmu_core.verify`` and the ruleset). The server checks again on upload; this
only decides what's worth uploading.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from lmu_core.bands import BAND_FIELDS, describe
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
    band: str = ""  # the conditions it competes in, when laps are kept per band

    @property
    def key(self) -> str:
        return f"{self.path}|{self.lap}"


def find_candidates(
    recordings: list[dict],
    ruleset: Ruleset,
    entries_for: Callable[[str, str, str | None, tuple | None], list[dict]],
    skip: set[str] = frozenset(),
    signed_in: bool = True,
) -> list[Candidate]:
    """``recordings``: the app's scanned recordings. ``entries_for(track, car,
    car_class, band)``: the leaderboard laps a lap of that car (and band: setup
    fixed, track temperature band, fuel band; None when laps aren't kept per band)
    competes with, each with ``lap_time_s``, ``layout``, ``recorded_at`` and
    ``mine``. ``skip``: lap keys not to suggest again (e.g. already tried).
    Signed out, the server doesn't say which laps are ``mine``; then laps on the
    board that are one of ``recordings``' laps (same recording, same lap time)
    are taken to be."""
    per_band = bool(set(BAND_FIELDS) & set(ruleset.top_n_per))
    groups = defaultdict(list)
    for r in recordings:
        for number, seconds in r["laps"]:
            band = None
            if per_band:
                temp, fuel = r.get("lap_bands", {}).get(str(number), (None, None))
                band = (r.get("setup_fixed"), temp, fuel)
            groups[(r["track"], r.get("layout"), r["car"], r.get("car_class"), band)].append(
                (seconds, r, number))
    found = []
    for (track, layout, car, car_class, band), laps in sorted(groups.items(), key=str):
        entries = [e for e in entries_for(track, car, car_class, band)
                   if e.get("layout") == layout]
        yours = [e for e in entries
                 if (e.get("mine") if signed_in else _from_these(e, recordings))]
        mine = min((e["lap_time_s"] for e in yours), default=None)
        others = [e for e in entries if e not in yours]
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
                found.append(Candidate(r["path"], number, track, layout, car, seconds, rank,
                                       describe(*band) if band else ""))
                break
    return found


def _from_these(entry: dict, recordings: list[dict]) -> bool:
    """The board's lap is one of these recordings' laps."""
    return any(_already_there([entry], r, seconds) for r in recordings for _, seconds in r["laps"])


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
