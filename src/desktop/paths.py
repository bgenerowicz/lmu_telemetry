"""Where things live on the user's machine, and the app's saved settings."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

APP_NAME = "LMU Lap Analyser"
LMU_TELEMETRY = Path("steamapps", "common", "Le Mans Ultimate", "UserData", "Telemetry")
# The public leaderboard server (see deploy/README.md); LMU_SERVER_URL overrides it.
DEFAULT_SERVER_URL = "https://vps-534a6b72.vps.ovh.net/lmu"


def app_dir() -> Path:
    return Path.home() / "Documents" / APP_NAME


def user_references_dir() -> Path:
    return app_dir() / "references"


def bundled_references_dir() -> Path:
    """References shipped with the app: next to the code, or inside the exe."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "references"
    return Path(__file__).resolve().parents[2] / "references"


def load_settings() -> dict:
    try:
        return json.loads((app_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    path = app_dir() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


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


def choose_folder(initial: str | None = None) -> str | None:
    """Native folder picker. The app runs locally, so the server can show it."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(
        title=f"{APP_NAME}: choose your LMU telemetry folder", initialdir=initial or None
    )
    root.destroy()
    return folder or None
