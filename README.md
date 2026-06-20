# customsAI — Indian Customs CTH Fraud Detection

Audit Bills of Entry against the official Indian customs tariff and notifications to surface importers who declared the wrong CTH or under-declared BCD.

> **Production deploy?** See [DEPLOY.md](DEPLOY.md) for the full free-tier (Vercel + Oracle Cloud + Neon + Upstash + Cloudflare) runbook with every manual step.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Next.js 14 frontend (frontend/)                                 │
│   /            landing                                           │
│   /login       Google OAuth (Identity Services credential flow)  │
│   /upload      drag-drop, live progress, pipeline phases         │
│   /jobs        past audits                                       │
│   /results/:id sortable Excel-style table + chat drawer          │
└──────────────────────┬───────────────────────────────────────────┘
                       │ REST + SSE (direct CORS to backend)
┌──────────────────────▼───────────────────────────────────────────┐
│  FastAPI (backend/app/main.py) — JWT-protected                   │
│   /api/auth/google /api/auth/me /api/auth/config                 │
│   /api/health    /api/uploads   /api/uploads/{id}{,/results,…}   │
│   /api/classify/reevaluate    (re-run Stages 1–5)               │
│   /api/chat                    (SSE streaming + heartbeat)       │
│   /api/feedback /api/cth/{cth}                                   │
│   slowapi rate limits, request-ID middleware, Sentry hooks       │
└──────────────────────┬───────────────────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────────────────┐
│  Hierarchical Adversarial Verification pipeline                  │
│  (backend/app/services/pipeline.py — process_rows)               │
│   S0  Enrichment        (retriever.enrich_row; PII never to LLM) │
│   S1  Hierarchical candidate gen (retriever.generate_candidates) │
│        └─ chapter → heading → sub-heading → 8-digit item         │
│        └─ deterministic TF-IDF/BM25 (lexical_index.py) + alias   │
│           index; every candidate is a real cth_master code       │
│   S2  Legal constraint pass (legal_constraints.py)               │
│        └─ Section/Chapter exclusion notes → prune + legal docket │
│   S3  GIR adjudicator   (Claude Opus ▸ Gemini ▸ heuristic)       │
│        └─ walks GIR 1→6; verifies 8-digit leaf vs schedule       │
│        └─ chosen CTH constrained to real candidates + citations  │
│   S4  Validation + precedent (custada_validator hard-block +     │
│        precedent.py CAAR/CESTAT/SC lookup)                       │
│   S5  Calibrated routing (rate_resolver + fraud_detector)        │
│        └─ verifies declared-rate basis (notification/FTA/ITA-1)  │
│        └─ routes by stakes; revenue-at-risk INR                  │
└──────────────────────┬───────────────────────────────────────────┘
                       │ reads
┌──────────────────────▼───────────────────────────────────────────┐
│  Enriched dataset (data/enriched/) — built once                  │
│   cth_master.parquet                12,468 CTHs                  │
│   cth_descriptions.parquet          47,082 description variants  │
│   cth_rate_variants.parquet        142,612 (cth, condition, BCD) │
│   cth_to_notifications.parquet     17,441 cross-references       │
│   notifications.parquet                 39 rate-altering ntfns   │
│   jumbo_50_2017_schedule.parquet       999 schedule rows         │
│   tfidf.pkl + lexical_index.pkl     lexical retrieval indices    │
│   audit_alias_index.parquet         CUSTADA-mined alias index    │
│   fewshot_examples.json             Stage-3 few-shot bank        │
│   adversarial_test_set.parquet      held-out eval set            │
│   data/precedent/rulings.parquet    CAAR/CESTAT/SC corpus        │
│      + precedent_index.pkl                                       │
└──────────────────────────────────────────────────────────────────┘
```

## Local development

### Prerequisites
- Python 3.10+
- Node.js 18+ and npm 9+
- ~2 GB free disk for the enriched dataset and indices

### 1. Backend setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
# Edit backend/.env — at minimum set ANTHROPIC_API_KEY (or leave blank for heuristic mode)
```

Key env vars (full list in `backend/.env.example`):

| Var | Default | Purpose |
|---|---|---|
| `APP_ENV` | `development` | `production` enforces JWT_SECRET + GOOGLE_OAUTH_CLIENT_ID at startup |
| `LLM_PROVIDER` | `claude` | `claude` \| `gemini` |
| `ANTHROPIC_API_KEY` | empty | Without it, pipeline falls back to heuristic top-candidate |
| `GOOGLE_OAUTH_CLIENT_ID` + `JWT_SECRET` | empty | Both set → auth required on all protected routes |
| `JWT_EXPIRE_DAYS` | `1` | Token lifetime |
| `MAX_UPLOAD_BYTES` | `52428800` | 50 MiB upload cap (also enforced byte-by-byte) |
| `DATABASE_URL` | empty (SQLite) | `postgresql+psycopg2://...` for Postgres |
| `REDIS_URL` | empty | For future RQ worker (jobs run in-process today) |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated CORS allowlist |
| `LLM_CONCURRENCY` / `BATCH_SIZE` / `TOP_K` | `4` / `15` / `8` | Pipeline tuning |

### 2. One-time data build (~10 minutes)

```bash
.venv/Scripts/python.exe -m backend.app.scripts.build_enriched_dataset --skip-existing
.venv/Scripts/python.exe -m backend.app.scripts.mine_audit_data          # audit_alias_index.parquet (Stage 1)
.venv/Scripts/python.exe -m backend.app.scripts.build_precedent_corpus    # data/precedent/ rulings + index (Stage 4)
```

### 3. Validation, accuracy eval, regression

```bash
.venv/Scripts/python.exe -m backend.scripts.run_validation
# Accuracy eval — headline metric is invalid-code rate (target 0):
.venv/Scripts/python.exe -m backend.scripts.eval_pipeline --full
# Classification regression suite:
.venv/Scripts/python.exe -m backend.scripts.test_regression_classification
```

> Full Stage-3 Opus accuracy requires `ANTHROPIC_API_KEY`; without it the pipeline runs in heuristic mode.

### 4. Frontend setup

```bash
cd frontend
npm install
cp .env.example .env.local
# default points at http://localhost:8000 — edit if backend is elsewhere
```

### 5. Run

Two terminals:

```bash
# Terminal 1 — backend
.venv/Scripts/python.exe -m uvicorn backend.app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev
```

Open **http://localhost:3000**. Cold start: 60–180 s while the master, lexical index, legal constraints, and precedent corpus load (lifespan preload makes this happen during boot — `/api/health` returns 503 until ready).

### 6. Local Docker prod-parity

```bash
docker compose build
docker compose up -d
docker compose logs -f backend
# wait for "models warm"
curl http://127.0.0.1:8000/api/health
```

Stop: `docker compose down`. Compose binds both services to `127.0.0.1` only.

## App walkthrough

| Route | What you can do |
|---|---|
| `/` | Landing — recent audits, jump to upload |
| `/login` | Google OAuth (when auth is enforced) |
| `/upload` | Drop a `.xlsx` (10 MB cap, magic-byte verified). Watch stage progression: `enrich → candidates → adjudicate → done` with live row counter. Auto-redirects to results. Resume in-flight job via `?job=ID`. |
| `/jobs` | All past + in-progress audits. Polls every 5 s. |
| `/results/:jobId` | Sortable, filterable, fraud-only-toggle table with progressive partial results while jobs run. Click a row → right-side detail drawer with reasoning + top 8 candidates + audit trace stages. Per-row **re-evaluate** re-runs Stages 1–5 (a real LLM call) and patches the row. Per-row or global **Ask AI** opens a side drawer with SSE-streamed answers grounded on retrieved candidates (heartbeats, abortable on close). **Download** exports the annotated `.xlsx`. |

## CLI mode (no server)

```bash
.venv/Scripts/python.exe -m backend.app.cli data/input_data.xlsx \
  --output var/results/audit.xlsx \
  --report-json var/results/report.json
```

## API reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| `GET` | `/api/health` | open | Backend status, model availability |
| `GET` | `/api/auth/config` | open | Returns `auth_required`, `google_client_id` |
| `POST` | `/api/auth/google` | open | Exchange Google credential for JWT |
| `GET` | `/api/auth/me` | required | Current user |
| `POST` | `/api/uploads` | required | Upload `.xlsx` (multipart). Rate-limited 5/hr. |
| `GET` | `/api/uploads` | required | List user's jobs |
| `GET` | `/api/uploads/{id}` | required | Job status + progress |
| `GET` | `/api/uploads/{id}/results` | required | Paginated row results |
| `GET` | `/api/uploads/{id}/download` | required | Annotated Excel download |
| `POST` | `/api/classify/reevaluate` | required | Single-row re-audit (Stages 1–5). Rate-limited 60/hr. |
| `POST` | `/api/chat` | required | SSE streaming chat. Rate-limited 30/min. |
| `POST` | `/api/feedback` | required | Save a corrected CTH (training data) |
| `GET` | `/api/feedback` | required | List user's labeled feedback |
| `GET` | `/api/cth/{cth}` | required | Master row for a specific 8-digit CTH |

## Folder layout

```
customsAI/
├── DEPLOY.md                            production deploy guide (Vercel + Oracle + …)
├── README.md                            this file
├── claude.md                            internal project context / build state
├── docker-compose.yml                   one-command local prod parity
├── data/
│   ├── unenriched custada files/        raw CUSTADA-IGST .xls (gitignored)
│   ├── Tariff/                          First Schedule chapter PDFs (gitignored)
│   ├── notifications/                   .htm + .pdf (gitignored)
│   ├── input_data.xlsx                  sample BE for testing (gitignored)
│   ├── precedent/                       CAAR/CESTAT/SC rulings corpus (Stage 4)
│   │   ├── rulings.parquet
│   │   └── precedent_index.pkl
│   └── enriched/                        ← BUILT BY PIPELINE
│       ├── cth_master.parquet
│       ├── cth_descriptions.parquet
│       ├── cth_rate_variants.parquet
│       ├── tfidf.pkl, lexical_index.pkl
│       ├── audit_alias_index.parquet
│       ├── fewshot_examples.json
│       └── adversarial_test_set.parquet
├── backend/
│   ├── Dockerfile                       multi-stage, non-root, healthcheck
│   ├── requirements.txt                 pinned, prod-only
│   ├── requirements-dev.txt             pdf/ocr extras for scripts
│   ├── alembic.ini + alembic/           Postgres migrations
│   └── app/
│       ├── main.py                      FastAPI entry, lifespan, middleware
│       ├── config.py                    Settings + production guards
│       ├── deps.py                      Auth dependency (JWT + Google OAuth)
│       ├── auth_routes.py / auth_token.py
│       ├── db.py                        SQLAlchemy models + pool config
│       ├── job_store.py                 Job persistence (relative paths, JSONL)
│       ├── core/
│       │   ├── llm_client.py            Claude Opus/Gemini GIR adjudicator (httpx timeouts + retry)
│       │   ├── text_cleaning.py         abbreviations + Porter stemming
│       │   └── logger.py                structured JSON / TTY logger
│       ├── services/
│       │   ├── pipeline.py              process_rows — 6-stage orchestrator (parallel batches)
│       │   ├── retriever.py             Stage 0 enrich_row + Stage 1 generate_candidates
│       │   ├── lexical_index.py         deterministic TF-IDF/BM25 over tariff text
│       │   ├── legal_constraints.py     Stage 2 Section/Chapter exclusion rules + legal docket
│       │   ├── custada_validator.py     Stage 4 hard-block invalid CTH
│       │   ├── precedent.py             Stage 4 CAAR/CESTAT/SC ruling lookup
│       │   ├── rate_resolver.py         cth_rate_variants + condition matching
│       │   ├── fraud_detector.py        fraud flag logic + revenue at risk
│       │   ├── chat_service.py          SSE chat (heartbeat + disconnect detection)
│       │   └── excel_processor.py       read_only, magic-byte, row caps, cell trim
│       ├── schemas/                     Pydantic request/response models
│       └── scripts/                     dataset build + mine_audit_data + build_precedent_corpus
└── frontend/
    ├── Dockerfile                       multi-stage standalone, non-root
    ├── next.config.mjs                  CSP, HSTS, security headers, standalone
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx                 /
        │   ├── login/page.tsx           /login
        │   ├── upload/page.tsx          /upload
        │   ├── jobs/page.tsx            /jobs
        │   └── results/[jobId]/page.tsx
        ├── components/
        │   ├── TopBar.tsx               sticky bar with health pill
        │   ├── ResultsTable.tsx         TanStack Table, sortable, paginated
        │   ├── ChatDrawer.tsx           SSE streaming chat (AbortController)
        │   ├── CorrectionDialog.tsx
        │   └── StatCard.tsx
        ├── lib/
        │   ├── api.ts                   typed client, central 401 handler
        │   ├── auth-storage.ts          sessionStorage JWT
        │   └── utils.ts
        └── providers/AppProviders.tsx   Auth context, Google OAuth provider
```

## Production hardening status

| Area | Status |
|---|---|
| Auth (Google OAuth + JWT, fail-closed in prod) | ✅ |
| CORS allowlist + scoped methods/headers | ✅ |
| File upload (filename sanitisation, magic bytes, size cap, row cap) | ✅ |
| Per-tenant job scoping | ✅ |
| Pydantic-validated request bodies | ✅ |
| Rate limiting (slowapi) | ✅ |
| SSE heartbeat + disconnect detection | ✅ |
| LLM timeouts + Retry-After-aware retries | ✅ |
| Model preload in lifespan (no cold-start request blockage) | ✅ |
| Structured logging + request ID | ✅ |
| Stale-job recovery on restart | ✅ |
| Sentry integration | ✅ |
| Postgres pool + Alembic migrations | ✅ |
| Excel cell truncation + PII strip before LLM | ✅ |
| Frontend security headers (CSP, HSTS, X-Frame-Options) | ✅ |
| AbortController on SSE streams | ✅ |
| Multi-stage Dockerfiles, non-root | ✅ |
| RQ worker (replace BackgroundTasks) | ⏳ Phase F |
| R2 object storage (replace VM disk for uploads) | ⏳ Phase F |
| Per-user LLM spend cap | ⏳ Phase F |
| `/api/me/delete` for DPDP / GDPR | ⏳ Phase F |

## Troubleshooting

**`/api/health` returns 503** — models still loading. First boot takes 60–180 s. Watch logs for `models warm`.

**Backend log says `[llm] No API keys set; using HeuristicClassifier`** — `ANTHROPIC_API_KEY` not set. Pipeline still runs end-to-end on heuristic top-candidate; quality drops.

**`enriched_master: false` in `/api/health`** — run the data build (step 2 above).

**Frontend "Backend offline"** — `NEXT_PUBLIC_BACKEND_URL` doesn't reach the backend. Check CORS (`ALLOWED_ORIGINS`) matches the frontend origin exactly.

**Job stuck at 0%** — candidate generation hit a lexical-index error; check backend logs. With `LLM_CONCURRENCY=4` the first batch can take 30–60 s before any progress.

**Production won't start with `RuntimeError: production requires …`** — `APP_ENV=production` and either `JWT_SECRET` is < 32 chars or `GOOGLE_OAUTH_CLIENT_ID` is unset. Generate a secret with `openssl rand -hex 32`.

**`401 Session expired` looping** — JWT expired (default 1 day). Sign in again. To extend, raise `JWT_EXPIRE_DAYS`.
