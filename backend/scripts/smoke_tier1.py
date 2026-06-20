"""Smoke-test the declared-first Tier 1 against real BE rows."""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

import pandas as pd

from backend.app.services import retriever


def main() -> None:
    xl = pd.read_excel("data/input_data.xlsx", engine="openpyxl", dtype=str).fillna("")
    cth_col = "CTH" if "CTH" in xl.columns else next(c for c in xl.columns if "cth" in c.lower())
    desc_col = (
        next((c for c in xl.columns if "item" in c.lower() and "desc" in c.lower()), None)
        or next(c for c in xl.columns if "desc" in c.lower())
    )
    bcd_col = next(c for c in xl.columns if "bcd" in c.lower() and "rate" in c.lower())
    ntfn_col = next((c for c in xl.columns if "notn" in c.lower() or "ntfn" in c.lower()), None)

    print(f"using cth={cth_col} desc={desc_col} bcd={bcd_col} ntfn={ntfn_col}")

    samples = xl.head(40)
    hits = miss = 0
    fraud_caught = 0
    for _, r in samples.iterrows():
        cth = str(r[cth_col]).strip()
        try:
            bcd = float(r[bcd_col])
        except Exception:
            bcd = None
        desc = str(r[desc_col])
        ntfn = str(r[ntfn_col]) if ntfn_col else ""
        res = retriever.tier1_short_circuit(cth, bcd, desc, declared_notification=ntfn)
        if res:
            hits += 1
            sim = res["similarity"]
            exp = res["bcd_final"]
            delta = (exp - bcd) if (exp is not None and bcd is not None) else None
            tag = ""
            if delta is not None and delta > 0.01:
                tag = f" UNDER-DECLARED by {delta:.2f}%"
                fraud_caught += 1
            elif delta is not None and delta < -0.01:
                tag = f" over-declared by {abs(delta):.2f}%"
            print(
                f"[T1] cth={cth} declared_bcd={bcd} expected={exp} sim={sim:.2f} src={res['bcd_source']}{tag}"
            )
            print(f"     desc: {desc[:90]}")
        else:
            miss += 1
            print(f"[--] cth={cth} desc={desc[:80]} -> escalated to LLM")
    print()
    print(f"tier1 hits {hits}/{hits + miss}, fraud-by-BCD caught at Tier 1: {fraud_caught}")


if __name__ == "__main__":
    main()
