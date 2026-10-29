"""Parse CUSTADA-IGST .xls files into a normalized parquet of 8-digit tariff rows.

Public entry: ``parse_custada(input_dir, output_path) -> stats dict``.
Forward-links Notes rows to the most recent preceding Tariff CTH, per CUSTADA convention.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any

import pandas as pd
import xlrd


# CUSTADA column positions (consistent across all 5 files for cols 0-5, 7-15)
COL_HS = 0
COL_DESC = 2
COL_UNIT = 3
COL_BCD_SCH = 4
COL_BCD_NTFN = 5
COL_IGST = 7
COL_SWS = 8
COL_IMP_POLICY = 12
COL_EXP_POLICY = 14
COL_REMARK = 15  # also holds the row-type label
COL_TYPE = 15

# header row contains "HS Code"; row-type label lives in COL_TYPE for data rows.
# For data rows COL_REMARK ("Remark" header) actually holds the type label; the
# real "Remark" content (rare) overlaps with the type label, so we leave remark blank.

_DIGIT_RE = re.compile(r"\D+")


def _clean_hs(raw: Any) -> str:
    """Strip non-digits. Returns the digits-only string (may be 0/4/6/8 chars)."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    return _DIGIT_RE.sub("", s)


def _cell(sheet: xlrd.sheet.Sheet, r: int, c: int) -> str:
    if c >= sheet.ncols:
        return ""
    v = sheet.cell_value(r, c)
    if v is None:
        return ""
    if isinstance(v, float):
        # xlrd surfaces ints as floats; keep numerics readable
        if v.is_integer():
            return str(int(v))
        return str(v)
    return str(v).strip()


def _classify(row_type: str, hs_digits: str) -> str:
    rt = row_type.strip().lower()
    if rt == "chapter":
        return "chapter"
    if rt == "notes":
        return "notes"
    if rt == "tariff":
        return "tariff8" if len(hs_digits) == 8 else "tariff_heading"
    return "other"


def _parse_one_file(path: pathlib.Path) -> tuple[list[dict], list[tuple[str, str]]]:
    """Return (tariff_records, orphan_notes) for a single .xls file.

    Each tariff record carries its description, duty fields, and accumulated notes_text.
    Orphan notes (Notes appearing before any Tariff8 row) are returned for logging.
    """
    wb = xlrd.open_workbook(str(path))
    sh = wb.sheet_by_index(0)

    records: list[dict] = []
    orphans: list[tuple[str, str]] = []
    current: dict | None = None

    for r in range(1, sh.nrows):  # skip header
        row_type = _cell(sh, r, COL_TYPE)
        hs_raw = _cell(sh, r, COL_HS)
        hs_digits = _clean_hs(hs_raw)
        kind = _classify(row_type, hs_digits)

        if kind == "tariff8":
            # finalize previous and start a new tariff record
            if current is not None:
                records.append(current)
            current = {
                "cth": hs_digits.zfill(8),
                "description": _cell(sh, r, COL_DESC),
                "unit": _cell(sh, r, COL_UNIT) or None,
                "bcd_sch": _cell(sh, r, COL_BCD_SCH) or None,
                "bcd_ntfn": _cell(sh, r, COL_BCD_NTFN) or None,
                "igst": _cell(sh, r, COL_IGST) or None,
                "sws": _cell(sh, r, COL_SWS) or None,
                "import_policy": _cell(sh, r, COL_IMP_POLICY) or None,
                "export_policy": _cell(sh, r, COL_EXP_POLICY) or None,
                "remark": None,  # COL_REMARK is overloaded with the type label in this dataset
                "_notes": [],
                "source_file": path.name,
            }
        elif kind == "notes":
            note_text = _cell(sh, r, COL_DESC)
            if not note_text:
                continue
            if current is None:
                orphans.append((path.name, note_text[:120]))
            else:
                current["_notes"].append(note_text)
        # tariff_heading / chapter / other: skip (not 8-digit; description-only headers)

    if current is not None:
        records.append(current)
    return records, orphans


def parse_custada(input_dir: pathlib.Path, output_path: pathlib.Path) -> dict:
    """Parse all CUSTADA .xls files under ``input_dir`` and write _custada_rows.parquet.

    Returns a stats dict with row counts and warnings.
    """
    input_dir = pathlib.Path(input_dir)
    output_path = pathlib.Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(input_dir.glob("*.xls"))
    if not files:
        raise FileNotFoundError(f"No .xls files in {input_dir}")

    all_records: list[dict] = []
    all_orphans: list[tuple[str, str]] = []
    per_file: dict[str, int] = {}
    for fp in files:
        recs, orphans = _parse_one_file(fp)
        per_file[fp.name] = len(recs)
        all_records.extend(recs)
        all_orphans.extend(orphans)

    # finalize notes_text and derived fields
    for rec in all_records:
        notes = rec.pop("_notes")
        rec["notes_text"] = "\n".join(n for n in notes if n).strip()
        rec["chapter"] = rec["cth"][:2]
        rec["heading"] = rec["cth"][:4]

    df = pd.DataFrame(all_records)

    # dedupe: same CTH may appear in chapter blocks split across files; keep first non-empty
    before_dedupe = len(df)
    df = df.sort_values(by=["cth", "notes_text"], key=lambda s: s.str.len() if s.name == "notes_text" else s, ascending=[True, False])
    df = df.drop_duplicates(subset=["cth"], keep="first").reset_index(drop=True)

    # enforce schema column order + dtypes
    cols = [
        "cth", "description", "unit", "bcd_sch", "bcd_ntfn", "igst", "sws",
        "import_policy", "export_policy", "remark", "notes_text",
        "chapter", "heading", "source_file",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols]
    for c in cols:
        if c == "notes_text":
            df[c] = df[c].fillna("").astype(str)
        else:
            df[c] = df[c].astype("string")

    df.to_parquet(output_path, index=False)

    stats = {
        "files_parsed": [str(f) for f in files],
        "per_file_tariff8_rows": per_file,
        "total_tariff8_rows_raw": before_dedupe,
        "total_tariff8_rows_deduped": int(len(df)),
        "rows_with_notes": int((df["notes_text"].str.len() > 0).sum()),
        "orphan_notes_count": len(all_orphans),
        "orphan_notes_sample": all_orphans[:5],
        "output_parquet": str(output_path.resolve()),
    }
    return stats


if __name__ == "__main__":
    stats = parse_custada(
        pathlib.Path("data/unenriched custada files"),
        pathlib.Path("data/enriched/_custada_rows.parquet"),
    )
    print(json.dumps(stats, indent=2, default=str))
