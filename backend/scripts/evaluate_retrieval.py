"""Evaluate hierarchy retrieval without injecting the reference code or calling an LLM."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import pandas as pd

from backend.app.config import settings
from backend.app.core.text_cleaning import normalize_for_matching
from backend.app.services import retriever


LEVELS = {2: "chapter", 4: "heading", 6: "subheading", 8: "tariff_item"}


def load_examples(path: Path, limit: int | None = None) -> tuple[pd.DataFrame, str]:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, dtype={"declared_cth": "string", "gold_cth": "string"})
    else:
        df = pd.read_parquet(path)
    reference = "gold_cth" if "gold_cth" in df.columns else "declared_cth"
    required = {"item_description", reference}
    if reference == "gold_cth":
        required.add("label_source")
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")
    if df.empty:
        raise ValueError("Evaluation dataset is empty")
    codes = df[reference].astype("string").str.strip()
    if not codes.str.fullmatch(r"\d{8}").fillna(False).all():
        raise ValueError(f"{reference} must contain eight-digit strings, preserving leading zeroes")
    df[reference] = codes
    if reference == "gold_cth" and df["label_source"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Every gold label requires label_source (officer confirmation or ruling citation)")
    if df["item_description"].fillna("").astype(str).str.strip().eq("").any():
        raise ValueError("Every evaluation row requires a product description")
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be positive")
        if len(df) > limit:
            df = df.sample(n=limit, random_state=20261001)
    return df.reset_index(drop=True), reference


def data_quality(df: pd.DataFrame) -> dict:
    master = pd.read_parquet(settings.data_dir / "cth_master.parquet")
    keys = set(df["item_description"].map(normalize_for_matching))
    alias_path = settings.data_dir / "audit_alias_index.parquet"
    alias = pd.read_parquet(alias_path) if alias_path.exists() else pd.DataFrame()
    alias_keys = set(alias["normalized_description"].map(normalize_for_matching)) if not alias.empty else set()
    fewshot_path = settings.data_dir / "fewshot_examples.json"
    fewshot = json.loads(fewshot_path.read_text(encoding="utf-8")) if fewshot_path.exists() else []
    fewshot_keys = {normalize_for_matching(e.get("description", "")) for e in fewshot}
    reference = "gold_cth" if "gold_cth" in df.columns else "declared_cth"
    conflicts = df.assign(matching_key=df["item_description"].map(normalize_for_matching)).groupby("matching_key")[reference].nunique()
    return {
        "master_rows": len(master),
        "duplicate_master_codes": int(master["cth"].duplicated().sum()),
        "missing_heading_text": int(master["heading_text"].fillna("").eq("").sum()),
        "missing_subheading_text": int(master["subheading_text"].fillna("").eq("").sum()),
        "missing_statutory_leaf": int(master["description_statutory"].fillna("").eq("").sum()),
        "evaluation_matching_keys": len(keys),
        "alias_overlap_keys": len(keys & alias_keys),
        "fewshot_overlap_keys": len(keys & fewshot_keys),
        "ambiguous_reference_keys": int(conflicts.gt(1).sum()),
        "conflicting_alias_keys": int(alias.groupby("normalized_description")["cth"].nunique().gt(1).sum()) if not alias.empty else 0,
    }


def hierarchy_metrics(records: list[dict]) -> dict:
    metrics = {}
    for digits, level in LEVELS.items():
        top1 = sum(bool(r["codes"] and r["codes"][0][:digits] == r["reference"][:digits]) for r in records)
        recalled = sum(any(c[:digits] == r["reference"][:digits] for c in r["codes"]) for r in records)
        metrics[level] = {
            "top1": top1 / len(records) if records else 0.0,
            "candidate_recall": recalled / len(records) if records else 0.0,
        }
    return metrics


def evaluate(df: pd.DataFrame, reference: str) -> dict:
    records = []
    latencies = []
    invalid = 0
    master_codes = set(retriever._load_master().index)
    retriever.preload_retrieval_stack()
    started = time.perf_counter()
    for r in df.to_dict("records"):
        t0 = time.perf_counter()
        candidates = retriever.generate_candidates(r["item_description"], declared_cth=None)
        latencies.append((time.perf_counter() - t0) * 1000)
        codes = [c["cth"] for c in candidates]
        invalid += sum(c not in master_codes for c in codes)
        records.append({
            "codes": codes,
            "reference": r[reference],
            "source": str(r.get("source", "unspecified")),
        })
    elapsed = time.perf_counter() - started
    return {
        "rows": len(records),
        "reference": reference,
        "interpretation": "Accuracy against supplied expert labels" if reference == "gold_cth" else "Agreement with weak declarations; not accuracy",
        "declared_code_injected": False,
        "llm_calls": 0,
        "invalid_candidates": invalid,
        "wall_seconds": elapsed,
        "p50_ms": float(pd.Series(latencies).quantile(0.5)),
        "p95_ms": float(pd.Series(latencies).quantile(0.95)),
        "hierarchy": hierarchy_metrics(records),
        "by_source": {
            source: {"rows": len(group), "hierarchy": hierarchy_metrics(group)}
            for source in sorted({r["source"] for r in records})
            for group in [[r for r in records if r["source"] == source]]
        },
        "data_quality": data_quality(df),
    }


def render_report(m: dict) -> str:
    lines = [
        "# Retrieval evaluation", "",
        f"{m['rows']} rows. {m['interpretation']}. No declared-code injection. No LLM calls.", "",
        "| Level | Top-1 | Candidate recall |", "|---|---:|---:|",
    ]
    for level, values in m["hierarchy"].items():
        lines.append(f"| {level} | {values['top1']:.1%} | {values['candidate_recall']:.1%} |")
    lines += ["", f"Warm retrieval: {m['wall_seconds']:.2f}s; p50 {m['p50_ms']:.1f}ms, p95 {m['p95_ms']:.1f}ms.", ""]
    for source, group in m["by_source"].items():
        values = group["hierarchy"]["tariff_item"]
        lines.append(f"- {source}: {group['rows']} rows; 8-digit top-1 {values['top1']:.1%}, recall {values['candidate_recall']:.1%}.")
    lines += ["", "## Data quality", ""]
    lines.extend(f"- {key}: {value}" for key, value in m["data_quality"].items())
    lines += ["", "A missing subheading string can reflect an unsplit tariff item; check the PDF before treating it as a parsing error.",
              "Overlap with the alias or few-shot bank makes this unsuitable as a held-out estimate.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=settings.data_dir / "adversarial_test_set.parquet")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output", type=Path, default=settings.project_root / "var" / "evaluation" / "retrieval.json")
    args = parser.parse_args()
    df, reference = load_examples(args.input, None if args.full else args.limit)
    metrics = evaluate(df, reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    args.output.with_suffix(".md").write_text(render_report(metrics), encoding="utf-8")
    print(render_report(metrics))
    if metrics["invalid_candidates"]:
        raise SystemExit("FAIL: retrieval emitted codes absent from the master")
    quality = metrics["data_quality"]
    if quality["alias_overlap_keys"] or quality["fewshot_overlap_keys"]:
        raise SystemExit("FAIL: evaluation descriptions overlap retrieval aliases or few-shot examples; rebuild the split")


if __name__ == "__main__":
    main()
