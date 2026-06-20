"""Lightweight lexical retrieval index over the CTH master.

Replaces the dense BGE embedder + cross-encoder stack with a pure-lexical,
rule-boosted ranker that loads in seconds and needs no torch:

    - word TF-IDF (1-2 grams) and char TF-IDF (3-5 grams) over hierarchy
      context documents (chapter title + heading + subheading + leaf)
    - BM25 over Porter-stemmed tokens
    - weighted fusion with a soft chapter prior from the chapter classifier
    - rule-based rerank: head-noun overlap with the leaf/heading text,
      residuary ("Other") penalty when nothing in the leaf matches

The index is pickled to ``data/enriched/lexical_index.pkl`` and rebuilt
automatically when missing or stale (~30 s, one time).
"""
from __future__ import annotations

import pickle
import threading

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

from backend.app.config import settings
from backend.app.core.logger import get as get_logger
from backend.app.core.text_cleaning import (
    clean_description,
    extract_head_noun,
    normalize_for_matching,
    token_set_for_overlap,
)

log = get_logger(__name__)

_LOCK = threading.Lock()
_SINGLETON: "LexicalIndex | None" = None

INDEX_VERSION = 2

FUSE_WORD = 0.40
FUSE_CHAR = 0.25
FUSE_BM25 = 0.35
CHAPTER_BOOST = 0.35
HEAD_LEAF_BONUS = 0.30
HEAD_HEADING_BONUS = 0.12
RESIDUARY_PENALTY = 0.08


def _context_doc(row) -> str:
    bits = []
    for col in ("chapter_title", "heading_text", "subheading_text", "description"):
        v = row.get(col)
        if v is not None and not pd.isna(v) and str(v).strip():
            bits.append(str(v).strip())
    return " | ".join(bits)[:1200]


def _leaf_doc(row) -> str:
    bits = []
    for col in ("subheading_text", "description"):
        v = row.get(col)
        if v is not None and not pd.isna(v) and str(v).strip():
            bits.append(str(v).strip())
    return " ".join(bits)


def _is_residuary(cth: str) -> bool:
    return len(cth) >= 8 and cth[-2:] in {"00", "90", "99"}


class LexicalIndex:
    def __init__(self, blob: dict):
        self.cths: list[str] = blob["cths"]
        self.chapters: np.ndarray = blob["chapters"]
        self.word_vec: TfidfVectorizer = blob["word_vec"]
        self.word_mat = blob["word_mat"]
        self.char_vec: TfidfVectorizer = blob["char_vec"]
        self.char_mat = blob["char_mat"]
        self.bm25 = blob["bm25"]
        self.leaf_tokens: list[frozenset[str]] = blob["leaf_tokens"]
        self.heading_tokens: list[frozenset[str]] = blob["heading_tokens"]
        self.residuary: np.ndarray = blob["residuary"]
        self.cth_to_idx = {c: i for i, c in enumerate(self.cths)}
        self.heading_to_idx: dict[str, list[int]] = {}
        for i, c in enumerate(self.cths):
            self.heading_to_idx.setdefault(c[:4], []).append(i)

    def _fused_scores(self, query: str) -> np.ndarray:
        cleaned = clean_description(query) or (query or "").lower()
        qw = self.word_vec.transform([cleaned])
        qc = self.char_vec.transform([cleaned])
        word_s = np.asarray((self.word_mat @ qw.T).todense()).ravel()
        char_s = np.asarray((self.char_mat @ qc.T).todense()).ravel()

        bm25_s = np.zeros(len(self.cths), dtype=np.float64)
        if self.bm25 is not None:
            toks = normalize_for_matching(query).split()
            if toks:
                bm25_s = np.asarray(self.bm25.get_scores(toks), dtype=np.float64)

        def norm(a: np.ndarray) -> np.ndarray:
            hi = a.max()
            return a / hi if hi > 0 else a

        return FUSE_WORD * norm(word_s) + FUSE_CHAR * norm(char_s) + FUSE_BM25 * norm(bm25_s)

    def _scores_with_prior(
        self, query: str, chapter_pairs: list[tuple[str, float]] | None
    ) -> np.ndarray:
        scores = self._fused_scores(query)
        if chapter_pairs:
            prior = np.zeros(len(self.cths), dtype=np.float64)
            for ch, p in chapter_pairs:
                prior[self.chapters == str(ch).zfill(2)] = max(0.0, float(p))
            scores = scores * (1.0 + CHAPTER_BOOST * prior)
        return scores

    def search(
        self,
        query: str,
        k: int = 50,
        chapter_pairs: list[tuple[str, float]] | None = None,
    ) -> list[tuple[str, float]]:
        """Fused lexical scores with a soft chapter prior; top-k (cth, score)."""
        scores = self._scores_with_prior(query, chapter_pairs)
        order = np.argsort(-scores)[:k]
        return [(self.cths[i], float(scores[i])) for i in order if scores[i] > 0]

    def search_hierarchical(
        self,
        query: str,
        top_k: int = 8,
        chapter_pairs: list[tuple[str, float]] | None = None,
        pool_k: int = 150,
    ) -> list[tuple[str, float]]:
        """Heading-first candidate selection, the way an auditor classifies.

        Rank 4-digit headings by their best member score, then from each top
        heading take (a) the leaf whose own text best matches the query and
        (b) the heading's best-scoring residuary "Other" bucket — the correct
        code when nothing in the heading describes the item specifically.
        Without (b), residuary targets are unreachable by leaf-text match and
        exact recall@8 drops by a third (see benchmark_ml)."""
        scores = self._scores_with_prior(query, chapter_pairs)
        order = np.argsort(-scores)[:pool_k]
        q_toks = token_set_for_overlap(query)
        head, _mod = extract_head_noun(query)
        head_toks = token_set_for_overlap(head)

        heading_rank: list[str] = []
        seen_headings: set[str] = set()
        for i in order:
            if scores[i] <= 0:
                break
            h = self.cths[i][:4]
            if h not in seen_headings:
                seen_headings.add(h)
                heading_rank.append(h)

        def adjusted(i: int) -> float:
            s = float(scores[i])
            leaf = self.leaf_tokens[i]
            if head_toks:
                s += HEAD_LEAF_BONUS * len(head_toks & leaf) / len(head_toks)
                s += HEAD_HEADING_BONUS * len(head_toks & self.heading_tokens[i]) / len(head_toks)
            if self.residuary[i] and q_toks and not (q_toks & leaf):
                s -= RESIDUARY_PENALTY
            return s

        n_headings = max(2, (top_k + 1) // 2)
        chosen: list[int] = []
        chosen_set: set[int] = set()
        for h in heading_rank[:n_headings]:
            members = self.heading_to_idx.get(h, [])
            if not members:
                continue
            best = max(members, key=adjusted)
            if best not in chosen_set:
                chosen.append(best)
                chosen_set.add(best)
            residuals = [i for i in members if self.residuary[i]]
            if residuals:
                best_res = max(residuals, key=lambda i: float(scores[i]))
                if best_res not in chosen_set:
                    chosen.append(best_res)
                    chosen_set.add(best_res)

        for i in order:
            if len(chosen) >= top_k:
                break
            if int(i) not in chosen_set and scores[i] > 0:
                chosen.append(int(i))
                chosen_set.add(int(i))

        ranked = sorted(chosen, key=lambda i: -adjusted(i))[:top_k]
        return [(self.cths[i], adjusted(i)) for i in ranked]

    def rule_rerank(
        self, query: str, ranked: list[tuple[str, float]], top_k: int = 8
    ) -> list[tuple[str, float]]:
        """Rule-based rerank: reward head-noun hits in the leaf/heading text,
        penalise residuary buckets whose leaf shares nothing with the query."""
        head, _mod = extract_head_noun(query)
        head_toks = token_set_for_overlap(head)
        query_toks = token_set_for_overlap(query)
        out: list[tuple[str, float]] = []
        for cth, score in ranked:
            i = self.cth_to_idx.get(cth)
            if i is None:
                out.append((cth, score))
                continue
            leaf = self.leaf_tokens[i]
            heading = self.heading_tokens[i]
            bonus = 0.0
            if head_toks:
                bonus += HEAD_LEAF_BONUS * len(head_toks & leaf) / len(head_toks)
                bonus += HEAD_HEADING_BONUS * len(head_toks & heading) / len(head_toks)
            if self.residuary[i] and query_toks and not (query_toks & leaf):
                bonus -= RESIDUARY_PENALTY
            out.append((cth, score + bonus))
        out.sort(key=lambda t: -t[1])
        return out[:top_k]

    def similarity(self, query: str, cth: str) -> float | None:
        """Lexical similarity between a BE description and one CTH's context doc.

        Blend of query-token coverage (what fraction of the item's informative
        stems appear in the CTH's leaf/heading text) and word/char TF-IDF
        cosine — used by the Tier-1 declared-first gate in place of the dense
        embedding cosine. AUC 0.91 separating true vs wrong declared CTHs on
        data/input_data.xlsx."""
        i = self.cth_to_idx.get((cth or "").strip())
        if i is None:
            return None
        cleaned = clean_description(query) or (query or "").lower()
        if not cleaned:
            return None
        qw = self.word_vec.transform([cleaned])
        qc = self.char_vec.transform([cleaned])
        word_s = float((self.word_mat[i] @ qw.T).todense()[0, 0])
        char_s = float((self.char_mat[i] @ qc.T).todense()[0, 0])
        cosine = 0.5 * word_s + 0.5 * char_s

        coverage = 0.0
        q_toks = token_set_for_overlap(query)
        if q_toks:
            doc = self.leaf_tokens[i] | self.heading_tokens[i]
            coverage = len(q_toks & doc) / len(q_toks)
        return 0.6 * coverage + 0.4 * cosine


def build_index() -> dict:
    master = pd.read_parquet(settings.data_dir / "cth_master.parquet")
    cths = master["cth"].astype(str).tolist()
    rows = master.to_dict("records")
    docs = [_context_doc(r) for r in rows]
    cleaned_docs = [clean_description(d) or d.lower() for d in docs]

    word_vec = TfidfVectorizer(ngram_range=(1, 2), max_features=60_000,
                               lowercase=True, stop_words="english",
                               min_df=1, sublinear_tf=True)
    word_mat = word_vec.fit_transform(cleaned_docs)
    char_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                               max_features=150_000, lowercase=True,
                               min_df=2, sublinear_tf=True)
    char_mat = char_vec.fit_transform(cleaned_docs)

    bm25 = None
    try:
        from rank_bm25 import BM25Okapi
        tokenized = []
        for d in docs:
            toks = normalize_for_matching(d).split()
            tokenized.append(toks if toks else ["_"])
        bm25 = BM25Okapi(tokenized)
    except ImportError:
        log.warning("rank_bm25 not installed; lexical index built without BM25")

    leaf_tokens = [frozenset(token_set_for_overlap(_leaf_doc(r))) for r in rows]
    heading_tokens = [
        frozenset(token_set_for_overlap(str(r.get("heading_text") or "")))
        for r in rows
    ]
    residuary = np.array([_is_residuary(c) for c in cths], dtype=bool)

    return {
        "version": INDEX_VERSION,
        "n": len(cths),
        "cths": cths,
        "chapters": np.array([c[:2] for c in cths]),
        "word_vec": word_vec,
        "word_mat": word_mat,
        "char_vec": char_vec,
        "char_mat": char_mat,
        "bm25": bm25,
        "leaf_tokens": leaf_tokens,
        "heading_tokens": heading_tokens,
        "residuary": residuary,
    }


def _index_path():
    return settings.data_dir / "lexical_index.pkl"


def build_and_save() -> dict:
    blob = build_index()
    with open(_index_path(), "wb") as f:
        pickle.dump(blob, f, protocol=pickle.HIGHEST_PROTOCOL)
    log.info("lexical index built", extra={"n": blob["n"]})
    return blob


def get_lexical_index() -> LexicalIndex:
    global _SINGLETON
    with _LOCK:
        if _SINGLETON is None:
            blob = None
            path = _index_path()
            if path.exists():
                try:
                    with open(path, "rb") as f:
                        blob = pickle.load(f)
                    if blob.get("version") != INDEX_VERSION:
                        blob = None
                except Exception:
                    log.warning("lexical index unreadable; rebuilding", exc_info=True)
                    blob = None
            if blob is None:
                blob = build_and_save()
            _SINGLETON = LexicalIndex(blob)
    return _SINGLETON
