"""Talking to the leaderboard server from the app."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import httpx

from lmu_core.extract import extract_lap

TIMEOUT = httpx.Timeout(10.0, read=60.0)
# Leaderboards change rarely; don't refetch on every click.
CACHE_SECONDS = 60


class ServerError(Exception):
    """Something to show the user as-is."""


class ServerClient:
    def __init__(self, url: str, token: str | None = None):
        self.url = url.rstrip("/")
        self.token = token
        self._cache: dict[str, tuple[float, object]] = {}

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _get(self, path: str, **params) -> object:
        key = path + repr(sorted(params.items()))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        try:
            r = httpx.get(self.url + path, params=params, headers=self._headers(), timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise ServerError(f"Can't reach the server ({exc.__class__.__name__}).") from exc
        if r.status_code == 401:
            raise ServerError("Not signed in, or the sign-in expired.")
        if r.status_code >= 400:
            raise ServerError(_detail(r))
        data = r.json()
        self._cache[key] = (time.monotonic(), data)
        return data

    def forget_cache(self) -> None:
        self._cache.clear()

    def health(self) -> dict:
        return self._get("/api/health")

    def me(self) -> dict:
        return self._get("/api/me")

    def leaderboards(self) -> list[dict]:
        return self._get("/api/leaderboards")

    def leaderboard(self, track: str, car: str) -> list[dict]:
        return self._get("/api/leaderboard", track=track, car=car)

    def download_lap(self, lap_id: str, cache_dir: Path) -> Path:
        """The processed lap, from the local cache or the server."""
        path = Path(cache_dir) / f"{lap_id}.lap.npz"
        if path.exists():
            return path
        try:
            r = httpx.get(f"{self.url}/api/laps/{lap_id}/lap.npz", timeout=TIMEOUT)
        except httpx.HTTPError as exc:
            raise ServerError(f"Can't reach the server ({exc.__class__.__name__}).") from exc
        if r.status_code >= 400:
            raise ServerError(_detail(r))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return path

    def upload_lap(self, recording: str | Path, lap_number: int) -> dict:
        """Cut the lap out of the recording and upload it. Returns the server's verdict."""
        if not self.token:
            raise ServerError("Sign in to upload laps.")
        with tempfile.TemporaryDirectory() as tmp:
            extract = extract_lap(recording, lap_number, Path(tmp) / "lap.duckdb.gz")
            try:
                with open(extract, "rb") as f:
                    r = httpx.post(f"{self.url}/api/laps", headers=self._headers(),
                                   files={"extract": ("lap.duckdb.gz", f)}, timeout=TIMEOUT)
            except httpx.HTTPError as exc:
                raise ServerError(f"Can't reach the server ({exc.__class__.__name__}).") from exc
        if r.status_code == 401:
            raise ServerError("Not signed in, or the sign-in expired.")
        try:
            body = r.json()
        except ValueError:
            raise ServerError(f"Unexpected answer from the server ({r.status_code}).") from None
        if "accepted" not in body:
            raise ServerError(_detail(r))
        self.forget_cache()
        return body


def _detail(r: httpx.Response) -> str:
    try:
        body = r.json()
        return str(body.get("detail") or body.get("reason") or body)
    except ValueError:
        return f"Server error ({r.status_code})."
