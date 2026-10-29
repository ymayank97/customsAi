"""Stage 2 — parse Indian Customs First Schedule chapter PDFs (Tariff)."""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import defaultdict
from typing import Iterable

import pandas as pd
import pdfplumber

_FNAME_RE = re.compile(r"^S(?P<sec>\d{1,2})\s*C(?P<ch>\d{1,2})\.pdf$", re.IGNORECASE)
_CTH8_RE = re.compile(r"^(\d{4})\s+(\d{2})\s+(\d{2})\b")
_CTH6_RE = re.compile(r"^(\d{4})\s+(\d{2})(?!\s*\d{2})\b")
_CTH4_RE = re.compile(r"^(\d{4})(?!\s*\d)\b")
_RATE_RE = re.compile(
    r"(?:\*+\s*)?(?:\#+\s*)?(?:\$+\s*)?(?:Free|FREE|free|\d+(?:\.\d+)?\s*%)",
)
_TABLE_HDR_RE = re.compile(r"Tariff\s+Item.*Description\s+of\s+goods", re.IGNORECASE)
_COLNUM_HDR_RE = re.compile(r"^\s*\(\s*1\s*\)\s*\(\s*2\s*\)\s*\(\s*3\s*\)\s*\(\s*4\s*\)\s*\(\s*5\s*\)\s*$")
_BANNER_RE = re.compile(r"^SECTION[-\s]\w+\s+\d+\s+CHAPTER[-\s]\d+\s*$", re.IGNORECASE)
_BANNER_LOOSE_RE = re.compile(r"^SECTION[-\s]", re.IGNORECASE)
_FOOTNOTE_RE = re.compile(r"^\**\s*w\.?e\.?f\.?", re.IGNORECASE)
_NOTES_HDR_RE = re.compile(
    r"^\s*(NOTES?\s*:|CHAPTER\s+NOTES?\s*:?|Chapter\s+Notes?\s*:?)\s*$"
)
_SUBNOTE_HDR_RE = re.compile(
    r"^\s*(SUB[-\s]?HEADING\s+NOTES?\s*:?|Sub[-\s]?heading\s+Notes?\s*:?)\s*$"
)
_UNIT_TOKENS = {
    "u", "kg.", "kg", "Kg.", "Kg", "KG", "KG.", "g", "g.", "G", "G.",
    "t", "t.", "T", "T.", "l", "l.", "L", "L.", "m", "m.", "M", "M.",
    "m2", "m3", "m^2", "m^3", "sq.m", "cu.m", "pcs", "pcs.", "PCS",
    "Tu", "1000", "1000u", "ce/el", "1000s", "MT", "MT.", "Pa", "Tcal",
    "kvar", "kw", "kwh", "Cu/cm",
}


def _filename_meta(path: pathlib.Path) -> tuple[int, int] | None:
    m = _FNAME_RE.match(path.name)
    if not m:
        return None
    return int(m.group("sec")), int(m.group("ch"))


def _extract_pages_text(pdf: pdfplumber.PDF) -> list[str]:
    pages = []
    for page in pdf.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        pages.append(t)
    return pages


def _full_text(pages: list[str]) -> str:
    return "\n".join(pages)


def _find_chapter_title(pages_text: list[str], chapter_no: int) -> str:
    # Search the first 2 pages for "CHAPTER N" then take next non-empty line.
    head = "\n".join(pages_text[:2]).splitlines()
    chap_re = re.compile(rf"^\s*CHAPTER[-\s]+0*{chapter_no}\b", re.IGNORECASE)
    for i, line in enumerate(head):
        if chap_re.match(line):
            for j in range(i + 1, min(i + 6, len(head))):
                cand = head[j].strip()
                if not cand:
                    continue
                if cand.upper().startswith("NOTES") or cand.upper().startswith("NOTE"):
                    break
                return cand
    # Fallback: first non-banner non-section non-empty line
    for line in head:
        s = line.strip()
        if not s:
            continue
        if _BANNER_LOOSE_RE.match(s):
            continue
        if s.upper().startswith("THE FIRST SCHEDULE"):
            continue
        if s.upper().startswith("SECTION"):
            continue
        if s.upper().startswith("CHAPTER"):
            continue
        return s
    return ""


def _extract_notes_blocks(full_text: str) -> tuple[str, str]:
    """Return (chapter_notes, sub_heading_notes). Looks between NOTES header and Tariff Item header."""
    lines = full_text.splitlines()
    notes_start = None
    sub_start = None
    table_start = None
    for i, line in enumerate(lines):
        if notes_start is None and _NOTES_HDR_RE.match(line):
            notes_start = i + 1
        if sub_start is None and _SUBNOTE_HDR_RE.match(line):
            sub_start = i + 1
        if table_start is None and _TABLE_HDR_RE.search(line):
            table_start = i
            break

    def _slice(a: int | None, b: int | None) -> str:
        if a is None:
            return ""
        if b is None or b <= a:
            b = len(lines)
        chunk = []
        for ln in lines[a:b]:
            s = ln.rstrip()
            if not s:
                continue
            if _BANNER_LOOSE_RE.match(s):
                continue
            if _FOOTNOTE_RE.match(s):
                continue
            chunk.append(s)
        return "\n".join(chunk).strip()

    if notes_start is not None and sub_start is not None and sub_start > notes_start:
        notes = _slice(notes_start, sub_start - 1)
        subnotes = _slice(sub_start, table_start)
    else:
        notes = _slice(notes_start, sub_start if sub_start is not None else table_start)
        subnotes = _slice(sub_start, table_start)
    return notes, subnotes


def _strip_noise_lines(lines: Iterable[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        s = ln.rstrip()
        if not s:
            continue
        if _BANNER_LOOSE_RE.match(s):
            continue
        if _COLNUM_HDR_RE.match(s):
            continue
        if _TABLE_HDR_RE.search(s):
            continue
        if re.match(r"^\s*(Standard|Prefer-?|ential|Areas|Stand|ard)\s*$", s):
            continue
        if _FOOTNOTE_RE.match(s):
            continue
        out.append(s)
    return out


def _isolate_table_lines(full_text: str) -> list[str]:
    lines = full_text.splitlines()
    # Find the FIRST table header occurrence and start after it.
    start = 0
    for i, line in enumerate(lines):
        if _TABLE_HDR_RE.search(line):
            start = i + 1
            break
    return _strip_noise_lines(lines[start:])


def _parse_rate_unit(tail: str) -> tuple[str, str]:
    """Given the segment after the description, try to split into (unit, rate).
    The PDF text typically reads: <unit>  <rate>  <pref>  i.e. "u 30% -" or "kg. **7.5% -" or just "u" / blank.
    """
    t = tail.strip()
    if not t:
        return "", ""
    # rate match: first occurrence
    rate = ""
    m = _RATE_RE.search(t)
    if m:
        rate = m.group(0).strip()
    # unit: everything before the rate, trimmed
    if m:
        unit = t[: m.start()].strip()
    else:
        unit = t
    # Trim trailing dashes / parentheticals from rate
    rate = rate.replace(" ", "")
    return unit, rate


def _parse_tariff_lines(lines: list[str]) -> tuple[list[dict], list[dict]]:
    """Return (eight_digit_rows, prefix_rows). prefix_rows have keys prefix,length,description."""
    eight: list[dict] = []
    prefixes: list[dict] = []

    cur8: dict | None = None
    cur_prefix: dict | None = None

    def _flush8():
        nonlocal cur8
        if cur8 is None:
            return
        # Final tidy: collapse whitespace in description
        cur8["description_statutory"] = re.sub(r"\s+", " ", cur8["description_statutory"]).strip()
        eight.append(cur8)
        cur8 = None

    def _flush_prefix():
        nonlocal cur_prefix
        if cur_prefix is None:
            return
        cur_prefix["description_statutory"] = re.sub(
            r"\s+", " ", cur_prefix["description_statutory"]
        ).strip()
        prefixes.append(cur_prefix)
        cur_prefix = None

    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        # Underscore separator rows
        if set(line) <= {"_", " "}:
            continue
        # Roman numeral / part headings like "I. SATURATED..." — keep as continuation if inside a prefix
        m8 = _CTH8_RE.match(line)
        if m8:
            _flush8()
            _flush_prefix()
            code = m8.group(1) + m8.group(2) + m8.group(3)
            rest = line[m8.end():].strip()
            # Strip leading "---" or "-" markers
            rest = re.sub(r"^[-–—]+\s*", "", rest)
            # The line ends with unit + rate + optional preferential marker
            # Try to peel rate from the right
            unit, rate = "", ""
            # Find rate via search from right
            mr = list(_RATE_RE.finditer(rest))
            desc = rest
            if mr:
                last = mr[-1]
                pre = rest[: last.start()].rstrip()
                rate = last.group(0).replace(" ", "")
                # tail after rate (preferential), drop
                # Now split pre into desc + unit by checking last token
                tokens = pre.rsplit(None, 1)
                if len(tokens) == 2 and tokens[1] in _UNIT_TOKENS:
                    desc, unit = tokens[0], tokens[1]
                elif len(tokens) == 2 and re.match(r"^[a-zA-Z./]{1,6}$", tokens[1]):
                    desc, unit = tokens[0], tokens[1]
                else:
                    desc = pre
            cur8 = {
                "cth": code,
                "description_statutory": desc,
                "bcd_statutory": rate,
                "unit_statutory": unit,
            }
            continue

        m6 = _CTH6_RE.match(line)
        if m6:
            _flush8()
            _flush_prefix()
            prefix = m6.group(1) + m6.group(2)
            rest = line[m6.end():].strip()
            rest = re.sub(r"^[-–—]+\s*", "", rest)
            # Drop trailing rate / unit if any (rare for 6-digit parents but possible)
            mr = list(_RATE_RE.finditer(rest))
            if mr:
                rest = rest[: mr[-1].start()].rstrip()
            cur_prefix = {"prefix": prefix, "length": 6, "description_statutory": rest}
            continue

        m4 = _CTH4_RE.match(line)
        if m4:
            _flush8()
            _flush_prefix()
            prefix = m4.group(1)
            rest = line[m4.end():].strip()
            rest = re.sub(r"^[-–—]+\s*", "", rest)
            mr = list(_RATE_RE.finditer(rest))
            if mr:
                rest = rest[: mr[-1].start()].rstrip()
            cur_prefix = {"prefix": prefix, "length": 4, "description_statutory": rest}
            continue

        # Continuation line — appends to whichever record is current.
        if cur8 is not None:
            cur8["description_statutory"] += " " + line
        elif cur_prefix is not None:
            cur_prefix["description_statutory"] += " " + line
        # else: stray text outside any record — drop

    _flush8()
    _flush_prefix()

    # Filter: drop prefix entries whose description is empty
    prefixes = [p for p in prefixes if p["description_statutory"]]
    return eight, prefixes


def parse_tariff_pdfs(tariff_dir: pathlib.Path, out_dir: pathlib.Path) -> dict:
    tariff_dir = pathlib.Path(tariff_dir)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    chapter_rows: list[dict] = []
    item_rows: list[dict] = []
    prefix_rows: list[dict] = []
    per_chapter_status: list[dict] = []
    failures: list[str] = []

    pdfs = sorted(tariff_dir.glob("*.pdf"))
    for pdf_path in pdfs:
        meta = _filename_meta(pdf_path)
        if meta is None:
            failures.append(f"{pdf_path.name}: filename does not match S{{n}} C{{n}}.pdf")
            continue
        section_no, chapter_no = meta

        try:
            with pdfplumber.open(pdf_path) as pdf:
                pages_text = _extract_pages_text(pdf)
        except Exception as exc:
            failures.append(f"{pdf_path.name}: open/extract failed: {exc!r}")
            chapter_rows.append({
                "chapter_no": chapter_no, "section_no": section_no,
                "title": "", "chapter_notes": "", "section_notes": "",
                "raw_text": "", "parse_status": "table_extract_failed",
                "n_items_extracted": 0,
            })
            per_chapter_status.append({
                "chapter_no": chapter_no, "section_no": section_no,
                "parse_status": "table_extract_failed", "n_items": 0,
                "file": pdf_path.name,
            })
            continue

        full = _full_text(pages_text)
        non_ws = sum(1 for c in full if not c.isspace())
        parse_status = "ok"
        if non_ws < 500:
            parse_status = "insufficient_text"

        title = _find_chapter_title(pages_text, chapter_no) if full else ""
        chapter_notes, sub_notes = _extract_notes_blocks(full) if full else ("", "")
        tariff_lines = _isolate_table_lines(full) if full else []
        items, prefixes = _parse_tariff_lines(tariff_lines)

        if parse_status == "ok" and not items:
            parse_status = "table_extract_failed"

        for it in items:
            it["chapter_no"] = chapter_no
            it["section_no"] = section_no
        for pr in prefixes:
            pr["chapter_no"] = chapter_no

        item_rows.extend(items)
        prefix_rows.extend(prefixes)

        chapter_rows.append({
            "chapter_no": chapter_no,
            "section_no": section_no,
            "title": title,
            "chapter_notes": chapter_notes,
            "section_notes": "",
            "raw_text": full,
            "parse_status": parse_status,
            "n_items_extracted": len(items),
        })
        per_chapter_status.append({
            "chapter_no": chapter_no, "section_no": section_no,
            "parse_status": parse_status, "n_items": len(items),
            "file": pdf_path.name,
        })

    chapters_df = pd.DataFrame(chapter_rows).sort_values("chapter_no").reset_index(drop=True)
    items_df = pd.DataFrame(item_rows)
    if not items_df.empty:
        items_df = items_df[[
            "cth", "description_statutory", "bcd_statutory", "unit_statutory",
            "chapter_no", "section_no",
        ]]
        # Deduplicate (cth, chapter_no): keep first occurrence
        items_df = items_df.drop_duplicates(subset=["cth"], keep="first").reset_index(drop=True)
    prefix_df = pd.DataFrame(prefix_rows)
    if not prefix_df.empty:
        prefix_df = prefix_df[["prefix", "length", "description_statutory", "chapter_no"]]
        prefix_df = prefix_df.drop_duplicates(subset=["prefix"], keep="first").reset_index(drop=True)

    chapters_path = out_dir / "tariff_chapters.parquet"
    items_path = out_dir / "_tariff_items.parquet"
    prefix_path = out_dir / "_tariff_subheading_index.parquet"
    val_path = out_dir / "_tariff_validation.json"

    chapters_df.to_parquet(chapters_path, index=False)
    items_df.to_parquet(items_path, index=False)
    prefix_df.to_parquet(prefix_path, index=False)

    stats = {
        "chapters_parsed": int(len(chapters_df)),
        "total_items": int(len(items_df)),
        "total_prefix_entries": int(len(prefix_df)),
        "parse_failures": failures,
        "non_ok_chapters": [
            r for r in per_chapter_status if r["parse_status"] != "ok"
        ],
    }
    val_path.write_text(json.dumps({
        "stats": {k: v for k, v in stats.items() if k != "non_ok_chapters"},
        "per_chapter": per_chapter_status,
        "non_ok_chapters": stats["non_ok_chapters"],
    }, indent=2), encoding="utf-8")

    return stats


def _spot_check(items_path: pathlib.Path) -> None:
    df = pd.read_parquet(items_path)
    print("\n--- Spot check (chapters 1, 29, 84, 87, 95) ---")
    for ch in [1, 29, 84, 87, 95]:
        sub = df[df["chapter_no"] == ch]
        if sub.empty:
            print(f"  chapter {ch}: NO ROWS")
            continue
        sample = sub.head(1)
        for _, row in sample.iterrows():
            print(f"  ch{ch:02d}  {row['cth']}  unit={row['unit_statutory']!r}  "
                  f"rate={row['bcd_statutory']!r}  desc={row['description_statutory'][:80]!r}")


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[3]
    tariff_dir = root / "data" / "Tariff"
    out_dir = root / "data" / "enriched"
    print(f"[tariff] tariff_dir = {tariff_dir}")
    print(f"[tariff] out_dir    = {out_dir}")

    stats = parse_tariff_pdfs(tariff_dir, out_dir)
    print("\n=== Tariff parsing summary ===")
    print(f"  Chapters parsed       : {stats['chapters_parsed']}")
    print(f"  8-digit items         : {stats['total_items']}")
    print(f"  Prefix entries (4/6d) : {stats['total_prefix_entries']}")
    if stats["parse_failures"]:
        print(f"  Parse-time errors     :")
        for f in stats["parse_failures"]:
            print(f"    - {f}")
    if stats["non_ok_chapters"]:
        print(f"  Chapters NOT 'ok'     :")
        for r in stats["non_ok_chapters"]:
            print(f"    - C{r['chapter_no']:02d} S{r['section_no']:02d} "
                  f"({r['file']}): {r['parse_status']} (n_items={r['n_items']})")
    else:
        print("  All chapters parsed with status 'ok'.")

    _spot_check(out_dir / "_tariff_items.parquet")

    if stats["total_items"] < 10000:
        print(f"\nFAIL: extracted only {stats['total_items']} 8-digit items (< 10000).")
        return 2
    print("\nOK: passed minimum-rows assertion (>= 10000 items).")
    print(f"Wrote: {out_dir / 'tariff_chapters.parquet'}")
    print(f"Wrote: {out_dir / '_tariff_items.parquet'}")
    print(f"Wrote: {out_dir / '_tariff_subheading_index.parquet'}")
    print(f"Wrote: {out_dir / '_tariff_validation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
