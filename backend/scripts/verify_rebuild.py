"""Verify the rebuilt master has heading_text + rich description_combined,
and that retrieval now surfaces 85369090 for a wire/cable connector query."""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

import pandas as pd

from backend.app.services import retriever


def main() -> None:
    m = pd.read_parquet("data/enriched/cth_master.parquet").set_index("cth")
    print("=== New explicit columns + chained description for the test CTHs ===\n")
    for cth in ("85369090", "85367000", "85369010", "85044090", "85447030"):
        if cth not in m.index:
            print(f"{cth}: missing from master")
            continue
        r = m.loc[cth]
        print(f"cth {cth}")
        print(f"  heading_text:    {(r['heading_text'] or '')[:160]}")
        print(f"  subheading_text: {(r['subheading_text'] or '')[:120]}")
        print(f"  description:     {(r['description'] or '')[:80]!r}")
        print(f"  combined ({len(r['description_combined'])} chars):")
        print(f"    {r['description_combined'][:280]}")
        print()

    # Stats: how much shorter has the bare-"Other" problem become?
    other_like = m[m["description_combined"].fillna("").str.lower().str.strip().isin({"other", "others"})]
    short = m[m["description_combined"].fillna("").str.len() < 25]
    print(f"description_combined still bare 'Other':              {len(other_like)} / {len(m)}")
    print(f"description_combined still < 25 chars:                {len(short)} / {len(m)}")
    print()

    # Now test the actual retrieval path
    print("=== Retrieval test: 'CONNECTOR (CONTACT ELEMENT FOR WIRE AND CABLE)' ===\n")
    cands = retriever.retrieve_candidates(
        "CONNECTOR (CONTACT ELEMENT FOR WIRE AND CABLE) FOR CAPTIVE CONSUMPTION",
        top_k=10,
    )
    for i, c in enumerate(cands, 1):
        marker = "  <-- target!" if c["cth"] == "85369090" else ""
        print(
            f"  #{i}  {c['cth']}  ch{c['chapter']}  hd{c['heading']}  res={c['is_residuary']}  score={c['retrieval_score']:.3f}{marker}"
        )
        print(f"       leaf:   {c['leaf_text'][:90]}")
        print(f"       hd_txt: {c['heading_text'][:120]}")


if __name__ == "__main__":
    main()
