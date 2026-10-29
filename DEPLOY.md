# Deployment

The API runs on CPU. No AI provider keys or model downloads are required.

1. Build the tariff master offline using `backend/requirements-data.txt` and `python -m backend.app.scripts.build_enriched_dataset`.
2. Train a reviewed dataset using `python -m backend.scripts.train reviewed.csv --tariff-version YOUR_VERSION`.
3. Inspect `data/enriched/models/hierarchy.report.json`, including test accuracy and coverage, before enabling operational use.
4. Copy `backend/.env.example` to `backend/.env`. Set `APP_ENV=production`, `GOOGLE_OAUTH_CLIENT_ID`, and a random `JWT_SECRET` of at least 32 characters. Set `ALLOWED_ORIGINS` to the frontend origin.
5. Run `docker compose up --build -d`. The existing Compose file mounts `data` read-only and persists `var` for uploads, job results and SQLite. Both services bind to localhost; expose them through your HTTPS proxy.

The frontend API origin is a build-time setting, `NEXT_PUBLIC_BACKEND_URL`; update the Compose build argument before building for another host. Google OAuth authorized origins must include the frontend's origin.

SQLite is the default. For Postgres, install `backend/requirements-postgres.txt` in the API image, set `DATABASE_URL`, and run `python -m alembic -c backend/alembic.ini upgrade head` before starting, or set `AUTO_MIGRATE=1`.

`GET /api/health` reports API artifact-loading readiness and a separate `classification_available` flag. Missing training artifacts allow manual-review uploads; incompatible artifacts fail readiness. Replace trusted model/master files together and restart the API after a training release. Model serialization is executable Python data: never load artifacts from upload requests or untrusted sources.

Back up `var`, the reviewed training CSV, the master, model, evaluation report and any reviewed `rules.json` together. Feedback is queued for explicit review and offline retraining. No external inference service, Redis, object store, vector database or GPU is required.
