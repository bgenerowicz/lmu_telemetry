"""LMU Lap Analyser: runs the web app locally and opens it in the browser.

    lmu-analyser.exe                 start the app (close the window to quit)
    lmu-analyser.exe --port 8050     fixed port instead of a free one
    lmu-analyser.exe --no-browser    don't open the browser

The telemetry folder is remembered in Documents/LMU Lap Analyser/settings.json.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import webbrowser

from desktop import paths
from webapp.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lmu-analyser", description=paths.APP_NAME)
    parser.add_argument("--port", type=int, default=0, help="port to serve on (default: any free)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the browser")
    args = parser.parse_args(argv)

    settings = paths.load_settings()
    folder = settings.get("telemetry_dir") or str(paths.find_telemetry_dir() or "")

    def remember(folder: str) -> None:
        if settings.get("telemetry_dir") != folder:
            settings["telemetry_dir"] = folder
            paths.save_settings(settings)

    app = create_app(
        reference_dirs=[paths.bundled_references_dir()],
        user_reference_dir=paths.user_references_dir(),
        initial_folder=folder,
        on_folder_scanned=remember,
        choose_folder=paths.choose_folder,
    )

    port = args.port or _free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"{paths.APP_NAME} is running at {url}")
    print("Keep this window open while you use it; close it to quit.")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(host="127.0.0.1", port=port, debug=False)
    return 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


if __name__ == "__main__":
    sys.exit(main())
