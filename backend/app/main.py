"""FastAPI entry point for the CTH fraud detection service.

Endpoints:
    POST /api/uploads                  — upload Excel, kick off background processing
    GET  /api/uploads                  — list jobs
    GET  /api/uploads/{job_id}         — job status + progress
    GET  /api/uploads/{job_id}/results — paginated row results
    GET  /api/uploads/{job_id}/download — annotated Excel download
    POST /api/classify/reevaluate      — re-evaluate a single row with stricter LLM params
    POST /api/chat                     — RAG-grounded chat (SSE streaming)
    GET  /api/cth/{cth}                — fetch master row by CTH
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import queue
import re
import threading
import time
import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.app.auth_routes import router as auth_router
from backend.app.config import settings
from backend.app.core.logger import configure as configure_logging, get as get_logger
from backend.app.db import User, fail_stale_running_jobs
from backend.app.deps import get_current_user
from backend.app.jobs import (
    create as create_job,
    get as get_job,
    list_all as list_jobs,
    update as update_job,
)
from backend.app.schemas.api_requests import ChatBody, ReevaluateBody
from backend.app.reviews import router as reviews_router, load_labels
from backend.app.services import excel_processor, pipeline
from backend.app.services.chat_service import stream_chat
from backend.app.services.custada_validator import get_master_row
from backend.app.services.retriever import preload_retrieval_stack
from backend.app.rate_limit import limiter, rate_key_user_or_ip

configure_logging()
log = get_logger("customsai.api")

UPLOAD_DIR = settings.project_root / "var" / "uploads"
RESULTS_DIR = settings.project_root / "var" / "results"

_MODELS_READY = False


def _init_sentry() -> None:
    dsn = settings.sentry_dsn.strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        log.warning("sentry-sdk not installed; SENTRY_DSN ignored")
        return
    sentry_sdk.init(
        dsn=dsn,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=settings.sentry_traces_sample_rate,
        environment=settings.app_env,
    )
    log.info("sentry initialized", extra={"environment": settings.app_env})


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _MODELS_READY

    _init_sentry()

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

    log.info("warming retrieval / ML stack")
    try:
        preload_retrieval_stack()
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
        traceback.print_exc()
        log.exception("job failed", extra={"job_id": job_id})
        update_job(
            job_id,
            status="failed",
            error=f"{type(e).__name__}: {e}",
            completed_at=time.time(),
        )


# ---- routes -----------------------------------------------------------------

app.include_router(auth_router)
app.include_router(reviews_router)


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
        "llm_provider": settings.llm_provider,
        "has_anthropic_key": bool(settings.anthropic_api_key),
        "has_google_key": bool(settings.google_api_key),
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
    page: int = 1,
    page_size: int = 50,
    fraud_only: bool = False,
    current: User | None = Depends(get_current_user),
):
    j = get_job(job_id, user_id=_job_scope_user_id(current))
    if not j:
        raise HTTPException(404, "Unknown job")
    rows = j.results
    if current is not None:
        reviews = {label["row_idx"]: label for label in load_labels(current.id, job_id)}
        rows = [{**row, "review": reviews.get(row["row_idx"])} for row in rows]
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


def _poll_queue_item(q: queue.Queue):
    try:
        return q.get(timeout=0.5)
    except queue.Empty:
        return "__empty__"


@app.post("/api/chat")
@limiter.limit("30/minute", key_func=rate_key_user_or_ip)
async def chat(request: Request, body: ChatBody, _: User | None = Depends(get_current_user)):
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            for line in stream_chat(body.message.strip(), context=body.context, top_k=body.top_k):
                q.put(line)
        except Exception as e:
            log.exception("chat worker failed")
            err_line = f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"
            q.put(err_line)
        q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    last_ping = time.monotonic()

    async def event_gen():
        nonlocal last_ping
        while True:
            if await request.is_disconnected():
                log.info("chat client disconnected")
                return
            item = await asyncio.to_thread(_poll_queue_item, q)
            if item == "__empty__":
                if time.monotonic() - last_ping > 15.0:
                    yield f"event: ping\ndata: {json.dumps({})}\n\n"
                    last_ping = time.monotonic()
                continue
            if item is None:
                break
            yield item

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_gen(), media_type="text/event-stream", headers=headers)


@app.get("/api/cth/{cth}")
def get_cth(cth: str, current: User | None = Depends(get_current_user)):
    if settings.auth_enforced and current is None:
        raise HTTPException(401, "Not authenticated")
    row = get_master_row(cth)
    if not row:
        raise HTTPException(404, f"CTH {cth} not found in master")
    return row
