"""Batch hierarchical prediction, then separate tariff/rate comparison."""
from __future__ import annotations

from backend.app.config import settings
from backend.app.core.text_cleaning import clean_description
from backend.app.services import classifier, fraud_detector, legal_rules, rate_resolver


def _result(row, prediction):
    code = prediction["cth"]
    master = classifier.get_master_row(code) or {}
    declared = str(row.get("declared_cth") or "")
    declared_master = classifier.get_master_row(declared) or {}
    candidates = []
    for candidate in prediction["candidates"]:
        tariff = classifier.get_master_row(candidate["cth"])
        if tariff is not None:
            candidates.append({**tariff, **candidate, "source": "hierarchical_logistic_regression"})
    # A missing/changed master can never produce a final invalid code.
    if code and not master:
        code = ""
    suggested = candidates[0]["cth"] if candidates else code
    conflicts = legal_rules.conflicts(row.get("item_description", ""), code or suggested)
    if conflicts:
        code = ""
        master = {}
        suggested = ""
    manual = not code
    rate = rate_resolver.resolve_rate(master if code else {}, row.get("ntfn_ref", ""))
    flags = []
    if manual:
        flags.extend(["manual_review", "legal_rule_conflict" if conflicts else prediction["reason"]])
    if code and rate["bcd_audit"] is None:
        flags.append("rate_requires_review")
    source = prediction.get("training_source", "verified_invoices")
    reason = ("TF-IDF + logistic regression selected a valid tariff path. "
              "Score is uncalibrated; this is a statistical suggestion, not a legal determination."
              if code else "No automatic eight-digit classification: " + ("reviewed exclusion rule conflict" if conflicts else prediction["reason"].replace("_", " ")))
    if source == "tariff_descriptions" and suggested:
        reason = "Tariff-trained model suggestion; invoice accuracy has not been validated. Verify the product attributes and leaf text before accepting this code."
    prefix = "" if conflicts else prediction["supported_prefix"]
    final = {
        "cth_final": code, "bcd_final": rate["bcd_audit"],
        "suggested_cth": suggested, "training_source": source,
        "tier_used": "manual_queue" if manual else "hierarchical_model",
        "confidence": prediction["score"], "reasoning": reason,
        "candidates": candidates, "disagreement": "", "manual_queue": manual,
        "supported_prefix": prefix,
        "alternatives": [c["cth"] for c in candidates],
        "breakdown": {
            "chapter": (code or prefix)[:2], "chapter_title": master.get("chapter_title", ""),
            "heading": (code or prefix)[:4] if len(code or prefix) >= 4 else "",
            "heading_text": master.get("heading_text", ""),
            "subheading_6": (code or prefix)[:6] if len(code or prefix) >= 6 else "",
            "subheading_text": master.get("subheading_text", ""),
            "leaf_text": master.get("description", ""),
            "discriminator": "Learned distinctions between sibling tariff categories",
            "is_residuary": "other" in str(master.get("description", "")).casefold().split(),
        },
    }
    fraud = fraud_detector.detect(declared, row.get("declared_bcd"), code,
                                  rate["bcd_audit"], row.get("assess_val"))
    steps = candidates[0]["steps"] if candidates else []
    return {
        **row, "declared_cth": declared, "declared_bcd": row.get("declared_bcd"),
        "declared_notification": row.get("ntfn_ref", ""),
        "final": final, "fraud": fraud.model_dump(), "review_flags": flags,
        "rate_resolution": rate,
        "declared_chapter": {"no": declared[:2], "title": declared_master.get("chapter_title", "")},
        "recommended_chapter": {"no": (code or prefix)[:2], "title": master.get("chapter_title", "")},
        "chapter_changed": bool(code and declared and code[:2] != declared[:2]),
        "unit": master.get("unit", ""), "import_policy": master.get("import_policy", ""),
        "export_policy": master.get("export_policy", ""),
        "applicable_notifications": master.get("applicable_notifications") or [],
        "bcd_breakdown": {"statutory": master.get("bcd_statutory"), "effective": master.get("bcd_effective")},
        "audit_trace": {"summary": reason, "stages": [
            {"stage": "preprocessing", "explanation": clean_description(row.get("item_description"))},
            *[{"stage": "hierarchy", "explanation":
               f"{step['parent'] or 'Root'} -> {step['code']}; score {step['score']:.3f}; "
               f"all sibling categories trained: {step['all_children_trained']}"} for step in steps],
            *[{"stage": "legal_rule", "explanation": rule["citation"]} for rule in conflicts],
            {"stage": "rate_check", "explanation": rate["detail"]},
        ]},
    }


def process_rows(rows, progress=None, batch_size=None):
    size = batch_size or settings.batch_size
    if size < 1:
        raise ValueError("Batch size must be positive")
    results = []
    if progress:
        progress({"phase": "classify", "completed": 0, "total": len(rows)})
    for offset in range(0, len(rows), size):
        batch = rows[offset:offset + size]
        predictions = classifier.predict([r.get("item_description", "") for r in batch])
        results.extend(_result(row, prediction) for row, prediction in zip(batch, predictions))
        if progress:
            progress({"phase": "classify", "completed": len(results), "total": len(rows)})
    if progress:
        progress({"phase": "done", "completed": len(results), "total": len(rows)})
    return results


def reevaluate_one(row, top_k=None):
    row = {**row, "row_idx": row.get("row_idx", 0)}
    result = process_rows([row])[0]
    return {**result["final"], "rate_resolution": result["rate_resolution"],
            "review_flags": result["review_flags"]}
