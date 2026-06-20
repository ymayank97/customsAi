# Precedent Corpus — Starter Build Report

> Starter corpus of Indian customs **classification rulings** for the pipeline's
> Stage 4 "contested-pair precedent check". Built offline; the runtime reads it
> only. Rebuild with:
> `.venv/Scripts/python.exe -m backend.app.scripts.build_precedent_corpus`

## Counts

- **Rulings: 21** (all verified — see "Verification" below).
- Authorities: **7** distinct.
- Distinct contested pairs indexed: **17**.

## Authorities covered

| Authority | Rulings |
|---|---|
| CAAR Mumbai | 5 |
| CAAR Delhi | 4 |
| CESTAT Delhi | 4 |
| Supreme Court of India | 4 |
| CESTAT Chennai | 2 |
| CESTAT Mumbai | 1 |
| CBEC Circular 51/2004-Cus | 1 |

## Contested pairs covered

Auto / electronics / instruments (the live BE chapters 84/85/87/90):

- `8537|9032` — Mobis BCM/IBU (programmable process controller vs PLC/panel)
- `8537|8708` — Valeo EV VCU/PCU; EPS-ECU power-steering unit
- `8483|8708` — Kafila Forge universal joints
- `7318|8708` — G.S. Auto spring bolts/nuts (Supreme Court sole-use test)
- `5703|8708` — Uniproducts car matting (Supreme Court)
- `8504|8708` — Mitsubishi & Denso automotive inverter assemblies
- `8504|8537` — Sanmina AC-DC/DC-DC power modules
- `8531|9013` — Secure Meters LCDs (Supreme Court)
- `8536|8537` — Lever Combination Switch (CBEC Circular)
- `8471|8528` — Hitevision interactive flat panel
- `85177990|85258900` — Apple rear camera module
- `8517|8544` — Nokia & Cisco SFP optical transceivers (vs connector/LCD)
- `85176290|85177010` — Huawei DWDM populated PCB assemblies
- `85176290|85177090` — Ingram Micro data-centre switch routers
- `85177010|85177090` — Bharti Airtel router cards; Vodafone Idea matrix cards

General GIR / HSN precedents (broaden the corpus, useful as legal-basis anchors):

- `0802|2008` — Universal Impex roasted areca nuts (HSN EN, specificity)
- `1513|3305` — Madhan Agro coconut oil (Supreme Court, GIR Rule 3(c))

> Note: the canonical example pair `8536|8544` (wiring harness) has **no direct
> on-point ruling** in this starter set — for that query the index falls back to
> TF-IDF matches on the adjacent 8544 (transceiver) and 8536 (switch) rulings.
> A future build should add a verified wiring-harness ruling (e.g. Valeo's
> 8544 harness AAR) when its full text can be retrieved.

## Verification

Every row was retrieved via `WebFetch`/`curl`+`pdfplumber` from a primary or
reputable secondary source (URL stored per row in `rulings.parquet`):
official CESTAT order PDFs (cestat.gov.in), Supreme Court PDF (sci.gov.in),
CAAR ruling PDFs (taxguru.in), and law-firm / LiveLaw / IndianBarAssociation
case reports. Rulings whose outcome could not be confirmed from the retrieved
text (e.g. paywalled Viewsonic projector rejection, Siemens VCB parts) were
**omitted**. Where only the secondary report was machine-readable, the outcome
CTH and legal basis were taken verbatim from that report.

## Schema — `data/precedent/rulings.parquet`

| column | type | notes |
|---|---|---|
| `ruling_id` | str | stable slug, unique |
| `title` | str | short human title |
| `authority` | str | tribunal/authority + bench |
| `date` | str | ISO date (year-only where the day was unverified) |
| `headings` | list[str] | 4-digit headings in play |
| `cth_codes` | list[str] | 8-digit tariff items where stated |
| `competing_pair` | str | sorted `"AAAA\|BBBB"` the dispute turned on |
| `outcome_cth` | str | heading/item held correct |
| `legal_basis` | str | GIR rule / section/chapter note / circular / HSN EN |
| `summary` | str | 2-4 sentence factual summary |
| `source_url` | str | verification URL |

## Interface for Agent A5 (pipeline Stage 4)

Import the read-only helper from `backend/app/services/precedent.py`:

```python
from backend.app.services.precedent import lookup_precedents

results = lookup_precedents(
    cth_a="85369090",      # first contested code/heading (4- or 8-digit, any format)
    cth_b="85443000",      # optional second contested code/heading
    query_text="vehicle wiring harness",  # PII-free item/dispute text
    k=5,                   # max results
)
# -> list[dict] sorted best-first; each dict = the ruling row plus:
#      "score"            float (TF-IDF cosine + 1.0 * heading_overlap)
#      "heading_overlap"  int   (# of the contested headings the ruling touches)
# Returns [] if the index file is missing or nothing matches.
```

- The module **only reads** `data/precedent/precedent_index.pkl` (loaded once,
  cached, thread-safe). No network at runtime.
- Scoring: exact overlap on the contested 4-digit headings dominates (boost 1.0
  each), with TF-IDF cosine over title+summary+legal_basis+codes as the
  tie-breaker / free-text fallback.
- A ruling with `heading_overlap >= 1` is an on-point precedent for the pair;
  `heading_overlap == 0` rows are surfaced only by text similarity.
