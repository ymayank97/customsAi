"""Persistent job lifecycle: SQLAlchemy rows + JSONL results on disk.

Row payloads for large workbooks are appended to ``var/results/{job_id}_results.jsonl``
under a lock so concurrent updates cannot corrupt files.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.db import AuditJob, get_engine

_LOCK = threading.RLock()
RESULTS_DIR = settings.project_root / "var" / "results"


@dataclass
class Job:
    id: str
    filename: str
    status: str = "queued"
    progress: dict = field(default_factory=lambda: {"phase": "queued", "completed": 0, "total": 0})
    rows_total: int = 0
    rows_processed: int = 0
    started_at: float = 0.0
    completed_at: float = 0.0
    error: str = ""
    input_path: str = ""
    output_path: str = ""
    results: list[dict] = field(default_factory=list)
    columns_detected: dict = field(default_factory=dict)
    eta_seconds: float | None = None
    user_id: int | None = None


def _rel_project_path(p: pathlib.Path | str) -> str:
    path = pathlib.Path(p).resolve()
    root = settings.project_root.resolve()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _abs_project_path(rel_or_abs: str) -> str:
    if not rel_or_abs:
        return ""
    p = pathlib.Path(rel_or_abs)
    if p.is_absolute():
        return str(p.resolve())
    return str((settings.project_root / rel_or_abs).resolve())


def _jsonl_path(job_id: str) -> pathlib.Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / f"{job_id}_results.jsonl"


def _load_results_unlocked(job_id: str) -> list[dict]:
    jl = _jsonl_path(job_id)
    if jl.exists():
        by_idx: dict[int, dict] = {}
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ri = r.get("row_idx")
            if isinstance(ri, int):
                by_idx[ri] = r
        return [by_idx[k] for k in sorted(by_idx)]
    return []


def _append_results_jsonl(job_id: str, partial_list: list[dict]) -> None:
    """Append only rows whose row_idx is not yet present (stable checkpoints)."""
    jl = _jsonl_path(job_id)
    seen: set[int] = set()
    if jl.exists():
        for line in jl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if isinstance(r.get("row_idx"), int):
                    seen.add(r["row_idx"])
            except json.JSONDecodeError:
                continue
    to_append = [r for r in partial_list if isinstance(r.get("row_idx"), int) and r["row_idx"] not in seen]
    if not to_append:
        return
    with jl.open("a", encoding="utf-8") as f:
        for r in to_append:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _hydrate(row: AuditJob, include_results: bool = True) -> Job:
    return Job(
        id=row.id,
        filename=row.filename,
        status=row.status or "queued",
        progress=dict(row.progress_data or {"phase": "queued", "completed": 0, "total": 0}),
        rows_total=int(row.rows_total or 0),
        rows_processed=int(row.rows_processed or 0),
        started_at=float(row.started_at or 0.0),
        completed_at=float(row.completed_at or 0.0),
        error=row.error or "",
        input_path=_abs_project_path(row.input_path or ""),
        output_path=_abs_project_path(row.output_path or ""),
        results=_load_results_unlocked(row.id) if include_results else [],
        columns_detected=dict(row.columns_detected or {}),
        eta_seconds=row.eta_seconds,
        user_id=row.user_id,
    )


def create(filename: str, input_path: pathlib.Path, user_id: int | None) -> Job:
    jid = uuid.uuid4().hex[:12]
    now = time.time()
    rel_in = _rel_project_path(input_path)
    engine = get_engine()
    with _LOCK:
        with Session(engine) as session:
            row = AuditJob(
                id=jid,
                user_id=user_id,
                filename=filename,
                status="queued",
                input_path=rel_in,
                started_at=now,
                progress_data={"phase": "queued", "completed": 0, "total": 0},
                columns_detected={},
            )
            session.add(row)
            session.commit()
    got = get(jid, internal=True)
    assert got is not None
    return got


def get(jid: str, user_id: int | None = None, *, internal: bool = False) -> Job | None:
    engine = get_engine()
    with _LOCK:
        with Session(engine) as session:
            row = session.get(AuditJob, jid)
            if row is None:
                return None
            if not internal and user_id is not None and row.user_id != user_id:
                return None
            if not internal and user_id is None and settings.auth_enforced and row.user_id is not None:
                return None
            session.expunge(row)
    return _hydrate(row)


def list_all(user_id: int | None = None) -> list[Job]:
    """Newest jobs first. When ``auth_enforced``, rows are scoped to ``user_id``."""
    engine = get_engine()
    with _LOCK:
        with Session(engine) as session:
            stmt = select(AuditJob).order_by(AuditJob.started_at.desc())
            if settings.auth_enforced:
                stmt = stmt.where(AuditJob.user_id == user_id)
            rows = session.execute(stmt).scalars().all()
            frozen = []
            for r in rows:
                session.expunge(r)
                frozen.append(r)
    return [_hydrate(r, include_results=False) for r in frozen]


def update(jid: str, **fields: Any) -> None:
    engine = get_engine()
    results_payload = fields.pop("results", "__absent__")
    with _LOCK:
        if results_payload != "__absent__" and results_payload is not None:
            _append_results_jsonl(jid, list(results_payload))
        with Session(engine) as session:
            row = session.get(AuditJob, jid)
            if row is None:
                return
            for k, v in fields.items():
                if k == "progress" and v is not None:
                    row.progress_data = dict(v)
                    continue
                if k == "input_path" and v is not None:
                    setattr(row, k, _rel_project_path(str(v)))
                    continue
                if k == "output_path" and v is not None:
                    setattr(row, k, _rel_project_path(str(v)))
                    continue
                if hasattr(row, k):
                    setattr(row, k, v)
            session.commit()
