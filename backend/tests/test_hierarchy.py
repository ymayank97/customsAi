from pathlib import Path

import pandas as pd
import pytest

from backend.app.core.text_cleaning import clean_description
from backend.app.ml.hierarchy import HierarchicalClassifier
from backend.scripts.train import load_examples, select_threshold, split_examples, train


@pytest.fixture
def trained():
    descriptions = ["steel bolts threaded fasteners", "steel screws threaded bolts", "steel fastener screw bolt",
                    "cotton woven fabric textile", "woven cotton textile cloth", "cotton cloth fabric weave"]
    labels = ["73181500"] * 3 + ["52081100"] * 3
    model = HierarchicalClassifier().fit(descriptions, labels, set(labels))
    model.threshold = 0.5
    return model


def test_real_paths_serialization_and_predictions(trained, tmp_path):
    predictions = trained.predict(["steel bolt threaded fastener", "woven cotton cloth"])
    assert [p["cth"] for p in predictions] == ["73181500", "52081100"]
    for prediction in predictions:
        for candidate in prediction["candidates"]:
            previous = ""
            for step in candidate["steps"]:
                assert step["parent"] == previous
                assert step["code"] in trained.tree[previous]
                previous = step["code"]
    trained.save(tmp_path / "model.joblib")
    restored = HierarchicalClassifier.load(tmp_path / "model.joblib")
    assert predictions == restored.predict(["steel bolt threaded fastener", "woven cotton cloth"])


def test_empty_unknown_and_missing_training_abstain(trained):
    assert trained.predict(["", "🦄"])[0]["manual_review"]
    assert trained.predict(["", "🦄"])[1]["manual_review"]
    trained.threshold = 1.01
    assert trained.predict(["steel bolts threaded fasteners"])[0]["cth"] == ""


def test_untrained_sibling_prevents_automatic_leaf():
    model = HierarchicalClassifier().fit(["steel bolt"] * 3, ["73181500"] * 3,
                                        ["73181500", "73181600"])
    model.threshold = 0.5
    result = model.predict(["steel bolt"])[0]
    assert result["cth"] == ""
    assert result["supported_prefix"] == "7318"
    assert result["candidates"][0]["steps"][2]["all_children_trained"] is False


def test_distinctions_at_heading_subheading_and_leaf():
    examples = {
        "73181510": "brass threaded bolt fastener",
        "73181590": "steel threaded bolt fastener",
        "73181600": "hexagonal threaded nut fastener",
        "73201000": "flat leaf spring suspension",
    }
    descriptions = [f"{text} specification {i}" for text in examples.values() for i in range(5)]
    labels = [code for code in examples for _ in range(5)]
    model = HierarchicalClassifier().fit(descriptions, labels, examples)
    predictions = model.predict(examples.values())
    assert [p["candidates"][0]["cth"] for p in predictions] == list(examples)
    assert model.models["73"][0] is not None
    assert model.models["7318"][0] is not None
    assert model.models["731815"][0] is not None


def test_preprocessing_preserves_decisive_attributes():
    cleaned = clean_description("HSN: 8536 90 90; CONN ASSY 220 V not exceeding 1000 V stainless 304 2 mm QTY: 50 PCS as per invoice")
    assert "8536" not in cleaned and "50" not in cleaned
    assert "connector assembly" in cleaned
    assert "not exceeding 1000v" in cleaned
    assert "stainless 304 2mm" in cleaned
    assert clean_description(cleaned) == cleaned
    assert clean_description("parts of steel for use in aircraft") == "parts of steel for use in aircraft"


def test_data_labels_are_verified_and_groups_do_not_leak(tmp_path):
    frame = pd.DataFrame([{"description": f"steel bolt specification {i}", "cth": "73181500",
                           "verified": "true", "group_id": str(i // 2)} for i in range(40)])
    # Link a duplicated description across two families: both must stay together.
    frame.loc[len(frame)] = {"description": "steel bolt specification 0", "cth": "73181500", "verified": "true", "group_id": "1"}
    path = tmp_path / "reviewed.csv"
    frame.to_csv(path, index=False)
    loaded = load_examples(path, {"73181500"})
    assert loaded.iloc[0].group == loaded.iloc[2].group
    splits = split_examples(loaded)
    for i, left in enumerate(splits):
        for right in splits[i + 1:]:
            assert not set(left.group) & set(right.group)
            assert not set(left.normalized) & set(right.normalized)
    frame.loc[0, "verified"] = "false"
    frame.to_csv(path, index=False)
    with pytest.raises(ValueError, match="verified"):
        load_examples(path, {"73181500"})


def test_threshold_needs_empirical_support():
    predictions = [{"score": 0.8, "candidates": [{"cth": "73181500", "supported": True}]}] * 5
    assert select_threshold(predictions, ["73181500"] * 5) > 1
    assert select_threshold(predictions, ["73181500"] * 5, min_accepted=5) == 0.8
    assert select_threshold(predictions, ["52081100"] * 5, min_accepted=5) > 1


def test_training_command_builds_independent_report(tmp_path):
    frame = pd.DataFrame([{"description": f"{text} specification {i}", "cth": code, "verified": "true", "group_id": f"{code}-{i}"}
                          for code, text in [("73181500", "steel threaded bolt"), ("52081100", "woven cotton fabric")]
                          for i in range(30)])
    examples, master, output = tmp_path / "examples.csv", tmp_path / "master.parquet", tmp_path / "model.joblib"
    frame.to_csv(examples, index=False)
    pd.DataFrame({"cth": ["73181500", "52081100"]}).to_parquet(master)
    report = train(examples, master, output, "test-version", min_accepted=2)
    assert sum(report["split_rows"].values()) == 60
    assert set(report["test"]["accuracy_by_digits"]) == {"2", "4", "6", "8"}
    assert output.exists() and output.with_suffix(".report.json").exists()
    assert report["metadata"]["tariff_version"] == "test-version"
