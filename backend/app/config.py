"""App-wide settings, loaded from env."""
from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass, field


def _load_local_env() -> None:
    """Populate ``os.environ`` from ``backend/.env`` for local (non-Docker) runs.

    Docker passes the file via compose ``env_file``; this covers bare ``uvicorn``.
    Real environment variables always win (``setdefault``).
    """
    env_path = pathlib.Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            os.environ.setdefault(key, val.strip().strip('"').strip("'"))


_load_local_env()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    project_root: pathlib.Path = field(default_factory=lambda: pathlib.Path(__file__).resolve().parents[2])
    data_dir: pathlib.Path = field(init=False)
    models_dir: pathlib.Path = field(init=False)

    # deployment
    app_env: str = field(default_factory=lambda: _env("APP_ENV", _env("ENV", "development")))

    # CPU-only classifier; no external model providers.
    batch_size: int = field(default_factory=lambda: int(_env("BATCH_SIZE", "256")))
    top_k: int = 3

    # CORS
    allowed_origins: list[str] = field(default_factory=lambda: [
        o.strip() for o in _env("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:8000").split(",") if o.strip()
    ])

    # Auth (OAuth + JWT). When both are set, protected routes require a valid Bearer token.
    google_oauth_client_id: str = field(default_factory=lambda: _env("GOOGLE_OAUTH_CLIENT_ID"))
    jwt_secret: str = field(default_factory=lambda: _env("JWT_SECRET"))
    jwt_exp_days: int = field(default_factory=lambda: int(_env("JWT_EXPIRE_DAYS", "1")))

    # Max upload bytes (multipart). Excel row apps: default 50 MB ceiling.
    max_upload_bytes: int = field(default_factory=lambda: int(_env("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))))
    # When true and DATABASE_URL is Postgres, run `alembic upgrade head` during API lifespan (before DB use).
    auto_migrate: bool = field(
        default_factory=lambda: _env("AUTO_MIGRATE", "0").lower() in ("1", "true", "yes")
    )

    def __post_init__(self):
        self.data_dir = self.project_root / "data" / "enriched"
        self.models_dir = self.data_dir / "models"

    @property
    def auth_enforced(self) -> bool:
        return bool(self.google_oauth_client_id and self.jwt_secret)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in ("production", "prod")


settings = Settings()
