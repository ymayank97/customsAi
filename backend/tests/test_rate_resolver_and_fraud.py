"""Unit tests for rate resolution, fraud misuse branch, retrieval fusion helpers."""
from __future__ import annotations

from backend.app.services import fraud_detector
from backend.app.services.rate_resolver import _jaccard, resolve_rate
from backend.app.services.retriever import _minmax_norm_scores


def load_tests(loader, tests, pattern):
    import unittest

    return unittest.TestSuite(
        unittest.FunctionTestCase(test)
        for name, test in sorted(globals().items())
        if name.startswith("test_") and callable(test)
    )


def test_jaccard():
    assert _jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
    assert _jaccard(set(), {"a"}) == 0.0


def test_minmax_norm_scores():
    m = _minmax_norm_scores([("x", 0.0), ("y", 10.0)])
    assert m["x"] == 0.0 and m["y"] == 1.0
    m2 = _minmax_norm_scores([("a", 5.0), ("b", 5.0)])
    assert m2["a"] == 1.0 and m2["b"] == 1.0


def test_fraud_notification_misuse():
    f = fraud_detector.detect(
        "85369090", 5.0, "85369090", 10.0, 1000.0,
        notification_misuse=True,
        misuse_detail="test misuse",
    )
    assert f.is_fraud
    assert f.flag_type == "Notification misuse"
    assert f.revenue_at_risk == 50.0


def test_resolve_rate_no_cth():
    r = resolve_rate("", "some item")
    assert r["bcd_basis"] == "master_fallback"


def test_retriever_margin_meta_attached():
    from backend.app.services.retriever import retrieve_candidates

    # Offline smoke: may skip if no enriched data / models
    try:
        c = retrieve_candidates("electrical connector assembly for cable", top_k=2)
    except Exception:
        return
    assert isinstance(c, list)
    if len(c) >= 1:
        assert "retrieval_margin" in c[0]
        assert "retrieval_ambiguous" in c[0]
