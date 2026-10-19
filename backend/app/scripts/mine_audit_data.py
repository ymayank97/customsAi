"""Mine non-supervised assets from CUSTADA audit rows + a sample Bill of Entry.

There is NO labelled ground truth in the audit data: CUSTADA files and the sample
BE contain only DECLARED values (declared CTH + importer description), never an
officer-corrected code. We therefore mine three *weak-signal* assets for the
hierarchical-cascade + LLM-adjudicator rebuild:

  1. audit_alias_index.parquet — real importer description variants -> the CTH
     they were filed under. A retrieval / candidate-recall booster ONLY. Because
     these are declared, a (description -> cth) pair is a weak prior, NOT proof
     the code is correct.
  2. fewshot_examples.json — a chapter-diverse reference bank of declarations.
     The adjudicator ignores these unless expert gold_cth + label_source are supplied.
  3. adversarial_test_set.parquet — a deterministic held-out split of distinct
     descriptions reserved for pipeline evaluation (Agent A6). Declared values are
     kept as a WEAK reference, explicitly NOT gold truth.

Pure pandas + the existing text_cleaning helpers. No trained models.

Run:  .venv/Scripts/python.exe -m backend.app.scripts.mine_audit_data
Flags: --skip-existing  (skip artifacts already on disk)
"""
from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

from backend.app.core.text_cleaning import clean_description, normalize_for_matching

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
ENRICHED = pathlib.Path("data/enriched")
CUSTADA_ROWS = ENRICHED / "_custada_rows.parquet"
CTH_MASTER = ENRICHED / "cth_master.parquet"
INPUT_BE = pathlib.Path("data/input_data.xlsx")

OUT_ALIAS = ENRICHED / "audit_alias_index.parquet"
OUT_FEWSHOT = ENRICHED / "fewshot_examples.json"
OUT_TESTSET = ENRICHED / "adversarial_test_set.parquet"
OUT_REPORT = ENRICHED / "AUDIT_MINING_REPORT.md"

SEED = 20260614  # deterministic split seed (today's date)
TEST_FRACTION = 0.18  # ~18% of distinct descriptions held out for eval
FEWSHOT_PER_CHAPTER_CAP = 4
FEWSHOT_TARGET = 150  # soft cap on total few-shot examples

# BE column names (note trailing spaces in the source workbook)
BE_DESC = "Item Desc "
BE_CTH = "CTH "
BE_BCD = "BCD Rate "
BE_ASSESS = "Assess Val "


def _norm_cth(raw) -> str:
    """Coerce any CTH representation to an 8-digit zero-padded string."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s or s.lower() in {"nan", "none", "<na>"}:
        return ""
    # floats like '85366990.0' -> strip trailing .0
    if s.endswith(".0"):
        s = s[:-2]
    digits = "".join(ch for ch in s if ch.isdigit())
    if not digits:
        return ""
    return digits.zfill(8)


def _load_master() -> pd.DataFrame:
    """Master keyed by 8-digit cth with hierarchy text columns."""
    m = pd.read_parquet(
        CTH_MASTER,
        columns=[
            "cth", "chapter", "heading", "heading_text",
            "subheading_text", "description", "chapter_title",
        ],
    )
    m["cth"] = m["cth"].map(_norm_cth)
    m = m.drop_duplicates(subset=["cth"], keep="first").set_index("cth")
    return m


# ---------------------------------------------------------------------------
# Source extraction: (raw_description, cth, source) tuples
# ---------------------------------------------------------------------------
def _custada_pairs() -> pd.DataFrame:
    df = pd.read_parquet(CUSTADA_ROWS, columns=["cth", "description"])
    df["cth"] = df["cth"].map(_norm_cth)
    df = df.rename(columns={"description": "raw_description"})
    df["source"] = "custada"
    df["declared_bcd"] = pd.Series([pd.NA] * len(df), dtype="object")
    df["assess_val"] = pd.Series([pd.NA] * len(df), dtype="object")
    return df[["raw_description", "cth", "source", "declared_bcd", "assess_val"]]


def _input_pairs() -> pd.DataFrame:
    if not INPUT_BE.exists():
        if OUT_ALIAS.exists():
            saved = pd.read_parquet(OUT_ALIAS)
            saved = saved[saved["source"].eq("input")]
            if not saved.empty:
                saved = saved.loc[saved.index.repeat(saved["freq"].astype(int))].reset_index(drop=True)
                print("[load] BE workbook missing; retaining previously mined input aliases (cleaned text only).")
                return pd.DataFrame({
                    "raw_description": saved["normalized_description"],
                    "cth": saved["cth"],
                    "source": "input",
                    "declared_bcd": None,
                    "assess_val": None,
                })
        return pd.DataFrame(
            columns=["raw_description", "cth", "source", "declared_bcd", "assess_val"]
        )
    df = pd.read_excel(INPUT_BE)
    out = pd.DataFrame({
        "raw_description": df[BE_DESC].astype("string"),
        "cth": df[BE_CTH].map(_norm_cth),
        "declared_bcd": df[BE_BCD].astype("object") if BE_BCD in df.columns else None,
        "assess_val": df[BE_ASSESS].astype("object") if BE_ASSESS in df.columns else None,
    })
    out["source"] = "input"
    return out[["raw_description", "cth", "source", "declared_bcd", "assess_val"]]


# ---------------------------------------------------------------------------
# Artifact 1: alias / exemplar index
# ---------------------------------------------------------------------------
def build_alias_index(pairs: pd.DataFrame, master: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df = pairs.copy()
    df["normalized_description"] = df["raw_description"].map(clean_description)

    stats = {"raw_pairs": int(len(df))}

    # drop empty normalized descriptions / empty cth
    df = df[(df["normalized_description"].str.len() > 0) & (df["cth"].str.len() == 8)]
    stats["after_empty_drop"] = int(len(df))

    # validate cth against master
    valid_mask = df["cth"].isin(master.index)
    unknown = df[~valid_mask]
    stats["unknown_cth_pairs"] = int(len(unknown))
    stats["unknown_cth_codes"] = sorted(unknown["cth"].unique().tolist())[:50]
    stats["unknown_cth_code_count"] = int(unknown["cth"].nunique())
    df = df[valid_mask]

    # frequency per (normalized_description, cth, source)
    grp = (
        df.groupby(["normalized_description", "cth", "source"], dropna=False)
        .size()
        .reset_index(name="freq")
    )

    # attach hierarchy
    grp["chapter"] = grp["cth"].map(master["chapter"])
    grp["heading"] = grp["cth"].map(master["heading"])
    grp["subheading_6"] = grp["cth"].str[:6]

    grp = grp[[
        "normalized_description", "cth", "chapter", "heading",
        "subheading_6", "source", "freq",
    ]].sort_values(["freq", "normalized_description"], ascending=[False, True])
    grp = grp.reset_index(drop=True)

    stats["alias_rows"] = int(len(grp))
    stats["alias_distinct_desc"] = int(grp["normalized_description"].nunique())
    stats["alias_distinct_cth"] = int(grp["cth"].nunique())
    stats["alias_chapters"] = int(grp["chapter"].nunique())
    return grp, stats


# ---------------------------------------------------------------------------
# Deterministic train/test split on distinct (normalized) descriptions
# ---------------------------------------------------------------------------
def split_descriptions(alias: pd.DataFrame) -> tuple[set[str], set[str]]:
    """Split distinct normalized descriptions into (fewshot_pool, test) sets.

    Deterministic via fixed SEED. Test descriptions are disjoint from the
    few-shot pool so the eval set never overlaps exemplars used to prime the LLM.
    """
    distinct = (
        alias[["normalized_description"]]
        .drop_duplicates()
        .sort_values("normalized_description")
        .reset_index(drop=True)
    )
    distinct["split_key"] = distinct["normalized_description"].map(normalize_for_matching)
    keys = distinct["split_key"].drop_duplicates().sort_values()
    test_keys = set(keys.sample(frac=TEST_FRACTION, random_state=SEED))
    test_set = set(distinct.loc[distinct["split_key"].isin(test_keys), "normalized_description"])
    pool_set = set(distinct["normalized_description"]) - test_set
    return pool_set, test_set


# ---------------------------------------------------------------------------
# Artifact 2: few-shot example bank (chapter-diverse, from the few-shot pool)
# ---------------------------------------------------------------------------
def build_fewshot(alias: pd.DataFrame, pool: set[str], master: pd.DataFrame) -> tuple[list[dict], dict]:
    cand = alias[alias["normalized_description"].isin(pool)].copy()
    # prefer higher-frequency, longer (more informative) descriptions per chapter
    cand["desc_len"] = cand["normalized_description"].str.len()
    cand = cand.sort_values(
        ["chapter", "freq", "desc_len"], ascending=[True, False, False]
    )
    # one exemplar per (chapter, cth) to avoid near-duplicate leaves, then cap per chapter
    cand = cand.drop_duplicates(subset=["chapter", "cth"], keep="first")
    cand = cand.groupby("chapter", group_keys=False).head(FEWSHOT_PER_CHAPTER_CAP)

    # if still over target, keep breadth: round-robin already capped; trim by global freq
    if len(cand) > FEWSHOT_TARGET:
        cand = cand.sort_values(["freq", "desc_len"], ascending=[False, False]).head(FEWSHOT_TARGET)

    examples: list[dict] = []
    for _, r in cand.iterrows():
        cth = r["cth"]
        mrow = master.loc[cth]
        examples.append({
            "description": r["normalized_description"],
            "declared_cth": cth,
            "chapter": r["chapter"],
            "heading": r["heading"],
            "subheading_text": _s(mrow.get("subheading_text")),
            "leaf_text": _s(mrow.get("description")),
            "chapter_title": _s(mrow.get("chapter_title")),
            "heading_text": _s(mrow.get("heading_text")),
        })
    examples.sort(key=lambda e: (e["chapter"], e["declared_cth"]))
    stats = {
        "fewshot_count": len(examples),
        "fewshot_chapters": len({e["chapter"] for e in examples}),
    }
    return examples, stats


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and pd.isna(v):
        return ""
    try:
        if pd.isna(v):
            return ""
    except (ValueError, TypeError):
        pass
    return str(v).strip()


# ---------------------------------------------------------------------------
# Artifact 3: adversarial test set (held-out distinct descriptions)
# ---------------------------------------------------------------------------
def build_testset(pairs: pd.DataFrame, test_descs: set[str], master: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df = pairs.copy()
    df["normalized_description"] = df["raw_description"].map(clean_description)
    df = df[(df["normalized_description"].isin(test_descs)) & (df["cth"].isin(master.index))]

    # keep ONE representative raw row per (normalized_description, cth): prefer
    # source 'input' (live BE format, noisier) when available, else custada.
    df["src_rank"] = (df["source"] == "input").astype(int)
    df = df.sort_values("src_rank", ascending=False)
    df = df.drop_duplicates(subset=["normalized_description", "cth"], keep="first")

    out = pd.DataFrame({
        "item_description": df["raw_description"].astype("string"),
        "declared_cth": df["cth"],
        "declared_bcd": df["declared_bcd"],
        "assess_val": df["assess_val"],
        "chapter": df["cth"].map(master["chapter"]),
        "heading": df["cth"].map(master["heading"]),
        "source": df["source"],
    })
    # noisy = description shrank a lot under cleaning (codes/qty stripped) -> tag mix
    norm_len = df["normalized_description"].str.len().to_numpy()
    raw_len = df["raw_description"].fillna("").str.len().to_numpy()
    out["is_noisy"] = (raw_len > (norm_len + 15))
    out = out.sort_values(["chapter", "declared_cth"]).reset_index(drop=True)

    stats = {
        "testset_rows": int(len(out)),
        "testset_chapters": int(out["chapter"].nunique()),
        "testset_noisy": int(out["is_noisy"].sum()),
        "testset_from_input": int((out["source"] == "input").sum()),
        "testset_from_custada": int((out["source"] == "custada").sum()),
    }
    return out, stats


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def write_report(stats: dict) -> None:
    a = stats["alias"]
    f = stats["fewshot"]
    t = stats["testset"]
    lines = [
        "# Audit Data Mining Report",
        "",
        f"Generated by `backend/app/scripts/mine_audit_data.py` (seed `{SEED}`).",
        "",
        "## CRITICAL CAVEAT — declared CTH is a WEAK signal, NOT gold truth",
        "",
        "All three artifacts are derived from DECLARED data (importer-filed CTH +",
        "importer description). The CUSTADA files and the sample Bill of Entry contain",
        "NO officer-corrected codes. A `(description -> cth)` pair therefore records",
        "only what an importer *claimed*, which is exactly the thing the fraud pipeline",
        "exists to second-guess. Treat the alias index as a candidate-recall booster,",
        "the few-shot bank as unverified reference material, and the test set's `declared_cth` as a",
        "weak reference for agreement metrics — never as ground-truth correctness.",
        "",
        "## Sources",
        "",
        f"- `{CUSTADA_ROWS.as_posix()}` — normalized CUSTADA tariff rows",
        f"- `{INPUT_BE.as_posix()}` — sample Bill of Entry (live input format)",
        f"- `{CTH_MASTER.as_posix()}` — canonical tariff (CTH validation + hierarchy text)",
        "",
        "## Artifact 1 — audit_alias_index.parquet",
        "",
        "Columns: `normalized_description, cth, chapter, heading, subheading_6, source, freq`.",
        "Descriptions normalized via `text_cleaning.clean_description`; deduplicated on",
        "`(normalized_description, cth, source)` with a frequency count. Only CTHs present",
        "in the master are kept.",
        "",
        f"- Raw (description, cth) pairs ingested: **{a['raw_pairs']:,}**",
        f"- After dropping empty descriptions/CTHs: **{a['after_empty_drop']:,}**",
        f"- Pairs with a CTH NOT in master (dropped): **{a['unknown_cth_pairs']:,}** "
        f"across **{a['unknown_cth_code_count']}** distinct codes",
        f"- Final alias rows: **{a['alias_rows']:,}**",
        f"- Distinct normalized descriptions: **{a['alias_distinct_desc']:,}**",
        f"- Distinct CTHs covered: **{a['alias_distinct_cth']:,}**",
        f"- Chapters covered: **{a['alias_chapters']}**",
        f"- Alias rows excluded for evaluation: **{a.get('heldout_alias_rows', 0):,}**",
        "",
        "## Train/test split logic",
        "",
        f"- Grouped by stemmed matching keys (including abbreviation expansion), seed `{SEED}`.",
        f"- Test fraction: **{TEST_FRACTION:.0%}** held out for evaluation (Agent A6).",
        "- Both the alias index and few-shot bank use only the training pool.",
        "  Test descriptions and their matching-key variants are excluded from both.",
        "",
        "## Artifact 2 — fewshot_examples.json",
        "",
        "Unverified reference bank. Runtime prompting requires expert `gold_cth` + `label_source`.",
        "Sampled from the",
        f"few-shot pool, capped at **{FEWSHOT_PER_CHAPTER_CAP} per chapter** "
        f"(one exemplar per (chapter, cth)), soft global cap **{FEWSHOT_TARGET}**.",
        "Each example carries `{description, declared_cth, chapter, heading,",
        "subheading_text, leaf_text}` (+ chapter_title/heading_text) pulled from the master.",
        "",
        f"- Few-shot examples: **{f['fewshot_count']}**",
        f"- Chapters represented: **{f['fewshot_chapters']}**",
        "",
        "## Artifact 3 — adversarial_test_set.parquet",
        "",
        "Columns: `item_description, declared_cth, declared_bcd, assess_val, chapter,",
        "heading, source, is_noisy`. One representative raw row per (description, cth),",
        "preferring the live BE format ('input') over CUSTADA when both exist. `is_noisy`",
        "flags rows whose raw text shrank substantially under cleaning (embedded codes /",
        "quantity / part-number noise) so the eval mixes clean and noisy inputs.",
        "",
        f"- Test rows: **{t['testset_rows']:,}**",
        f"- Chapters covered: **{t['testset_chapters']}**",
        f"- Noisy rows: **{t['testset_noisy']:,}**",
        f"- From live BE (input): **{t['testset_from_input']:,}**; "
        f"from CUSTADA: **{t['testset_from_custada']:,}**",
        "",
        "## Data-quality notes",
        "",
        f"- {a['unknown_cth_code_count']} distinct declared CTH codes were absent from the",
        "  master and dropped (sample below). For a 1-row-per-CTH master built from the",
        "  same CUSTADA source, CUSTADA codes match by construction; mismatches mostly",
        "  arise from the sample BE.",
        "",
        f"  Sample unknown codes: `{a['unknown_cth_codes'][:20]}`",
    ]
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--skip-existing", action="store_true",
        help="Skip generation if all three artifacts already exist.",
    )
    ap.add_argument(
        "--preserve-test-set", action="store_true",
        help="Keep the existing evaluation rows and exclude their matching keys from rebuilt aliases and few-shot examples.",
    )
    args = ap.parse_args()

    if args.skip_existing and OUT_ALIAS.exists() and OUT_FEWSHOT.exists() and OUT_TESTSET.exists():
        print("[skip] all artifacts present; --skip-existing set. Nothing to do.")
        return

    ENRICHED.mkdir(parents=True, exist_ok=True)
    master = _load_master()
    print(f"[load] master CTHs: {len(master):,}")

    pairs = pd.concat([_custada_pairs(), _input_pairs()], ignore_index=True)
    print(f"[load] raw (desc, cth) pairs: {len(pairs):,}")

    alias, alias_stats = build_alias_index(pairs, master)
    if args.preserve_test_set:
        if not OUT_TESTSET.exists():
            raise ValueError("--preserve-test-set requires an existing adversarial_test_set.parquet")
        testset = pd.read_parquet(OUT_TESTSET)
        test_keys = set(testset["item_description"].map(normalize_for_matching))
        test_descs = set(alias.loc[alias["normalized_description"].map(normalize_for_matching).isin(test_keys), "normalized_description"])
        pool = set(alias["normalized_description"]) - test_descs
        test_stats = {
            "testset_rows": len(testset),
            "testset_chapters": int(testset["chapter"].nunique()),
            "testset_noisy": int(testset["is_noisy"].sum()),
            "testset_from_input": int(testset["source"].eq("input").sum()),
            "testset_from_custada": int(testset["source"].eq("custada").sum()),
        }
    else:
        pool, test_descs = split_descriptions(alias)
        testset, test_stats = build_testset(pairs, test_descs, master)
    print(f"[split] fewshot-pool descs={len(pool):,}  test descs={len(test_descs):,}  seed={SEED}")
    train_alias = alias[alias["normalized_description"].isin(pool)].reset_index(drop=True)
    alias_stats.update({
        "heldout_alias_rows": len(alias) - len(train_alias),
        "alias_rows": len(train_alias),
        "alias_distinct_desc": int(train_alias["normalized_description"].nunique()),
        "alias_distinct_cth": int(train_alias["cth"].nunique()),
        "alias_chapters": int(train_alias["chapter"].nunique()),
    })
    train_alias.to_parquet(OUT_ALIAS, index=False)
    print(f"[write] {OUT_ALIAS}  rows={len(train_alias):,}")

    fewshot, fewshot_stats = build_fewshot(alias, pool, master)
    OUT_FEWSHOT.write_text(json.dumps(fewshot, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[write] {OUT_FEWSHOT}  examples={len(fewshot)}  chapters={fewshot_stats['fewshot_chapters']}")

    testset.to_parquet(OUT_TESTSET, index=False)
    print(f"[write] {OUT_TESTSET}  rows={len(testset):,}")

    write_report({"alias": alias_stats, "fewshot": fewshot_stats, "testset": test_stats})
    print(f"[write] {OUT_REPORT}")

    # --- inspect heads ---
    print("\n=== audit_alias_index head ===")
    print(alias.head(8).to_string())
    print("\n=== fewshot_examples[0:2] ===")
    print(json.dumps(fewshot[:2], indent=2, ensure_ascii=False))
    print("\n=== adversarial_test_set head ===")
    print(testset.head(8).to_string())

    print("\n=== SUMMARY ===")
    print(json.dumps(
        {"alias": alias_stats, "fewshot": fewshot_stats, "testset": test_stats,
         "split": {"seed": SEED, "test_fraction": TEST_FRACTION,
                   "fewshot_pool_descs": len(pool), "test_descs": len(test_descs)}},
        indent=2, default=str,
    ))


if __name__ == "__main__":
    main()
