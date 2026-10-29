import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.app.config import settings
from backend.app.ml.hierarchy import HierarchicalClassifier
from backend.app.services import classifier, pipeline
from backend.app.services.excel_processor import _to_cth_str, load_be_excel, normalise_input_rows, write_annotated_excel
from backend.app.services.rate_resolver import resolve_rate


def test_missing_model_abstains_without_false_findings(monkeypatch):
    monkeypatch.setattr(classifier, "get_model", lambda: None)
    monkeypatch.setattr(classifier, "get_master_row", lambda code: None)
    progress = []
    results = pipeline.process_rows([{"row_idx": 0, "item_description": "connector 220v", "declared_cth": "85369090", "declared_bcd": 0}], progress.append)
    assert results[0]["final"]["cth_final"] == ""
    assert results[0]["final"]["manual_queue"]
    assert results[0]["fraud"]["is_fraud"] is False
    assert results[0]["fraud"]["flag_type"] == ""
    assert progress[-1]["completed"] == 1


def test_importer_code_does_not_affect_predictions(monkeypatch):
    prediction = {"cth": "73181500", "score": 0.9, "manual_review": False, "reason": "accepted", "supported_prefix": "73181500", "candidates": []}
    monkeypatch.setattr(classifier, "predict", lambda texts: [prediction for _ in texts])
    monkeypatch.setattr(classifier, "get_master_row", lambda code: {"cth": code, "bcd_statutory": 10})
    rows = [{"row_idx": i, "item_description": "steel bolt", "declared_cth": code} for i, code in enumerate(["73181500", "52081100"])]
    results = pipeline.process_rows(rows)
    assert results[0]["final"]["cth_final"] == results[1]["final"]["cth_final"]


def test_rate_conditions_are_not_inferred_from_keywords():
    master = {"bcd_statutory": 10, "bcd_effective": 0, "applicable_notifications": ["example"]}
    assert resolve_rate(master)["bcd_audit"] is None
    assert resolve_rate({"bcd_statutory": 0})["bcd_audit"] == 0
    assert resolve_rate({"bcd_statutory": 10}, "claimed notification")["bcd_audit"] is None


def test_reviewed_legal_conflict_abstains(monkeypatch):
    from backend.app.services import legal_rules
    monkeypatch.setattr(legal_rules, "load_rules", lambda: [
        {"id": "test", "citation": "Reviewed test exclusion", "all_tokens": ["bolt"], "excluded_prefixes": ["73"]}])
    monkeypatch.setattr(classifier, "predict", lambda texts: [{
        "cth": "73181500", "score": 0.95, "supported_prefix": "73181500", "reason": "accepted", "candidates": []}])
    monkeypatch.setattr(classifier, "get_master_row", lambda code: {"cth": code, "bcd_statutory": 10})
    result = pipeline.process_rows([{"row_idx": 0, "item_description": "steel bolt", "declared_bcd": 0}])[0]
    assert result["final"]["cth_final"] == ""
    assert result["final"]["supported_prefix"] == ""
    assert "legal_rule_conflict" in result["review_flags"]
    assert result["fraud"]["is_fraud"] is False


@pytest.mark.parametrize("raw,expected", [(85369090.0, "85369090"), ("01 01 21 00", "01012100"), ("853690", "853690"), (None, "")])
def test_codes_are_not_fabricated(raw, expected):
    assert _to_cth_str(raw) == expected


def test_excel_round_trip_without_model(tmp_path, monkeypatch):
    monkeypatch.setattr(classifier, "get_model", lambda: None)
    monkeypatch.setattr(classifier, "get_master_row", lambda code: None)
    source = tmp_path / "input.xlsx"
    pd.DataFrame({"Description": ["connector 220v"], "CTH": [85369090.0], "BCD": [0]}).to_excel(source, index=False)
    frame, columns = load_be_excel(source)
    rows = normalise_input_rows(frame, columns)
    output = tmp_path / "result.xlsx"
    write_annotated_excel(frame, pipeline.process_rows(rows), output)
    result = pd.read_excel(output)
    assert "Model score" in result
    assert result["Review flags"].str.contains("manual_review").all()


def test_api_and_health_without_ai_providers(monkeypatch):
    from backend.app.main import app
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(classifier, "get_model", lambda: None)
    monkeypatch.setattr(classifier, "get_master_row", lambda code: None)
    client = TestClient(app)
    response = client.post("/api/classify/reevaluate", json={"item_description": "steel bolt"})
    assert response.status_code == 200
    assert response.json()["classification"]["manual_queue"]
    assert client.post("/api/chat", json={"message": "hello"}).status_code == 404
    assert client.get("/api/uploads/missing/results?page_size=100000").status_code == 422


def test_local_frontend_origins_receive_cors_headers():
    from backend.app.main import app
    client = TestClient(app)
    for origin in ("http://localhost:3000", "http://127.0.0.1:3000"):
        response = client.get("/api/auth/config", headers={"Origin": origin})
        assert response.headers["access-control-allow-origin"] == origin
    response = client.get("/api/auth/config", headers={"Origin": "https://unrelated.example"})
    assert "access-control-allow-origin" not in response.headers


def test_upload_status_results_feedback_and_download(tmp_path, monkeypatch):
    from io import BytesIO
    from sqlalchemy import create_engine
    from backend.app import db, job_store, main

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "_ENGINE", engine)
    monkeypatch.setattr(settings, "google_oauth_client_id", "")
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(job_store, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(main, "get_master", lambda: {"73181500": {"cth": "73181500"}})
    monkeypatch.setattr(main, "get_master_row", lambda code: {"cth": code} if code == "73181500" else None)
    monkeypatch.setattr(main, "get_model", lambda: None)
    monkeypatch.setattr(classifier, "get_model", lambda: None)
    monkeypatch.setattr(classifier, "get_master_row", lambda code: None)
    content = BytesIO()
    pd.DataFrame({"Description": ["steel bolt", "cotton cloth"], "CTH": ["73181500", "52081100"], "BCD": [0, 5]}).to_excel(content, index=False)
    with TestClient(main.app) as client:
        response = client.post("/api/uploads", files={"file": ("sample.xlsx", content.getvalue())})
        assert response.status_code == 200
        job_id = response.json()["job_id"]
        assert client.get(f"/api/uploads/{job_id}").json()["status"] == "done"
        result = client.get(f"/api/uploads/{job_id}/results").json()
        assert result["summary"]["review"] == 2
        assert result["summary"]["classified"] == 0
        assert len(result["rows"]) == 2
        download = client.get(f"/api/uploads/{job_id}/download")
        assert download.status_code == 200
        assert len(pd.read_excel(BytesIO(download.content))) == 2
        correction = client.post("/api/feedback", json={"job_id": job_id, "row_idx": 0, "item_description": "steel bolt", "corrected_cth": "73181500"})
        assert correction.status_code == 200
        assert client.post("/api/feedback", json={"corrected_cth": "00000000"}).status_code == 422
    engine.dispose()
