"""Stages 7-9: build canonical cth_master + cth_descriptions + cth_rate_variants.

Inputs (all from data/enriched):
    _custada_rows.parquet
    _tariff_items.parquet
    _tariff_subheading_index.parquet
    tariff_chapters.parquet
    notifications.parquet
    cth_to_notifications.parquet
    jumbo_50_2017_schedule.parquet
    jumbo_50_2017_conditions.parquet

Outputs:
    cth_master.parquet
    cth_master.csv
    cth_descriptions.parquet
    cth_rate_variants.parquet
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Iterable

import pandas as pd

ENRICHED = pathlib.Path("data/enriched")

_RATE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_BARE_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_NIL_RE = re.compile(r"(?i)\b(nil|free)\b")


def _parse_rate(text) -> float | None:
    """Extract numeric BCD rate as percent. Returns None if not parseable."""
    try:
        if text is None or pd.isna(text):
            return None
    except (TypeError, ValueError):
        pass
    s = str(text).strip()
    if not s or s.lower() in {"nan", "<na>", "none"}:
        return None
    if _NIL_RE.search(s):
        return 0.0
    m = _RATE_RE.search(s)
    if m:
        return float(m.group(1))
    if _BARE_NUM_RE.match(s):
        return float(s)
    return None


def _expand_prefix_inherit(tariff_items: pd.DataFrame, sub_index: pd.DataFrame,
                           cth_set: set[str]) -> pd.DataFrame:
    """For CTHs not in tariff_items, inherit from nearest 6-digit, then 4-digit prefix.
    Returns DataFrame: cth, description_statutory, bcd_statutory, unit_statutory,
    chapter_no, section_no, source_match (exact|inherited_6d|inherited_4d|none)."""
    by_cth = tariff_items.set_index("cth")
    by_pref6 = sub_index[sub_index["length"] == 6].set_index("prefix")
    by_pref4 = sub_index[sub_index["length"] == 4].set_index("prefix")

    rows = []
    for cth in sorted(cth_set):
        if cth in by_cth.index:
            r = by_cth.loc[cth]
            rows.append({
                "cth": cth,
                "description_statutory": r["description_statutory"],
                "bcd_statutory_text": r["bcd_statutory"],
                "unit_statutory": r["unit_statutory"],
                "chapter_no": int(r["chapter_no"]) if pd.notna(r["chapter_no"]) else None,
                "section_no": int(r["section_no"]) if pd.notna(r["section_no"]) else None,
                "source_match": "exact",
            })
            continue
        # Try 6-digit prefix
        p6 = cth[:6]
        if p6 in by_pref6.index:
            r = by_pref6.loc[p6]
            if isinstance(r, pd.DataFrame):
                r = r.iloc[0]
            rows.append({
                "cth": cth,
                "description_statutory": r["description_statutory"],
                "bcd_statutory_text": None,
                "unit_statutory": None,
                "chapter_no": int(r["chapter_no"]) if pd.notna(r["chapter_no"]) else int(cth[:2]),
                "section_no": None,
                "source_match": "inherited_6d",
            })
            continue
        p4 = cth[:4]
        if p4 in by_pref4.index:
            r = by_pref4.loc[p4]
            if isinstance(r, pd.DataFrame):
                r = r.iloc[0]
            rows.append({
                "cth": cth,
                "description_statutory": r["description_statutory"],
                "bcd_statutory_text": None,
                "unit_statutory": None,
                "chapter_no": int(r["chapter_no"]) if pd.notna(r["chapter_no"]) else int(cth[:2]),
                "section_no": None,
                "source_match": "inherited_4d",
            })
            continue
        rows.append({
            "cth": cth,
            "description_statutory": None,
            "bcd_statutory_text": None,
            "unit_statutory": None,
            "chapter_no": int(cth[:2]),
            "section_no": None,
            "source_match": "none",
        })
    return pd.DataFrame(rows)


def _normalise_for_dedupe(s: str) -> str:
    """Lowercase + strip + collapse whitespace + drop trailing punctuation
    so that ``"Other apparatus :"`` and ``"OTHER APPARATUS"`` dedupe."""
    return re.sub(r"\s+", " ", str(s)).strip().strip(":-., ").lower()


def _lineage_description(
    chapter_title: str | None,
    heading_text: str | None,
    subheading_text: str | None,
    stat_desc: str | None,
    custada_desc: str | None,
    jumbo_desc: str | None,
) -> str:
    """Build a hierarchical, dedupe-concatenated description.

    For Indian customs tariff a residuary leaf like ``85369090`` carries
    only the literal word "Other" — useless for retrieval.  We chain the
    parent-heading text (4-digit) and sub-heading text (6-digit) onto every
    leaf so that:

        heading 8536  : "Electrical apparatus for switching... ≤ 1,000 V"
                         + sub 853690: "Other apparatus"
                         + leaf 85369090: "Other"

    becomes a single rich passage that embedding and TF-IDF retrieval can
    actually match on (``connector``, ``electrical``, ``apparatus``,
    ``switching`` ...).  Sources are joined left-to-right with ``" | "``;
    duplicates (case-insensitive, punctuation-tolerant) are dropped, the
    first occurrence wins.
    """
    parts: list[str] = []
    seen: set[str] = set()
    for p in (chapter_title, heading_text, subheading_text,
              stat_desc, custada_desc, jumbo_desc):
        if not p or not isinstance(p, str):
            continue
        ps = p.strip()
        if not ps:
            continue
        key = _normalise_for_dedupe(ps)
        if not key or key in seen:
            continue
        # Skip any source that is a substring of an already-included
        # source (e.g. leaf "Other" when sub-heading is "Other apparatus").
        if any(key in seen_key for seen_key in seen):
            continue
        seen.add(key)
        parts.append(ps)
    return " | ".join(parts)


def build_master(out_dir: pathlib.Path) -> dict:
    custada = pd.read_parquet(out_dir / "_custada_rows.parquet")
    tariff = pd.read_parquet(out_dir / "_tariff_items.parquet")
    subidx = pd.read_parquet(out_dir / "_tariff_subheading_index.parquet")
    chapters = pd.read_parquet(out_dir / "tariff_chapters.parquet")
    xref = pd.read_parquet(out_dir / "cth_to_notifications.parquet")
    jumbo = pd.read_parquet(out_dir / "jumbo_50_2017_schedule.parquet")

    cth_set: set[str] = set(custada["cth"].astype(str).tolist())

    # Statutory join with prefix inheritance
    stat = _expand_prefix_inherit(tariff, subidx, cth_set)

    # Chapter notes lookup
    ch_lookup = chapters.set_index("chapter_no")[["title", "chapter_notes", "section_notes"]]

    # Notifications by CTH (aggregated lists)
    ntfn_by_cth = (xref.groupby("cth")["ntfn_id"]
                       .apply(lambda s: sorted(set(s.tolist())))
                       .reset_index()
                       .rename(columns={"ntfn_id": "applicable_notifications"}))

    # Jumbo description first hit per CTH (for description_combined)
    jumbo_desc_by_cth: dict[str, str] = {}
    for r in jumbo.itertuples(index=False):
        if isinstance(r.cth, str) and r.cth and r.cth in cth_set and r.cth not in jumbo_desc_by_cth:
            if r.description:
                jumbo_desc_by_cth[r.cth] = r.description.strip()

    # Heading-level (4-digit) and sub-heading-level (6-digit) text from the
    # tariff PDF parser. Both already exist in subidx — we just weren't
    # using them, which left every residuary leaf as the bare word "Other".
    heading_text_by_prefix: dict[str, str] = {}
    subheading_text_by_prefix: dict[str, str] = {}
    for r in subidx.itertuples(index=False):
        prefix = str(r.prefix).strip()
        desc = (r.description_statutory or "").strip()
        if not prefix or not desc:
            continue
        if r.length == 4:
            heading_text_by_prefix.setdefault(prefix, desc)
        elif r.length == 6:
            subheading_text_by_prefix.setdefault(prefix, desc)

    # Merge
    master = (custada.merge(stat, on="cth", how="left")
                     .merge(ntfn_by_cth, on="cth", how="left"))

    # Fill applicable_notifications NaN with empty list
    master["applicable_notifications"] = master["applicable_notifications"].apply(
        lambda v: v if isinstance(v, list) else []
    )

    # Chapter / section text
    master["chapter_no"] = master["chapter_no"].fillna(master["chapter"].astype(str).str.lstrip("0").replace("", "0").astype(int))
    master["chapter_no"] = master["chapter_no"].astype(int)
    master["chapter_title"] = master["chapter_no"].map(ch_lookup["title"]).fillna("")
    master["chapter_notes"] = master["chapter_no"].map(ch_lookup["chapter_notes"]).fillna("")
    master["section_notes_chapter"] = master["chapter_no"].map(ch_lookup["section_notes"]).fillna("")

    # Statutory BCD numeric
    master["bcd_statutory"] = master["bcd_statutory_text"].apply(_parse_rate)
    master["bcd_effective"] = master["bcd_ntfn"].apply(_parse_rate)
    master["igst_pct"] = master["igst"].apply(_parse_rate)
    master["sws_pct"] = master["sws"].apply(_parse_rate)

    # heading_text (4-digit) and subheading_text (6-digit) — explicit
    # columns so downstream code (retriever, UI, LLM prompt) can read the
    # legal hierarchy text directly instead of brittle string-parsing of
    # ``description_combined``.
    master["heading_text"] = master["cth"].astype(str).str[:4].map(heading_text_by_prefix).fillna("")
    master["subheading_text"] = master["cth"].astype(str).str[:6].map(subheading_text_by_prefix).fillna("")

    # description_combined now chains the full lineage:
    #   chapter_title → heading_text → subheading_text → leaf → custada → jumbo
    # so even bare-"Other" residuary CTHs become richly retrievable.
    master["description_combined"] = [
        _lineage_description(
            ct, ht, sht, s, c, jumbo_desc_by_cth.get(cth),
        )
        for ct, ht, sht, s, c, cth in zip(
            master["chapter_title"],
            master["heading_text"],
            master["subheading_text"],
            master["description_statutory"],
            master["description"],
            master["cth"],
        )
    ]

    # bcd_notifications_applied: which ntfns drove the rate diff (heuristic: if statutory != effective, list applicable Jumbo + others that explicitly mention the CTH exact)
    jumbo_cths = set(jumbo[jumbo["cth"].notna()]["cth"].astype(str).tolist())
    exact_xref = xref[xref["match_type"] == "exact_8d"].groupby("cth")["ntfn_id"].apply(set).to_dict()

    def _applied(row):
        if row["bcd_statutory"] is None or row["bcd_effective"] is None:
            return []
        if abs(row["bcd_statutory"] - row["bcd_effective"]) < 0.001:
            return []
        applied: list[str] = []
        if row["cth"] in jumbo_cths:
            applied.append("Customs-050-2017")
        applied.extend(sorted(exact_xref.get(row["cth"], set())))
        return sorted(set(applied))

    master["bcd_notifications_applied"] = master.apply(_applied, axis=1)

    # rate variant count placeholder; filled after build_rate_variants
    master["rate_variant_count"] = 0

    # provenance JSON
    def _prov(row):
        p = {
            "description_statutory": row.get("source_match", "none"),
            "description_custada": "custada_xls" if row["description"] else "missing",
            "bcd_statutory": "tariff_pdf" if row["bcd_statutory"] is not None else ("custada_sch" if row["bcd_sch"] else "missing"),
            "bcd_effective": "custada_ntfn",
            "applicable_notifications": "xref",
            "chapter_notes": "tariff_pdf",
        }
        return json.dumps(p, separators=(",", ":"))

    master["provenance"] = master.apply(_prov, axis=1)

    # Final canonical column ordering
    cols_out = [
        "cth", "chapter", "heading", "description_statutory", "description",
        "description_combined", "heading_text", "subheading_text",
        "chapter_title", "chapter_notes", "section_notes_chapter",
        "unit", "unit_statutory",
        "bcd_statutory", "bcd_effective", "bcd_sch", "bcd_ntfn",
        "bcd_notifications_applied",
        "igst", "igst_pct", "sws", "sws_pct",
        "import_policy", "export_policy",
        "notes_text", "applicable_notifications", "rate_variant_count",
        "section_no", "chapter_no", "source_match", "provenance",
    ]
    master_out = master[cols_out].copy()
    master_out.to_parquet(out_dir / "cth_master.parquet", index=False)

    return {
        "rows": int(len(master_out)),
        "with_statutory_desc": int(master_out["description_statutory"].notna().sum()),
        "with_chapter_notes": int((master_out["chapter_notes"].fillna("").str.len() > 0).sum()),
        "with_applicable_ntfns": int(master_out["applicable_notifications"].apply(lambda x: len(x) > 0).sum()),
        "with_bcd_diff": int(master_out["bcd_notifications_applied"].apply(lambda x: len(x) > 0).sum()),
    }


def build_descriptions(out_dir: pathlib.Path) -> dict:
    master = pd.read_parquet(out_dir / "cth_master.parquet")
    chapters = pd.read_parquet(out_dir / "tariff_chapters.parquet").set_index("chapter_no")
    jumbo = pd.read_parquet(out_dir / "jumbo_50_2017_schedule.parquet")
    subidx = pd.read_parquet(out_dir / "_tariff_subheading_index.parquet")

    sub_lookup = subidx[subidx["length"] == 6].set_index("prefix")["description_statutory"].to_dict()
    jumbo_by_cth: dict[str, str] = {}
    for r in jumbo.itertuples(index=False):
        if isinstance(r.cth, str) and r.cth and r.cth not in jumbo_by_cth and r.description:
            jumbo_by_cth[r.cth] = r.description.strip()

    rows: list[dict] = []
    for r in master.itertuples(index=False):
        cth = r.cth
        # statutory_full
        if r.description_statutory:
            rows.append({"cth": cth, "variant_type": "statutory_full",
                         "text": r.description_statutory, "source_doc": "tariff_pdf"})
        # statutory_subheading
        sh = sub_lookup.get(cth[:6])
        if sh and sh != r.description_statutory:
            rows.append({"cth": cth, "variant_type": "statutory_subheading",
                         "text": sh, "source_doc": "tariff_pdf"})
        # custada_short
        if r.description:
            rows.append({"cth": cth, "variant_type": "custada_short",
                         "text": r.description, "source_doc": "custada_xls"})
        # chapter_title
        if r.chapter_title:
            rows.append({"cth": cth, "variant_type": "chapter_title",
                         "text": r.chapter_title, "source_doc": "tariff_pdf"})
        # notes_excerpt
        if r.notes_text:
            rows.append({"cth": cth, "variant_type": "notes_excerpt",
                         "text": str(r.notes_text)[:200], "source_doc": "custada_notes"})
        # jumbo_description
        jd = jumbo_by_cth.get(cth)
        if jd:
            rows.append({"cth": cth, "variant_type": "jumbo_description",
                         "text": jd, "source_doc": "jumbo_50_2017"})

    df = pd.DataFrame(rows, columns=["cth", "variant_type", "text", "source_doc"])
    df["text"] = df["text"].astype(str).str.strip()
    df = df[df["text"].str.len() > 0].reset_index(drop=True)
    df["char_len"] = df["text"].str.len()
    df["text_norm"] = df["text"].str.lower().str.replace(r"\s+", " ", regex=True).str.strip()
    df = df.drop_duplicates(subset=["cth", "variant_type", "text_norm"]).reset_index(drop=True)
    df.to_parquet(out_dir / "cth_descriptions.parquet", index=False)

    by_var = df["variant_type"].value_counts().to_dict()
    return {
        "rows": int(len(df)),
        "by_variant": {k: int(v) for k, v in by_var.items()},
        "unique_cths": int(df["cth"].nunique()),
    }


def build_rate_variants(out_dir: pathlib.Path) -> dict:
    master = pd.read_parquet(out_dir / "cth_master.parquet")
    jumbo = pd.read_parquet(out_dir / "jumbo_50_2017_schedule.parquet")
    conditions = pd.read_parquet(out_dir / "jumbo_50_2017_conditions.parquet")

    cond_lookup = dict(zip(conditions["condition_no"].astype(str), conditions["condition_text"]))

    rows: list[dict] = []

    # Default statutory variant per CTH
    for r in master.itertuples(index=False):
        rows.append({
            "cth": r.cth,
            "bcd_rate": r.bcd_statutory if r.bcd_statutory is not None else None,
            "bcd_rate_text": r.bcd_sch,
            "condition_code": "STATUTORY",
            "condition_text": "First Schedule statutory rate (no exemption applied)",
            "notification_id": None,
            "effective_from": None,
            "effective_to": None,
            "is_default": True,
        })

    # Build subheading->cths and heading->cths from master
    cth_set: set[str] = set(master["cth"].astype(str).tolist())
    subheading_to_cths: dict[str, list[str]] = {}
    heading_to_cths: dict[str, list[str]] = {}
    chapter_to_cths: dict[str, list[str]] = {}
    for cth in cth_set:
        subheading_to_cths.setdefault(cth[:6], []).append(cth)
        heading_to_cths.setdefault(cth[:4], []).append(cth)
        chapter_to_cths.setdefault(cth[:2], []).append(cth)

    # Jumbo variants — explode each schedule row to applicable CTHs
    for r in jumbo.itertuples(index=False):
        rate_text = r.bcd_rate
        rate_num = _parse_rate(rate_text)
        cond_no_raw = r.condition_no
        cond_no = ""
        if isinstance(cond_no_raw, str):
            cond_no = cond_no_raw.strip()
        cond_txt = cond_lookup.get(cond_no, "") if cond_no else ""
        cond_code = f"Jumbo-S.No.{r.s_no}" + (f"-Cond.{cond_no}" if cond_no else "")

        cths_for_row: list[str] = []
        if isinstance(r.cth, str) and r.cth and r.cth in cth_set:
            cths_for_row.append(r.cth)
        elif isinstance(r.prefix, str) and r.prefix:
            p = r.prefix.replace(" ", "")
            if len(p) == 6:
                cths_for_row.extend(subheading_to_cths.get(p, ()))
            elif len(p) == 4:
                cths_for_row.extend(heading_to_cths.get(p, ()))
            elif len(p) == 2:
                cths_for_row.extend(chapter_to_cths.get(p, ()))

        for cth in cths_for_row:
            rows.append({
                "cth": cth,
                "bcd_rate": rate_num,
                "bcd_rate_text": rate_text,
                "condition_code": cond_code,
                "condition_text": cond_txt or (r.description or "")[:200],
                "notification_id": "Customs-050-2017",
                "effective_from": "2017-06-30",
                "effective_to": None,
                "is_default": False,
            })

    df = pd.DataFrame(rows, columns=[
        "cth", "bcd_rate", "bcd_rate_text", "condition_code", "condition_text",
        "notification_id", "effective_from", "effective_to", "is_default",
    ])
    df.to_parquet(out_dir / "cth_rate_variants.parquet", index=False)

    # Update rate_variant_count on master
    counts = df.groupby("cth").size().to_dict()
    master["rate_variant_count"] = master["cth"].map(counts).fillna(1).astype(int)
    master.to_parquet(out_dir / "cth_master.parquet", index=False)
    # Also dump CSV mirror now that master is final
    master.to_csv(out_dir / "cth_master.csv", index=False)

    return {
        "rows": int(len(df)),
        "default_rows": int((df["is_default"] == True).sum()),
        "jumbo_rows": int((df["notification_id"] == "Customs-050-2017").sum()),
        "cths_with_multiple_variants": int((master["rate_variant_count"] > 1).sum()),
        "max_variants_for_one_cth": int(master["rate_variant_count"].max()),
    }


def main() -> int:
    s7 = build_master(ENRICHED)
    s8 = build_descriptions(ENRICHED)
    s9 = build_rate_variants(ENRICHED)
    print("=== Stage 7: cth_master ===")
    print(json.dumps(s7, indent=2))
    print("=== Stage 8: cth_descriptions ===")
    print(json.dumps(s8, indent=2))
    print("=== Stage 9: cth_rate_variants ===")
    print(json.dumps(s9, indent=2))
    (ENRICHED / "_merge_validation.json").write_text(
        json.dumps({"stage7": s7, "stage8": s8, "stage9": s9}, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
