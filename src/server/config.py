"""Server settings, from environment variables (in production: deploy/.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    # Where the database and lap files live.
    data_dir: Path
    # The server's public address, used in Steam sign-in redirects.
    public_url: str
    ruleset_path: Path
    # Optional: lets the server look up Steam display names.
    steam_api_key: str | None = None
    max_upload_mb: float = 10.0
    # Unpacked extracts are ~20 MB; anything much bigger isn't a lap.
    max_unpacked_mb: float = 100.0
    uploads_per_hour: int = 30
    # Development only: /auth/dev signs in without Steam. Never enable in production.
    dev_login: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        return cls(
            data_dir=Path(env("LMU_DATA_DIR", ROOT / "data" / "server")),
            public_url=env("LMU_PUBLIC_URL", "http://127.0.0.1:8000").rstrip("/"),
            ruleset_path=Path(env("LMU_RULESET", ROOT / "rulesets" / "loose-v4.yaml")),
            steam_api_key=env("STEAM_API_KEY") or None,
            max_upload_mb=float(env("LMU_MAX_UPLOAD_MB", 10)),
            uploads_per_hour=int(env("LMU_UPLOADS_PER_HOUR", 30)),
            dev_login=env("LMU_DEV_LOGIN", "") == "1",
        )
