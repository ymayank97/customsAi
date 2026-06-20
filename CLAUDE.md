# CustomsAI — Project Context for Claude

> Last updated: 2026-06-15 — production-ready build, awaiting Phase B/C/D manual deployment.

## Business problem

Indian customs officers audit Bills of Entry (12K+ rows) to find importers who declared wrong CTH codes or BCD rates to underpay duty. Manual review is impossible at scale. CustomsAI is a SaaS that ingests an Excel BE, audits each row against the official tariff with a six-stage **Hierarchical Adversarial Verification** pipeline, and surfaces fraud-flagged rows ranked by revenue at risk.

**Sensitive data**: BE rows contain importer names, IECs, BE numbers, and assessable values. PII is stripped from LLM prompts (only `item_description`, `declared_cth`, `declared_bcd` reach the model).

## Current build state (what's actually shipped)

### Backend — production-hardened
- **FastAPI** at [backend/app/main.py](backend/app/main.py), JWT-protected, lifespan preloads master + lexical index (TF-IDF/BM25) + legal constraints + precedent corpus (no cold-start request blockage; `/api/health` returns 503 until ready).
- **Auth**: Google Identity Services credential flow + HS256 JWT (24 h default). Production refuses to boot if `JWT_SECRET < 32 chars` or `GOOGLE_OAUTH_CLIENT_ID` unset ([config.py:88-91](backend/app/config.py#L88), [main.py:81-91](backend/app/main.py#L81)).
- **Hardened upload**: filename sanitisation, `PK\x03\x04` magic-byte check, byte-counting size enforcement, openpyxl `read_only=True`, 50K row / 5 sheet caps, 32K cell-trunc on Reasoning + audit comment ([excel_processor.py:23-105](backend/app/services/excel_processor.py)).
- **Pipeline — Hierarchical Adversarial Verification** ([backend/app/services/pipeline.py](backend/app/services/pipeline.py) `process_rows`): reframes BE auditing as adversarial verification (given a declared CTH + description, find the strongest legally-supported alternative and compute the duty differential) rather than 12,000-way classification. Six stages:
  - **Stage 0 Enrichment** ([retriever.py](backend/app/services/retriever.py) `enrich_row`): per-row context; PII (importer/IEC/BE-no/assess-val) loaded for the report but **never** sent to the LLM; BE-level sibling lines attached.
  - **Stage 1 Hierarchical candidate generation** ([retriever.py](backend/app/services/retriever.py) `generate_candidates`): narrows the actual tariff tree chapter → heading → sub-heading → 8-digit item using deterministic lexical retrieval (TF-IDF/BM25 over tariff text via [lexical_index.py](backend/app/services/lexical_index.py)) + an audit alias index mined from past CUSTADA filings. Recall-oriented, deliberately cross-chapter. **Invariant**: every candidate is a real CTH in `cth_master`, so an invented/non-existent code can never be surfaced.
  - **Stage 2 Legal constraint pass** ([legal_constraints.py](backend/app/services/legal_constraints.py)): pulls Section/Chapter exclusion notes; encodes hard exclusions (e.g. Section XVII Note 2 excludes Chapter-85 electrical goods from vehicle "parts" headings like 8708) as machine-checkable rules; prunes violating candidates and attaches a legal docket.
  - **Stage 3 GIR adjudicator** ([llm_client.py](backend/app/core/llm_client.py)): Claude Opus (`claude-opus-4-8`; Gemini fallback; heuristic top-candidate when no API key) walks the GIR 1→6 sequence against heading terms + the legal docket, verifies the 8-digit leaf text against the actual schedule, and emits a chosen CTH (constrained to the real candidates), a conformal candidate set, and pinpoint note citations.
  - **Stage 4 Validation + precedent** ([custada_validator.py](backend/app/services/custada_validator.py) hard-block; [precedent.py](backend/app/services/precedent.py)): the validator **hard-blocks** any CTH not in the master (substitutes the best valid candidate or routes to a manual verification queue — never emits an invalid code); then looks up CAAR/CESTAT/SC rulings on the contested pair from a starter precedent corpus (`data/precedent/`).
  - **Stage 5 Calibrated routing** ([rate_resolver.py](backend/app/services/rate_resolver.py) + [fraud_detector.py](backend/app/services/fraud_detector.py)): before asserting any duty delta it verifies the basis of the declared rate — a declared 0%/concessional rate backed by a matched notification / India-Japan CEPA-style FTA / ITA-1 passive-component bound rate does **not** raise a revenue finding; routes by stakes (revenue-neutral reclassifications never reach the queue; ambiguous high-differential rows always do). Emits CTH Mismatch, BCD Under/Over-declared, Notification misuse + revenue-at-risk INR.
- **Rate limiting** via `slowapi`: uploads 5/hr, chat 30/min, reevaluate 60/hr, Google login 10/min.
- **SSE chat** ([chat_service.py](backend/app/services/chat_service.py)): heartbeat every 15 s, `await request.is_disconnected()` cancels generator and stops billing on tab close.
- **Job persistence** ([job_store.py](backend/app/job_store.py)): SQLAlchemy + SQLite (default) or Postgres via `DATABASE_URL`. Relative paths only (Windows-dev / Linux-prod safe). Append-only JSONL results checkpointing.
- **Pool config** ([db.py:143-149](backend/app/db.py)): `pool_size=2, max_overflow=3, pool_pre_ping=True, pool_recycle=300` — Neon scale-to-zero friendly.
- **Stale-job recovery** in lifespan: any `running` jobs at startup → `failed: interrupted by restart`.
- **Alembic** migrations ([backend/alembic/](backend/alembic/)). Auto-run on startup if `AUTO_MIGRATE=1` (Postgres).
- **Sentry** integration when `SENTRY_DSN` set.
- **Request-ID middleware** + structured JSON logs ([core/logger.py](backend/app/core/logger.py)).

### Frontend — production-hardened
- **Next.js 14 App Router** ([frontend/src/app/](frontend/src/app/)). Multi-stage Dockerfile with `output: "standalone"`, non-root.
- **Direct CORS calls** to `${NEXT_PUBLIC_BACKEND_URL}/api/...` (no Next.js rewrite — Vercel-friendly).
- **Auth**: Google credential flow → backend `/api/auth/google` → JWT in `sessionStorage` (1-day lifetime).
- **Central 401 handler** ([api.ts:29-34](frontend/src/lib/api.ts#L29)) → clears token + redirects to `/login`.
- **AbortController** on SSE chat ([api.ts:385-432](frontend/src/lib/api.ts#L385), [ChatDrawer.tsx:31-44](frontend/src/components/ChatDrawer.tsx#L31)).
- **Polling** in [results/[jobId]/page.tsx:55-64](frontend/src/app/results/[jobId]/page.tsx#L55) — only status during processing, full results once on `done`.
- **Security headers** ([next.config.mjs:5-27](frontend/next.config.mjs#L5)): CSP, HSTS, X-Frame-Options=DENY, X-Content-Type-Options=nosniff, Referrer-Policy.
- **File-size validation** client-side at 10 MB ([upload/page.tsx:131-148](frontend/src/app/upload/page.tsx#L131)).
- **Inline error UI** for login failures (no `alert()`).

### Infra
- [docker-compose.yml](docker-compose.yml) binds both services to `127.0.0.1` only — production reverse-proxies via nginx + Cloudflare.
- [.gitignore](.gitignore) and [.dockerignore](.dockerignore) cover `.env`, `var/`, `data/input_data.xlsx`, `*.db`, `.next/`, `node_modules/`, `__pycache__/`, etc.
- Backend Dockerfile multi-stage, non-root (`appuser`), HEALTHCHECK on `/api/health`.

## Enriched dataset (`data/enriched/`)

Built once via `python -m backend.app.scripts.build_enriched_dataset --skip-existing` (~10 min):

| File | Rows | Purpose |
|---|---|---|
| `cth_master.parquet` | 12,468 | Canonical 1-row-per-CTH with statutory + effective BCD, IGST, SWS, policies |
| `cth_descriptions.parquet` | 47,082 | Description variants for lexical retrieval |
| `cth_rate_variants.parquet` | 142,612 | (cth, condition, BCD, notification_id) — drives Rate Resolver |
| `cth_to_notifications.parquet` | 17,441 | Cross-references CTH ↔ applicable notifications |
| `notifications.parquet` | 39 | Rate-altering notification metadata |
| `jumbo_50_2017_schedule.parquet` | 999 | Schedule rows for the master concessional notification |
| `tfidf.pkl` + `lexical_index.pkl` | — | Deterministic lexical (TF-IDF/BM25) retrieval indices |
| `audit_alias_index.parquet` | — | Alias/exemplar index mined from past CUSTADA filings (Stage 1 recall booster; declared = weak signal, not ground truth) |
| `fewshot_examples.json` | — | Chapter-diverse few-shot bank priming the Stage 3 adjudicator |
| `adversarial_test_set.parquet` | — | Held-out eval set (declared values are a weak reference, not officer-verified gold) |
| `data/precedent/rulings.parquet` + `precedent_index.pkl` | — | Starter CAAR/CESTAT/SC classification rulings corpus (under `data/precedent/`) |

## Configuration surface

All env vars in [backend/.env.example](backend/.env.example). Critical ones:

| Var | Default | Required for prod? |
|---|---|---|
| `APP_ENV` | `development` | **Set to `production`** to enable startup guards |
| `JWT_SECRET` | empty | **Yes** (≥ 32 chars) |
| `GOOGLE_OAUTH_CLIENT_ID` | empty | **Yes** |
| `ANTHROPIC_API_KEY` | empty | **Yes** (or accept heuristic mode) |
| `DATABASE_URL` | empty (SQLite) | **Yes for prod** (Neon Postgres) |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | **Yes** (frontend domain) |
| `REDIS_URL` | empty | Optional (for future RQ worker) |
| `SENTRY_DSN` | empty | Optional |
| `R2_*` (4 vars) | empty | Optional (Phase F — object storage) |

## Deployment plan (current)

See [DEPLOY.md](DEPLOY.md) for the full step-by-step. Free-tier stack:

- **Frontend**: Vercel Hobby — `https://app.example.com`
- **Backend**: Oracle Cloud Always Free Ampere A1 (4 OCPU / 24 GB) — `https://api.example.com` via nginx + Let's Encrypt + Cloudflare proxy
- **Database**: Neon Postgres (pooled URL with `sslmode=require`)
- **Queue/cache**: Upstash Redis (currently unused — reserved for Phase F worker)
- **Object storage**: Cloudflare R2 (currently unused — VM disk for v1)
- **DNS/TLS/WAF**: Cloudflare Free
- **Auth**: Google OAuth Web client
- **LLM**: Anthropic API (paid, capped at $50/mo)
- **Errors**: Sentry (optional)
- **Uptime**: UptimeRobot 5-min ping on `/api/health`

Steady-state RAM: ~2 GB. Peak (12K-row job): ~3–4 GB. Fits Ampere A1 with margin.

### Phases
- **Phase A (code hardening)** — DONE, 16/16 critical fixes shipped.
- **Phase B (provision managed services)** — manual, ~30 min, see DEPLOY.md §2.
- **Phase C (deploy backend to Oracle)** — manual, ~45 min, see DEPLOY.md §3.
- **Phase D (deploy frontend to Vercel)** — manual, ~10 min, see DEPLOY.md §4.
- **Phase E (go-live verification)** — checklist, see DEPLOY.md §5.
- **Phase F (post-launch hardening)** — deferred to first pilot week:
  - Replace `BackgroundTasks` with RQ + dedicated worker (uses `REDIS_URL`).
  - Move Excel storage VM disk → R2 (uses `R2_*`).
  - `/api/me/delete` for DPDP / GDPR.
  - Per-user daily LLM spend cap.
  - 12K-row table virtualization (current pagination at 50/page is fine for v1).

## Working with this codebase

### Run validation locally before any deploy
```bash
.venv/Scripts/python.exe -m backend.scripts.run_validation
```

### Local Docker prod-parity
```bash
docker compose build && docker compose up -d
docker compose logs -f backend
```

### Migrate Postgres
```bash
docker compose run --rm backend python -m alembic -c backend/alembic.ini upgrade head
```

### Rebuild enriched dataset
```bash
docker compose run --rm backend python -m backend.app.scripts.build_enriched_dataset --skip-existing
```

### Build audit + precedent assets
```bash
.venv/Scripts/python.exe -m backend.app.scripts.mine_audit_data          # audit_alias_index.parquet
.venv/Scripts/python.exe -m backend.app.scripts.build_precedent_corpus    # data/precedent/ rulings + index
```

### Accuracy eval + regression
```bash
.venv/Scripts/python.exe -m backend.scripts.eval_pipeline --full          # headline metric: invalid-code rate (target 0)
.venv/Scripts/python.exe -m backend.scripts.test_regression_classification
```
> Full Stage-3 Opus accuracy requires `ANTHROPIC_API_KEY`; without it the pipeline runs in heuristic mode.

## Important behavioural notes

1. **PII stays out of LLM prompts.** Pipeline only sends `item_description`, `declared_cth`, `declared_bcd`, `declared_notification`, and retrieved candidates to Claude/Gemini. Importer name, IEC, BE No, and assessable value are loaded for the audit report in Stage 0 ([retriever.py](backend/app/services/retriever.py) `enrich_row`) but never serialised to the LLM.

2. **Production fail-closed.** Missing `JWT_SECRET` or `GOOGLE_OAUTH_CLIENT_ID` aborts startup when `APP_ENV=production`. This is intentional — do not weaken.

3. **Single-process worker.** Pipeline runs in-process via FastAPI `BackgroundTasks`. On restart, in-flight jobs are marked `failed`. Phase F migrates to RQ for resumable jobs.

4. **First-row latency 60-180 s on cold start.** Lifespan preload tries to absorb this — `/api/health` returns 503 until models warm. UptimeRobot ping every 5 min keeps the process hot.

5. **Validity is hard-blocked, not trusted.** Every Stage-1 candidate is a real `cth_master` code and Stage 4 hard-blocks anything else, so the headline `eval_pipeline` metric (invalid-code rate) targets 0. Re-run `backend.scripts.eval_pipeline --full` after any change to candidate generation, the legal constraints, or the adjudicator prompt.

## When working with this repo, please

- Default to no comments in code (project convention).
- Reference files with markdown links: `[file.py:42](path/to/file.py#L42)`.
- Don't add backwards-compat shims — this is greenfield, prune freely.
- Don't reintroduce `BackgroundTasks → RQ` or `VM disk → R2` work as one-shot edits; they're substantial Phase F refactors.
- Don't remove the production startup guards in [main.py lifespan](backend/app/main.py) or [config.py](backend/app/config.py). They are deliberate.
- Don't expose port 8000 in `docker-compose.yml` — production uses nginx-only ingress.

## Doc map

| File | Purpose |
|---|---|
| [README.md](README.md) | Local dev quickstart + API reference + folder layout |
| [DEPLOY.md](DEPLOY.md) | Production deployment guide (free-tier stack, manual steps) |
| [claude.md](claude.md) | Project context for AI assistants (this file) |
| [backend/.env.example](backend/.env.example) | Backend env reference |
| [frontend/.env.example](frontend/.env.example) | Frontend env reference |
