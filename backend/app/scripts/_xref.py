"""Stage 6: build CTH <-> notification cross-reference.

Inputs:
    data/enriched/_custada_rows.parquet      (CTH set)
    data/enriched/notifications.parquet      (full_text per notification)
    data/enriched/jumbo_50_2017_schedule.parquet (direct CTH/prefix mappings)

Output:
    data/enriched/cth_to_notifications.parquet
        cth, ntfn_id, match_type, snippet
        match_type in {exact_8d, heading_4d, chapter_2d, jumbo_schedule}
"""
from __future__ import annotations

import pathlib
import re
from typing import Iterable

import pandas as pd

ENRICHED = pathlib.Path("data/enriched")
OUT_PATH = ENRICHED / "cth_to_notifications.parquet"

_RE_8D = re.compile(r"\b(\d{4})\s?(\d{2})\s?(\d{2})\b")
_RE_HEADING = re.compile(r"(?i)\b(?:heading|sub-?heading|tariff item)\s+(?:no\.?\s+)?(\d{4})\b")
_RE_CHAPTER = re.compile(r"(?i)\bchapter\s+(\d{1,2})\b")


def _snippet(text: str, start: int, end: int, pad: int = 60) -> str:
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def _scan_text(ntfn_id: str, body: str, valid_cths: set[str], chapter_to_cths: dict[str, list[str]],
               heading_to_cths: dict[str, list[str]]) -> list[dict]:
    """Scan one notification body for CTH/heading/chapter references."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()  # (cth, match_type) — dedupe within this notification

    # Pass 1: 8-digit exact
    for m in _RE_8D.finditer(body):
        cth = (m.group(1) + m.group(2) + m.group(3))
        if cth in valid_cths:
            key = (cth, "exact_8d")
            if key not in seen:
                seen.add(key)
                out.append({"cth": cth, "ntfn_id": ntfn_id, "match_type": "exact_8d",
                            "snippet": _snippet(body, m.start(), m.end())})

    # Pass 2: 4-digit heading
    for m in _RE_HEADING.finditer(body):
        h = m.group(1)
        for cth in heading_to_cths.get(h, ()):
            key = (cth, "heading_4d")
            if key not in seen:
                seen.add(key)
                out.append({"cth": cth, "ntfn_id": ntfn_id, "match_type": "heading_4d",
                            "snippet": _snippet(body, m.start(), m.end())})

    # Pass 3: 2-digit chapter
    for m in _RE_CHAPTER.finditer(body):
        c = m.group(1).zfill(2)
        for cth in chapter_to_cths.get(c, ()):
            key = (cth, "chapter_2d")
            if key not in seen:
                seen.add(key)
                out.append({"cth": cth, "ntfn_id": ntfn_id, "match_type": "chapter_2d",
                            "snippet": _snippet(body, m.start(), m.end())})
    return out


def _expand_jumbo(jumbo: pd.DataFrame, valid_cths: set[str],
                  chapter_to_cths: dict[str, list[str]],
                  heading_to_cths: dict[str, list[str]],
                  subheading_to_cths: dict[str, list[str]]) -> Iterable[dict]:
    """Convert Jumbo schedule rows to (cth, ntfn_id='Customs-050-2017', match_type='jumbo_schedule')."""
    ntfn_id = "Customs-050-2017"
    seen: set[str] = set()
    for r in jumbo.itertuples(index=False):
        snippet_parts = [f"S.No {r.s_no}", r.description or ""]
        if r.bcd_rate:
            snippet_parts.append(f"Standard rate: {r.bcd_rate}")
        if r.condition_no:
            snippet_parts.append(f"Condition {r.condition_no}")
        snippet = " | ".join(p for p in snippet_parts if p)[:240]

        cths_for_row: list[str] = []
        if isinstance(r.cth, str) and r.cth and r.cth in valid_cths:
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
            key = cth + "::" + str(r.s_no)
            if key in seen:
                continue
            seen.add(key)
            yield {"cth": cth, "ntfn_id": ntfn_id, "match_type": "jumbo_schedule", "snippet": snippet}


def build_xref(custada_path: pathlib.Path, notifications_path: pathlib.Path,
               jumbo_path: pathlib.Path, out_path: pathlib.Path) -> dict:
    custada = pd.read_parquet(custada_path)
    ntfns = pd.read_parquet(notifications_path)
    jumbo = pd.read_parquet(jumbo_path)

    valid_cths: set[str] = set(custada["cth"].astype(str).tolist())
    chapter_to_cths: dict[str, list[str]] = {}
    heading_to_cths: dict[str, list[str]] = {}
    subheading_to_cths: dict[str, list[str]] = {}
    for cth in valid_cths:
        chapter_to_cths.setdefault(cth[:2], []).append(cth)
        heading_to_cths.setdefault(cth[:4], []).append(cth)
        subheading_to_cths.setdefault(cth[:6], []).append(cth)

    rows: list[dict] = []
    for r in ntfns.itertuples(index=False):
        body = r.full_text or ""
        if not body:
            continue
        rows.extend(_scan_text(r.ntfn_id, body, valid_cths,
                               chapter_to_cths, heading_to_cths))

    jumbo_rows = list(_expand_jumbo(jumbo, valid_cths, chapter_to_cths,
                                    heading_to_cths, subheading_to_cths))
    rows.extend(jumbo_rows)

    df = pd.DataFrame(rows, columns=["cth", "ntfn_id", "match_type", "snippet"])
    # Sort and dedupe (same cth/ntfn/match_type may surface multiple snippets — keep first)
    df = (df.sort_values(["cth", "ntfn_id", "match_type"])
            .drop_duplicates(subset=["cth", "ntfn_id", "match_type"])
            .reset_index(drop=True))
    df.to_parquet(out_path, index=False)

    by_type = df["match_type"].value_counts().to_dict()
    cths_with_any = df["cth"].nunique()
    return {
        "rows": int(len(df)),
        "by_match_type": {k: int(v) for k, v in by_type.items()},
        "unique_cths_with_link": int(cths_with_any),
        "pct_cths_with_link": round(cths_with_any / max(1, len(valid_cths)), 4),
        "out_path": str(out_path),
        "jumbo_rows_emitted": int(len(jumbo_rows)),
    }


def main() -> int:
    stats = build_xref(
        ENRICHED / "_custada_rows.parquet",
        ENRICHED / "notifications.parquet",
        ENRICHED / "jumbo_50_2017_schedule.parquet",
        OUT_PATH,
    )
    import json
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
