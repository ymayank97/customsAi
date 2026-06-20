"""Resolve applicable BCD from ``cth_rate_variants`` using condition vs description matching.

v1: rule-based Jaccard overlap on stemmed tokens + optional declared-notification
hint.  Concessional rates apply only when the tariff condition text matches the
item description; otherwise the statutory row wins.  Flags probable notification
misuse when the declared BCD matches a concessional rate the description does
not support.
"""
from __future__ import annotations

import math
import threading
from typing import Any

import pandas as pd

from backend.app.config import settings
from backend.app.core.text_cleaning import token_set_for_overlap

_LOCK = threading.RLock()
_VARIANTS_BY_CTH: dict[str, list[dict]] | None = None


def _maybe_float(v) -> float | None:
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


def _load_variants_by_cth() -> dict[str, list[dict]]:
    global _VARIANTS_BY_CTH
    with _LOCK:
        if _VARIANTS_BY_CTH is not None:
            return _VARIANTS_BY_CTH
        path = settings.data_dir / "cth_rate_variants.parquet"
        if not path.exists():
            _VARIANTS_BY_CTH = {}
            return _VARIANTS_BY_CTH
        df = pd.read_parquet(path)
        by_cth: dict[str, list[dict]] = {}
        for r in df.itertuples(index=False):
            cth = str(getattr(r, "cth", "")).strip()
            if not cth:
                continue
            row = {
                "cth": cth,
                "bcd_rate": _maybe_float(getattr(r, "bcd_rate", None)),
                "bcd_rate_text": str(getattr(r, "bcd_rate_text", "") or ""),
                "condition_code": str(getattr(r, "condition_code", "") or ""),
                "condition_text": str(getattr(r, "condition_text", "") or ""),
                "notification_id": getattr(r, "notification_id", None),
                "is_default": bool(getattr(r, "is_default", False)),
            }
            by_cth.setdefault(cth, []).append(row)
        _VARIANTS_BY_CTH = by_cth
        return _VARIANTS_BY_CTH


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return float(inter) / float(union) if union else 0.0


def _declared_ntfn_matches(
    declared_ntfn: str, notification_id: Any,
) -> bool:
    if not declared_ntfn or not notification_id:
        return False
    d = declared_ntfn.lower().strip()
    nid = str(notification_id).lower().strip()
    if not nid:
        return False
    return d in nid or nid in d


def resolve_rate(
    cth: str | None,
    item_description: str | None,
    *,
    declared_ntfn: str = "",
    declared_bcd: float | None = None,
    master_bcd_statutory: float | None = None,
    master_bcd_effective: float | None = None,
) -> dict[str, Any]:
    """Return applicable BCD for audit and misuse heuristics.

    Keys:
        ``bcd_audit`` — rate to compare against ``declared_bcd``
        ``bcd_basis`` — ``"statutory"`` | ``"concessional"`` | ``"master_fallback"``
        ``matched_variant`` — optional dict snapshot
        ``condition_scores`` — list of per-variant diagnostics
        ``misuse_suspected`` — declared rate aligns with a concessional row but
            condition match is below threshold
    """
    cth = (cth or "").strip()
    if not cth:
        return {
            "bcd_audit": declared_bcd,
            "bcd_basis": "master_fallback",
            "matched_variant": None,
            "condition_scores": [],
            "misuse_suspected": False,
            "detail": "No CTH; skipped rate resolution",
        }

    by_cth = _load_variants_by_cth()
    variants = by_cth.get(cth, [])
    thr = settings.condition_match_threshold

    if not variants:
        fb = master_bcd_effective if master_bcd_effective is not None else master_bcd_statutory
        return {
            "bcd_audit": fb,
            "bcd_basis": "master_fallback",
            "matched_variant": None,
            "condition_scores": [],
            "misuse_suspected": False,
            "detail": "No rate variants on disk; using master effective/statutory",
        }

    desc_tokens = token_set_for_overlap(item_description or "")
    declared_ntfn = (declared_ntfn or "").strip()

    statutory_rows = [v for v in variants if v.get("is_default") or str(v.get("condition_code") or "").upper() == "STATUTORY"]
    statutory_rate = None
    for v in statutory_rows:
        br = v.get("bcd_rate")
        if br is not None:
            statutory_rate = float(br)
            break
    if statutory_rate is None:
        statutory_rate = master_bcd_statutory

    scores_out: list[dict[str, Any]] = []
    best_conc: tuple[float, dict, float] | None = None  # (rate, variant, score)

    for v in variants:
        if v.get("is_default") or str(v.get("condition_code") or "").upper() == "STATUTORY":
            continue
        br = v.get("bcd_rate")
        if br is None:
            continue
        cond_tokens = token_set_for_overlap(v.get("condition_text") or "")
        # Strip generic boilerplate tokens from condition side
        cond_tokens = {t for t in cond_tokens if t not in {"first", "schedule", "statutory", "rate", "entry", "customs", "notification"}}
        jac = _jaccard(desc_tokens, cond_tokens)
        nt_boost = 0.15 if _declared_ntfn_matches(declared_ntfn, v.get("notification_id")) else 0.0
        combined = min(1.0, jac + nt_boost)
        scores_out.append({
            "condition_code": v.get("condition_code"),
            "bcd_rate": br,
            "jaccard": round(jac, 4),
            "combined_score": round(combined, 4),
            "notification_id": v.get("notification_id"),
        })
        if combined >= thr:
            rate_f = float(br)
            if statutory_rate is not None and rate_f < float(statutory_rate) - 1e-6:
                if best_conc is None or combined > best_conc[2] or (
                    abs(combined - best_conc[2]) < 1e-6 and rate_f < best_conc[0]
                ):
                    best_conc = (rate_f, v, combined)

    misuse = False
    if declared_bcd is not None and statutory_rate is not None:
        for v in variants:
            if v.get("is_default") or str(v.get("condition_code") or "").upper() == "STATUTORY":
                continue
            br = v.get("bcd_rate")
            if br is None:
                continue
            if abs(float(declared_bcd) - float(br)) > 0.02:
                continue
            cond_tokens = token_set_for_overlap(v.get("condition_text") or "")
            cond_tokens = {t for t in cond_tokens if t not in {"first", "schedule", "statutory", "rate", "entry", "customs", "notification"}}
            jac = _jaccard(desc_tokens, cond_tokens)
            nt_boost = 0.15 if _declared_ntfn_matches(declared_ntfn, v.get("notification_id")) else 0.0
            combined = min(1.0, jac + nt_boost)
            if combined < thr and float(declared_bcd) + 1e-6 < float(statutory_rate):
                misuse = True
                break

    if best_conc is not None:
        rate, var, sc = best_conc
        return {
            "bcd_audit": rate,
            "bcd_basis": "concessional",
            "matched_variant": {
                "condition_code": var.get("condition_code"),
                "bcd_rate": var.get("bcd_rate"),
                "notification_id": var.get("notification_id"),
                "match_score": round(sc, 4),
            },
            "condition_scores": scores_out,
            "misuse_suspected": misuse,
            "detail": f"Concessional rate {rate}% applies (condition match {sc:.2f})",
        }

    fb = statutory_rate
    if fb is None:
        fb = master_bcd_effective if master_bcd_effective is not None else master_bcd_statutory

    return {
        "bcd_audit": fb,
        "bcd_basis": "statutory",
        "matched_variant": None,
        "condition_scores": scores_out,
        "misuse_suspected": misuse,
        "detail": "Statutory schedule rate — no concessional condition matched the description",
    }


def apply_resolution_to_master_bcd(
    resolution: dict[str, Any],
    master_bcd_effective: float | None,
    master_bcd_statutory: float | None,
) -> float | None:
    """Prefer resolver output; fall back to master fields."""
    aud = resolution.get("bcd_audit")
    if aud is not None:
        return float(aud)
    if master_bcd_effective is not None:
        return float(master_bcd_effective)
    if master_bcd_statutory is not None:
        return float(master_bcd_statutory)
    return None
