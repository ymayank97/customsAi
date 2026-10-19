"""Stage 10: notification chunking + TF-IDF index over enriched descriptions."""
from __future__ import annotations

import pathlib
import pickle
from collections.abc import Iterator

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

ENRICHED = pathlib.Path("data/enriched")


def split_notification(text: str, chunk_size: int = 1500, overlap: int = 200) -> Iterator[str]:
    if chunk_size <= 0 or not 0 <= overlap < chunk_size:
        raise ValueError("Require chunk_size > 0 and 0 <= overlap < chunk_size")
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for separator in ("\n\n", "\n", ". ", " "):
                boundary = text.rfind(separator, start + chunk_size // 2, end)
                if boundary >= 0:
                    end = boundary + len(separator)
                    break
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def chunk_notifications(out_dir: pathlib.Path) -> dict:
    ntfns = pd.read_parquet(out_dir / "notifications.parquet")
    rows: list[dict] = []
    for r in ntfns.itertuples(index=False):
        body = r.full_text or ""
        if not body:
            continue
        for idx, chunk in enumerate(split_notification(body)):
            rows.append({
                "ntfn_id": r.ntfn_id,
                "ntfn_type": r.ntfn_type,
                "ntfn_date": r.ntfn_date,
                "chunk_idx": idx,
                "text": chunk,
                "char_len": len(chunk),
            })
    df = pd.DataFrame(rows, columns=["ntfn_id", "ntfn_type", "ntfn_date",
                                     "chunk_idx", "text", "char_len"])
    df.to_parquet(out_dir / "notification_chunks.parquet", index=False)
    return {
        "rows": int(len(df)),
        "ntfns_chunked": int(df["ntfn_id"].nunique()),
        "avg_char_len": int(df["char_len"].mean()) if len(df) else 0,
    }


def build_tfidf(out_dir: pathlib.Path) -> dict:
    master = pd.read_parquet(out_dir / "cth_master.parquet")
    corpus_text = (
        master["description_combined"].fillna("").astype(str) + " " +
        master["notes_text"].fillna("").astype(str) + " " +
        master["chapter_title"].fillna("").astype(str)
    )
    vec = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=50_000,
        lowercase=True,
        stop_words="english",
        min_df=1,
    )
    matrix = vec.fit_transform(corpus_text.tolist())
    index_cths = master["cth"].tolist()
    with open(out_dir / "tfidf.pkl", "wb") as f:
        pickle.dump({
            "vectorizer": vec,
            "matrix": matrix,
            "cths": index_cths,
        }, f, protocol=pickle.HIGHEST_PROTOCOL)
    return {
        "rows": int(matrix.shape[0]),
        "vocab_size": int(matrix.shape[1]),
        "nnz": int(matrix.nnz),
    }


def main() -> int:
    import json
    s10a = chunk_notifications(ENRICHED)
    s10b = build_tfidf(ENRICHED)
    print("=== Stage 10a: notification_chunks ===")
    print(json.dumps(s10a, indent=2))
    print("=== Stage 10b: tfidf ===")
    print(json.dumps(s10b, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
