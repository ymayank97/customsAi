"""Sparse TF-IDF and local logistic regression on a versioned tariff tree."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion

from backend.app.core.text_cleaning import PREPROCESSING_VERSION, clean_description

MODEL_VERSION = 1
LEVELS = (2, 4, 6, 8)


def tariff_tree(codes):
    tree = defaultdict(set)
    for code in codes:
        if len(code) != 8 or not code.isdigit():
            raise ValueError(f"Expected an eight-digit tariff code: {code!r}")
        for width in LEVELS:
            tree[code[:width - 2]].add(code[:width])
    return {parent: sorted(children) for parent, children in tree.items()}


@dataclass
class HierarchicalClassifier:
    min_leaf_samples: int = 3
    beam_width: int = 3
    threshold: float = 1.01  # No automatic decisions until validated.
    models: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def fit(self, descriptions, labels, valid_codes):
        descriptions, labels = list(descriptions), np.asarray(labels, dtype=str)
        valid_codes = set(valid_codes)
        if len(descriptions) != len(labels) or not len(labels):
            raise ValueError("Nonempty descriptions and labels of equal length required")
        self.tree = tariff_tree(valid_codes)
        if not set(labels).issubset(valid_codes):
            raise ValueError("Training labels must exist in the tariff master")
        self.features = FeatureUnion([
            ("word", TfidfVectorizer(preprocessor=clean_description, lowercase=False,
                 ngram_range=(1, 2), max_features=20_000, sublinear_tf=True, dtype=np.float32)),
            ("char", TfidfVectorizer(preprocessor=clean_description, lowercase=False,
                 analyzer="char_wb", ngram_range=(3, 5), max_features=30_000,
                 sublinear_tf=True, dtype=np.float32)),
        ])
        matrix = self.features.fit_transform(descriptions).tocsr()
        self.counts = Counter(labels)
        self.models = {}
        indices_by_parent = defaultdict(list)
        for index, label in enumerate(labels):
            for width in LEVELS:
                indices_by_parent[label[:width - 2]].append(index)
        for parent, positions in indices_by_parent.items():
            width = len(parent) + 2
            indices = np.asarray(positions)
            children = np.asarray([label[:width] for label in labels[indices]])
            classes = np.unique(children)
            if len(classes) == 1:
                self.models[parent] = (None, classes, None)
                continue
            local = matrix[indices]
            columns = np.unique(local.indices)
            model = LogisticRegression(C=4.0, max_iter=400, solver="lbfgs")
            model.fit(local[:, columns], children)
            self.models[parent] = (model, model.classes_, columns)
        self.metadata.update(model_version=MODEL_VERSION,
                             preprocessing_version=PREPROCESSING_VERSION,
                             training_rows=len(labels), training_codes=len(self.counts))
        return self

    def predict(self, descriptions):
        descriptions = list(descriptions)
        if not descriptions:
            return []
        matrix = self.features.transform(descriptions).tocsr()
        return [self._predict_row(matrix[i], text) for i, text in enumerate(descriptions)]

    def _predict_row(self, row, description):
        if not clean_description(description) or row.nnz == 0:
            return self._empty("empty_or_unknown_description")
        # Uncalibrated product of conditional class probabilities. Never
        # renormalize the beam: discarded paths still carry probability.
        beam = [("", 1.0, [], "")]
        for width in LEVELS:
            expanded = []
            for parent, score, steps, supported in beam:
                entry = self.models.get(parent)
                if entry is None:
                    continue
                model, classes, columns = entry
                probabilities = model.predict_proba(row[:, columns])[0] if model is not None else [1.0]
                complete = set(classes) == set(self.tree[parent])
                for child, probability in zip(classes, probabilities):
                    child, probability = str(child), float(probability)
                    step = {"parent": parent, "code": child, "score": probability,
                            "all_children_trained": complete}
                    prefix = child if complete and supported == parent and probability >= 0.7 else supported
                    expanded.append((child, score * probability, steps + [step], prefix))
            beam = sorted(expanded, key=lambda path: (-path[1], path[0]))[:self.beam_width]
            if not beam:
                return self._empty("unsupported_branch")
        candidates = [{"cth": code, "score": score, "steps": steps,
                       "supported_prefix": supported,
                       "supported": supported == code and self.counts[code] >= self.min_leaf_samples}
                      for code, score, steps, supported in beam]
        best = candidates[0]
        accepted = best["supported"] and best["score"] >= self.threshold
        return {"cth": best["cth"] if accepted else "", "score": best["score"],
                "supported_prefix": best["supported_prefix"], "candidates": candidates,
                "manual_review": not accepted,
                "reason": "accepted" if accepted else "insufficient_support_or_confidence"}

    @staticmethod
    def _empty(reason):
        return {"cth": "", "score": 0.0, "supported_prefix": "", "candidates": [],
                "manual_review": True, "reason": reason}

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        joblib.dump(self, temporary, compress=3)
        temporary.replace(path)

    @classmethod
    def load(cls, path: Path):
        # Model files are trusted local artifacts, never user uploads.
        model = joblib.load(path)
        if model.metadata.get("model_version") != MODEL_VERSION or model.metadata.get("preprocessing_version") != PREPROCESSING_VERSION:
            raise ValueError("Model is incompatible; retrain with current preprocessing")
        return model
