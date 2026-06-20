"""Accuracy / safety evaluation harness for the Hierarchical Adversarial
Verification pipeline.

Runs ``pipeline.process_rows`` over a sample of the adversarial test set and
reports the headline INVALID-CODE rate (must be 0), manual-queue rate,
declared-agreement (a HEURISTIC-MODE PROXY when no ANTHROPIC_API_KEY is set —
the real Stage-3 Opus adjudicator is NOT exercised), chapter agreement, revenue
sanity, and per-row timing. Writes ``data/enriched/EVAL_REPORT.md``.

Usage (repo root, venv active):
    .venv/Scripts/python.exe -m backend.scripts.eval_pipeline            # 300 rows
    .venv/Scripts/python.exe -m backend.scripts.eval_pipeline --limit 500
    .venv/Scripts/python.exe -m backend.scripts.eval_pipeline --full
"""
from __future__ import annotations

import argparse
import time

import pandas as pd

from backend.app.config import settings
from backend.app.services import custada_validator, pipeline


def _llm_mode() -> str:
    if settings.anthropic_api_key:
        return "claude"
    if settings.google_api_key:
        return "gemini"
    return "heuristic"


def _load_rows(limit: int | None) -> list[dict]:
    df = pd.read_parquet(settings.data_dir / "adversarial_test_set.parquet")
    if limit is not None:
        df = df.head(limit)
    rows: list[dict] = []
    for i, r in df.iterrows():
        declared_bcd = r.get("declared_bcd")
        if pd.isna(declared_bcd):
            declared_bcd = None
        assess_val = r.get("assess_val")
        if pd.isna(assess_val):
            assess_val = None
        rows.append({
            "row_idx": int(i),
            "item_description": str(r.get("item_description", "") or "").strip(),
            "declared_cth": str(r.get("declared_cth", "") or "").strip(),
            "declared_bcd": float(declared_bcd) if declared_bcd is not None else None,
            "assess_val": float(assess_val) if assess_val is not None else None,
            "ntfn_ref": "",
            "importer": "",
            "be_no": "",
        })
    return rows


def _evaluate(rows: list[dict]) -> dict:
    t0 = time.time()
    results = pipeline.process_rows(rows)
    wall = time.time() - t0

    n = len(results)
    invalid = []
    manual_queue = 0
    top1_agree = 0
    set_agree = 0
    chapter_agree = 0
    fraud_findings = 0
    revenue_suppressed = 0  # basis verified -> finding withheld
    revenue_raised = 0

    for r in results:
        final = r["final"]
        cth_final = (final.get("cth_final") or "").strip()
        declared = (r.get("declared_cth") or "").strip()
        conformal = final.get("conformal_set") or []
        mq = bool(final.get("manual_queue")) or cth_final == ""

        if mq:
            manual_queue += 1

        # INVALID-CODE: a non-empty final that is not a real master code.
        if cth_final and not custada_validator.cth_exists(cth_final):
            invalid.append({"row_idx": r["row_idx"], "cth_final": cth_final,
                            "declared": declared})

        if cth_final and declared and cth_final == declared:
            top1_agree += 1
        if declared and declared in conformal:
            set_agree += 1
        if cth_final and declared and cth_final[:2] == declared[:2]:
            chapter_agree += 1

        fraud = r.get("fraud") or {}
        rb = final.get("revenue_basis") or {}
        if fraud.get("is_fraud"):
            fraud_findings += 1
        # A finding is "raised" when revenue_at_risk > 0; "suppressed" when the
        # declared-rate basis verified and a CTH/BCD divergence existed.
        if (fraud.get("revenue_at_risk") or 0) > 0:
            revenue_raised += 1
        if rb.get("valid"):
            revenue_suppressed += 1

    denom_declared = sum(1 for r in results if (r.get("declared_cth") or "").strip())
    return {
        "n": n,
        "wall_seconds": round(wall, 2),
        "per_row_ms": round(wall / n * 1000, 1) if n else 0.0,
        "invalid": invalid,
        "invalid_rate": round(len(invalid) / n, 5) if n else 0.0,
        "manual_queue": manual_queue,
        "manual_queue_rate": round(manual_queue / n, 4) if n else 0.0,
        "top1_agree": top1_agree,
        "top1_agree_rate": round(top1_agree / denom_declared, 4) if denom_declared else 0.0,
        "set_agree": set_agree,
        "set_agree_rate": round(set_agree / denom_declared, 4) if denom_declared else 0.0,
        "chapter_agree": chapter_agree,
        "chapter_agree_rate": round(chapter_agree / denom_declared, 4) if denom_declared else 0.0,
        "denom_declared": denom_declared,
        "fraud_findings": fraud_findings,
        "revenue_raised": revenue_raised,
        "revenue_suppressed": revenue_suppressed,
    }


def _render_report(m: dict, mode: str, limit_desc: str) -> str:
    proxy = " *(heuristic-mode proxy)*" if mode == "heuristic" else ""
    lines = [
        "# CustomsAI Pipeline Evaluation Report",
        "",
        f"- Sample: {limit_desc} ({m['n']} rows scored)",
        f"- LLM mode: **{mode}**"
        + ("  — declared-agreement numbers are a PROXY; the Stage-3 Opus "
           "adjudicator is NOT exercised. Deterministic guarantees "
           "(invalid-code hard-block, Section XVII pruning) hold in every mode."
           if mode == "heuristic" else ""),
        f"- Wall time: {m['wall_seconds']}s  ({m['per_row_ms']} ms/row)",
        "",
        "## Headline safety metric",
        "",
        "| Metric | Value | Target |",
        "|---|---|---|",
        f"| **INVALID-CODE rate** (final not in master) | **{m['invalid_rate']}** "
        f"({len(m['invalid'])}/{m['n']}) | **0** |",
        f"| Manual-queue rate | {m['manual_queue_rate']} ({m['manual_queue']}/{m['n']}) | — |",
        "",
        "## Declared-agreement" + proxy,
        "",
        "`declared_cth` is importer-declared (a WEAK reference, not officer-verified "
        "gold). Read these as *agreement with what the importer declared*, not accuracy.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Top-1 (final == declared) | {m['top1_agree_rate']} "
        f"({m['top1_agree']}/{m['denom_declared']}) |",
        f"| Set-agreement (declared in conformal_set) | {m['set_agree_rate']} "
        f"({m['set_agree']}/{m['denom_declared']}) |",
        f"| Chapter-agreement (first 2 digits) | {m['chapter_agree_rate']} "
        f"({m['chapter_agree']}/{m['denom_declared']}) |",
        "",
        "## Revenue sanity",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Fraud findings (is_fraud) | {m['fraud_findings']} |",
        f"| Findings raised (revenue_at_risk > 0) | {m['revenue_raised']} |",
        f"| Rows with verified declared-rate basis (finding withheld) | {m['revenue_suppressed']} |",
        "",
    ]
    if m["invalid"]:
        lines += ["## INVALID CODES (must be empty)", ""]
        for x in m["invalid"][:50]:
            lines.append(f"- row {x['row_idx']}: final={x['cth_final']} declared={x['declared']}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate the HAV pipeline.")
    ap.add_argument("--limit", type=int, default=300,
                    help="Rows from adversarial_test_set.parquet (default 300).")
    ap.add_argument("--full", action="store_true", help="Score the full set.")
    args = ap.parse_args()

    limit = None if args.full else args.limit
    limit_desc = "full set" if args.full else f"first {args.limit} rows"
    mode = _llm_mode()

    rows = _load_rows(limit)
    m = _evaluate(rows)

    print("=" * 64)
    print(f"PIPELINE EVAL  ({limit_desc}, LLM mode={mode})")
    print("=" * 64)
    print(f"  INVALID-CODE rate (HEADLINE) : {m['invalid_rate']}  "
          f"({len(m['invalid'])}/{m['n']})   [target 0]")
    print(f"  manual-queue rate            : {m['manual_queue_rate']}  "
          f"({m['manual_queue']}/{m['n']})")
    label = " (heuristic proxy)" if mode == "heuristic" else ""
    print(f"  declared top-1 agreement{label:18}: {m['top1_agree_rate']}  "
          f"({m['top1_agree']}/{m['denom_declared']})")
    print(f"  declared set-agreement       : {m['set_agree_rate']}  "
          f"({m['set_agree']}/{m['denom_declared']})")
    print(f"  chapter agreement            : {m['chapter_agree_rate']}  "
          f"({m['chapter_agree']}/{m['denom_declared']})")
    print(f"  fraud findings               : {m['fraud_findings']}  "
          f"(raised>0: {m['revenue_raised']}, basis-verified: {m['revenue_suppressed']})")
    print(f"  timing                       : {m['per_row_ms']} ms/row "
          f"({m['wall_seconds']}s total)")
    print("=" * 64)

    out = settings.data_dir / "EVAL_REPORT.md"
    out.write_text(_render_report(m, mode, limit_desc), encoding="utf-8")
    print(f"wrote {out}")

    if m["invalid"]:
        raise SystemExit(f"FAIL: {len(m['invalid'])} invalid codes emitted (target 0).")


if __name__ == "__main__":
    main()
