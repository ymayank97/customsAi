"""Stage 0 (enrichment) + Stage 1 (hierarchical candidate generation).

This module replaces the prototype's flat retrieval + chapter classifier with
the recall-oriented, deliberately cross-chapter candidate generator of the
Hierarchical Adversarial Verification pipeline.

Stage 0 — :func:`enrich_row` builds a PII-aware context object per BE row.
Importer / IEC / BE-number are carried for the audit report but are NEVER
placed in the candidate payload that reaches the LLM.

Stage 1 — :func:`generate_candidates` narrows the *actual* tariff tree
(chapter → heading → subheading → 8-digit item) using purely deterministic
lexical signals:

    - the lexical index (word/char TF-IDF + BM25, heading-first selection),
    - the audit alias index (normalized declared-description → cth), a weak
      recall booster mined from real Bills of Entry,
    - always-inject the declared CTH (tagged) when it exists in the master.

CRITICAL INVARIANT: every emitted candidate is a real ``cth`` present in
``cth_master``. Candidates are only ever *constructed from a master row*, so it
is structurally impossible to surface an invented code (the "85442010"-class
bug). The output spans multiple chapters (recall-oriented) and carries a
retrieval margin / ambiguity flag for Stage 5 routing.
"""
from __future__ import annotations

import threading

import pandas as pd

from backend.app.config import settings
from backend.app.core.logger import get as get_logger
from backend.app.core.text_cleaning import clean_description, normalize_for_matching
from backend.app.services.custada_validator import get_notification_details
from backend.app.services.lexical_index import get_lexical_index

log = get_logger(__name__)

_LOCK = threading.Lock()
_MASTER_CACHE = None
_ALIAS_CACHE: dict[str, list[str]] | None = None

# Stage 1 recall target: deliberately wider than the old TOP_K (8) so the
# candidate set spans competing chapters and the adjudicator can never be
# starved of the right code by a retrieval miss.
CANDIDATE_TARGET = max(10, settings.top_k + 4)


def _load_master():
    global _MASTER_CACHE
    with _LOCK:
        if _MASTER_CACHE is None:
            df = pd.read_parquet(settings.data_dir / "cth_master.parquet")
            df = df.set_index("cth")
            _MASTER_CACHE = df
    return _MASTER_CACHE


def _load_alias_index() -> dict[str, list[str]]:
    """normalized_description -> [cth, ...] from the audit alias index.

    Weak signal: these are DECLARED codes mined from real BEs, not ground
    truth, so they only widen recall — never decide the answer.
    """
    global _ALIAS_CACHE
    with _LOCK:
        if _ALIAS_CACHE is not None:
            return _ALIAS_CACHE
        path = settings.data_dir / "audit_alias_index.parquet"
        if not path.exists():
            _ALIAS_CACHE = {}
            return _ALIAS_CACHE
        df = pd.read_parquet(path)
        idx: dict[str, list[str]] = {}
        for r in df.itertuples(index=False):
            key = str(getattr(r, "normalized_description", "") or "").strip()
            cth = str(getattr(r, "cth", "") or "").strip()
            if not key or not cth:
                continue
            idx.setdefault(key, [])
            if cth not in idx[key]:
                idx[key].append(cth)
        _ALIAS_CACHE = idx
        return _ALIAS_CACHE


def _alias_candidates(query: str, limit: int = 6) -> list[str]:
    """Look up declared-code aliases for the query (exact + token-overlap)."""
    alias = _load_alias_index()
    if not alias:
        return []
    key = normalize_for_matching(query)
    if not key:
        return []
    out: list[str] = []
    if key in alias:
        out.extend(alias[key])
    if len(out) < limit:
        q_toks = set(key.split())
        if q_toks:
            scored: list[tuple[float, str]] = []
            for k, cths in alias.items():
                k_toks = set(k.split())
                if not k_toks:
                    continue
                ov = len(q_toks & k_toks) / len(q_toks | k_toks)
                if ov >= 0.6:
                    for c in cths:
                        scored.append((ov, c))
            scored.sort(reverse=True)
            for _, c in scored:
                if c not in out:
                    out.append(c)
    return out[:limit]


def _cell(row, name: str, default=None):
    return row.get(name, default) if hasattr(row, "get") else default


def _text(row, name: str) -> str:
    v = _cell(row, name, "")
    try:
        if v is None or pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    return str(v).strip()


def _float(row, name: str) -> float | None:
    v = _cell(row, name)
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _list(row, name: str) -> list[str]:
    v = _cell(row, name, [])
    if v is None:
        return []
    try:
        if pd.isna(v):
            return []
    except (TypeError, ValueError):
        pass
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


def _is_residuary(cth: str) -> bool:
    cth = (cth or "").strip()
    if len(cth) < 8:
        return False
    return cth[-2:] in {"00", "90", "99"}


def _hierarchy_for_row(row, leaf_desc: str) -> dict[str, str]:
    explicit_heading = _text(row, "heading_text")
    explicit_sub = _text(row, "subheading_text")
    if explicit_heading or explicit_sub:
        return {
            "heading_text": explicit_heading,
            "subheading_text": explicit_sub,
            "leaf_text": (leaf_desc or "").strip(),
        }
    raw = _text(row, "description_combined")
    if not raw:
        leaf = (leaf_desc or "").strip()
        return {"heading_text": leaf, "subheading_text": "", "leaf_text": leaf}
    parts = [p.strip(" :|") for p in raw.split("|")]
    parts = [p for p in parts if p]
    if not parts:
        leaf = (leaf_desc or "").strip()
        return {"heading_text": leaf, "subheading_text": "", "leaf_text": leaf}
    return {
        "heading_text": parts[0],
        "subheading_text": parts[-2] if len(parts) >= 3 else "",
        "leaf_text": (leaf_desc or parts[-1]).strip(),
    }


def _candidate_from_row(cth: str, row, *, retrieval_score: float, source: str) -> dict:
    """Build the enriched candidate dict from a master row.

    The single construction point for every candidate — guarantees the
    INVARIANT that candidates only ever describe real master CTHs.
    """
    desc = (
        _text(row, "description")
        or _text(row, "description_statutory")
        or _text(row, "description_combined")
    )
    applicable_notifications = _list(row, "applicable_notifications")
    applied_notifications = _list(row, "bcd_notifications_applied")
    bcd_statutory = _float(row, "bcd_statutory")
    bcd_effective = _float(row, "bcd_effective")
    hierarchy = _hierarchy_for_row(row, desc)
    return {
        "cth": cth,
        "description": desc,
        "chapter_no": int(_float(row, "chapter_no") or int(str(cth)[:2])),
        "chapter": str(cth)[:2],
        "heading": str(cth)[:4],
        "subheading_6": str(cth)[:6],
        "heading_text": hierarchy["heading_text"],
        "subheading_text": hierarchy["subheading_text"],
        "leaf_text": hierarchy["leaf_text"],
        "is_residuary": _is_residuary(cth),
        "chapter_title": _text(row, "chapter_title"),
        "chapter_notes": _text(row, "chapter_notes"),
        "unit": _text(row, "unit") or _text(row, "unit_statutory"),
        "import_policy": _text(row, "import_policy"),
        "export_policy": _text(row, "export_policy"),
        "bcd_statutory": bcd_statutory,
        "bcd_effective": bcd_effective,
        "bcd_sch": _text(row, "bcd_sch"),
        "bcd_ntfn": _text(row, "bcd_ntfn"),
        "bcd_notifications_applied": applied_notifications,
        "applicable_notifications": applicable_notifications,
        "notification_details": get_notification_details(applicable_notifications),
        "bcd_breakdown": {
            "statutory": bcd_statutory,
            "effective": bcd_effective,
            "schedule_text": _text(row, "bcd_sch"),
            "notification_text": _text(row, "bcd_ntfn"),
            "notification_source": applied_notifications,
        },
        "retrieval_score": float(retrieval_score),
        "source": source,
    }


def build_candidate_from_master(cth: str, source: str = "declared_cth") -> dict | None:
    """Construct a candidate directly from the master, bypassing retrieval.

    Returns ``None`` when the CTH isn't in the master — the caller MUST treat a
    ``None`` as "this code does not exist" rather than fabricating one.
    """
    cth = (cth or "").strip()
    if not cth:
        return None
    master = _load_master()
    if cth not in master.index:
        return None
    return _candidate_from_row(cth, master.loc[cth], retrieval_score=0.0, source=source)


def _minmax_norm_scores(pairs: list[tuple[str, float]]) -> dict[str, float]:
    """Per-query min-max normalise scores to [0, 1]; max-pool duplicate CTHs."""
    if not pairs:
        return {}
    vals = [s for _, s in pairs]
    lo, hi = min(vals), max(vals)
    out: dict[str, float] = {}
    for cth, s in pairs:
        v = 1.0 if hi - lo < 1e-12 else (s - lo) / (hi - lo)
        prev = out.get(cth)
        out[cth] = max(prev, v) if prev is not None else v
    return out


# --- Stage 0 -----------------------------------------------------------------

def enrich_row(row: dict, siblings: list[dict] | None = None) -> dict:
    """Stage 0: build a per-row context object.

    PII (importer / IEC / BE-number / assessable value) is preserved on the
    object for the audit report but kept OUT of the ``llm_payload`` that Stage 3
    serialises. ``siblings`` are other lines on the same BE (BE-level context);
    only their PII-free descriptions/CTHs travel to the model.
    """
    declared_cth = (row.get("declared_cth") or "").strip()
    in_master = declared_cth in _load_master().index if declared_cth else False
    sib_payload = []
    for s in (siblings or []):
        if s.get("row_idx") == row.get("row_idx"):
            continue
        sib_payload.append({
            "item_description": s.get("item_description", ""),
            "declared_cth": (s.get("declared_cth") or "").strip(),
        })
    return {
        "row_idx": row.get("row_idx"),
        "item_description": row.get("item_description", ""),
        "declared_cth": declared_cth,
        "declared_cth_in_master": in_master,
        "declared_bcd": row.get("declared_bcd"),
        "declared_notification": (row.get("ntfn_ref") or "").strip(),
        # PII — for the report, never serialised to the LLM.
        "importer": row.get("importer", ""),
        "be_no": row.get("be_no", ""),
        "assess_val": row.get("assess_val"),
        "siblings": sib_payload[:8],
    }


# --- Stage 1 -----------------------------------------------------------------

def generate_candidates(
    query: str,
    declared_cth: str | None = None,
    target: int | None = None,
) -> list[dict]:
    """Stage 1: recall-oriented, cross-chapter candidate generation.

    Returns up to ``target`` enriched candidates, every one a real master CTH.
    Sources, in order of trust: lexical hierarchical search → audit-alias recall
    booster → always-injected declared CTH (tagged). A retrieval margin /
    ambiguity flag is attached to each candidate for downstream routing.
    """
    target = target or CANDIDATE_TARGET
    master = _load_master()

    lex = get_lexical_index()
    # Pull a wide hierarchical set; lexical index handles heading-first +
    # residuary recall internally.
    scored = lex.search_hierarchical(
        query, top_k=target, chapter_pairs=None,
        pool_k=max(200, settings.retrieve_top_n),
    )

    seen: set[str] = set()
    out: list[dict] = []
    for cth, score in scored:
        if cth in seen or cth not in master.index:
            continue
        seen.add(cth)
        out.append(_candidate_from_row(cth, master.loc[cth],
                                       retrieval_score=score, source="lexical"))

    # Alias recall booster — declared-code exemplars from real BEs.
    for cth in _alias_candidates(query):
        if cth in seen or cth not in master.index:
            continue
        seen.add(cth)
        out.append(_candidate_from_row(cth, master.loc[cth],
                                       retrieval_score=0.0, source="alias"))

    # Always inject the declared CTH (tagged) so it is adjudicated head-to-head.
    declared_cth = (declared_cth or "").strip()
    if declared_cth and declared_cth not in seen and declared_cth in master.index:
        seen.add(declared_cth)
        out.append(_candidate_from_row(declared_cth, master.loc[declared_cth],
                                       retrieval_score=0.0, source="declared_cth"))

    out.sort(key=lambda c: c.get("retrieval_score") or 0.0, reverse=True)

    margin = None
    ambiguous = False
    if len(out) >= 2:
        s0 = float(out[0].get("retrieval_score") or 0.0)
        s1 = float(out[1].get("retrieval_score") or 0.0)
        margin = round(s0 - s1, 4)
        ambiguous = margin < settings.retrieval_rerank_margin_threshold
    elif len(out) == 1:
        margin = float(out[0].get("retrieval_score") or 0.0)

    chapters = sorted({c["chapter"] for c in out})
    for c in out:
        c["retrieval_margin"] = margin
        c["retrieval_ambiguous"] = ambiguous
        c["candidate_chapters"] = chapters
    return out


def retrieve_candidates(
    query: str,
    top_k: int = 8,
    retrieve_top_n: int | None = None,
    chapter_predictions: list[tuple[str, float]] | None = None,
) -> list[dict]:
    """Back-compat shim for ``reevaluate_one`` and tests — delegates to
    :func:`generate_candidates`. ``chapter_predictions`` is ignored (the
    chapter classifier was retired)."""
    return generate_candidates(query, target=max(top_k, CANDIDATE_TARGET))


def preload_retrieval_stack() -> None:
    """Eager-load heavy singletons so the first API request is not blocked."""
    from backend.app.services import legal_constraints

    _load_master()
    _load_alias_index()
    get_lexical_index()
    legal_constraints.preload()
