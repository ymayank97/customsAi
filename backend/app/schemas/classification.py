"""Audit discrepancy schema."""
from typing import Literal
from pydantic import BaseModel


class FraudFlag(BaseModel):
    is_fraud: bool
    flag_type: Literal["", "CTH Mismatch", "BCD Under-declared", "BCD Over-declared", "Both"] = ""
    revenue_at_risk: float | None = None
    detail: str = ""
