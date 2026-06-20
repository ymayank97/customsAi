"""Fraud detection: compare declared CTH/BCD against final validated values.

Revenue-policy rule (per auditor):
    - Only mark a row as fraud when the declared BCD is **lower** than the
      final validated BCD (the importer paid less duty than they should have).
    - A CTH mismatch alone, or BCD over-declared (paid more), is reported as
      an *informational* flag — `is_fraud=False` — and does not get highlighted
      as fraud in the UI / Excel.
"""
from __future__ import annotations

from backend.app.schemas.classification import FraudFlag


def detect(
    declared_cth: str | None,
    declared_bcd: float | None,
    final_cth: str,
    final_bcd: float | None,
    assess_value: float | None = None,
    *,
    notification_misuse: bool = False,
    misuse_detail: str = "",
    revenue_basis_valid: bool = False,
    revenue_basis_detail: str = "",
) -> FraudFlag:
    """Stage 5 revenue logic.

    ``revenue_basis_valid`` is the fix for the unsubstantiated-revenue bug: when
    the importer's low/zero declared rate has a *verified* legal basis (a
    matched concessional notification / FTA-CEPA condition, or an ITA-1 passive
    component), the duty differential is NOT a revenue finding — we never raise a
    finding against a legitimately bound rate. Stage 5 computes this basis from
    ``rate_resolver`` + the ITA-1 scope guard and passes it in here.
    """
    declared_cth = (declared_cth or "").strip()
    final_cth = (final_cth or "").strip()
    cth_mismatch = bool(declared_cth and declared_cth != final_cth)

    misuse_detail = (misuse_detail or "").strip()

    # Verified legal basis for the declared rate -> reclassification only, no
    # revenue finding (CTH mismatch still surfaced as informational below).
    if revenue_basis_valid:
        if cth_mismatch:
            return FraudFlag(
                is_fraud=False, flag_type="CTH Mismatch",
                detail=(
                    f"declared {declared_cth}, correct {final_cth}; declared rate has a "
                    f"verified basis ({revenue_basis_detail or 'concessional/FTA/ITA-1'}) "
                    f"— no revenue at risk."
                ),
            )
        return FraudFlag(
            is_fraud=False, flag_type="",
            detail=(
                f"Declared rate has a verified basis "
                f"({revenue_basis_detail or 'concessional/FTA/ITA-1'}); no revenue at risk."
            ),
        )

    # Notification misuse: declared concessional rate without supporting condition;
    # treat as revenue fraud when statutory / resolved rate is higher.
    if (
        notification_misuse
        and declared_bcd is not None
        and final_bcd is not None
        and declared_bcd + 0.01 < final_bcd
    ):
        rev = None
        if assess_value is not None:
            rev = round((final_bcd - declared_bcd) / 100.0 * float(assess_value), 2)
        detail = misuse_detail or (
            "Declared BCD matches a concessional notification rate but the item "
            "description does not appear to satisfy the tariff condition — "
            f"applicable rate treated as {final_bcd}%."
        )
        if cth_mismatch:
            detail = f"{detail} Declared CTH {declared_cth} vs final {final_cth}."
        return FraudFlag(
            is_fraud=True,
            flag_type="Notification misuse",
            revenue_at_risk=rev,
            detail=detail,
        )

    bcd_under = False
    bcd_over = False
    revenue_at_risk = None
    if declared_bcd is not None and final_bcd is not None:
        if abs(declared_bcd - final_bcd) > 0.01:
            if declared_bcd < final_bcd:
                bcd_under = True
                if assess_value is not None:
                    revenue_at_risk = round((final_bcd - declared_bcd) / 100.0 * float(assess_value), 2)
            else:
                bcd_over = True

    # Fraud only when the importer underpaid duty.
    if cth_mismatch and bcd_under:
        return FraudFlag(is_fraud=True, flag_type="Both",
                         revenue_at_risk=revenue_at_risk,
                         detail=f"declared {declared_cth} @ {declared_bcd}%, correct {final_cth} @ {final_bcd}%")
    if bcd_under:
        return FraudFlag(is_fraud=True, flag_type="BCD Under-declared",
                         revenue_at_risk=revenue_at_risk,
                         detail=f"declared {declared_bcd}% but correct rate is {final_bcd}%")
    # Informational: CTH differs but BCD is the same or higher — no revenue loss.
    if cth_mismatch:
        return FraudFlag(is_fraud=False, flag_type="CTH Mismatch",
                         detail=f"declared {declared_cth}, correct {final_cth} (BCD unchanged)")
    if bcd_over:
        return FraudFlag(is_fraud=False, flag_type="BCD Over-declared",
                         detail=f"declared {declared_bcd}% > correct {final_bcd}%")
    return FraudFlag(is_fraud=False)
