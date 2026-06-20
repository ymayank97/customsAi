"""SQLite-backed persistence for training feedback (human-labeled CTH corrections).

Lightweight on purpose:
    - file path under ``var/customsai.db`` (relative to project root)
    - SQLAlchemy core models, no ORM session caching
    - safe to import at module load (initialises schema on first use)

Future migrations to Postgres only need to swap the engine URL via env
``DATABASE_URL``; the schema is plain SQLAlchemy.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship

from backend.app.config import settings

_LOCK = threading.Lock()
_ENGINE = None


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    google_sub = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), nullable=False, index=True)
    name = Column(String(255), default="")
    picture = Column(String(1024), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    jobs = relationship("AuditJob", back_populates="owner")


class AuditJob(Base):
    """Persisted audit job — row-level results live in ``var/results/{id}_results.jsonl``."""

    __tablename__ = "audit_jobs"

    id = Column(String(32), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    filename = Column(String(512), nullable=False)
    status = Column(String(32), default="queued", index=True)
    input_path = Column(Text, default="")
    output_path = Column(Text, default="")
    error = Column(Text, default="")
    rows_total = Column(Integer, default=0)
    rows_processed = Column(Integer, default=0)
    started_at = Column(Float, default=0.0, index=True)
    completed_at = Column(Float, default=0.0)
    eta_seconds = Column(Float, nullable=True)
    progress_data = Column(JSON)
    columns_detected = Column(JSON)

    owner = relationship("User", back_populates="jobs")


class TrainingFeedback(Base):
    __tablename__ = "training_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    job_id = Column(String(64), index=True)
    row_idx = Column(Integer, index=True)
    item_description = Column(Text, default="")
    declared_cth = Column(String(16), default="")
    predicted_cth = Column(String(16), default="")
    predicted_bcd = Column(Float, nullable=True)
    corrected_cth = Column(String(16), default="")
    corrected_bcd = Column(Float, nullable=True)
    reasoning = Column(Text, default="")
    user_label = Column(String(120), default="")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


def _resolve_database_url() -> str:
    import os

    explicit = os.environ.get("DATABASE_URL", "").strip()
    if explicit:
        return explicit
    db_path = settings.project_root / "var" / "customsai.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def _ensure_training_feedback_schema(engine) -> None:
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("training_feedback")}
    except Exception:
        return
    with engine.begin() as conn:
        if "user_id" not in cols:
            conn.execute(text("ALTER TABLE training_feedback ADD COLUMN user_id INTEGER"))
            cols.add("user_id")
        if "user_label" not in cols:
            if "user" in cols:
                try:
                    conn.execute(text("ALTER TABLE training_feedback RENAME COLUMN user TO user_label"))
                except Exception:
                    conn.execute(text("ALTER TABLE training_feedback ADD COLUMN user_label VARCHAR(120) DEFAULT ''"))
            else:
                conn.execute(text("ALTER TABLE training_feedback ADD COLUMN user_label VARCHAR(120) DEFAULT ''"))


def get_engine():
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            url = _resolve_database_url()
            if url.startswith("sqlite"):
                connect_args = {"check_same_thread": False}
                _ENGINE = create_engine(url, future=True, connect_args=connect_args)
                Base.metadata.create_all(_ENGINE)
                _ensure_training_feedback_schema(_ENGINE)
            else:
                _ENGINE = create_engine(
                    url,
                    future=True,
                    pool_size=2,
                    max_overflow=3,
                    pool_pre_ping=True,
                    pool_recycle=300,
                )
                # Postgres / other servers: apply schema with Alembic
                # (`alembic upgrade head` from repo root). See DEPLOY_RUNBOOK.md Phase B.
        return _ENGINE


def fail_stale_running_jobs(message: str = "interrupted: server restarted") -> int:
    """Mark in-flight jobs as failed (crash recovery). Returns rows updated."""
    engine = get_engine()
    now = time.time()
    with Session(engine) as session:
        res = session.execute(
            update(AuditJob)
            .where(AuditJob.status == "running")
            .values(status="failed", error=message, completed_at=now)
        )
        session.commit()
        return res.rowcount or 0


def save_feedback(payload: dict, *, user_id: int | None = None, user_label: str = "") -> dict:
    engine = get_engine()
    with Session(engine) as session:
        row = TrainingFeedback(
            user_id=user_id,
            job_id=payload.get("job_id") or "",
            row_idx=int(payload.get("row_idx") or 0),
            item_description=payload.get("item_description") or "",
            declared_cth=payload.get("declared_cth") or "",
            predicted_cth=payload.get("predicted_cth") or "",
            predicted_bcd=_maybe_float(payload.get("predicted_bcd")),
            corrected_cth=payload.get("corrected_cth") or "",
            corrected_bcd=_maybe_float(payload.get("corrected_bcd")),
            reasoning=payload.get("reasoning") or "",
            user_label=user_label or "",
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _to_dict(row)


def list_feedback(
    job_id: str | None = None,
    limit: int = 200,
    *,
    user_id: int | None = None,
) -> list[dict]:
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(TrainingFeedback)
        conds = []
        if job_id:
            conds.append(TrainingFeedback.job_id == job_id)
        if settings.auth_enforced:
            if user_id is None:
                return []
            conds.append(TrainingFeedback.user_id == user_id)
        if conds:
            stmt = stmt.where(*conds)
        stmt = stmt.order_by(TrainingFeedback.created_at.desc()).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return [_to_dict(r) for r in rows]


def _maybe_float(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_dict(row: TrainingFeedback) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "job_id": row.job_id,
        "row_idx": row.row_idx,
        "item_description": row.item_description,
        "declared_cth": row.declared_cth,
        "predicted_cth": row.predicted_cth,
        "predicted_bcd": row.predicted_bcd,
        "corrected_cth": row.corrected_cth,
        "corrected_bcd": row.corrected_bcd,
        "reasoning": row.reasoning,
        "user": row.user_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
