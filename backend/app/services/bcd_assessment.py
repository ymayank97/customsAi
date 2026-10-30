"""Deterministic BCD agreement and fraud-review priority, not probabilities."""
import math

RATE_TOLERANCE = 0.01 + 1e-9


def _rate(value):
    try:
        number = float(value)
        return number if not isinstance(value, bool) and math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def assess(row):
    final = row.get("final") or {}
    resolution = row.get("rate_resolution") or {}
    flags = row.get("review_flags") or []
    declared = _rate(row.get("declared_bcd"))
    reference = _rate(final.get("bcd_final"))
    code = str(final.get("cth_final") or "")
    classification_verified = len(code) == 8 and code.isdigit() and not final.get("manual_queue", False)
    rate_verified = (classification_verified and reference is not None
                     and resolution.get("bcd_basis") == "statutory"
                     and "rate_requires_review" not in flags)
    mismatch = bool(row.get("declared_cth") and code and row["declared_cth"] != code)
    declared_code = str(row.get("declared_cth") or "")
    incomplete_declaration = len(declared_code) != 8 or not declared_code.isdigit()
    result = {"score": None, "status": "unverified", "provided_rate": declared,
              "reference_rate": reference if rate_verified else None,
              "difference_pp": None, "verified": False, "requires_review": True,
              "fraud_review_priority": "Review required", "potential_underpayment": None,
              "reason": "BCD comparison is unverified."}
    if not classification_verified:
        result["reason"] = "An accepted eight-digit classification is needed to verify the BCD rate."
        return result
    if not rate_verified:
        result["reason"] = "Applicable BCD rate or notification conditions have not been verified."
        return result
    if declared is None:
        result["reason"] = "The provided BCD rate is missing or invalid."
        return result

    difference = reference - declared
    matches = abs(difference) <= RATE_TOLERANCE
    # Symmetric rate agreement: 5 vs 10 and 10 vs 5 both score 50, but only
    # the former indicates potential underpayment. Equal zero rates score 100.
    score = 100.0 if matches else 100 * (1 - abs(difference) / max(reference, declared))
    result.update(score=round(max(0.0, min(100.0, score)), 1), verified=True,
                  difference_pp=round(difference, 6), potential_underpayment=difference > RATE_TOLERANCE)
    if difference > RATE_TOLERANCE:
        result.update(status="under_declared", fraud_review_priority="High",
                      reason=f"Provided BCD is {difference:g} percentage points below the verified reference; potential underpayment requires investigation.")
    elif difference < -RATE_TOLERANCE:
        result.update(status="over_declared", fraud_review_priority="Low",
                      reason=f"Provided BCD is {-difference:g} percentage points above the verified reference; this does not indicate BCD underpayment.")
    else:
        result.update(status="match", fraud_review_priority="Medium" if mismatch else "Low",
                      reason="BCD matches the verified reference." + (" The declared CTH differs and needs review." if mismatch else ""))
        if incomplete_declaration:
            result.update(fraud_review_priority="Review required",
                          reason="BCD matches the reference, but the declared eight-digit CTH is missing or incomplete.")
    result["requires_review"] = not matches or mismatch or bool(flags) or incomplete_declaration
    return result


def attach(row):
    """Recalculate for historical results as well as newly processed rows."""
    return {**row, "bcd_assessment": assess(row)}
