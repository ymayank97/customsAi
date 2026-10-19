from __future__ import annotations

import csv
import io
import unicodedata
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backend.app.db import TrainingLabel, User, get_engine
from backend.app.deps import require_premium_user
from backend.app.jobs import get as get_job
from backend.app.membership import capabilities
from backend.app.schemas.api_requests import ReviewSubmitBody
from backend.app.services.custada_validator import get_master_row

router = APIRouter(prefix="/api/reviews", tags=["officer reviews"])


def label_dict(label: TrainingLabel) -> dict:
    result = {column.name: getattr(label, column.name) for column in TrainingLabel.__table__.columns}
    for key in ("created_at", "updated_at"):
        result[key] = result[key].replace(tzinfo=timezone.utc).isoformat()
    return result


def load_labels(user_id: int | None, job_id: str | None = None) -> list[dict]:
    with Session(get_engine()) as session:
        stmt = select(TrainingLabel).order_by(TrainingLabel.updated_at.desc(), TrainingLabel.id.desc())
        if user_id is not None:
            stmt = stmt.where(TrainingLabel.user_id == user_id)
        if job_id is not None:
            stmt = stmt.where(TrainingLabel.job_id == job_id)
        return [label_dict(label) for label in session.scalars(stmt)]


def training_description(label: dict) -> str:
    raw = label["item_description"].strip()
    details = (label.get("product_details") or "").strip()
    return f"{raw}\nProduct details: {details}" if details else raw


def description_key(label: dict) -> str:
    return " ".join(unicodedata.normalize("NFKC", training_description(label)).casefold().split())


def assess_labels(labels: list[dict]) -> tuple[list[dict], list[dict]]:
    codes: dict[str, set[str]] = {}
    for label in labels:
        if label["decision"] != "needs_information" and label["corrected_cth"]:
            codes.setdefault(description_key(label), set()).add(label["corrected_cth"])
    assessed, ready, seen = [], [], set()
    for label in labels:
        key = description_key(label)
        quality = "needs_information" if label["decision"] == "needs_information" else (
            "conflict" if len(codes.get(key, set())) > 1 else "ready"
        )
        assessed.append({**label, "quality": quality})
        if quality == "ready" and key not in seen:
            ready.append(label)
            seen.add(key)
    return assessed, ready


@router.post("")
def save_review(body: ReviewSubmitBody, current: User = Depends(require_premium_user)):
    job = get_job(body.job_id, user_id=current.id)
    if job is None:
        raise HTTPException(404, "Audit not found.")
    if job.status != "done":
        raise HTTPException(409, "Wait for this audit to finish before reviewing an item.")
    source = next((row for row in job.results if row.get("row_idx") == body.row_idx), None)
    if source is None:
        raise HTTPException(404, "Item not found in this audit.")
    if not (source.get("item_description") or "").strip():
        raise HTTPException(422, "This item has no description to use for training.")
    predicted = source.get("final") or {}
    code = body.corrected_cth
    if body.decision == "needs_information":
        code = None
    else:
        if not body.reference:
            raise HTTPException(422, "Add the document or tariff reference used to verify this code.")
        if body.decision == "confirmed":
            code = str(predicted.get("cth_final") or "")
            if body.corrected_cth and body.corrected_cth != code:
                raise HTTPException(422, "Choose 'Correct the code' to record a different CTH.")
        if not code or len(code) != 8 or not code.isascii() or not code.isdigit() or not get_master_row(code):
            raise HTTPException(422, "Choose an eight-digit CTH that exists in the tariff master.")
        if body.decision == "corrected" and code == predicted.get("cth_final"):
            raise HTTPException(422, "Use 'Confirm the suggestion' when the code is unchanged.")
    now = datetime.now(timezone.utc)
    values = {
        "user_id": current.id, "job_id": job.id, "row_idx": body.row_idx,
        "item_description": source["item_description"], "declared_cth": source.get("declared_cth") or "",
        "predicted_cth": predicted.get("cth_final") or "", "predicted_bcd": predicted.get("bcd_final"),
        "decision": body.decision, "corrected_cth": code,
        "corrected_bcd": body.corrected_bcd if code else None,
        "reference": body.reference, "reasoning": body.reasoning, "product_details": body.product_details,
        "reviewer_name": current.name or current.email, "created_at": now, "updated_at": now,
    }
    engine = get_engine()
    insert = sqlite_insert if engine.dialect.name == "sqlite" else pg_insert
    stmt = insert(TrainingLabel).values(**values)
    editable = ("decision", "corrected_cth", "corrected_bcd", "reference", "reasoning", "product_details", "reviewer_name", "updated_at")
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "job_id", "row_idx"],
        set_={key: values[key] for key in editable},
    )
    with Session(engine) as session:
        session.execute(stmt)
        session.commit()
        label = session.scalar(select(TrainingLabel).where(
            TrainingLabel.user_id == current.id, TrainingLabel.job_id == job.id, TrainingLabel.row_idx == body.row_idx,
        ))
        return {"ok": True, "review": label_dict(label)}


def scoped_labels(current: User, job_id: str | None) -> list[dict]:
    return load_labels(None if capabilities(current)["is_admin"] else current.id, job_id)


@router.get("")
def reviewed_items(
    job_id: str | None = None,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    current: User = Depends(require_premium_user),
):
    labels, ready = assess_labels(scoped_labels(current, job_id))
    summary = {
        "reviewed": len(labels), "training_examples": len(ready),
        "needs_information": sum(label["quality"] == "needs_information" for label in labels),
        "conflicts": sum(label["quality"] == "conflict" for label in labels),
    }
    if q.strip():
        query = q.strip().casefold()
        labels = [label for label in labels if query in " ".join(str(label.get(key) or "") for key in (
            "item_description", "corrected_cth", "reference", "reviewer_name", "product_details",
        )).casefold()]
    start = (page - 1) * page_size
    return {"items": labels[start:start + page_size], "total": len(labels), "summary": summary,
            "scope": "all_reviewers" if capabilities(current)["is_admin"] else "my_reviews"}


@router.get("/export")
def export_training_labels(current: User = Depends(require_premium_user)):
    _, ready = assess_labels(scoped_labels(current, None))
    buffer = io.StringIO(newline="")
    fields = ["item_description", "gold_cth", "label_source", "raw_description", "product_details",
              "declared_cth", "predicted_cth", "corrected_bcd", "reasoning", "reviewer_id", "review_id", "job_id", "row_idx", "updated_at"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for label in ready:
        writer.writerow({
            **{key: label[key] for key in ("product_details", "declared_cth", "predicted_cth", "corrected_bcd", "reasoning", "job_id", "row_idx", "updated_at")},
            "item_description": training_description(label), "gold_cth": label["corrected_cth"],
            "label_source": f"Officer review {label['id']}: {label['reference']}",
            "raw_description": label["item_description"], "reviewer_id": label["user_id"], "review_id": label["id"],
        })
    return Response(buffer.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="reviewed_training_labels.csv"', "Cache-Control": "no-store",
    })
