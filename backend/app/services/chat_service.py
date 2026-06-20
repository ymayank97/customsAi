"""Chat service: retrieval-grounded streaming Q&A.

Flow:
  1. Retrieve top-k CTH candidates for the user's question (and optional row context).
  2. Stream LLM tokens as SSE events.
  3. Emit a `candidates` event so the UI can render code chips.

Returns an Iterator[str] of SSE-formatted lines.
"""
from __future__ import annotations

import json
import time
from typing import Iterator

from backend.app.core.llm_client import get_classifier
from backend.app.core.logger import get as get_logger
from backend.app.services import retriever

log = get_logger(__name__)


def _sse(event: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def stream_chat(
    question: str,
    context: dict | None = None,
    top_k: int = 5,
) -> Iterator[str]:
    """Yield SSE-formatted chunks for the chat stream."""
    question = (question or "").strip()
    if not question:
        yield _sse("error", {"message": "empty question"})
        yield _sse("done", {})
        return

    # Build retrieval query — combine question with row context (if any)
    query = question
    if context and (desc := context.get("description")):
        query = f"{question} :: {desc[:200]}"

    t0 = time.time()
    try:
        cands = retriever.retrieve_candidates(query, top_k=top_k)
    except Exception as e:
        log.exception("chat retrieval failed")
        yield _sse("error", {"message": f"Retrieval failed: {e}"})
        yield _sse("done", {})
        return

    log.info("chat.retrieve",
             extra={"count": len(cands), "ms": int((time.time() - t0) * 1000)})

    # Emit candidates first so the UI can render the chips immediately.
    yield _sse("candidates", {"candidates": cands})

    # Stream LLM tokens.
    cls = get_classifier()
    try:
        for chunk in cls.chat_stream(question, cands):
            yield _sse("delta", {"text": chunk})
    except Exception as e:
        log.exception("chat stream failed")
        yield _sse("error", {"message": str(e)})

    yield _sse("done", {})
