"""Read-only precedent lookup for the pipeline's Stage 4 (contested-pair check).

When the pipeline narrows a Bill-of-Entry row to a contested pair of tariff
headings (e.g. ``8536`` vs ``8544``), it calls :func:`lookup_precedents` to
surface any CAAR / CESTAT / Supreme Court / CBEC classification ruling on point.

This module only READS ``data/precedent/precedent_index.pkl`` (built offline by
``backend.app.scripts.build_precedent_corpus``). No live web fetching happens at
runtime. The index is loaded once and cached.

Scoring blends:
    - exact overlap on the contested 4-digit headings (strong boost), and
    - TF-IDF cosine similarity over title+summary+legal_basis+codes for the
      free-text query.

Public surface::

    lookup_precedents(cth_a, cth_b=None, query_text="", k=5) -> list[dict]
"""
from __future__ import annotations

import pathlib
import pickle
import threading

from backend.app.core.text_cleaning import normalize_for_matching

INDEX_PATH = pathlib.Path("data/precedent/precedent_index.pkl")

# Weight for an exact contested-heading overlap, added on top of cosine sim.
_HEADING_BOOST = 1.0

_LOCK = threading.Lock()
_INDEX: dict | None = None


def _heading4(cth: str | int | None) -> str:
    """Return the 4-digit heading prefix of a CTH/code, or ''."""
    if cth is None:
        return ""
    digits = "".join(ch for ch in str(cth) if ch.isdigit())
    return digits[:4]


def _load_index() -> dict | None:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    with _LOCK:
        if _INDEX is not None:
            return _INDEX
        if not INDEX_PATH.exists():
            return None
        with open(INDEX_PATH, "rb") as fh:
            _INDEX = pickle.load(fh)
        return _INDEX


def _row_headings(row: dict) -> set[str]:
    out: set[str] = set()
    for h in row.get("headings", []) or []:
        hh = _heading4(h)
        if hh:
            out.add(hh)
    for c in row.get("cth_codes", []) or []:
        hh = _heading4(c)
        if hh:
            out.add(hh)
    oc = _heading4(row.get("outcome_cth"))
    if oc:
        out.add(oc)
    pair = row.get("competing_pair") or ""
    for part in str(pair).split("|"):
        hh = _heading4(part)
        if hh:
            out.add(hh)
    return out


def lookup_precedents(
    cth_a: str | int | None,
    cth_b: str | int | None = None,
    query_text: str = "",
    k: int = 5,
) -> list[dict]:
    """Return up to ``k`` precedent rulings relevant to a contested CTH pair.

    Args:
        cth_a: first contested code/heading (4- or 8-digit, any format).
        cth_b: optional second contested code/heading.
        query_text: free-text item description / dispute context (PII-free).
        k: max results.

    Returns:
        List of dicts (best first), each with the ruling fields plus a float
        ``score``. Empty list if the index is missing or nothing matches.
    """
    index = _load_index()
    if not index:
        return []

    rows: list[dict] = index["rows"]
    target = {h for h in (_heading4(cth_a), _heading4(cth_b)) if h}

    # TF-IDF cosine over the free-text query (+ codes as bare tokens).
    query_parts = [normalize_for_matching(query_text or "")]
    query_parts.extend(h for h in (_heading4(cth_a), _heading4(cth_b)) if h)
    query_doc = " ".join(p for p in query_parts if p).strip()

    cos = None
    if query_doc:
        from sklearn.metrics.pairwise import linear_kernel

        qv = index["vectorizer"].transform([query_doc])
        cos = linear_kernel(qv, index["matrix"]).ravel()

    scored: list[dict] = []
    for i, row in enumerate(rows):
        sim = float(cos[i]) if cos is not None else 0.0
        overlap = len(target & _row_headings(row)) if target else 0
        score = sim + _HEADING_BOOST * overlap
        if score <= 0.0:
            continue
        out = dict(row)
        out["score"] = round(score, 4)
        out["heading_overlap"] = overlap
        scored.append(out)

    scored.sort(key=lambda r: (r["score"], r.get("heading_overlap", 0)), reverse=True)
    return scored[:k]


__all__ = ["lookup_precedents"]
