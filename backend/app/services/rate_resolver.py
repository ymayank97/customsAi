"""Conservative rate lookup. Text similarity cannot verify a concession."""
from __future__ import annotations

import math


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError):
        return None


def resolve_rate(master: dict, declared_notification=""):
    statutory = _number(master.get("bcd_statutory"))
    effective = _number(master.get("bcd_effective"))
    conditional = bool(declared_notification or master.get("applicable_notifications")
                       or master.get("bcd_notifications_applied") or master.get("bcd_ntfn"))
    # Conditional, specific or unavailable rates need a reviewer. Never infer
    # exemption eligibility from keywords, component type or importer claims.
    known = statutory is not None and not conditional and (effective is None or effective == statutory)
    return {"bcd_audit": statutory if known else None,
            "bcd_basis": "statutory" if known else "manual_review",
            "detail": "Unconditional master rate" if known else "Rate or notification conditions require verification"}
