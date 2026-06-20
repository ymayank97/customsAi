"""CUSTADA validator (Tier 4): override hallucinated CTHs/BCDs with master ground truth.

The cth_master.parquet is the authoritative source of CTHs and effective rates.
If an LLM proposes a CTH that doesn't exist in the master, flag it as a hallucination
and fall back to the best retrieval candidate.
"""
from __future__ import annotations

import math
import threading

import pandas as pd

from backend.app.config import settings

_LOCK = threading.RLock()
_CACHE: dict | None = None
_NTFN_CACHE: dict[str, dict] | None = None


def _maybe_float(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        f = float(v)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _maybe_str(v) -> str:
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _maybe_list(v) -> list[str]:
    if v is None:
        return []
    try:
        if pd.isna(v):
            return []
    except (TypeError, ValueError):
        pass
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, tuple):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _load_notifications() -> dict[str, dict]:
    global _NTFN_CACHE
    with _LOCK:
        if _NTFN_CACHE is None:
            path = settings.data_dir / "notifications.parquet"
            if not path.exists():
                _NTFN_CACHE = {}
            else:
                df = pd.read_parquet(path)
                by_id: dict[str, dict] = {}
                for r in df.itertuples(index=False):
                    nid = _maybe_str(getattr(r, "ntfn_id", ""))
                    if not nid:
                        continue
                    by_id[nid] = {
                        "id": nid,
                        "title": _maybe_str(getattr(r, "title", "")),
                        "date": _maybe_str(getattr(r, "ntfn_date", "")),
                        "type": _maybe_str(getattr(r, "ntfn_type", "")),
                        "effective_date": _maybe_str(getattr(r, "effective_date_override", "")),
                    }
                _NTFN_CACHE = by_id
    return _NTFN_CACHE


def get_notification_details(notification_ids: list[str]) -> list[dict]:
    ntfns = _load_notifications()
    details: list[dict] = []
    seen: set[str] = set()
    for nid in notification_ids:
        nid = str(nid).strip()
        if not nid or nid in seen:
            continue
        seen.add(nid)
        details.append(ntfns.get(nid, {"id": nid, "title": "", "date": "", "type": "", "effective_date": ""}))
    return details


def _load_master() -> dict:
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            df = pd.read_parquet(settings.data_dir / "cth_master.parquet")
            cths: set[str] = set(df["cth"].astype(str).tolist())
            by_cth: dict[str, dict] = {}
            for r in df.itertuples(index=False):
                applicable = _maybe_list(getattr(r, "applicable_notifications", []))
                applied = _maybe_list(getattr(r, "bcd_notifications_applied", []))
                by_cth[r.cth] = {
                    "cth": r.cth,
                    "chapter": _maybe_str(getattr(r, "chapter", "")) or str(r.cth)[:2],
                    "chapter_no": int(getattr(r, "chapter_no", int(str(r.cth)[:2]))),
                    "chapter_title": _maybe_str(getattr(r, "chapter_title", "")),
                    "chapter_notes": _maybe_str(getattr(r, "chapter_notes", "")),
                    "description_combined": _maybe_str(getattr(r, "description_combined", "")),
                    "description_statutory": _maybe_str(getattr(r, "description_statutory", "")),
                    "description": _maybe_str(getattr(r, "description", "")),
                    "heading_text": _maybe_str(getattr(r, "heading_text", "")),
                    "subheading_text": _maybe_str(getattr(r, "subheading_text", "")),
                    "unit": _maybe_str(getattr(r, "unit", "")) or _maybe_str(getattr(r, "unit_statutory", "")),
                    "unit_statutory": _maybe_str(getattr(r, "unit_statutory", "")),
                    "bcd_statutory": _maybe_float(r.bcd_statutory),
                    "bcd_effective": _maybe_float(r.bcd_effective),
                    "bcd_sch": _maybe_str(getattr(r, "bcd_sch", "")),
                    "bcd_ntfn": _maybe_str(getattr(r, "bcd_ntfn", "")),
                    "bcd_notifications_applied": applied,
                    "import_policy": _maybe_str(getattr(r, "import_policy", "")),
                    "export_policy": _maybe_str(getattr(r, "export_policy", "")),
                    "notes_text": _maybe_str(getattr(r, "notes_text", "")),
                    "applicable_notifications": applicable,
                    "notification_details": get_notification_details(applicable),
                }
            _CACHE = {"cths": cths, "by_cth": by_cth}
    return _CACHE


def cth_exists(cth: str) -> bool:
    if not cth or not isinstance(cth, str):
        return False
    return cth.strip() in _load_master()["cths"]


def get_master_row(cth: str) -> dict | None:
    return _load_master()["by_cth"].get(str(cth).strip())


def validate(proposed_cth: str, proposed_bcd: float | None,
             fallback_cth: str | None = None,
             candidate_cths: list[str] | None = None) -> dict:
    """Stage-4 hard validation against the CUSTADA master.

    HARD BLOCK: an invented CTH (one not present in ``cth_master``) is NEVER
    passed through as ``cth_final``. This structurally eliminates the
    "85442010"-class bug — a recommended code that does not exist in the
    tariff. Resolution order when the proposed CTH is invalid:

        1. ``fallback_cth`` if it is a real master code, else
        2. the first real code in ``candidate_cths`` (all of which were built
           from the master by Stage 1), else
        3. route to the manual queue: ``cth_final = ""`` and
           ``manual_queue = True``. We NEVER emit the invalid code.

    Returns: ``cth_final, bcd_final, override_reason, hallucination,
    manual_queue``. BCD is always overridden with the scheduled effective rate
    from the master when available.
    """
    proposed_cth = (proposed_cth or "").strip()
    fallback_cth = (fallback_cth or "").strip() if fallback_cth else None
    candidate_cths = [str(c).strip() for c in (candidate_cths or []) if str(c).strip()]

    if cth_exists(proposed_cth):
        master = get_master_row(proposed_cth)
        bcd_master = master.get("bcd_effective")
        if bcd_master is None:
            bcd_master = master.get("bcd_statutory")
        override_reason = ""
        if (proposed_bcd is not None and bcd_master is not None
                and abs(float(proposed_bcd) - float(bcd_master)) > 0.01):
            override_reason = f"BCD overridden: LLM proposed {proposed_bcd}%, master says {bcd_master}%"
        return {
            "cth_final": proposed_cth,
            "bcd_final": bcd_master if bcd_master is not None else proposed_bcd,
            "override_reason": override_reason,
            "hallucination": False,
            "manual_queue": False,
        }

    # --- Invalid / invented CTH: HARD BLOCK ---------------------------------
    # Find the best real substitute among supplied (master-built) codes.
    substitute = None
    if fallback_cth and cth_exists(fallback_cth):
        substitute = fallback_cth
    else:
        substitute = next((c for c in candidate_cths if cth_exists(c)), None)

    if substitute:
        master = get_master_row(substitute)
        return {
            "cth_final": substitute,
            "bcd_final": master.get("bcd_effective") or master.get("bcd_statutory"),
            "override_reason": (
                f"Proposed CTH {proposed_cth!r} is not in the CUSTADA master "
                f"(invalid/invented code, hard-blocked); replaced with best valid "
                f"candidate {substitute}."
            ),
            "hallucination": True,
            "manual_queue": False,
        }

    # No valid substitute anywhere — route to manual queue, NEVER emit the bad code.
    return {
        "cth_final": "",
        "bcd_final": None,
        "override_reason": (
            f"Proposed CTH {proposed_cth!r} is not in the CUSTADA master and no "
            f"valid candidate was available; routed to manual verification queue."
        ),
        "hallucination": True,
        "manual_queue": True,
    }
