# CustomsAI project context

The active architecture is CPU-only hierarchical classification: word/character TF-IDF and local logistic regression at each parent of the 2/4/6/8-digit tariff tree. See README.md for data contracts, setup, training, review behavior and checks.

- `backend/app/core/text_cleaning.py`: shared conservative preprocessing. Preserve specifications, negation, material and use context.
- `backend/app/ml/hierarchy.py`: sparse features, local models, three competing paths, unsupported-branch abstention.
- `backend/scripts/train.py`: verified labels only, grouped train/validation/test split, validation threshold, independent evaluation and flat baseline.
- `backend/app/services/classifier.py`: cached trusted master and model loading; version/hash checks.
- `backend/app/services/pipeline.py`: classification, reviewed exclusion checks and separate rate comparison.
- `backend/app/services/legal_rules.py`: optional expert-reviewed exclusions; conflicts require review.
- `backend/app/services/rate_resolver.py`: unconditional master rates only; concessions require review.
- `backend/app/main.py`: uploads, jobs, results, corrections and master lookup.
- `backend/app/scripts/build_enriched_dataset.py`: offline tariff ingestion. Optional builder dependencies are separate from runtime.
- `frontend`: Next.js uploads, paginated results and corrections; no LLM chat.

No verified invoice training dataset is bundled. Never substitute declared codes or schedule prototypes for verified invoice labels or report synthetic-test results as real accuracy. No model training or index building in API startup/requests. Preserve auth, per-user scoping and migration history. Source tariff documents are needed for reproducible builds.

Checks: `python -m pytest backend/tests -q`; in frontend: `npm run typecheck` and `npm run build`.
