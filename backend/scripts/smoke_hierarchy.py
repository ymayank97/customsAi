"""Sanity-check the hierarchical candidate enrichment + heading-grouped prompt."""
from __future__ import annotations

import json
import warnings

warnings.filterwarnings("ignore")

from backend.app.core.llm_client import _format_user_message
from backend.app.services import retriever


def main() -> None:
    desc = "CONNECTOR (CONTACT ELEMENT FOR WIRE AND CABLE) FOR CAPTIVE CONSUMPTION"
    print(f"query: {desc}\n")

    declared = "85369090"
    cands = retriever.retrieve_candidates(desc, top_k=8)
    if declared not in {c["cth"] for c in cands}:
        inj = retriever.build_candidate_from_master(declared, source="declared_cth")
        if inj:
            cands.append(inj)
            print(f"  injected declared CTH {declared} (was not in retrieval top-K)")
    print(f"got {len(cands)} candidates\n")
    for c in cands[:5]:
        print(f"  {c['cth']}  ch={c['chapter']}  hd={c['heading']}  6d={c['subheading_6']}  res={c['is_residuary']}")
        print(f"    heading_text: {c['heading_text'][:120]}")
        print(f"    leaf_text:    {c['leaf_text'][:120]}")
        print(f"    bcd_eff={c['bcd_effective']}  bcd_stat={c['bcd_statutory']}")
        print()

    # Build the LLM prompt JSON to inspect the structure.
    item = {
        "id": "1",
        "item_description": desc,
        "declared_cth": "85369090",
        "declared_bcd": 10.0,
        "declared_notification": "",
        "candidates": cands,
    }
    msg = _format_user_message([item])
    print("=== Prompt structure (heading-grouped) ===")
    json_start = msg.index("[", msg.index("INPUT:"))
    json_end = msg.rindex("]") + 1
    payload = json.loads(msg[json_start:json_end])
    item0 = payload[0]
    print(f"item id={item0['id']}, desc={item0['item_description'][:60]}")
    print(f"declared: cth={item0['declared_cth']} bcd={item0['declared_bcd']}")
    print(f"\n{len(item0['candidate_headings'])} heading bucket(s):")
    for h in item0["candidate_headings"]:
        print(f"\n  Heading {h['heading_4digit']} (chapter {h['chapter_2digit']} — {h['chapter_title']})")
        print(f"    {h['heading_text'][:140]}")
        for c in h["candidates"]:
            tag = "RESIDUARY" if c["is_residuary"] else "specific"
            print(f"      - {c['cth']}  [{tag}]  bcd_eff={c['bcd_effective']}  score={c['retrieval_score']}")
            print(f"        sub: {c['subheading_text'][:90] or '—'}")
            print(f"        leaf: {c['leaf_description'][:90]}")


if __name__ == "__main__":
    main()
