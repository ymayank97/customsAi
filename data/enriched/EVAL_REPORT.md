# CustomsAI Pipeline Evaluation Report

- Sample: full set (2086 rows scored)
- LLM mode: **heuristic**  — declared-agreement numbers are a PROXY; the Stage-3 Opus adjudicator is NOT exercised. Deterministic guarantees (invalid-code hard-block, Section XVII pruning) hold in every mode.
- Wall time: 113.91s  (54.6 ms/row)

## Headline safety metric

| Metric | Value | Target |
|---|---|---|
| **INVALID-CODE rate** (final not in master) | **0.0** (0/2086) | **0** |
| Manual-queue rate | 0.0 (0/2086) | — |

## Declared-agreement *(heuristic-mode proxy)*

`declared_cth` is importer-declared (a WEAK reference, not officer-verified gold). Read these as *agreement with what the importer declared*, not accuracy.

| Metric | Value |
|---|---|
| Top-1 (final == declared) | 0.5436 (1134/2086) |
| Set-agreement (declared in conformal_set) | 0.6117 (1276/2086) |
| Chapter-agreement (first 2 digits) | 0.7339 (1531/2086) |

## Revenue sanity

| Metric | Value |
|---|---|
| Fraud findings (is_fraud) | 106 |
| Findings raised (revenue_at_risk > 0) | 106 |
| Rows with verified declared-rate basis (finding withheld) | 23 |
