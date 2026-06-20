"""Run Alembic migrations (Postgres / non-SQLite DATABASE_URL)."""
from __future__ import annotations

import os
from pathlib import Path

from backend.app.core.logger import get as get_logger

log = get_logger(__name__)


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    root = Path(__file__).resolve().parents[2]
    db = root / "var" / "customsai.db"
    return f"sqlite:///{db.as_posix()}"


def upgrade_database_head(*, reason: str = "AUTO_MIGRATE", allow_sqlite: bool = False) -> None:
    """Apply all pending Alembic revisions.

    When ``allow_sqlite`` is false (default), skips SQLite so local dev can keep
    using SQLAlchemy ``create_all`` only. The CLI ``run_migrations`` script sets
    ``allow_sqlite=True``.
    """
    url = _database_url()
    if not url:
        return
    if url.startswith("sqlite") and not allow_sqlite:
        return
    ini = _backend_dir() / "alembic.ini"
    if not ini.is_file():
        log.warning("alembic.ini missing, skip migrations")
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ini))
    log.info("alembic upgrade head", extra={"reason": reason, "allow_sqlite": allow_sqlite})
    command.upgrade(cfg, "head")
