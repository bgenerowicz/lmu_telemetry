"""Finding laps: the driver's recordings and the expert reference laps.

- Recordings are LMU ``.duckdb`` files in a telemetry folder. Scanning reads
  only metadata and lap events, and is cached per file (path, size, mtime), so
  rescanning a folder only opens new or changed files.
- Reference ("expert") laps are processed laps saved as ``.lap.npz`` files in
  reference folders. They're small and self-describing (see ``ProcessedLap``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import ProcessedLap, resample_lap

REFERENCE_SUFFIX = ".lap.npz"


@dataclass(frozen=True)
class LapEntry:
    number: int
    lap_time_s: float


@dataclass(frozen=True)
class RecordingInfo:
    path: str
    track: str
    layout: str | None
    car: str
    car_class: str | None
    session_type: str | None
    driver: str | None
    recorded_at: str | None
    laps: tuple[LapEntry, ...]

    @property
    def best_lap(self) -> LapEntry | None:
        return min(self.laps, key=lambda l: l.lap_time_s, default=None)


@dataclass(frozen=True)
class Reference:
    path: str
    track: str
    car: str
    car_class: str | None
    driver: str | None
    lap_time_s: float


def scan_folder(folder: str | Path) -> tuple[list[RecordingInfo], list[str]]:
    """Recordings in ``folder`` (not recursive), newest first, and any errors."""
    infos, errors = [], []
    for path in sorted(Path(folder).glob("*.duckdb")):
        try:
            stat = path.stat()
            infos.append(_scan_file(str(path), stat.st_size, stat.st_mtime))
        except Exception as exc:  # noqa: BLE001 - one bad file shouldn't hide the rest
            errors.append(f"{path.name}: {exc}")
    infos.sort(key=lambda r: r.recorded_at or "", reverse=True)
    return infos, errors


@lru_cache(maxsize=1024)
def _scan_file(path: str, size: int, mtime: float) -> RecordingInfo:
    with Recording(path) as rec:
        meta = rec.metadata
        laps = tuple(
            LapEntry(lap.number, lap.lap_time_s) for lap in split_laps(rec) if lap.is_flying
        )
    return RecordingInfo(
        path=path,
        track=meta.get("TrackName", "?"),
        layout=meta.get("TrackLayout"),
        car=meta.get("CarName", "?"),
        car_class=meta.get("CarClass"),
        session_type=meta.get("SessionType"),
        driver=meta.get("DriverName"),
        recorded_at=meta.get("RecordingTime"),
        laps=laps,
    )


def load_lap(path: str | Path, number: int) -> ProcessedLap:
    """A lap from a recording, resampled by distance (cached)."""
    stat = Path(path).stat()
    return _load_lap(str(path), number, stat.st_size, stat.st_mtime)


@lru_cache(maxsize=64)
def _load_lap(path: str, number: int, size: int, mtime: float) -> ProcessedLap:
    with Recording(path) as rec:
        for lap in split_laps(rec):
            if lap.number == number:
                return resample_lap(rec, lap)
    raise KeyError(f"lap {number} not found in {Path(path).name}")


def load_references(folders: list[str | Path]) -> tuple[list[Reference], list[str]]:
    refs, errors = [], []
    for folder in folders:
        for path in sorted(Path(folder).glob(f"*{REFERENCE_SUFFIX}")):
            try:
                facts = load_reference(path).facts
                refs.append(
                    Reference(str(path), facts.track, facts.car, facts.car_class,
                              facts.driver, float(facts.lap_time_s))
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path.name}: {exc}")
    return refs, errors


def load_reference(path: str | Path) -> ProcessedLap:
    stat = Path(path).stat()
    return _load_reference(str(path), stat.st_size, stat.st_mtime)


@lru_cache(maxsize=64)
def _load_reference(path: str, size: int, mtime: float) -> ProcessedLap:
    return ProcessedLap.load(path)


def save_reference(lap: ProcessedLap, folder: str | Path) -> Path:
    f = lap.facts
    m, s = divmod(f.lap_time_s or 0, 60)
    name = f"{f.track} - {f.car} - {f.driver or 'unknown'} - {int(m)}m{s:06.3f}s"
    path = Path(folder) / (re.sub(r'[<>:"/\\|?*]+', "_", name) + REFERENCE_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    lap.save(path)
    return path
