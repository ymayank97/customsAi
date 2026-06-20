"""Stages 3-5: notification dedupe + filter, body extraction, Jumbo 50/2017 schedule parsing.

Public entries:
    dedupe_and_filter(ntfn_dir, out_path) -> stats
    parse_notifications(filelist_path, out_path) -> stats
    parse_jumbo_schedule(jumbo_pdf, out_dir) -> stats

Run as a module from project root with the venv Python to execute all three stages.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys
from typing import Any

import pandas as pd
import pdfplumber
from bs4 import BeautifulSoup

NTFN_DIR = pathlib.Path("data/notifications")
ENRICHED_DIR = pathlib.Path("data/enriched")
FILELIST_PATH = ENRICHED_DIR / "_ntfn_filelist.json"
NTFN_PARQUET = ENRICHED_DIR / "notifications.parquet"
JUMBO_PDF = NTFN_DIR / "C1-Jumbo Customs Notification-50-30.06.2017.pdf"
JUMBO_SCHED = ENRICHED_DIR / "jumbo_50_2017_schedule.parquet"
JUMBO_COND = ENRICHED_DIR / "jumbo_50_2017_conditions.parquet"
VALIDATION_PATH = ENRICHED_DIR / "_ntfn_validation.json"

# --- Stage 3: dedupe + filter --------------------------------------------------

# Dropped first because more specific (Cus(NT) is procedural, must be removed even though "Cus" matches)
_DROP_RE = re.compile(r"(?i)(Pol\.Cir|(?<!\w)PN-|CE\(NT\)|(?<!\w)CE-|^\d+-CE-|CEC-|Cus\(NT\)|InfrasCess|DeitY|^Order-|Press Release|Foreign Trade Bill|Customs Clearance|Custom Exchange Rates|CBEC Instruction|Anti-Dumping Duty Rules|^M&TP Act|^CUSTOMS_ACT|^CUSTOMS_TARIFF_ACT|Special CVD|WTO Bound Rates|Depreciation for Used Motor Vehicles|Clean Energy Cess on Coal|NCCD Incidence|Extract from Clause|Cus\.IV|^cst-|^S\d+ C\d+\.pdf|^\d+-SO-)")
_KEEP_RE = re.compile(r"(?i)(Cus|CBEC|IGST|CompCess|Jumbo)")
# Always-keep whitelist
_WHITELIST = {"C1-Jumbo Customs Notification-50-30.06.2017.pdf"}

# ntfn_id derivation
_DATE_RE = re.compile(r"(\d{2})[\.\-](\d{2})[\.\-](\d{4})")
_DATE_RE_LOOSE = re.compile(r"(\d{2})[\.\-](\d{2})[\.\-](\d{4})")
_NUM_RE_NTFN = re.compile(r"Ntfn-(\d+)")
_NUM_RE_LEAD = re.compile(r"^(\d{1,4})-")
_NUM_RE_CBEC = re.compile(r"(\d+)-CBEC", re.IGNORECASE)
_NUM_RE_JUMBO = re.compile(r"Notification-(\d+)", re.IGNORECASE)

_TYPE_PATTERNS = (
    ("CompCess", re.compile(r"CompCess", re.IGNORECASE)),
    ("IGST", re.compile(r"IGST", re.IGNORECASE)),
    ("CBEC", re.compile(r"CBEC", re.IGNORECASE)),
    ("Customs", re.compile(r"(Cus|Jumbo)", re.IGNORECASE)),  # default within "kept"
)


def _classify_type(name: str) -> str:
    for label, pat in _TYPE_PATTERNS:
        if pat.search(name):
            return label
    return "Customs"


def _extract_year(name: str) -> str | None:
    m = _DATE_RE.search(name)
    return m.group(3) if m else None


def _extract_number(name: str) -> str:
    """Best-effort notification number extraction."""
    m = _NUM_RE_JUMBO.search(name)
    if m:
        return m.group(1).zfill(3)
    m = _NUM_RE_NTFN.search(name)
    if m:
        return m.group(1).zfill(3)
    m = _NUM_RE_LEAD.match(name)
    if m:
        return m.group(1).zfill(3)
    m = _NUM_RE_CBEC.search(name)
    if m:
        return m.group(1).zfill(3)
    return "000"


def _make_ntfn_id(name: str) -> str:
    typ = _classify_type(name)
    num = _extract_number(name)
    yr = _extract_year(name) or "0000"
    return f"{typ}-{num}-{yr}"


def _strip_dup_marker(name: str) -> tuple[str, bool]:
    """Strip ' (1)' duplicate marker from filename. Returns (canonical, was_dup)."""
    stem = pathlib.Path(name).stem
    suffix = pathlib.Path(name).suffix
    new_stem = re.sub(r"\s*\(\d+\)\s*$", "", stem)
    return new_stem + suffix, new_stem != stem


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _filter_decision(name: str) -> tuple[bool, str | None]:
    """Return (keep, reason_if_dropped)."""
    if name in _WHITELIST:
        return True, None
    if _DROP_RE.search(name):
        return False, "drop_pattern"
    if _KEEP_RE.search(name):
        return True, None
    return False, "no_keep_match"


def dedupe_and_filter(ntfn_dir: pathlib.Path, out_path: pathlib.Path) -> dict:
    """Stage 3: dedupe (1) pairs, filter to rate-altering set, write filelist JSON."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in ntfn_dir.iterdir() if p.suffix.lower() in (".htm", ".html", ".pdf")])

    # Group by canonical name
    groups: dict[str, list[pathlib.Path]] = {}
    for p in files:
        canonical_name, _ = _strip_dup_marker(p.name)
        groups.setdefault(canonical_name, []).append(p)

    kept: list[dict] = []
    dropped: list[dict] = []
    diff_dups = 0

    for canonical_name, members in groups.items():
        keep, reason = _filter_decision(canonical_name)
        if not keep:
            for m in members:
                dropped.append({"path": str(m.resolve()), "reason": reason})
            continue
        # Pick canonical (member without "(1)") if present
        canonical_path: pathlib.Path | None = None
        dup_paths: list[pathlib.Path] = []
        for m in members:
            _, was_dup = _strip_dup_marker(m.name)
            if not was_dup:
                canonical_path = m
            else:
                dup_paths.append(m)
        if canonical_path is None:
            # No canonical — pick first dup as canonical
            canonical_path = dup_paths.pop(0)

        canonical_hash = _sha256(canonical_path)
        ntfn_id_base = _make_ntfn_id(canonical_path.name)
        entry = {
            "canonical_path": str(canonical_path.resolve()),
            "ntfn_id": ntfn_id_base,
            "kept_files": [str(canonical_path.resolve())],
            "content_sha256": canonical_hash,
            "dropped_reason": None,
        }
        kept.append(entry)

        for d in dup_paths:
            d_hash = _sha256(d)
            if d_hash == canonical_hash:
                entry["kept_files"].append(str(d.resolve()))
            else:
                # Keep as separate _v2
                diff_dups += 1
                kept.append({
                    "canonical_path": str(d.resolve()),
                    "ntfn_id": ntfn_id_base + "_v2",
                    "kept_files": [str(d.resolve())],
                    "content_sha256": d_hash,
                    "dropped_reason": None,
                })

    payload = {"kept": kept, "dropped": dropped}
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # Disambiguate duplicate ntfn_ids (e.g. multiple "CBEC-001-2000")
    seen: dict[str, int] = {}
    for entry in kept:
        nid = entry["ntfn_id"]
        seen[nid] = seen.get(nid, 0) + 1
    if any(c > 1 for c in seen.values()):
        counters: dict[str, int] = {}
        for entry in kept:
            nid = entry["ntfn_id"]
            if seen[nid] > 1:
                counters[nid] = counters.get(nid, 0) + 1
                entry["ntfn_id"] = f"{nid}-{counters[nid]}"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return {
        "input_files": len(files),
        "groups": len(groups),
        "kept_entries": len(kept),
        "dropped_files": len(dropped),
        "diff_duplicates": diff_dups,
    }


# --- Stage 4: parse notifications ----------------------------------------------

_WS_RE = re.compile(r"\s+")
_PRED_RE = re.compile(r"in supersession of [^.]{1,400}", re.IGNORECASE)
_PRED_NUM_RE = re.compile(r"\b(\d{1,3})/(\d{4})\b|\bNo\.\s*([\d/\-]+\d{4})\b", re.IGNORECASE)
_EFFECT_RE = re.compile(r"shall come into force on [^.]{1,200}", re.IGNORECASE)
_EFFECT_DATE_RE = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(?:day of\s+)?([A-Za-z]+),?\s+(\d{4})|(\d{2})[\.\-](\d{2})[\.\-](\d{4})")

_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05",
    "june": "06", "july": "07", "august": "08", "september": "09",
    "october": "10", "november": "11", "december": "12",
}


def _normalise(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _parse_htm(path: pathlib.Path) -> tuple[str, str | None, str]:
    raw = path.read_bytes().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "lxml")
    for t in soup(["style", "script"]):
        t.decompose()
    text = _normalise(soup.get_text(separator=" "))
    title = soup.title.string.strip() if soup.title and soup.title.string else None
    status = "ok" if len(text) > 200 else "low_text"
    return text, title, status


def _parse_pdf(path: pathlib.Path) -> tuple[str, str | None, str]:
    try:
        with pdfplumber.open(path) as pdf:
            chunks = [pg.extract_text() or "" for pg in pdf.pages]
        text = _normalise("\n".join(chunks))
        status = "ok" if len(text) > 200 else "low_text"
        return text, None, status
    except Exception as exc:  # pragma: no cover
        return f"[parse_error: {exc}]", None, "parse_error"


def _filename_meta(name: str) -> tuple[str, str, str | None]:
    typ = _classify_type(name)
    num = _extract_number(name)
    m = _DATE_RE.search(name)
    iso_date = None
    if m:
        dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
        iso_date = f"{yyyy}-{mm}-{dd}"
    return typ, num, iso_date


def _extract_predecessor_refs(body: str) -> list[str]:
    out: list[str] = []
    for m in _PRED_RE.finditer(body):
        seg = m.group(0)
        for nm in _PRED_NUM_RE.finditer(seg):
            ref = nm.group(1) or nm.group(3)
            if ref:
                yr = nm.group(2) or ""
                out.append(f"{ref}/{yr}".strip("/"))
    # dedupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for r in out:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def _extract_effective_override(body: str) -> str | None:
    m = _EFFECT_RE.search(body)
    if not m:
        return None
    seg = m.group(0)
    dm = _EFFECT_DATE_RE.search(seg)
    if not dm:
        return None
    if dm.group(1):
        day = dm.group(1).zfill(2)
        mon = _MONTHS.get(dm.group(2).lower())
        yr = dm.group(3)
        if mon:
            return f"{yr}-{mon}-{day}"
    elif dm.group(4):
        return f"{dm.group(6)}-{dm.group(5)}-{dm.group(4)}"
    return None


def parse_notifications(filelist_path: pathlib.Path, out_path: pathlib.Path) -> dict:
    payload = json.loads(filelist_path.read_text(encoding="utf-8"))
    rows: list[dict] = []
    failures: list[dict] = []
    for entry in payload["kept"]:
        path = pathlib.Path(entry["canonical_path"])
        ext = path.suffix.lower()
        try:
            if ext in (".htm", ".html"):
                body, title, status = _parse_htm(path)
            elif ext == ".pdf":
                body, title, status = _parse_pdf(path)
            else:
                body, title, status = "", None, "parse_error"
        except Exception as exc:
            body, title, status = f"[parse_error: {exc}]", None, "parse_error"

        if status == "parse_error" or status == "low_text":
            failures.append({"path": str(path), "ntfn_id": entry["ntfn_id"], "status": status})

        ntfn_type, ntfn_no, ntfn_date = _filename_meta(path.name)
        derived_title = title or body[:200]
        rows.append({
            "ntfn_id": entry["ntfn_id"],
            "ntfn_type": ntfn_type,
            "ntfn_no": ntfn_no,
            "ntfn_date": ntfn_date,
            "title": derived_title,
            "predecessor_refs": _extract_predecessor_refs(body),
            "effective_date_override": _extract_effective_override(body),
            "full_text": body,
            "source_path": str(path),
            "content_sha256": entry["content_sha256"],
            "parse_status": status,
        })
    df = pd.DataFrame(rows)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return {
        "rows": len(df),
        "ok": int((df["parse_status"] == "ok").sum()),
        "low_text": int((df["parse_status"] == "low_text").sum()),
        "parse_errors": int((df["parse_status"] == "parse_error").sum()),
        "failures": failures,
    }


# --- Stage 5: Jumbo schedule + conditions --------------------------------------

# Schedule row patterns — each line of interest starts with an S.No like "12.", "3AB", "21A.", "20B"
_SNO_RE = re.compile(r"^(\d{1,4}[A-Z]{0,3}\.?)\s+(.+)$")
# Captures rate-like tokens: "10%", "Nil", "Rs. 5/kg", "5", "30%", etc.
_RATE_RE = r"(?:Nil|nil|NIL|\d+(?:\.\d+)?\s*%|Rs\.?\s*\d+[\d/.,]*(?:/[a-zA-Z]+)?|-)"
# Tail captures up to 3 trailing tokens: bcd_rate, igst_rate, condition_no
_TAIL_RE = re.compile(rf"\s+({_RATE_RE})\s+({_RATE_RE})\s+(\S+)\s*$")
_TAIL_RATE_ONLY = re.compile(rf"\s+({_RATE_RE})\s*$")

_CTH_8 = re.compile(r"^\d{4}\s?\d{2}\s?\d{2}$")
_CTH_PREFIX = re.compile(r"^\d{2,8}$")
_HEADER_NOISE = re.compile(r"^(?:C1\s|Table|BIG|S\.?\s*No|Chapter\s|\(\d\)\s+\(\d\)|Heading|Description|Standard|Integrated|Condition|Provided|Explanation|\[)", re.IGNORECASE)

# Parse first-line of a schedule entry. The structure (after S.No):
#   <CTH or chapter list spanning some columns> <description start> <bcd_rate> <igst_rate> <condition_no>
# We extract trailing 3 tokens as bcd / igst / condition, then split CTH from description heuristically.

# CTH segment: digits with optional spaces, optionally repeated separated by commas/semicolons or "or"
_CTH_HEAD_RE = re.compile(
    r"^((?:\d{2,4}(?:\s\d{2})?(?:\s\d{2})?(?:\s*[,;]\s*\d{2,4}(?:\s\d{2})?(?:\s\d{2})?)*"
    r"|Any\s+chapter|Any\s+Chapter)"
    r"(?:\s+or\s+(?:any\s+other\s+chapter|\d{2,4}(?:\s\d{2})?(?:\s\d{2})?))?)\s+(.*)$"
)


def _split_cth(cth_raw: str) -> list[tuple[str, str | None, str | None]]:
    """Return list of (cth_or_chapter_raw, cth_8digit_or_none, prefix_or_none).
    Comma-list expands into multiple entries.
    """
    cth_raw = cth_raw.strip()
    if not cth_raw:
        return [("", None, None)]
    # Comma-separated list: split
    parts = [p.strip() for p in re.split(r"[;,]\s*|\s+or\s+", cth_raw) if p.strip()]
    out: list[tuple[str, str | None, str | None]] = []
    for p in parts:
        digits = re.sub(r"\s+", "", p)
        if re.fullmatch(r"\d{8}", digits):
            out.append((p, digits, None))
        elif re.fullmatch(r"\d{2,7}", digits):
            out.append((p, None, digits))
        else:
            # "Any chapter", "any other chapter", chapter-num
            out.append((p, None, None))
    if not out:
        out.append((cth_raw, None, None))
    return out


def _parse_schedule_line(line: str) -> dict | None:
    """Parse one composite schedule line; returns dict or None if not a row-start."""
    m = _SNO_RE.match(line)
    if not m:
        return None
    s_no = m.group(1).rstrip(".")
    rest = m.group(2).strip()

    # Strip trailing 3-token tail (rate rate condition) if present
    tail3 = _TAIL_RE.search(rest)
    bcd_rate = igst_rate = condition_no = None
    if tail3:
        bcd_rate = tail3.group(1)
        igst_rate = tail3.group(2)
        condition_no = tail3.group(3)
        rest = rest[: tail3.start()].rstrip()
    else:
        # Maybe only one rate column (description-only continuation later)
        tail1 = _TAIL_RATE_ONLY.search(rest)
        if tail1:
            bcd_rate = tail1.group(1)
            rest = rest[: tail1.start()].rstrip()

    # Split CTH portion from description
    cm = _CTH_HEAD_RE.match(rest)
    if cm:
        cth_part = cm.group(1).strip()
        desc_part = cm.group(2).strip()
    else:
        # Heuristic: take the leading digits/spaces tokens
        tokens = rest.split()
        idx = 0
        while idx < len(tokens) and (tokens[idx].rstrip(",;").replace(",", "").isdigit() or tokens[idx].lower() in ("any", "or", "chapter", "other")):
            idx += 1
        cth_part = " ".join(tokens[:idx]).rstrip(",;")
        desc_part = " ".join(tokens[idx:]).strip()

    return {
        "s_no": s_no,
        "cth_or_chapter": cth_part,
        "description": desc_part,
        "bcd_rate": bcd_rate,
        "igst_rate": igst_rate if igst_rate else "-",
        "condition_no": None if condition_no in (None, "-") else condition_no,
    }


def parse_jumbo_schedule(jumbo_pdf_path: pathlib.Path, out_dir: pathlib.Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    if not jumbo_pdf_path.exists():
        raise FileNotFoundError(f"Jumbo PDF not found: {jumbo_pdf_path}")

    rows: list[dict] = []
    last_row: dict | None = None
    raw_pages: list[tuple[int, str]] = []

    with pdfplumber.open(jumbo_pdf_path) as pdf:
        for page_no, pg in enumerate(pdf.pages):
            text = pg.extract_text() or ""
            raw_pages.append((page_no, text))
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("C1 –") or line.startswith("BIG"):
                    continue
                if _HEADER_NOISE.match(line):
                    # may still be a continuation if it's a "[" amendment marker — skip
                    continue
                parsed = _parse_schedule_line(line)
                if parsed is not None and parsed["bcd_rate"] is not None:
                    parsed["page_no"] = page_no
                    # Expand multi-CTH into multiple rows
                    splits = _split_cth(parsed["cth_or_chapter"])
                    for cth_raw, cth8, prefix in splits:
                        rows.append({
                            "s_no": parsed["s_no"],
                            "cth_or_chapter": cth_raw or parsed["cth_or_chapter"],
                            "cth": cth8,
                            "prefix": prefix,
                            "description": parsed["description"],
                            "bcd_rate": parsed["bcd_rate"],
                            "igst_rate": parsed["igst_rate"],
                            "condition_no": parsed["condition_no"],
                            "page_no": page_no,
                        })
                    last_row = parsed
                else:
                    # Continuation: append to last row's description if it doesn't look like an amendment marker
                    if last_row is not None and rows:
                        if line.startswith("[") or line.startswith("("):
                            continue
                        # Append to most recently appended group's description
                        last_s_no = rows[-1]["s_no"]
                        for r in rows:
                            if r["s_no"] == last_s_no and r["page_no"] == rows[-1]["page_no"]:
                                r["description"] = (r["description"] + " " + line).strip()

    # --- Conditions (Annexure not present in this PDF; harvest inline references)
    cond_rows: list[dict] = []
    seen_cond: set[str] = set()
    cond_inline_re = re.compile(r"\bCondition\s+(\d+[A-Z]?)\b[^\n]*", re.IGNORECASE)
    for page_no, text in raw_pages:
        for m in cond_inline_re.finditer(text):
            cn = m.group(1)
            snippet = m.group(0).strip()
            key = (cn, snippet[:80])
            if key in seen_cond:
                continue
            seen_cond.add(key)
            cond_rows.append({"condition_no": cn, "condition_text": snippet, "page_no": page_no})

    # Also harvest unique condition numbers referenced in schedule rows so the conditions list is at least populated
    referenced_cns = sorted({r["condition_no"] for r in rows if r["condition_no"] and re.fullmatch(r"\d+[A-Z]?", r["condition_no"])}, key=lambda s: (int(re.match(r"\d+", s).group()), s))
    for cn in referenced_cns:
        if not any(c["condition_no"] == cn for c in cond_rows):
            cond_rows.append({"condition_no": cn, "condition_text": f"[Referenced in schedule, full text in Annexure (not in this PDF)]", "page_no": -1})

    sched_df = pd.DataFrame(rows)
    cond_df = pd.DataFrame(cond_rows)

    sched_df.to_parquet(out_dir / "jumbo_50_2017_schedule.parquet", index=False)
    cond_df.to_parquet(out_dir / "jumbo_50_2017_conditions.parquet", index=False)

    pct_with_bcd = float((sched_df["bcd_rate"].notna() & (sched_df["bcd_rate"] != "")).mean()) if len(sched_df) else 0.0

    stats = {
        "schedule_rows": len(sched_df),
        "condition_rows": len(cond_df),
        "pct_with_bcd_rate": round(pct_with_bcd, 4),
        "unique_s_nos": int(sched_df["s_no"].nunique()) if len(sched_df) else 0,
        "schedule_path": str((out_dir / "jumbo_50_2017_schedule.parquet").resolve()),
        "conditions_path": str((out_dir / "jumbo_50_2017_conditions.parquet").resolve()),
    }

    # Sanity assertions (loud warnings; only schedule_rows is hard-fail since Annexure not in this PDF)
    warnings: list[str] = []
    if len(sched_df) < 700:
        warnings.append(f"FAIL: schedule_rows={len(sched_df)} < 700")
    if len(cond_df) < 80:
        warnings.append(f"WARN: condition_rows={len(cond_df)} < 80 (Annexure/Part II not in this PDF; only inline refs harvested)")
    if pct_with_bcd < 0.95:
        warnings.append(f"WARN: pct_with_bcd_rate={pct_with_bcd:.3f} < 0.95")

    stats["warnings"] = warnings
    if any(w.startswith("FAIL") for w in warnings):
        raise AssertionError("Jumbo parse sanity failed: " + "; ".join(warnings))
    return stats


# --- Runner --------------------------------------------------------------------

def main() -> int:
    ENRICHED_DIR.mkdir(parents=True, exist_ok=True)
    print("=== Stage 3: dedupe + filter ===")
    s3 = dedupe_and_filter(NTFN_DIR, FILELIST_PATH)
    print(json.dumps(s3, indent=2))

    print("\n=== Stage 4: parse notifications ===")
    s4 = parse_notifications(FILELIST_PATH, NTFN_PARQUET)
    print(json.dumps({k: v for k, v in s4.items() if k != "failures"}, indent=2))
    print(f"  failures: {len(s4['failures'])}")
    for f in s4["failures"][:10]:
        print(f"    - {f['ntfn_id']} [{f['status']}] {f['path']}")

    print("\n=== Stage 5: parse Jumbo 50/2017 schedule ===")
    s5 = parse_jumbo_schedule(JUMBO_PDF, ENRICHED_DIR)
    print(json.dumps(s5, indent=2))

    validation = {"stage3": s3, "stage4_summary": {k: v for k, v in s4.items() if k != "failures"},
                  "stage4_failures": s4["failures"], "stage5": s5}
    VALIDATION_PATH.write_text(json.dumps(validation, indent=2), encoding="utf-8")

    print(f"\nValidation written to: {VALIDATION_PATH.resolve()}")
    print(f"Filelist:              {FILELIST_PATH.resolve()}")
    print(f"Notifications:         {NTFN_PARQUET.resolve()}")
    print(f"Jumbo schedule:        {(ENRICHED_DIR / 'jumbo_50_2017_schedule.parquet').resolve()}")
    print(f"Jumbo conditions:      {(ENRICHED_DIR / 'jumbo_50_2017_conditions.parquet').resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
