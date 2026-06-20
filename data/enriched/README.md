# Enriched CTH Dataset

This folder is the canonical, reproducible output of the CTH enrichment pipeline.
It merges three primary data sources into a single training-and-retrieval-ready corpus:

- **CUSTADA-IGST 2025-26 .xls files** — structured 8-digit CTH master with effective rates.
- **Tariff first-schedule chapter PDFs** (`data/Tariff/`, 96 PDFs) — statutory descriptions, chapter notes, base BCD.
- **Rate-altering customs notifications** (HTM + PDF) — Customs / IGST / CompCess / CBEC, plus the **Jumbo Notification 50/2017** schedule + Annexure conditions.

## Files

| file | description |
|---|---|
| `cth_master.parquet` / `.csv` | One row per 8-digit CTH — the canonical record. |
| `cth_descriptions.parquet` | Long format: multiple description variants per CTH (BERT-ready). |
| `cth_rate_variants.parquet` | Long format: one row per (CTH, condition, applicable BCD). |
| `cth_to_notifications.parquet` | Many-to-many (cth, ntfn_id, match_type, snippet). |
| `tariff_chapters.parquet` | Per-chapter title + chapter notes + raw text from Tariff PDFs. |
| `notifications.parquet` | Parsed rate-altering notifications with metadata. |
| `notification_chunks.parquet` | ~1500-char chunks of notification text for RAG. |
| `jumbo_50_2017_schedule.parquet` | Parsed Jumbo schedule rows (S.No, CTH/prefix, description, BCD, condition). |
| `jumbo_50_2017_conditions.parquet` | Annexure conditions keyed by condition number. |
| `tfidf.pkl` | TF-IDF vectorizer + sparse matrix over enriched descriptions (12,468 rows). |
| `build_report.json` | Row counts, hashes, library versions, per-stage stats. |
| `excel/*.xlsx` | Human-readable mirrors of the parquets above. |

## Schema highlights

`cth_master` (canonical CTH record):

- `cth` (str, 8-digit zero-padded) — primary key
- `description_statutory` — from Tariff PDF (gold)
- `description_custada` — from CUSTADA xls
- `description_combined` — dedupe-concat with ` | ` separator
- `chapter_title`, `chapter_notes`, `section_notes_chapter` — chapter-level legal context
- `bcd_statutory` (float %) — First Schedule rate
- `bcd_effective` (float %) — post-notification rate from CUSTADA
- `bcd_notifications_applied` (list[str]) — ntfn_ids that drove the diff
- `applicable_notifications` (list[str]) — every ntfn_id mentioning this CTH
- `igst_pct`, `sws_pct` — numeric rates
- `import_policy`, `export_policy`, `unit`
- `notes_text` — accumulated CUSTADA Notes rows
- `provenance` (JSON) — per-field source map for audit

`cth_descriptions` (BERT training input):

- `cth`, `variant_type`, `text`, `source_doc`, `char_len`, `text_norm`
- `variant_type` ∈ {statutory_full, statutory_subheading, custada_short, chapter_title, notes_excerpt, jumbo_description}

`cth_rate_variants` (conditional rate combinations):

- `cth`, `bcd_rate`, `bcd_rate_text`, `condition_code`, `condition_text`
- `notification_id`, `effective_from`, `effective_to`, `is_default`
- For most CTHs: 1 row (statutory). For Jumbo-listed CTHs: multiple rows with each conditional rate variant.

## Reproducing this build

```
python -m backend.app.scripts.build_enriched_dataset
```

All stage stats and SHA-256 hashes of every output file are in `build_report.json`.
