# Improving classification accuracy with limited data

The first priority is a reliable benchmark of real Bills of Entry. The existing saved evaluation measures agreement with declarations, not classification correctness. A code existing in the master proves validity; it does not prove the product belongs under that code.

## What the code and data showed

- All held-out matching keys appeared in the original alias index. The split protected the few-shot bank but left retrieval exposed to test answers.
- Importer-declared examples were passed to the model under `correct_cth`. These declarations are the hypothesis being audited.
- Unnumbered tariff groups were appended to the preceding numbered record. For example, the 8544 heading acquired “Winding wire,” and the wiring-set leaf acquired the following voltage group. This changes the hierarchy presented to retrieval and adjudication.
- Notification lists read from Parquet are NumPy arrays. Two list readers silently discarded these arrays, losing notification metadata.
- The original large input workbook is absent locally. Existing evaluation rows and historical input aliases need to survive rebuilds.
- A model-generated list named `conformal_set` had no calibration procedure. It is an ambiguity set; it has no measured coverage guarantee. Genuine conformal prediction requires a calibration procedure and suitable data assumptions. See [Angelopoulos and Bates](https://arxiv.org/abs/2107.07511).

The implementation corrects these issues. Tariff groups are stored separately from leaf text; their context is attached to relevant descendants. Alias matching normalizes both sides and uses token postings. Both aliases and examples exclude held-out matching keys. Runtime examples require `gold_cth` and `label_source`. Validation restricts outputs to legally eligible candidates, including ambiguity-set members, and preserves zero rates. A heuristic or overridden revenue finding is routed for review.

## Keep the decision path small

Use one tariff hierarchy, one candidate generator, one legal-rule pass, and one adjudicator. Resolve rates separately from the classification decision. Reuse identical retrieval queries and identical adjudication inputs within a job, while calculating each row's revenue independently. The reuse key includes description, declaration, rate, claimed notification, and sibling context.

Keep lexical retrieval as the baseline. Benchmark a dense retriever only if reviewed examples show missing synonyms or product functions that lexical retrieval misses. This is an experiment to measure on your data: BEIR found BM25 a robust baseline across its retrieval tasks, while stronger reranking methods had higher computational costs. Those results do not establish which approach will perform best on customs descriptions. [BEIR paper](https://arxiv.org/abs/2104.08663).

The runtime no longer requires Torch, Transformers, sentence-transformers, FAISS, datasets, Accelerate, or LangChain. Notification chunking uses a small local splitter. Existing installations can retain those packages; the project no longer imports them.

## Build labels that match the business problem

Start with a few hundred officer-confirmed real BE cases, focusing first on the chapters actually audited. This is a practical starting sample, not a statistical sufficiency claim. Include common products, suspected misdeclarations, legitimate concessions, noisy part-number descriptions, and ambiguous cases. Record the original description, verified code, evidence source, review date, applicable tariff version, and the facts supporting the decision.

Use `data/gold_labels.template.csv` for the minimum evaluation schema. `gold_cth` must preserve all eight digits, including leading zeroes; `label_source` records the officer confirmation or ruling citation. The evaluator trusts supplied labels and provenance; it does not verify the external documents. Keep the test labels separate from aliases and prompting examples. Group related descriptions and product families across splits so minor spelling or part-number changes do not leak answers.

Tariff descriptions and real BE descriptions are different populations. “Other” in a tariff catalog can be valid when its parents are known; the same word on a BE cannot identify a product. Report these populations separately. Collect missing material, function, voltage, composition, and assembly facts from the input or supporting product documents when they affect the candidate distinction. The cleaner now retains product measurements, composition, model identifiers and numeric specifications. Keep the raw description as well.

When two items have the same available description but require different classifications, adding model complexity cannot supply the missing product facts. Ask for the discriminator or route for review. Do not turn importer declarations or model-generated labels into verified training answers.

## Measure the stage that failed

| Symptom | Next action |
|---|---|
| Correct heading absent from candidates | Improve hierarchy text and synonym coverage; evaluate a wider heading pool or hybrid retrieval |
| Correct heading present, correct leaf absent | Expand sibling leaves within that heading and retain intermediate group context |
| Correct leaf present, wrong final selection | Inspect distinguishing product facts, relevant notes, and verified examples |
| Classification supported, rate wrong | Inspect notification conditions, effective dates, origin, and claim evidence separately |
| Review queue too large | Measure review precision and coverage on verified cases before changing thresholds |

Track accuracy at 2, 4, 6, and 8 digits; candidate recall before injecting the declaration; review coverage; false revenue findings; and latency per unique adjudication input. The new retrieval evaluator reports hierarchy metrics, source cohorts, p50/p95 latency, invalid candidates, alias conflicts, and evaluation overlap. It rejects a leaked evaluation split. It evaluates Stage 1; a good retrieval result still requires a separate end-to-end adjudicator evaluation.

```powershell
.\.venv\Scripts\python.exe -m backend.scripts.evaluate_retrieval --full
.\.venv\Scripts\python.exe -m backend.scripts.evaluate_retrieval --input data\gold_labels.csv --full
.\.venv\Scripts\python.exe -m backend.scripts.eval_pipeline --full --offline
.\.venv\Scripts\python.exe -m backend.scripts.run_validation
.\.venv\Scripts\python.exe -m backend.scripts.test_regression_classification
```

The retrieval reports are written to `var/evaluation/retrieval.json` and `.md`. `--offline` makes the full pipeline evaluation use the heuristic classifier even when API credentials are configured.

## Remaining limits

No expert-labelled gold set is present, and no live adjudicator was exercised during this work. The current source tariff files are a local snapshot; this work does not establish that they reflect the tariff on the import date. Remaining missing hierarchy text needs source-page checks. The starter precedent summaries and rate/FTA/ITA keyword gates also need evidence-based validation; a shared heading or product keyword alone should not be treated as proof of an applicable ruling or concession.

The next accuracy experiment should therefore compare final decisions against reviewed real BE labels. Freeze that benchmark, inspect its retrieval misses, and add only the data or model component that resolves a measured failure.
