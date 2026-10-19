"""Validated request bodies for public API routes."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReevaluateBody(BaseModel):
    item_description: str = Field(..., min_length=1, max_length=8000)
    declared_cth: str | None = None
    declared_bcd: float | None = None
    row_idx: int = 0
    top_k: int | None = None
    strict: bool | None = None


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)
    context: dict[str, Any] | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    stream: bool = True


class ReviewSubmitBody(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    job_id: str = Field(min_length=1, max_length=32)
    row_idx: int = Field(ge=0)
    decision: Literal["confirmed", "corrected", "needs_information"]
    corrected_cth: str | None = Field(default=None, pattern=r"^[0-9]{8}$")
    corrected_bcd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    reference: str = Field(default="", max_length=2000)
    reasoning: str = Field(min_length=3, max_length=8000)
    product_details: str = Field(default="", max_length=8000)
