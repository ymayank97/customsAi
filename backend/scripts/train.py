"""Train and evaluate: python -m backend.scripts.train --help."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score
from sklearn.model_selection import GroupShuffleSplit

from backend.app.config import settings
from backend.app.core.text_cleaning import clean_description
from backend.app.ml.hierarchy import HierarchicalClassifier, LEVELS


def taxonomy_hash(codes):
    return hashlib.sha256("\n".join(sorted(codes)).encode()).hexdigest()


def load_examples(path: Path, valid_codes):
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    required = {"description", "cth", "verified"}
    if not required.issubset(frame):
        raise ValueError(f"CSV requires columns: {sorted(required)}; optional group_id")
    if not frame.verified.str.lower().isin(["true", "1", "yes"]).all():
        raise ValueError("Every training label must be verified; review declared codes first")
    frame["cth"] = frame.cth.str.strip()
    frame["normalized"] = frame.description.map(clean_description)
    if not frame.cth.isin(valid_codes).all() or not frame.normalized.str.contains(r"[^\W\d_]", regex=True).all():
        raise ValueError("Empty descriptions or CTH labels missing from the tariff master")
    if (frame.groupby("normalized").cth.nunique() > 1).any():
        raise ValueError("Identical cleaned descriptions have conflicting labels; add distinguishing attributes")
    # Connected groups prevent both duplicate text and related product families
    # crossing splits, including when a duplicate links two different families.
    parents = list(range(len(frame)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    seen = {}
    for i, row in frame.iterrows():
        keys = [("description", row.normalized)]
        if row.get("group_id", "").strip():
            keys.append(("family", row.group_id.strip()))
        for key in keys:
            if key in seen:
                parents[find(i)] = find(seen[key])
            else:
                seen[key] = i
    frame["group"] = [find(i) for i in range(len(frame))]
    # Repeated identical rows do not increase support counts or training weight.
    return frame.drop_duplicates("normalized").reset_index(drop=True)


def split_examples(frame):
    if frame.group.nunique() < 10:
        raise ValueError("At least ten independent product groups required for train/validation/test")
    train_val, test = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42).split(frame, groups=frame.group))
    pool = frame.iloc[train_val]
    train, validation = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=43).split(pool, groups=pool.group))
    return pool.iloc[train], pool.iloc[validation], frame.iloc[test]


def select_threshold(predictions, labels, target=0.95, min_accepted=20):
    eligible = [(p["score"], p["candidates"][0]["cth"] == y)
                for p, y in zip(predictions, labels)
                if p["candidates"] and p["candidates"][0]["supported"]]
    for threshold in sorted({score for score, _ in eligible if score >= 0.5}):
        accepted = [correct for score, correct in eligible if score >= threshold]
        if len(accepted) >= min_accepted and np.mean(accepted) >= target:
            return float(threshold)
    return 1.01


def evaluate(model, frame):
    predictions = model.predict(frame.description)
    labels = frame.cth.tolist()
    top = [p["candidates"][0]["cth"] if p["candidates"] else "" for p in predictions]
    accepted = [(p["cth"] == y) for p, y in zip(predictions, labels) if p["cth"]]
    return {
        "rows": len(frame),
        "accuracy_by_digits": {str(n): float(np.mean([a[:n] == b[:n] for a, b in zip(top, labels)])) for n in LEVELS},
        "macro_f1_by_digits": {str(n): float(f1_score([y[:n] for y in labels], [y[:n] for y in top], average="macro", zero_division=0)) for n in LEVELS},
        "top3_accuracy": float(np.mean([y in [c["cth"] for c in p["candidates"]] for p, y in zip(predictions, labels)])),
        "automatic_coverage": len(accepted) / len(frame),
        "automatic_accuracy": float(np.mean(accepted)) if accepted else None,
        "unseen_test_codes": sorted(set(labels) - set(model.counts)),
    }


def train(examples: Path, master: Path, output: Path, tariff_version: str,
          target=0.95, min_accepted=20):
    codes = pd.read_parquet(master)["cth"].astype(str).tolist()
    frame = load_examples(examples, set(codes))
    training, validation, test = split_examples(frame)
    model = HierarchicalClassifier().fit(training.description, training.cth, codes)
    model.threshold = select_threshold(model.predict(validation.description), validation.cth, target, min_accepted)
    model.metadata.update(tariff_version=tariff_version, taxonomy_hash=taxonomy_hash(codes),
                          master_sha256=hashlib.sha256(master.read_bytes()).hexdigest(),
                          trained_at=datetime.now(timezone.utc).isoformat(),
                          examples_sha256=hashlib.sha256(examples.read_bytes()).hexdigest(),
                          threshold=model.threshold, target_validation_accuracy=target)
    # A flat supervised baseline uses the same training-only feature vocabulary.
    flat = LogisticRegression(C=4.0, max_iter=400)
    if training.cth.nunique() > 1:
        flat.fit(model.features.transform(training.description), training.cth)
        flat_predictions = flat.predict(model.features.transform(test.description))
    else:
        flat_predictions = [training.cth.iloc[0]] * len(test)
    report = {"metadata": model.metadata, "split_rows": {"train": len(training), "validation": len(validation), "test": len(test)},
              "validation": evaluate(model, validation), "test": evaluate(model, test),
              "flat_test_accuracy": float(np.mean(np.asarray(flat_predictions) == test.cth.to_numpy())),
              "score_note": "Uncalibrated model scores; thresholds selected on validation data. Test metrics are independent. No coverage guarantee."}
    model.save(output)
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("examples", type=Path, help="Reviewed CSV: description,cth,verified[,group_id]")
    parser.add_argument("--master", type=Path, default=settings.data_dir / "cth_master.parquet")
    parser.add_argument("--output", type=Path, default=settings.models_dir / "hierarchy.joblib")
    parser.add_argument("--tariff-version", required=True)
    parser.add_argument("--target-accuracy", type=float, default=0.95)
    parser.add_argument("--min-validation-accepted", type=int, default=20)
    args = parser.parse_args()
    if not 0.5 <= args.target_accuracy <= 1 or args.min_validation_accepted < 1:
        parser.error("Target accuracy must be 0.5..1 and minimum accepted must be positive")
    print(json.dumps(train(args.examples, args.master, args.output, args.tariff_version,
                           args.target_accuracy, args.min_validation_accepted), indent=2))


if __name__ == "__main__":
    main()
