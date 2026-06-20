"""Validated request bodies for public API routes."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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


class FeedbackSubmitBody(BaseModel):
    job_id: str | None = None
    row_idx: int = 0
    item_description: str = Field(default="", max_length=8000)
    declared_cth: str | None = Field(default=None, max_length=32)
    predicted_cth: str | None = Field(default=None, max_length=32)
    predicted_bcd: float | None = None
    corrected_cth: str = Field(..., min_length=1, max_length=32)
    corrected_bcd: float | None = None
    reasoning: str | None = Field(default=None, max_length=8000)
