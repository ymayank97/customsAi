"""Hierarchical Adversarial Verification pipeline — orchestrates Stages 0-5.

Reframes BE auditing as adversarial verification rather than 12,000-way
classification: given a declared CTH + description, find the strongest
legally-supported alternative and compute the duty differential.

  Stage 0  enrichment      — per-row context object (PII kept out of LLM payload),
                             BE-level sibling lines attached.
  Stage 1  candidate gen    — recall-oriented, cross-chapter; every candidate is a
                             real master CTH (structurally no invented codes).
  Stage 2  legal constraints— Section/Chapter exclusion notes; hard prune +
                             legal docket attached for the adjudicator.
  Stage 3  GIR adjudicator  — Opus walks GIR 1->6 against heading terms + docket;
                             outputs chosen cth, conformal set, citations.
  Stage 4  precedent        — CAAR/CESTAT lookup on the contested pair; on-point
                             ruling strengthens; no precedent -> manual queue.
  Stage 5  calibrated routing— verify the declared-rate basis before asserting a
                             duty delta (CEPA/ITA-1/notification); route by stakes.

The per-row ``audit_trace`` records every stage; the result schema is preserved
for the Excel writer / frontend and extended with conformal_set, legal_docket,
precedent_citations, and revenue_basis.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from backend.app.config import settings
from backend.app.core.llm_client import get_classifier
from backend.app.core.logger import get as get_logger
from backend.app.services import (
    custada_validator,
    fraud_detector,
    legal_constraints,
    precedent,
    rate_resolver,
    retriever,
)

log = get_logger(__name__)

LLM_CONCURRENCY = max(1, settings.llm_concurrency)


def _siblings_by_be(rows: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        be = (r.get("be_no") or "").strip()
        if be:
            groups.setdefault(be, []).append(r)
    return groups


def process_rows(
    rows: list[dict],
    progress: Callable[[dict], None] | None = None,
    batch_size: int | None = None,
) -> list[dict]:
    batch_size = batch_size or settings.batch_size
    total = len(rows)
    classifier = get_classifier()
    results: dict[int, dict] = {}
    results_lock = threading.Lock()
    started_at = time.time()

    be_groups = _siblings_by_be(rows)

    def _eta(completed: int) -> float | None:
        if completed <= 0 or total <= 0:
            return None
        elapsed = time.time() - started_at
        rate = completed / elapsed if elapsed > 0 else 0
        if rate <= 0:
            return None
        return max(0, total - completed) / rate

    def report(phase: str, completed: int) -> None:
        if not progress:
            return
        progress({
            "phase": phase,
            "completed": completed,
            "total": total,
            "eta_seconds": _eta(completed),
        })

    def report_partial(phase: str, **extra) -> None:
        if not progress:
            return
        with results_lock:
            partial = [results[i] for i in sorted(results.keys())]
        payload = {
            "phase": phase,
            "completed": len(partial),
            "total": total,
            "eta_seconds": _eta(len(partial)),
            "partial_results": partial,
        }
        payload.update(extra)
        progress(payload)

    # --- Stage 0 + 1 + 2: enrich, generate candidates, legal pass -----------
    t0 = time.time()
    report("retrieve", 0)
    enriched: dict[int, dict] = {}
    cands_per_row: dict[int, list[dict]] = {}
    docket_per_row: dict[int, dict] = {}
    for i, r in enumerate(rows):
        if i % 25 == 0:
            report("retrieve", i)
        siblings = be_groups.get((r.get("be_no") or "").strip(), [])
        ctx = retriever.enrich_row(r, siblings=siblings)
        enriched[r["row_idx"]] = ctx
        try:
            cands = retriever.generate_candidates(
                ctx["item_description"], declared_cth=ctx["declared_cth"],
            )
            legal = legal_constraints.apply(
                ctx["item_description"], cands, declared_cth=ctx["declared_cth"],
            )
            cands_per_row[r["row_idx"]] = legal["candidates"]
            docket_per_row[r["row_idx"]] = legal
        except Exception as e:
            log.exception(f"stage0-2 failed for row {r['row_idx']}: {e}")
            cands_per_row[r["row_idx"]] = []
            docket_per_row[r["row_idx"]] = {"candidates": [], "docket": [], "pruned": [], "summary": "stage error"}
    log.info("candidate generation done",
             extra={"count": total, "phase": "retrieve", "ms": int((time.time() - t0) * 1000)})
    report_partial("retrieve")

    # --- Stage 3-5: adjudicate (batched, concurrent) + verify + route -------
    # Phase string kept as ``tier3_llm`` for progress-UI compatibility; this is
    # the LLM-bearing phase the frontend's progress timeline already knows.
    ADJ_PHASE = "tier3_llm"
    batches: list[list[dict]] = [rows[b:b + batch_size] for b in range(0, len(rows), batch_size)]
    report(ADJ_PHASE, 0)
    llm_degraded = {"v": False}

    def run_batch(batch: list[dict]) -> int:
        items_for_llm = []
        for r in batch:
            ctx = enriched[r["row_idx"]]
            legal = docket_per_row.get(r["row_idx"], {})
            items_for_llm.append({
                "id": str(r["row_idx"]),
                "item_description": ctx["item_description"],
                "declared_cth": ctx["declared_cth"],
                "declared_bcd": ctx["declared_bcd"],
                "declared_notification": ctx["declared_notification"],
                "be_siblings": ctx.get("siblings", []),
                "legal_docket": [
                    {"rule": d["rule_id"], "citation": d["citation"], "kind": d["kind"]}
                    for d in legal.get("docket", [])
                ][:8],
                "candidates": cands_per_row.get(r["row_idx"], []),
            })
        try:
            t = time.time()
            classifications = classifier.classify_batch(items_for_llm)
            log.info("adjudicator batch ok",
                     extra={"count": len(batch), "phase": ADJ_PHASE,
                            "ms": int((time.time() - t) * 1000)})
        except Exception as e:
            log.warning(f"adjudicator batch failed, degrading to top candidate: {e}")
            llm_degraded["v"] = True
            report_partial(ADJ_PHASE, llm_degraded=True)
            from backend.app.core.llm_client import HeuristicClassifier
            classifications = HeuristicClassifier().classify_batch(items_for_llm)

        by_id = {}
        for c in classifications:
            d = c if isinstance(c, dict) else c.dict()
            by_id[d["id"]] = d

        for r in batch:
            cls = by_id.get(str(r["row_idx"]))
            result = _finalise_row(r, enriched[r["row_idx"]], cls,
                                   cands_per_row.get(r["row_idx"], []),
                                   docket_per_row.get(r["row_idx"], {}))
            with results_lock:
                results[r["row_idx"]] = result
        return len(batch)

    if batches:
        with ThreadPoolExecutor(max_workers=LLM_CONCURRENCY) as pool:
            futures = [pool.submit(run_batch, b) for b in batches]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    log.exception(f"batch crashed: {e}")
                with results_lock:
                    completed_now = len(results)
                report(ADJ_PHASE, completed_now)
                report_partial(ADJ_PHASE)

    report("done", total)
    if llm_degraded["v"] and progress:
        progress({"phase": "done", "completed": total, "total": total,
                  "eta_seconds": None, "llm_degraded": True})
    return [results[i] for i in sorted(results.keys())]


# --- Stage 3-5 finalisation per row -----------------------------------------

def _finalise_row(r: dict, ctx: dict, cls: dict | None,
                  cands: list[dict], legal: dict) -> dict:
    """Validate (Stage 4 hard-block), precedent (Stage 4), revenue (Stage 5)."""
    llm_failed = cls is None
    candidate_cths = [c["cth"] for c in cands]

    if cls:
        cth_proposed = cls.get("cth", "")
        bcd_proposed = cls.get("bcd_rate")
        reasoning = cls.get("reasoning", "")
        confidence = cls.get("confidence", 0.5)
        conformal_set = cls.get("conformal_set") or ([cth_proposed] if cth_proposed else [])
        citations = cls.get("citations") or []
        gir_path = cls.get("gir_path", "")
    else:
        top = cands[0] if cands else None
        cth_proposed = top["cth"] if top else ""
        bcd_proposed = (top.get("bcd_effective") or top.get("bcd_statutory")) if top else None
        reasoning = "No adjudicator output; used top legally-surviving candidate."
        confidence = 0.4 if top else 0.0
        conformal_set = [cth_proposed] if cth_proposed else []
        citations = []
        gir_path = ""

    # Stage 4a — HARD BLOCK invalid/invented CTH (the 85442010-class bug).
    fallback_cth = cands[0]["cth"] if cands else None
    v = custada_validator.validate(cth_proposed, bcd_proposed, fallback_cth,
                                   candidate_cths=candidate_cths)
    cth_final = v["cth_final"]
    manual_queue = v.get("manual_queue", False)

    master_row = custada_validator.get_master_row(cth_final) or {} if cth_final else {}

    # Stage 5a — resolve the rate basis for the FINAL code and re-check the
    # DECLARED rate's basis before asserting any delta.
    rr = rate_resolver.resolve_rate(
        cth_final, ctx["item_description"],
        declared_ntfn=ctx["declared_notification"],
        declared_bcd=ctx["declared_bcd"],
        master_bcd_statutory=master_row.get("bcd_statutory"),
        master_bcd_effective=master_row.get("bcd_effective"),
    ) if cth_final else {"bcd_audit": None, "bcd_basis": "manual_queue", "misuse_suspected": False, "detail": "manual queue"}
    bcd_final = rate_resolver.apply_resolution_to_master_bcd(
        rr, master_row.get("bcd_effective"), master_row.get("bcd_statutory"),
    )
    if bcd_final is None:
        bcd_final = v.get("bcd_final")

    revenue_basis = _verify_declared_rate_basis(ctx, rr)

    # Stage 4b — precedent check on the contested pair.
    runner_up = next((c for c in conformal_set if c and c != cth_final), None) or ctx["declared_cth"]
    precedents = []
    no_precedent = False
    if cth_final:
        try:
            precedents = precedent.lookup_precedents(
                cth_final, runner_up or None, query_text=ctx["item_description"], k=5,
            )
        except Exception as e:
            log.warning(f"precedent lookup failed: {e}")
        contested = bool(runner_up and runner_up != cth_final)
        on_point = any(p.get("heading_overlap", 0) > 0 for p in precedents)
        no_precedent = contested and not on_point

    # Stage 5b — fraud / revenue with verified basis gate.
    fraud = fraud_detector.detect(
        ctx["declared_cth"], ctx["declared_bcd"], cth_final, bcd_final, ctx["assess_val"],
        notification_misuse=bool(rr.get("misuse_suspected")),
        misuse_detail=str(rr.get("detail", "")),
        revenue_basis_valid=revenue_basis["valid"],
        revenue_basis_detail=revenue_basis["detail"],
    )

    # Stage 5c — review routing by stakes.
    review_flags: list[str] = []
    ambiguous = bool(cands and cands[0].get("retrieval_ambiguous"))
    if ambiguous:
        review_flags.append("ambiguous_retrieval")
    if len([c for c in conformal_set if c]) >= 2:
        review_flags.append("ambiguous_classification")
    if manual_queue:
        review_flags.append("manual_review")
    if no_precedent and (fraud.revenue_at_risk or 0) > 0:
        review_flags.append("no_precedent")
    if llm_failed:
        review_flags.append("adjudicator_unavailable")
    # High-differential ambiguous rows always reach the queue; revenue-neutral
    # reclassifications never do.
    if (fraud.revenue_at_risk or 0) > 0 and ("ambiguous_classification" in review_flags or no_precedent):
        if "manual_review" not in review_flags:
            review_flags.append("manual_review")

    final = {
        "cth_final": cth_final,
        "bcd_final": bcd_final,
        "tier_used": _stage_label(v, manual_queue, llm_failed),
        "confidence": confidence,
        "reasoning": reasoning,
        "candidates": cands,
        "disagreement": v.get("override_reason", ""),
        "conformal_set": [c for c in conformal_set if c],
        "citations": citations,
        "gir_path": gir_path,
        "legal_docket": legal.get("docket", []),
        "legal_summary": legal.get("summary", ""),
        "precedent_citations": [_precedent_brief(p) for p in precedents[:3]],
        "revenue_basis": revenue_basis,
        "manual_queue": manual_queue,
        "breakdown": _build_breakdown(cls, cth_final, cands),
    }

    audit = _audit_trace(r, ctx, cands, legal, cls, v, rr, revenue_basis,
                         precedents, llm_failed)
    return _build_result(r, ctx, final, fraud, rr, review_flags, master_row, audit)


def _verify_declared_rate_basis(ctx: dict, rr: dict) -> dict:
    """Stage 5 fix: is the importer's low/zero declared rate legally supported?

    A declared 0%/concessional rate is NOT a revenue finding when:
      - the rate resolver matched a concessional/FTA condition for the declared
        notification (CEPA / jumbo 50/2017 / ITA), OR
      - the goods read as an ITA-1 passive electrical component (connectors,
        coaxial, passive automotive parts) which carry a bound 0% rate.
    """
    declared_bcd = ctx.get("declared_bcd")
    declared_ntfn = ctx.get("declared_notification", "")

    if declared_bcd is None or declared_bcd > 0.01:
        return {"valid": False, "detail": "", "basis": "none"}

    # Concessional / FTA basis matched by the rate resolver for the declared rate.
    matched = rr.get("matched_variant")
    if rr.get("bcd_basis") == "concessional" and matched:
        return {
            "valid": True,
            "basis": "concessional_notification",
            "detail": (
                f"declared 0%/concessional rate supported by notification "
                f"{matched.get('notification_id')} (condition match "
                f"{matched.get('match_score')})"
            ),
        }
    if declared_ntfn and rr.get("bcd_basis") == "concessional":
        return {"valid": True, "basis": "fta_cepa",
                "detail": f"declared notification {declared_ntfn} matched a concessional variant (CEPA/FTA)"}

    # ITA-1 passive-component bar: connectors / passive automotive electricals
    # carry a legitimate bound 0% rate; do not raise a finding.
    if legal_constraints.in_ita1_scope(ctx.get("item_description", "")):
        return {"valid": True, "basis": "ita1",
                "detail": "ITA-1 / passive electrical component — bound 0% rate, no revenue finding"}

    return {"valid": False, "detail": "", "basis": "unsupported"}


def _precedent_brief(p: dict) -> dict:
    return {
        "ruling_id": p.get("ruling_id"),
        "title": (p.get("title") or "")[:200],
        "authority": p.get("authority"),
        "outcome_cth": p.get("outcome_cth"),
        "competing_pair": p.get("competing_pair"),
        "legal_basis": (p.get("legal_basis") or "")[:300],
        "source_url": p.get("source_url"),
        "score": p.get("score"),
        "heading_overlap": p.get("heading_overlap", 0),
    }


def _stage_label(v: dict, manual_queue: bool, llm_failed: bool) -> str:
    if manual_queue:
        return "manual_queue"
    if v.get("hallucination"):
        return "stage4_validated"
    if llm_failed:
        return "stage1_fallback"
    return "stage3_adjudicated"


def reevaluate_one(row: dict, top_k: int | None = None,
                   sim_threshold: float = 0.90) -> dict:
    """Re-evaluate a single row through the full Stage 0-5 pipeline."""
    desc = (row.get("item_description") or "").strip()
    if not desc:
        return {"error": "item_description required"}

    classifier = get_classifier()
    ctx = retriever.enrich_row(row, siblings=[])
    cands = retriever.generate_candidates(desc, declared_cth=ctx["declared_cth"])
    legal = legal_constraints.apply(desc, cands, declared_cth=ctx["declared_cth"])
    cands = legal["candidates"]

    item = {
        "id": str(row.get("row_idx", "0")),
        "item_description": desc,
        "declared_cth": ctx["declared_cth"],
        "declared_bcd": ctx["declared_bcd"],
        "declared_notification": ctx["declared_notification"],
        "be_siblings": [],
        "legal_docket": [
            {"rule": d["rule_id"], "citation": d["citation"], "kind": d["kind"]}
            for d in legal.get("docket", [])
        ],
        "candidates": cands,
    }
    try:
        cls = classifier.reevaluate(item)
        cls_dict = cls.dict() if not isinstance(cls, dict) else cls
    except Exception as e:
        log.warning(f"reevaluate adjudicator failed, falling back: {e}")
        cls_dict = None

    result = _finalise_row(
        {**row, "row_idx": row.get("row_idx", 0)}, ctx, cls_dict, cands, legal,
    )
    final = result["final"]
    return {
        "cth_final": final["cth_final"],
        "bcd_final": final["bcd_final"],
        "confidence": final["confidence"],
        "reasoning": final["reasoning"],
        "tier_used": final["tier_used"],
        "disagreement": final["disagreement"],
        "candidates": cands,
        "hallucination": result["hallucination"],
        "conformal_set": final["conformal_set"],
        "citations": final["citations"],
        "legal_docket": final["legal_docket"],
        "precedent_citations": final["precedent_citations"],
        "revenue_basis": final["revenue_basis"],
        "rate_resolution": result.get("rate_resolution"),
        "review_flags": result.get("review_flags", []),
        "retrieval_diagnostics": result.get("retrieval_diagnostics"),
    }


# --- Audit trace ------------------------------------------------------------

def _audit_trace(r: dict, ctx: dict, cands: list[dict], legal: dict,
                 cls: dict | None, v: dict, rr: dict, revenue_basis: dict,
                 precedents: list[dict], llm_failed: bool) -> dict:
    chapters = sorted({c.get("chapter", "") for c in cands if c.get("chapter")})
    stage0 = {
        "stage": "enrichment",
        "explanation": (
            f"Declared CTH {ctx['declared_cth'] or '—'} "
            f"({'in master' if ctx.get('declared_cth_in_master') else 'NOT in master'}); "
            f"{len(ctx.get('siblings', []))} BE sibling line(s) attached."
        ),
    }
    stage1 = {
        "stage": "candidate_generation",
        "candidate_chapters": chapters,
        "top_candidates": [
            {"cth": c["cth"], "score": round(float(c.get("retrieval_score") or 0.0), 4),
             "source": c.get("source"), "description": (c.get("description") or "")[:100]}
            for c in cands[:6]
        ],
        "explanation": (
            f"Generated {len(cands)} cross-chapter candidates spanning chapters "
            f"{', '.join(chapters) or '—'} (recall-oriented; all real master codes)."
        ),
    }
    stage2 = {
        "stage": "legal_constraints",
        "docket": legal.get("docket", []),
        "pruned": legal.get("pruned", []),
        "explanation": legal.get("summary", "no governing notes"),
    }
    stage3 = {
        "stage": "gir_adjudication",
        "llm_failed": bool(llm_failed),
        "proposed_cth": (cls or {}).get("cth", ""),
        "conformal_set": (cls or {}).get("conformal_set", []),
        "gir_path": (cls or {}).get("gir_path", ""),
        "citations": (cls or {}).get("citations", []),
        "explanation": (
            f"Adjudicator chose {(cls or {}).get('cth','—')} via "
            f"{(cls or {}).get('gir_path') or 'GIR walk'}; conformal set "
            f"{(cls or {}).get('conformal_set', [])}."
            if cls and not llm_failed
            else "Adjudicator unavailable; used top legally-surviving candidate."
        ),
    }
    stage4 = {
        "stage": "validation_precedent",
        "cth_final": v["cth_final"],
        "manual_queue": v.get("manual_queue", False),
        "override_reason": v.get("override_reason", ""),
        "precedents": [_precedent_brief(p) for p in precedents[:2]],
        "explanation": (
            v.get("override_reason")
            or (f"Final CTH {v['cth_final']} verified against the master; "
                f"{len(precedents)} precedent ruling(s) checked.")
        ),
    }
    stage5 = {
        "stage": "revenue_routing",
        "bcd_basis": rr.get("bcd_basis"),
        "revenue_basis": revenue_basis,
        "explanation": (
            f"BCD basis: {rr.get('bcd_basis')}. "
            + (f"Declared rate has a verified basis ({revenue_basis['detail']}) — no finding."
               if revenue_basis.get("valid")
               else "Declared rate has no verified concessional/FTA/ITA-1 basis.")
        ),
    }
    summary = (
        f"{len(cands)} candidates [{', '.join(chapters) or '—'}] -> "
        f"legal: {legal.get('summary','')} -> "
        f"final {v['cth_final'] or 'MANUAL QUEUE'}"
        + (f" @ basis {rr.get('bcd_basis')}" if v['cth_final'] else "")
    )
    return {"stages": [stage0, stage1, stage2, stage3, stage4, stage5], "summary": summary}


# --- Result assembly --------------------------------------------------------

def _build_result(r: dict, ctx: dict, final: dict, fraud, rr: dict,
                  review_flags: list[str], master_row: dict, audit: dict) -> dict:
    cands = final.get("candidates") or []
    declared_master_row = custada_validator.get_master_row(ctx["declared_cth"]) or {}
    declared_chapter_no = _chapter_no(ctx["declared_cth"], declared_master_row)
    recommended_chapter_no = _chapter_no(final["cth_final"], master_row)
    applicable_notifications = master_row.get("applicable_notifications", [])
    notification_details = master_row.get("notification_details") or \
        custada_validator.get_notification_details(applicable_notifications)

    retrieval_diagnostics = None
    if cands:
        retrieval_diagnostics = {
            "margin": cands[0].get("retrieval_margin"),
            "ambiguous": bool(cands[0].get("retrieval_ambiguous")),
        }

    rate_resolution_out = {
        "bcd_audit": rr.get("bcd_audit"),
        "bcd_basis": rr.get("bcd_basis"),
        "misuse_suspected": bool(rr.get("misuse_suspected")),
        "detail": rr.get("detail"),
        "matched_variant": rr.get("matched_variant"),
        "condition_scores": (rr.get("condition_scores") or [])[:12],
    }

    return {
        "row_idx": r["row_idx"],
        "declared_cth": ctx["declared_cth"],
        "declared_bcd": ctx["declared_bcd"],
        "declared_notification": ctx["declared_notification"],
        "item_description": ctx["item_description"],
        "importer": ctx.get("importer", ""),
        "be_no": ctx.get("be_no", ""),
        "assess_val": ctx.get("assess_val"),
        "final": final,
        "fraud": fraud.dict(),
        "review_flags": review_flags,
        "rate_resolution": rate_resolution_out,
        "retrieval_diagnostics": retrieval_diagnostics,
        "declared_chapter": {
            "no": declared_chapter_no,
            "title": declared_master_row.get("chapter_title", ""),
        },
        "recommended_chapter": {
            "no": recommended_chapter_no,
            "title": master_row.get("chapter_title", ""),
        },
        "chapter_changed": bool(
            declared_chapter_no and recommended_chapter_no
            and declared_chapter_no != recommended_chapter_no
        ),
        "unit": master_row.get("unit", ""),
        "import_policy": master_row.get("import_policy", ""),
        "export_policy": master_row.get("export_policy", ""),
        "bcd_breakdown": {
            "statutory": master_row.get("bcd_statutory"),
            "effective": master_row.get("bcd_effective"),
            "schedule_text": master_row.get("bcd_sch", ""),
            "notification_text": master_row.get("bcd_ntfn", ""),
            "notification_source": master_row.get("bcd_notifications_applied", []),
        },
        "applicable_notifications": applicable_notifications,
        "notification_details": notification_details,
        "hallucination": final.get("disagreement", "").startswith("Proposed CTH"),
        "audit_trace": audit or {"stages": [], "summary": ""},
    }


def _is_residuary(cth: str | None) -> bool:
    cth = (cth or "").strip()
    if len(cth) < 8:
        return False
    return cth[-2:] in {"00", "90", "99"}


def _build_breakdown(cls: dict | None, cth_final: str, cands: list[dict]) -> dict:
    cth_final = (cth_final or "").strip()
    cls = cls or {}
    matched = next((c for c in cands if c.get("cth") == cth_final), None)
    chapter = str(cls.get("chapter") or (matched or {}).get("chapter") or cth_final[:2])
    heading = str(cls.get("heading") or (matched or {}).get("heading") or cth_final[:4])
    return {
        "chapter": chapter[:2],
        "chapter_title": (matched or {}).get("chapter_title", ""),
        "heading": heading[:4],
        "heading_text": (matched or {}).get("heading_text", ""),
        "subheading_6": cth_final[:6],
        "subheading_text": (matched or {}).get("subheading_text", ""),
        "leaf_text": (matched or {}).get("leaf_text", ""),
        "discriminator": str(cls.get("discriminator", ""))[:600],
        "is_residuary": bool(cls.get("is_residuary", _is_residuary(cth_final))),
        "attributes": cls.get("attributes") or {},
    }


def _chapter_no(cth: str | None, master_row: dict) -> str:
    chapter = str(master_row.get("chapter_no") or master_row.get("chapter") or "").strip()
    if chapter:
        digits = "".join(ch for ch in chapter if ch.isdigit())
        return digits.zfill(2) if digits else chapter
    cth_digits = "".join(ch for ch in str(cth or "") if ch.isdigit())
    return cth_digits[:2] if len(cth_digits) >= 2 else ""
