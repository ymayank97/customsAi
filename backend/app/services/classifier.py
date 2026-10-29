"""Load compact local artifacts once. Never train in a request or at startup."""
from functools import lru_cache
import hashlib

import pandas as pd

from backend.app.config import settings
from backend.app.ml.hierarchy import HierarchicalClassifier


@lru_cache(maxsize=1)
def get_master():
    path = settings.data_dir / "cth_master.parquet"
    if not path.exists():
        return {}
    from pyarrow.parquet import read_schema
    needed = {"cth", "description", "heading_text", "subheading_text", "chapter_title",
              "bcd_statutory", "bcd_effective", "bcd_ntfn", "bcd_notifications_applied",
              "applicable_notifications", "unit", "import_policy", "export_policy"}
    columns = [name for name in read_schema(path).names if name in needed]
    frame = pd.read_parquet(path, columns=columns)
    # Pandas JSON conversion handles numpy arrays, timestamps and missing values.
    import json
    rows = json.loads(frame.to_json(orient="records"))
    return {str(row["cth"]): row for row in rows}


def get_master_row(cth):
    return get_master().get(str(cth or ""))


@lru_cache(maxsize=1)
def get_model():
    path = settings.models_dir / "hierarchy.joblib"
    if not path.exists():
        return None
    model = HierarchicalClassifier.load(path)
    digest = hashlib.sha256("\n".join(sorted(get_master())).encode()).hexdigest()
    master_hash = hashlib.sha256((settings.data_dir / "cth_master.parquet").read_bytes()).hexdigest()
    if model.metadata.get("taxonomy_hash") != digest or model.metadata.get("master_sha256") != master_hash:
        raise ValueError("Tariff tree changed; retrain the classifier")
    return model


def predict(descriptions):
    model = get_model()
    if model is None:
        return [HierarchicalClassifier._empty("model_not_trained") for _ in descriptions]
    return model.predict(descriptions)
