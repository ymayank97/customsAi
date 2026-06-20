"""Stage 11: build_report.json, README.md, human-readable Excel exports.

Excel exports (in data/enriched/excel/):
    cth_master.xlsx              — canonical record per CTH
    cth_master_fraud_view.xlsx   — slimmer view focused on rate enforcement
    cth_rate_variants.xlsx       — every (CTH, condition, rate) row
    cth_descriptions.xlsx        — multi-variant descriptions per CTH
    notifications.xlsx           — parsed notifications
    jumbo_50_2017.xlsx           — Jumbo schedule + conditions (two sheets)
    cth_to_notifications.xlsx    — cross-reference table
    SUMMARY.xlsx                 — single workbook with one sheet per artefact (top 5000 rows each)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import pathlib

import pandas as pd

ENRICHED = pathlib.Path("data/enriched")
EXCEL_DIR = ENRICHED / "excel"


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _list_lib_versions() -> dict:
    import pandas as _p
    import pyarrow as _pa
    import sklearn as _sk
    try:
        import pdfplumber as _pp
        pp_v = _pp.__version__
    except Exception:
        pp_v = None
    return {
        "python": _build_python_version(),
        "pandas": _p.__version__,
        "pyarrow": _pa.__version__,
        "scikit-learn": _sk.__version__,
        "pdfplumber": pp_v,
    }


def _build_python_version() -> str:
    import sys
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def build_report(out_dir: pathlib.Path) -> dict:
    custada = pd.read_parquet(out_dir / "_custada_rows.parquet")
    tariff_items = pd.read_parquet(out_dir / "_tariff_items.parquet")
    chapters = pd.read_parquet(out_dir / "tariff_chapters.parquet")
    ntfns = pd.read_parquet(out_dir / "notifications.parquet")
    jumbo_sched = pd.read_parquet(out_dir / "jumbo_50_2017_schedule.parquet")
    jumbo_cond = pd.read_parquet(out_dir / "jumbo_50_2017_conditions.parquet")
    xref = pd.read_parquet(out_dir / "cth_to_notifications.parquet")
    master = pd.read_parquet(out_dir / "cth_master.parquet")
    descs = pd.read_parquet(out_dir / "cth_descriptions.parquet")
    variants = pd.read_parquet(out_dir / "cth_rate_variants.parquet")
    chunks = pd.read_parquet(out_dir / "notification_chunks.parquet")

    # CUSTADA parity vs cache
    cust_val = json.loads((out_dir / "_custada_validation.json").read_text())

    report = {
        "build_timestamp": _dt.datetime.utcnow().isoformat() + "Z",
        "library_versions": _list_lib_versions(),
        "stage_summary": {
            "stage1_custada": {
                "rows": int(len(custada)),
                "with_notes_text": int((custada["notes_text"].fillna("").str.len() > 0).sum()),
            },
            "stage2_tariff": {
                "chapters_parsed": int(len(chapters)),
                "items": int(len(tariff_items)),
                "non_ok_chapters": chapters[chapters["parse_status"] != "ok"]["chapter_no"].tolist(),
            },
            "stage3_4_notifications": {
                "rows": int(len(ntfns)),
                "by_type": ntfns["ntfn_type"].value_counts().to_dict(),
            },
            "stage5_jumbo": {
                "schedule_rows": int(len(jumbo_sched)),
                "conditions": int(len(jumbo_cond)),
                "pct_with_bcd_rate": round(float(jumbo_sched["bcd_rate"].notna().mean()), 4),
            },
            "stage6_xref": {
                "rows": int(len(xref)),
                "unique_cths_with_link": int(xref["cth"].nunique()),
                "by_match_type": xref["match_type"].value_counts().to_dict(),
            },
            "stage7_master": {
                "rows": int(len(master)),
                "with_statutory_desc": int(master["description_statutory"].notna().sum()),
                "with_chapter_notes": int((master["chapter_notes"].fillna("").str.len() > 0).sum()),
                "with_applicable_ntfns": int(master["applicable_notifications"].apply(lambda x: len(x) > 0).sum()),
                "with_bcd_diff": int(master["bcd_notifications_applied"].apply(lambda x: len(x) > 0).sum()),
                "source_match_distribution": master["source_match"].value_counts().to_dict(),
            },
            "stage8_descriptions": {
                "rows": int(len(descs)),
                "by_variant": descs["variant_type"].value_counts().to_dict(),
                "unique_cths": int(descs["cth"].nunique()),
            },
            "stage9_rate_variants": {
                "rows": int(len(variants)),
                "default_rows": int((variants["is_default"] == True).sum()),
                "jumbo_rows": int((variants["notification_id"] == "Customs-050-2017").sum()),
                "cths_with_multiple_variants": int((master["rate_variant_count"] > 1).sum()),
                "max_variants_for_one_cth": int(master["rate_variant_count"].max()),
            },
            "stage10_chunks_tfidf": {
                "notification_chunks": int(len(chunks)),
                "tfidf_rows": int(len(master)),
            },
        },
        "validation": {
            "custada_vs_cache": cust_val,
        },
        "output_files": {},
    }

    # Hash every output parquet/pkl/csv for provenance
    for p in sorted(out_dir.glob("*")):
        if p.is_file() and p.suffix in {".parquet", ".pkl", ".csv", ".json"}:
            report["output_files"][p.name] = {
                "size_bytes": p.stat().st_size,
                "sha256": _sha256_file(p),
            }

    (out_dir / "build_report.json").write_text(json.dumps(report, indent=2))
    return report


_README = """# Enriched CTH Dataset

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
"""


def write_readme(out_dir: pathlib.Path) -> None:
    (out_dir / "README.md").write_text(_README, encoding="utf-8")


def export_excel(out_dir: pathlib.Path) -> dict:
    EXCEL_DIR.mkdir(parents=True, exist_ok=True)

    master = pd.read_parquet(out_dir / "cth_master.parquet")
    descs = pd.read_parquet(out_dir / "cth_descriptions.parquet")
    variants = pd.read_parquet(out_dir / "cth_rate_variants.parquet")
    ntfns = pd.read_parquet(out_dir / "notifications.parquet")
    jumbo_sched = pd.read_parquet(out_dir / "jumbo_50_2017_schedule.parquet")
    jumbo_cond = pd.read_parquet(out_dir / "jumbo_50_2017_conditions.parquet")
    xref = pd.read_parquet(out_dir / "cth_to_notifications.parquet")

    # cth_master.xlsx — full canonical
    # Excel cell limit is 32k chars — clip long fields
    m_excel = master.copy()
    for col in ("chapter_notes", "notes_text", "section_notes_chapter", "provenance"):
        m_excel[col] = m_excel[col].astype(str).str.slice(0, 32000)
    m_excel["applicable_notifications"] = m_excel["applicable_notifications"].apply(
        lambda lst: ", ".join(lst) if isinstance(lst, (list, tuple)) else ""
    )
    m_excel["bcd_notifications_applied"] = m_excel["bcd_notifications_applied"].apply(
        lambda lst: ", ".join(lst) if isinstance(lst, (list, tuple)) else ""
    )
    _write_xlsx(EXCEL_DIR / "cth_master.xlsx", m_excel)

    # Slimmer fraud-detection view
    fraud_cols = [
        "cth", "description_combined", "chapter_title",
        "bcd_statutory", "bcd_effective", "bcd_notifications_applied",
        "igst_pct", "sws_pct", "applicable_notifications", "rate_variant_count",
    ]
    fraud_view = m_excel[fraud_cols].copy()
    _write_xlsx(EXCEL_DIR / "cth_master_fraud_view.xlsx", fraud_view)

    # cth_rate_variants
    _write_xlsx(EXCEL_DIR / "cth_rate_variants.xlsx", variants)

    # cth_descriptions
    _write_xlsx(EXCEL_DIR / "cth_descriptions.xlsx", descs)

    # notifications — clip full_text for Excel readability
    n_excel = ntfns.copy()
    n_excel["full_text"] = n_excel["full_text"].astype(str).str.slice(0, 32000)
    n_excel["predecessor_refs"] = n_excel["predecessor_refs"].apply(
        lambda lst: ", ".join(lst) if isinstance(lst, (list, tuple)) else ""
    )
    _write_xlsx(EXCEL_DIR / "notifications.xlsx", n_excel)

    # Jumbo — two sheets
    with pd.ExcelWriter(EXCEL_DIR / "jumbo_50_2017.xlsx", engine="openpyxl") as w:
        jumbo_sched.to_excel(w, sheet_name="schedule", index=False)
        jumbo_cond.to_excel(w, sheet_name="conditions", index=False)

    # Cross-reference
    _write_xlsx(EXCEL_DIR / "cth_to_notifications.xlsx", xref)

    # SUMMARY workbook — one sheet per artefact (capped to 5000 rows so Excel opens fast)
    cap = 5000
    with pd.ExcelWriter(EXCEL_DIR / "SUMMARY.xlsx", engine="openpyxl") as w:
        m_excel.head(cap).to_excel(w, sheet_name="cth_master", index=False)
        fraud_view.head(cap).to_excel(w, sheet_name="fraud_view", index=False)
        descs.head(cap).to_excel(w, sheet_name="descriptions", index=False)
        variants.head(cap).to_excel(w, sheet_name="rate_variants", index=False)
        n_excel.to_excel(w, sheet_name="notifications", index=False)
        jumbo_sched.head(cap).to_excel(w, sheet_name="jumbo_schedule", index=False)
        jumbo_cond.to_excel(w, sheet_name="jumbo_conditions", index=False)
        xref.head(cap).to_excel(w, sheet_name="xref", index=False)

    return {
        "files": [str(p.name) for p in sorted(EXCEL_DIR.glob("*.xlsx"))],
    }


def _write_xlsx(path: pathlib.Path, df: pd.DataFrame) -> None:
    """Write DataFrame to xlsx with frozen header + auto-filter."""
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="data", index=False)
        ws = w.sheets["data"]
        ws.freeze_panes = "A2"
        if df.shape[0]:
            ws.auto_filter.ref = ws.dimensions


def main() -> int:
    write_readme(ENRICHED)
    report = build_report(ENRICHED)
    excel_stats = export_excel(ENRICHED)
    print("Build report:", ENRICHED / "build_report.json")
    print("README:", ENRICHED / "README.md")
    print("Excel files:", excel_stats["files"])
    print()
    print("=== Stage summary ===")
    print(json.dumps(report["stage_summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
