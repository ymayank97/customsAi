"""End-to-end orchestrator for the CTH enrichment pipeline.

Stages:
  1  CUSTADA .xls       -> _custada_rows.parquet
  2  Tariff PDFs        -> tariff_chapters.parquet, _tariff_items.parquet, _tariff_subheading_index.parquet
  3  Ntfn dedupe+filter -> _ntfn_filelist.json
  4  Ntfn body parse    -> notifications.parquet
  5  Jumbo 50/2017      -> jumbo_50_2017_schedule.parquet, jumbo_50_2017_conditions.parquet
  6  Xref CTH<->ntfn    -> cth_to_notifications.parquet
  7  Canonical master   -> cth_master.parquet, cth_master.csv
  8  Descriptions       -> cth_descriptions.parquet
  9  Rate variants      -> cth_rate_variants.parquet
 10  Chunks + TF-IDF    -> notification_chunks.parquet, tfidf.pkl
 11  Report + README    -> build_report.json, README.md, excel/*.xlsx
 12  Lexical index      -> lexical_index.pkl

Usage (from project root, with venv Python active):
    python -m backend.app.scripts.build_enriched_dataset
    python -m backend.app.scripts.build_enriched_dataset --skip-existing
    python -m backend.app.scripts.build_enriched_dataset --only 6,7,8,9,10,11
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ENRICHED = pathlib.Path("data/enriched")
DATA_ROOT = pathlib.Path("data")


# Each stage: (id, name, output_paths, runner_callable_factory)
def _stage_specs():
    from backend.app.scripts import (
        _custada_parse, _tariff_pdf_parse, _ntfn_parse, _xref, _merge,
        _chunks_and_tfidf, _report_and_excel,
    )

    return [
        (1, "CUSTADA xls",
         [ENRICHED / "_custada_rows.parquet"],
         lambda: _custada_parse.parse_custada(
             DATA_ROOT / "unenriched custada files",
             ENRICHED / "_custada_rows.parquet")),
        (2, "Tariff PDFs",
         [ENRICHED / "tariff_chapters.parquet",
          ENRICHED / "_tariff_items.parquet",
          ENRICHED / "_tariff_subheading_index.parquet"],
         lambda: _tariff_pdf_parse.parse_tariff_pdfs(
             DATA_ROOT / "Tariff", ENRICHED)),
        (3, "Ntfn dedupe+filter",
         [ENRICHED / "_ntfn_filelist.json"],
         lambda: _ntfn_parse.dedupe_and_filter(
             DATA_ROOT / "notifications", ENRICHED / "_ntfn_filelist.json")),
        (4, "Ntfn body parse",
         [ENRICHED / "notifications.parquet"],
         lambda: _ntfn_parse.parse_notifications(
             ENRICHED / "_ntfn_filelist.json", ENRICHED / "notifications.parquet")),
        (5, "Jumbo 50/2017",
         [ENRICHED / "jumbo_50_2017_schedule.parquet",
          ENRICHED / "jumbo_50_2017_conditions.parquet"],
         lambda: _ntfn_parse.parse_jumbo_schedule(
             DATA_ROOT / "notifications" / "C1-Jumbo Customs Notification-50-30.06.2017.pdf",
             ENRICHED)),
        (6, "Xref CTH<->ntfn",
         [ENRICHED / "cth_to_notifications.parquet"],
         lambda: _xref.build_xref(
             ENRICHED / "_custada_rows.parquet",
             ENRICHED / "notifications.parquet",
             ENRICHED / "jumbo_50_2017_schedule.parquet",
             ENRICHED / "cth_to_notifications.parquet")),
        (7, "Canonical master",
         [ENRICHED / "cth_master.parquet"],
         lambda: _merge.build_master(ENRICHED)),
        (8, "Descriptions",
         [ENRICHED / "cth_descriptions.parquet"],
         lambda: _merge.build_descriptions(ENRICHED)),
        (9, "Rate variants",
         [ENRICHED / "cth_rate_variants.parquet"],
         lambda: _merge.build_rate_variants(ENRICHED)),
        (10, "Chunks + TF-IDF",
         [ENRICHED / "notification_chunks.parquet", ENRICHED / "tfidf.pkl"],
         lambda: {
             "chunks": _chunks_and_tfidf.chunk_notifications(ENRICHED),
             "tfidf": _chunks_and_tfidf.build_tfidf(ENRICHED),
         }),
        (11, "Report + README + Excel",
         [ENRICHED / "build_report.json", ENRICHED / "README.md"],
         lambda: {
             "readme": _report_and_excel.write_readme(ENRICHED) or "ok",
             "report": _report_and_excel.build_report(ENRICHED),
             "excel": _report_and_excel.export_excel(ENRICHED),
         }),
        (12, "Lexical index",
         [ENRICHED / "lexical_index.pkl"],
         lambda: _build_lexical_index()),
    ]


def _build_lexical_index() -> dict:
    from backend.app.services import lexical_index

    blob = lexical_index.build_and_save()
    return {"rows": blob["n"]}


def _outputs_exist(paths) -> bool:
    return all(p.exists() for p in paths)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-existing", action="store_true",
                    help="Skip a stage if all its output files already exist")
    ap.add_argument("--only", type=str, default="",
                    help="Comma-separated stage IDs to run, e.g. '6,7,8,9'")
    args = ap.parse_args()

    ENRICHED.mkdir(parents=True, exist_ok=True)

    only: set[int] | None = None
    if args.only:
        only = {int(x.strip()) for x in args.only.split(",") if x.strip()}

    specs = _stage_specs()
    log: list[dict] = []
    t0_total = time.time()

    for stage_id, name, outputs, run in specs:
        if only is not None and stage_id not in only:
            continue
        if args.skip_existing and _outputs_exist(outputs):
            print(f"[stage {stage_id:>2}] {name:25s} SKIP (outputs exist)")
            log.append({"stage": stage_id, "name": name, "status": "skipped"})
            continue

        print(f"[stage {stage_id:>2}] {name:25s} RUN")
        t0 = time.time()
        try:
            stats = run()
        except Exception as e:
            elapsed = time.time() - t0
            print(f"[stage {stage_id:>2}] {name:25s} FAIL  ({elapsed:.1f}s): {e}")
            log.append({"stage": stage_id, "name": name, "status": "failed", "error": str(e)})
            (ENRICHED / "_orchestrator_log.json").write_text(json.dumps(log, indent=2))
            return 2
        elapsed = time.time() - t0
        print(f"[stage {stage_id:>2}] {name:25s} OK    ({elapsed:.1f}s)")
        log.append({"stage": stage_id, "name": name, "status": "ok",
                    "elapsed_s": round(elapsed, 1),
                    "stats": stats if isinstance(stats, dict) else None})

    total = time.time() - t0_total
    print(f"\nAll stages complete in {total:.1f}s")
    (ENRICHED / "_orchestrator_log.json").write_text(json.dumps(log, indent=2, default=str))
    print(f"Orchestrator log: {ENRICHED / '_orchestrator_log.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
