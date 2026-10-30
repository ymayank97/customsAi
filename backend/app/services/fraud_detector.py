"""Compare supported classifications and known rates; abstention is no finding."""
from backend.app.schemas.classification import FraudFlag
from backend.app.services.bcd_assessment import RATE_TOLERANCE


def detect(declared_cth, declared_bcd, final_cth, final_bcd, assess_value=None):
    if not final_cth:
        return FraudFlag(is_fraud=False, detail="Classification requires review")
    mismatch = bool(declared_cth and declared_cth != final_cth)
    under = declared_bcd is not None and final_bcd is not None and final_bcd - declared_bcd > RATE_TOLERANCE
    over = declared_bcd is not None and final_bcd is not None and declared_bcd - final_bcd > RATE_TOLERANCE
    revenue = round((final_bcd - declared_bcd) / 100 * assess_value, 2) if under and assess_value is not None else None
    flag = "Both" if under and mismatch else "BCD Under-declared" if under else "CTH Mismatch" if mismatch else "BCD Over-declared" if over else ""
    return FraudFlag(is_fraud=under, flag_type=flag, revenue_at_risk=revenue,
                     detail="Potential discrepancy for auditor verification" if flag else "")
