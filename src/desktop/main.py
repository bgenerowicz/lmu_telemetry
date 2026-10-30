"""LMU Lap Analyser: pick one or more recordings, get a report in the browser.

    lmu-analyser.exe                      choose recordings in a file dialog
    lmu-analyser.exe A.duckdb B.duckdb    analyse these (also: drop files on the exe)

Recordings from several sessions are combined, as long as they are the same
track and car. The report is saved under Documents/LMU Lap Analyser.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import traceback
import webbrowser
from pathlib import Path

from desktop.report import render
from lmu_core.analysis import analyse
from lmu_core.laps import split_laps
from lmu_core.recording import Recording
from lmu_core.resample import ProcessedLap, resample_lap

APP_NAME = "LMU Lap Analyser"
LMU_TELEMETRY = Path("steamapps", "common", "Le Mans Ultimate", "UserData", "Telemetry")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lmu-analyser", description=APP_NAME)
    parser.add_argument("recordings", nargs="*", help="LMU .duckdb telemetry recordings")
    parser.add_argument("-o", "--output", help="where to write the HTML report")
    parser.add_argument("--no-open", action="store_true", help="don't open the report")
    args = parser.parse_args(argv)

    interactive = not args.recordings
    try:
        paths = [Path(p) for p in args.recordings] or choose_recordings()
        if not paths:
            return 0
        laps, sources = load_laps(paths)
        print(f"Analysing {len(laps)} timed laps...")
        page = render(analyse(laps), sources)

        output = Path(args.output) if args.output else default_output(paths)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(page, encoding="utf-8")
        print(f"Report: {output}")
        if not args.no_open:
            webbrowser.open(output.resolve().as_uri())
        return 0
    except Exception as exc:  # noqa: BLE001 - show anything to the user, not a vanishing console
        traceback.print_exc()
        if interactive:
            _show_error(str(exc))
        return 1


def load_laps(paths: list[Path]) -> tuple[dict[str, ProcessedLap], list[str]]:
    """Timed laps from all recordings, labelled "3" or, with several files, "2.3"."""
    laps: dict[str, ProcessedLap] = {}
    key = None
    for i, path in enumerate(paths, start=1):
        print(f"Reading {path.name}")
        with Recording(path) as rec:
            this_key = (rec.metadata.get("TrackName"), rec.metadata.get("CarName"))
            if key is None:
                key = this_key
            elif this_key != key:
                raise ValueError(
                    f"{path.name} is {this_key[0]} / {this_key[1]}, but the first recording is "
                    f"{key[0]} / {key[1]}. Only recordings of the same track and car can be combined."
                )
            for lap in split_laps(rec):
                if lap.is_flying:
                    label = str(lap.number) if len(paths) == 1 else f"{i}.{lap.number}"
                    laps[label] = resample_lap(rec, lap)
    if not laps:
        raise ValueError("No timed laps found. Out laps, in laps and unfinished laps are skipped.")
    sources = [p.name for p in paths]
    return laps, sources


def find_telemetry_dir() -> Path | None:
    """LMU's telemetry folder, looked up through Steam's library folders."""
    env = os.environ.get("LMU_TELEMETRY_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    for library in _steam_libraries():
        candidate = library / LMU_TELEMETRY
        if candidate.is_dir():
            return candidate
    return None


def _steam_libraries() -> list[Path]:
    roots = []
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                roots.append(Path(winreg.QueryValueEx(k, "SteamPath")[0]))
        except OSError:
            pass
        roots.append(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Steam"))
    libraries = []
    for root in roots:
        libraries.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            text = vdf.read_text(encoding="utf-8", errors="replace")
            libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', text)]
    return libraries


def choose_recordings() -> list[Path]:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    folder = find_telemetry_dir()
    names = filedialog.askopenfilenames(
        title=f"{APP_NAME}: choose one or more recordings (same track and car)",
        initialdir=str(folder) if folder else None,
        filetypes=[("LMU telemetry", "*.duckdb"), ("All files", "*.*")],
    )
    root.destroy()
    return [Path(n) for n in names]


def default_output(paths: list[Path]) -> Path:
    name = paths[0].stem + (f" +{len(paths) - 1}" if len(paths) > 1 else "")
    return Path.home() / "Documents" / APP_NAME / f"{name}.html"


def _show_error(message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, message)
        root.destroy()
    except Exception:  # noqa: BLE001 - no display; the console has the traceback
        pass


if __name__ == "__main__":
    sys.exit(main())
