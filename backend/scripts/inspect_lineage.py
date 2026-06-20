"""Inspect the lineage data we have to confirm the fix plan."""
from __future__ import annotations

import pandas as pd

sub = pd.read_parquet("data/enriched/_tariff_subheading_index.parquet")
m = pd.read_parquet("data/enriched/cth_master.parquet").set_index("cth")

# 1) Confirm 4-digit and 6-digit lookups exist for residuary cases
test_codes = ["85369090", "85044090", "85447030", "84439990"]
for cth in test_codes:
    h4 = sub[(sub["prefix"] == cth[:4]) & (sub["length"] == 4)]
    h6 = sub[(sub["prefix"] == cth[:6]) & (sub["length"] == 6)]
    print(f"cth {cth}")
    print(f"  4d({cth[:4]}): {(h4.iloc[0].description_statutory if len(h4) else '(missing)')[:120]}")
    print(f"  6d({cth[:6]}): {(h6.iloc[0].description_statutory if len(h6) else '(missing)')[:120]}")
    leaf = m.loc[cth] if cth in m.index else None
    print(f"  leaf desc:    {(leaf['description_statutory'] if leaf is not None else '(missing)')!r}")
    print()

# 2) Quantify how many leaves currently have only "Other"/empty statutory text
flat_other = m[m["description_statutory"].fillna("").str.strip().str.lower().isin(
    ["other", "others", "other:", "other.", "other apparatus", "other:- ---"]
)]
print(f"Master rows whose statutory desc is bare 'Other'-like: {len(flat_other)} / {len(m)}")

# 3) Quantify rows where description_combined is short (likely losing context)
short_combined = m[m["description_combined"].fillna("").str.len() < 25]
print(f"Master rows with description_combined < 25 chars:        {len(short_combined)} / {len(m)}")
print()
print("Sample very-short combined descriptions (first 8):")
print(short_combined[["heading", "description_combined"]].head(8).to_string())
