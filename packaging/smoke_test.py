"""Start the built app, check it serves its page and all its scripts, then stop it.

    python packaging/smoke_test.py dist/lmu-analyser.exe

Callbacks are covered by the test suite; this checks the bundle has what the
browser needs (Dash and Plotly JavaScript) and that the app starts at all.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def get(path: str) -> bytes:
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return r.read()


def main(exe: str) -> int:
    home = tempfile.mkdtemp()
    env = dict(os.environ, HOME=home, USERPROFILE=home, LMU_TELEMETRY_DIR=os.path.abspath("samples"))
    proc = subprocess.Popen([exe, "--port", str(PORT), "--no-browser"], env=env)
    try:
        for _ in range(120):
            try:
                page = get("/").decode()
                break
            except OSError:
                if proc.poll() is not None:
                    print(f"app exited with code {proc.returncode}")
                    return 1
                time.sleep(0.5)
        else:
            print("app did not start")
            return 1
        scripts = re.findall(r'<script src="([^"]+)"', page)
        assert scripts, "no scripts in page"
        for src in scripts:
            get(src)
        get("/_dash-layout")
        get("/_dash-dependencies")
        print(f"ok: page, layout and {len(scripts)} scripts served")
        return 0
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
