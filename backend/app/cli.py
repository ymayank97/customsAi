"""End-to-end CLI: process a Bill of Entry Excel file and emit annotated output.

Usage:
    python -m backend.app.cli data/input_data.xlsx
    python -m backend.app.cli data/input_data.xlsx --limit 50
    python -m backend.app.cli data/input_data.xlsx --output var/results/audit.xlsx
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time

from backend.app.services import excel_processor, pipeline


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=pathlib.Path, help="Input .xlsx Bill of Entry file")
    ap.add_argument("--output", type=pathlib.Path, default=None,
                    help="Output annotated Excel path (default: var/results/<input>_audit.xlsx)")
    ap.add_argument("--limit", type=int, default=0, help="Process at most N rows (0 = all)")
    ap.add_argument("--report-json", type=pathlib.Path, default=None)
    args = ap.parse_args()

    if not args.input.exists():
        print(f"Input not found: {args.input}")
        return 1

    output = args.output
    if output is None:
        proj = pathlib.Path(__file__).resolve().parents[2]
        output = proj / "var" / "results" / f"{args.input.stem}_audit.xlsx"
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"[cli] loading {args.input.name}")
    df, cols = excel_processor.load_be_excel(args.input)
    print(f"[cli] {len(df)} rows; columns detected: {cols}")
    rows = excel_processor.normalise_input_rows(df, cols)
    if args.limit:
        rows = rows[:args.limit]
        df = df.head(args.limit).reset_index(drop=True)
    print(f"[cli] processing {len(rows)} rows")

    t0 = time.time()
    last_print = 0.0
    def progress(p):
        nonlocal last_print
        now = time.time()
        if now - last_print > 1.0 or p["completed"] >= p["total"]:
            print(f"  [{p['phase']}] {p['completed']}/{p['total']}")
            last_print = now

    results = pipeline.process_rows(rows, progress=progress)
    elapsed = time.time() - t0

    fraud_rows = [r for r in results if r["fraud"]["is_fraud"]]
    revenue = sum(r["fraud"]["revenue_at_risk"] or 0 for r in fraud_rows)
    tier_counts: dict[str, int] = {}
    for r in results:
        tier_counts[r["final"]["tier_used"]] = tier_counts.get(r["final"]["tier_used"], 0) + 1

    print()
    print("=== Summary ===")
    print(f"Total rows         : {len(results)}")
    print(f"Fraud flags        : {len(fraud_rows)}")
    print(f"Revenue at risk    : Rs {round(revenue):,}")
    print(f"Tier distribution  : {tier_counts}")
    print(f"Wall time          : {elapsed:.1f}s ({len(results) / max(0.001, elapsed):.1f} rows/s)")

    print(f"\n[cli] writing annotated Excel -> {output}")
    excel_processor.write_annotated_excel(df, results, output)

    if args.report_json:
        args.report_json.write_text(json.dumps({
            "summary": {
                "rows": len(results),
                "fraud_flags": len(fraud_rows),
                "revenue_at_risk": round(revenue),
                "tier_counts": tier_counts,
                "wall_time_s": round(elapsed, 1),
            },
            "fraud_rows": [{
                "row_idx": r["row_idx"],
                "declared_cth": r["declared_cth"],
                "declared_bcd": r["declared_bcd"],
                "correct_cth": r["final"]["cth_final"],
                "correct_bcd": r["final"]["bcd_final"],
                "fraud_type": r["fraud"]["flag_type"],
                "revenue_at_risk": r["fraud"]["revenue_at_risk"],
                "item_description": r["item_description"][:120],
            } for r in fraud_rows[:200]],
        }, indent=2))
        print(f"[cli] report -> {args.report_json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
