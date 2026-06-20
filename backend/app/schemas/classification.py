"""Pydantic schemas for classification + fraud detection."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    id: str
    cth: str = Field(description="8-digit CTH code")
    bcd_rate: Optional[float] = Field(default=None, description="Effective BCD rate as percentage")
    reasoning: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    # Hierarchical breakdown — populated by the hierarchical CoT prompt.  All
    # default to derived/empty values for backward compatibility.
    chapter: str = Field(default="", description="2-digit chapter code")
    heading: str = Field(default="", description="4-digit heading code")
    discriminator: str = Field(default="", description="Legal rule that picked this heading/sub-heading")
    is_residuary: bool = Field(default=False, description="True when the 8-digit code is the 'other' fallback")
    attributes: dict = Field(default_factory=dict,
                              description="Item attributes extracted from the description (function, voltage, material, end-use, etc.)")
    # Stage 3 (GIR adjudicator) extensions.
    conformal_set: list[str] = Field(
        default_factory=list,
        description="Codes the adjudicator cannot rule out (conformal prediction set)")
    citations: list[str] = Field(
        default_factory=list,
        description="Pinpoint citations to heading/section/chapter note text supporting the choice")
    gir_path: str = Field(default="", description="The GIR 1->6 rule sequence walked to reach the code")


class TierResult(BaseModel):
    cth_final: str
    bcd_final: Optional[float]
    tier_used: str
    confidence: float
    reasoning: str = ""
    candidates: list[dict] = Field(default_factory=list)
    disagreement: str = ""


class FraudFlag(BaseModel):
    is_fraud: bool
    flag_type: Literal[
        "",
        "CTH Mismatch",
        "BCD Under-declared",
        "BCD Over-declared",
        "Both",
        "Notification misuse",
    ] = ""
    revenue_at_risk: Optional[float] = None
    detail: str = ""


class RowResult(BaseModel):
    row_idx: int
    declared_cth: str
    declared_bcd: Optional[float]
    item_description: str
    final: TierResult
    fraud: FraudFlag
