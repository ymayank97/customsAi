"""Excel upload, local hierarchical classification, review feedback and audit results."""
from __future__ import annotations

import pathlib
import re
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.app.auth_routes import router as auth_router
from backend.app.config import settings
from backend.app.core.logger import configure as configure_logging, get as get_logger
from backend.app.db import User, fail_stale_running_jobs, list_feedback, save_feedback
from backend.app.deps import get_current_user
from backend.app.job_store import (
    create as create_job,
    get as get_job,
    list_all as list_jobs,
    update as update_job,
)
from backend.app.schemas.api_requests import FeedbackSubmitBody, ReevaluateBody
from backend.app.services import excel_processor, pipeline
from backend.app.services.classifier import get_master, get_master_row, get_model
from backend.app.rate_limit import limiter, rate_key_user_or_ip

configure_logging()
log = get_logger("customsai.api")

UPLOAD_DIR = settings.project_root / "var" / "uploads"
RESULTS_DIR = settings.project_root / "var" / "results"

_MODELS_READY = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _MODELS_READY
    _MODELS_READY = False

    if settings.is_production:
        if not settings.google_oauth_client_id or not settings.jwt_secret or len(settings.jwt_secret) < 32:
            raise RuntimeError(
                "Production requires JWT_SECRET (32+ chars) and GOOGLE_OAUTH_CLIENT_ID"
            )

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if settings.auto_migrate:
        from backend.app.migrations import upgrade_database_head

        upgrade_database_head(reason="AUTO_MIGRATE")

    n = fail_stale_running_jobs()
    if n:
        log.info("marked stale running jobs failed", extra={"count": n})

    log.info("loading local tariff and classifier")
    try:
        get_master()
        get_model()
        from backend.app.services.legal_rules import load_rules
        load_rules()
    except Exception:
        log.exception("model warmup failed — /api/health will report not ready")
    else:
        _MODELS_READY = True

    log.info(
        "startup",
        extra={"phase": "boot", "auth_enforced": settings.auth_enforced, "models_ready": _MODELS_READY},
    )
    log.info("data_dir=%s", settings.data_dir)
    yield


app = FastAPI(
    title="customsAI fraud detection",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


def _safe_upload_filename(name: str) -> str:
    base = pathlib.Path(name).name
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base).strip("._") or "upload.xlsx"
    if not safe.lower().endswith(".xlsx"):
        safe = f"{safe}.xlsx"
    return safe[:180]


def _assert_under_upload_dir(path: pathlib.Path) -> pathlib.Path:
    resolved = path.resolve()
    root = UPLOAD_DIR.resolve()
    if not resolved.is_relative_to(root):
        raise HTTPException(400, "Invalid upload path")
    return resolved


# ---- background runner ------------------------------------------------------


def _process_job(job_id: str) -> None:
    j = get_job(job_id, internal=True)
    if j is None:
        return
    try:
        update_job(job_id, status="running")
        in_path = pathlib.Path(j.input_path)
        df, cols = excel_processor.load_be_excel(in_path)
        rows = excel_processor.normalise_input_rows(df, cols)
        update_job(job_id, rows_total=len(rows), columns_detected=cols)
        log.info("job started", extra={"job_id": job_id, "count": len(rows)})

        def progress_cb(p):
            progress_dict = {k: v for k, v in p.items() if k != "partial_results"}
            updates = {
                "progress": progress_dict,
                "rows_processed": p.get("completed", 0),
                "eta_seconds": p.get("eta_seconds"),
            }
            if "partial_results" in p:
                updates["results"] = p["partial_results"]
            update_job(job_id, **updates)

        results = pipeline.process_rows(rows, progress=progress_cb)
        out_path = RESULTS_DIR / f"{job_id}_annotated.xlsx"
        excel_processor.write_annotated_excel(df, results, out_path)

        update_job(
            job_id,
            status="done",
            results=results,
            output_path=str(out_path),
            completed_at=time.time(),
            progress={"phase": "done", "completed": len(rows), "total": len(rows)},
        )
        log.info("job done", extra={"job_id": job_id, "count": len(rows)})
    except Exception as e:
        log.exception("job failed", extra={"job_id": job_id})
        update_job(
            job_id,
            status="failed",
            error=f"{type(e).__name__}: {e}",
            completed_at=time.time(),
        )


# ---- routes -----------------------------------------------------------------

app.include_router(auth_router)


def _job_scope_user_id(current: User | None) -> int | None:
    return current.id if (settings.auth_enforced and current is not None) else None


@app.middleware("http")
async def enforce_max_upload_body(request: Request, call_next):
    if (
        request.method == "POST"
        and request.scope.get("path") == "/api/uploads"
        and settings.max_upload_bytes > 0
    ):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > settings.max_upload_bytes:
            return JSONResponse(
                {"detail": f"File larger than limit ({settings.max_upload_bytes} bytes)"},
                status_code=413,
            )
    return await call_next(request)


@app.get("/api/health")
def health():
    master = settings.data_dir / "cth_master.parquet"
    body = {
        "status": "ok" if _MODELS_READY else "starting",
        "models_ready": _MODELS_READY,
        "enriched_master": master.exists(),
        "classifier": "tfidf_logistic_hierarchy",
        "model_trained": (settings.models_dir / "hierarchy.joblib").exists(),
        "classification_available": _MODELS_READY and bool(get_master()) and (settings.models_dir / "hierarchy.joblib").exists(),
        "training_source": (get_model().metadata.get("training_source", "verified_invoices")
                            if _MODELS_READY and get_model() is not None else None),
        "auth_enforced": settings.auth_enforced,
    }
    if not _MODELS_READY:
        return JSONResponse(body, status_code=503)
    return body


@app.post("/api/uploads")
@limiter.limit("5/hour", key_func=rate_key_user_or_ip)
async def upload(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile,
    current: User | None = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Only .xlsx uploads are supported")

    safe_name = _safe_upload_filename(file.filename)
    target = UPLOAD_DIR / f"{int(time.time() * 1000)}_{safe_name}"
    target = _assert_under_upload_dir(target)

    nbytes = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                nbytes += len(chunk)
                if nbytes > settings.max_upload_bytes:
                    raise HTTPException(413, "File too large")
                out.write(chunk)
    except HTTPException:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    scope_uid = _job_scope_user_id(current)
    j = create_job(file.filename, target, scope_uid)
    background.add_task(_process_job, j.id)
    log.info("upload received", extra={"job_id": j.id})
    return {"job_id": j.id, "status": j.status}


@app.get("/api/uploads")
def list_uploads(current: User | None = Depends(get_current_user)):
    rows = list_jobs(_job_scope_user_id(current))
    return [
        {
            "id": j.id,
            "filename": j.filename,
            "status": j.status,
            "rows_total": j.rows_total,
            "rows_processed": j.rows_processed,
            "started_at": j.started_at,
            "completed_at": j.completed_at,
        }
        for j in rows
    ]


@app.post("/api/uploads/{job_id}/reclassify")
@limiter.limit("10/hour", key_func=rate_key_user_or_ip)
def reclassify(job_id: str, request: Request, background: BackgroundTasks,
               current: User | None = Depends(get_current_user)):
    original = get_job(job_id, user_id=_job_scope_user_id(current))
    if original is None:
        raise HTTPException(404, "Unknown job")
    if get_model() is None:
        raise HTTPException(409, "Train a model before reclassifying")
    path = _assert_under_upload_dir(pathlib.Path(original.input_path))
    if not path.is_file():
        raise HTTPException(404, "Original workbook is no longer available")
    created = create_job(original.filename, path, _job_scope_user_id(current))
    background.add_task(_process_job, created.id)
    return {"job_id": created.id, "status": created.status}


@app.get("/api/uploads/{job_id}")
def job_status(job_id: str, current: User | None = Depends(get_current_user)):
    j = get_job(job_id, user_id=_job_scope_user_id(current))
    if not j:
        raise HTTPException(404, "Unknown job")
    return {
        "id": j.id,
        "filename": j.filename,
        "status": j.status,
        "progress": j.progress,
        "rows_total": j.rows_total,
        "rows_processed": j.rows_processed,
        "started_at": j.started_at,
        "completed_at": j.completed_at,
        "error": j.error,
        "columns_detected": j.columns_detected,
        "eta_seconds": j.eta_seconds,
    }


@app.get("/api/uploads/{job_id}/results")
def job_results(
    job_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    fraud_only: bool = False,
    current: User | None = Depends(get_current_user),
):
    j = get_job(job_id, user_id=_job_scope_user_id(current))
    if not j:
        raise HTTPException(404, "Unknown job")
    rows = j.results
    summary = {
        "total": len(rows),
        "classified": sum(bool(r["final"]["cth_final"]) for r in rows),
        "suggested": sum(bool(r["final"].get("suggested_cth") or r["final"]["cth_final"]) for r in rows),
        "review": sum(not bool(r["final"]["cth_final"]) for r in rows),
        "findings": sum(bool(r["fraud"]["is_fraud"]) for r in rows),
        "revenue_at_risk": sum(r["fraud"]["revenue_at_risk"] or 0 for r in rows),
    }
    if fraud_only:
        rows = [r for r in rows if r["fraud"]["is_fraud"]]
    total = len(rows)
    start = max(0, (page - 1) * page_size)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "status": j.status,
        "rows_processed": j.rows_processed,
        "rows_total": j.rows_total,
        "rows": rows[start:start + page_size],
        "summary": summary,
    }


@app.get("/api/uploads/{job_id}/download")
def download_annotated(job_id: str, current: User | None = Depends(get_current_user)):
    j = get_job(job_id, user_id=_job_scope_user_id(current))
    if not j or j.status != "done" or not j.output_path:
        raise HTTPException(404, "No annotated file available")
    out = pathlib.Path(j.output_path).resolve()
    if not out.is_file() or not out.is_relative_to(RESULTS_DIR.resolve()):
        raise HTTPException(404, "No annotated file available")
    return FileResponse(
        str(out),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"audit_{j.id}.xlsx",
    )


@app.post("/api/classify/reevaluate")
@limiter.limit("60/hour", key_func=rate_key_user_or_ip)
def reevaluate(request: Request, body: ReevaluateBody, _: User | None = Depends(get_current_user)):
    row = {
        "row_idx": body.row_idx,
        "item_description": body.item_description.strip(),
        "declared_cth": body.declared_cth,
        "declared_bcd": body.declared_bcd,
    }
    top_k = int(body.top_k or settings.top_k)
    out = pipeline.reevaluate_one(row, top_k=top_k)
    desc = body.item_description.strip()
    return {
        "item_description": desc,
        "candidates": out.get("candidates", []),
        "classification": {k: v for k, v in out.items() if k != "candidates"},
    }


@app.get("/api/cth/{cth}")
def get_cth(cth: str, current: User | None = Depends(get_current_user)):
    if settings.auth_enforced and current is None:
        raise HTTPException(401, "Not authenticated")
    row = get_master_row(cth)
    if not row:
        raise HTTPException(404, f"CTH {cth} not found in master")
    return row


@app.post("/api/feedback")
def submit_feedback(body: FeedbackSubmitBody, current: User | None = Depends(get_current_user)):
    if not get_master_row(body.corrected_cth):
        raise HTTPException(422, "Corrected CTH must exist in the tariff master")
    payload = body.model_dump()
    label = (current.name or current.email) if (current is not None) else ""
    uid = current.id if current is not None else None
    row = save_feedback(payload, user_id=uid, user_label=label or "")
    return {"ok": True, "feedback": row}


@app.get("/api/feedback")
def list_feedback_endpoint(
    job_id: str | None = None,
    limit: int = 200,
    current: User | None = Depends(get_current_user),
):
    if settings.auth_enforced and current is None:
        raise HTTPException(401, "Not authenticated")
    return {
        "items": list_feedback(
            job_id=job_id,
            limit=int(limit),
            user_id=current.id if current else None,
        )
    }
