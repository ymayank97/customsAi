"""Smoke-test the head-noun parser and confirm BGE reranker + boosted query
moves 85369090 (residuary connector) into the top retrieval slots.

Run:
    cd customsAI
    python -m backend.scripts.verify_head_noun
"""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

from backend.app.core.text_cleaning import boost_head_noun, extract_head_noun
from backend.app.services import retriever


HEAD_NOUN_CASES = [
    "CONNECTOR (CONTACT ELEMENT FOR WIRE AND CABLE) FOR CAPTIVE CONSUMPTION",
    "PARTS FOR LOUD SPEAKER",
    "BEARING USED IN AUTOMOTIVE GEARBOX",
    "RUBBER GASKET FOR WASHING MACHINE",
    "DIODE 1N4007",
    "STEEL PLATE 6MM",
    "ARTICLES OF PLASTIC FOR PACKING",
    "PCB ASSEMBLY USED IN INVERTER",
]


def show_head_noun() -> None:
    print("=== Head-noun extraction ===\n")
    for q in HEAD_NOUN_CASES:
        head, mod = extract_head_noun(q)
        boosted = boost_head_noun(q)
        print(f"  query:    {q}")
        print(f"  head:     {head!r}")
        print(f"  modifier: {mod!r}")
        print(f"  boosted:  {boosted!r}\n")


def show_retrieval() -> None:
    print("=== Retrieval after boost + BGE reranker ===\n")
    queries = [
        "CONNECTOR (CONTACT ELEMENT FOR WIRE AND CABLE) FOR CAPTIVE CONSUMPTION",
        "RUBBER GASKET FOR WASHING MACHINE",
        "PARTS FOR LOUD SPEAKER",
    ]
    targets = {
        queries[0]: "85369090",
        queries[1]: None,  # just inspect top results
        queries[2]: None,
    }
    for q in queries:
        print(f"-- {q!r}")
        cands = retriever.retrieve_candidates(q, top_k=10)
        target = targets.get(q)
        for i, c in enumerate(cands, 1):
            marker = "  <-- target!" if target and c["cth"] == target else ""
            print(
                f"  #{i:>2} {c['cth']}  hd{c['heading']}  res={c['is_residuary']}  "
                f"score={c['retrieval_score']:.3f}{marker}"
            )
            print(f"        leaf:   {c['leaf_text'][:90]}")
        print()


def main() -> None:
    show_head_noun()
    show_retrieval()


if __name__ == "__main__":
    main()
