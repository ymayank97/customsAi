from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import numpy as np

from backend.app.core import llm_client
from backend.app.core.text_cleaning import normalize_for_matching
from backend.app.scripts import _tariff_pdf_parse, mine_audit_data, _chunks_and_tfidf
from backend.app.services import custada_validator, pipeline, retriever
from backend.scripts.evaluate_retrieval import hierarchy_metrics, load_examples


class TariffBoundaries(unittest.TestCase):
    def test_uncoded_groups_do_not_pollute_heading_or_preceding_leaf(self):
        items, prefixes = _tariff_pdf_parse._parse_tariff_lines([
            "8544 INSULATED WIRE AND CABLE",
            "WHETHER OR NOT FITTED WITH CONNECTORS",
            "- Winding wire :",
            "8544 11 -- Of copper:",
            "8544 11 10 --- Enamelled m 10% -",
            "8544 30 00 - Ignition wiring sets and other wiring sets of a m 15% -",
            "kind used in vehicles, aircraft or ships",
            "- Other electric conductors, for a voltage not",
            "exceeding 1,000V:",
            "8544 42 -- Fitted with connectors:",
            "8544 42 10 --- Paper insulated m 15% -",
        ])
        by_code = {r["cth"]: r for r in items}
        heading = next(r for r in prefixes if r["prefix"] == "8544")
        self.assertNotIn("Winding", heading["description_statutory"])
        wiring = by_code["85443000"]
        self.assertEqual(wiring["description_statutory"], "Ignition wiring sets and other wiring sets of a kind used in vehicles, aircraft or ships")
        self.assertEqual(wiring["group_context"], "")
        self.assertIn("Winding wire", by_code["85441110"]["group_context"])
        self.assertIn("exceeding 1,000V", by_code["85444210"]["group_context"])

    def test_medical_groups_keep_multiline_context_separate_from_leaf(self):
        items, prefixes = _tariff_pdf_parse._parse_tariff_lines([
            "9018 MEDICAL INSTRUMENTS",
            "9018 90 - Other instruments and appliances:",
            "--- Artificial kidney (dialysis) apparatus, blood",
            "transfusion apparatus:",
            "9018 90 31 ---- Artificial kidney (dialysis) apparatus u *7.5% -",
            "9018 90 32 ---- Blood transfusion apparatus u *7.5% -",
            "--- Anesthetic apparatus and instruments:",
            "9018 90 41 ---- Anesthetic apparatus u *7.5% -",
        ])
        self.assertEqual(prefixes[-1]["description_statutory"], "Other instruments and appliances:")
        self.assertEqual(items[0]["description_statutory"], "Artificial kidney (dialysis) apparatus")
        self.assertIn("blood transfusion", items[0]["group_context"])
        self.assertNotIn("Anesthetic", items[1]["description_statutory"])
        self.assertNotIn("kidney", items[2]["group_context"])


class AliasAndSplit(unittest.TestCase):
    def test_split_keeps_matching_variants_together(self):
        descriptions = ["rubber gasket", "rubber gaskets", "conn assembly", "connector assy"]
        descriptions += [f"different product {i}" for i in range(100)]
        alias = pd.DataFrame({"normalized_description": descriptions})
        pool, heldout = mine_audit_data.split_descriptions(alias)
        self.assertTrue(heldout)
        self.assertFalse({normalize_for_matching(s) for s in pool} & {normalize_for_matching(s) for s in heldout})
        self.assertEqual(pool | heldout, set(descriptions))

    def test_alias_lookup_normalizes_both_sides_and_preserves_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            pd.DataFrame({
                "normalized_description": ["rubber gaskets", "rubber gaskets for pumps", "unrelated food"],
                "cth": ["40169390", "40169310", "12345678"],
            }).to_parquet(Path(directory) / "audit_alias_index.parquet")
            with patch.object(retriever.settings, "data_dir", Path(directory)), \
                 patch.object(retriever, "_ALIAS_CACHE", None), \
                 patch.object(retriever, "_ALIAS_TOKENS", {}), \
                 patch.object(retriever, "_ALIAS_POSTINGS", {}):
                self.assertEqual(retriever._alias_candidates("RUBBER GASKET"), ["40169390"])
                self.assertEqual(set(retriever._alias_candidates("rubber gasket pump")), {"40169390", "40169310"})

    def test_unverified_fewshot_is_not_presented_as_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "fewshot_examples.json").write_text(json.dumps([
                {"chapter": "85", "description": "connector", "declared_cth": "85369090"},
                {"description": "verified product", "gold_cth": "85443000", "label_source": "officer confirmation"},
            ]), encoding="utf-8")
            with patch.object(llm_client.settings, "data_dir", Path(directory)), \
                 patch.object(llm_client, "_FEWSHOT_CACHE", None):
                examples = llm_client._load_fewshot()
                self.assertEqual(len(examples), 1)
                self.assertEqual(examples[0]["correct_cth"], "85443000")
                self.assertEqual(llm_client._load_fewshot(headings={"8536"}), [])


class EligibleCodeValidation(unittest.TestCase):
    def setUp(self):
        self.master = {c: {"bcd_effective": 0.0, "bcd_statutory": 15.0} for c in ["87089900", "85443000"]}
        exists = patch.object(custada_validator, "cth_exists", side_effect=lambda c: c in self.master)
        lookup = patch.object(custada_validator, "get_master_row", side_effect=lambda c: self.master.get(c))
        exists.start()
        lookup.start()
        self.addCleanup(exists.stop)
        self.addCleanup(lookup.stop)

    def test_real_but_excluded_code_is_replaced_and_zero_rate_is_preserved(self):
        result = custada_validator.validate("87089900", 15.0, "85443000", ["85443000"])
        self.assertEqual(result["cth_final"], "85443000")
        self.assertEqual(result["bcd_final"], 0.0)
        self.assertTrue(result["hallucination"])

    def test_no_eligible_candidates_routes_to_manual(self):
        result = custada_validator.validate("87089900", 15.0, "87089900", [])
        self.assertEqual(result["cth_final"], "")
        self.assertTrue(result["manual_queue"])

    def test_heuristic_never_reintroduces_excluded_candidates(self):
        result = llm_client.HeuristicClassifier().classify_batch([
            {"id": "1", "candidates": [{"cth": "87089900", "legal_pruned": True}]},
        ])[0]
        self.assertEqual(result.cth, "")

    def test_parquet_notification_arrays_are_preserved(self):
        values = np.array(["50/2017", "25/1999"])
        self.assertEqual(custada_validator._maybe_list(values), ["50/2017", "25/1999"])
        self.assertEqual(retriever._list({"notifications": values}, "notifications"), ["50/2017", "25/1999"])
        self.assertEqual(custada_validator._maybe_list(np.array([])), [])


class PipelineReuse(unittest.TestCase):
    def test_reuse_respects_notification_rate_and_siblings_and_finalises_every_row(self):
        rows = [
            {"row_idx": i, "item_description": "connector", "declared_cth": "85369090", "declared_bcd": 0.0,
             "declared_notification": "A", "siblings": [], "assess_val": 1000 + i}
            for i in range(5)
        ]
        rows[2]["declared_notification"] = "B"
        rows[3]["siblings"] = [{"item_description": "machine", "declared_cth": "84713010"}]
        rows[4]["declared_bcd"] = 15.0
        classifier = unittest.mock.Mock()
        classifier.classify_batch.side_effect = lambda items: [
            {"id": it["id"], "cth": "85369090"} for it in items
        ]
        candidates = [{"cth": "85369090"}]
        legal = {"candidates": candidates, "docket": [], "pruned": [], "summary": ""}
        with patch.object(retriever, "enrich_row", side_effect=lambda r, siblings: dict(r)), \
             patch.object(retriever, "generate_candidates", return_value=candidates) as retrieve, \
             patch.object(pipeline.legal_constraints, "apply", return_value=legal), \
             patch.object(pipeline, "_finalise_row", side_effect=lambda r, ctx, cls, *a, **kw: {"row_idx": r["row_idx"], "amount": r["assess_val"], "id": cls["id"]}):
            results = pipeline.process_rows(rows, batch_size=10, classifier=classifier)
        self.assertEqual(retrieve.call_count, 1)
        self.assertEqual(len(classifier.classify_batch.call_args.args[0]), 4)
        self.assertEqual(len(results), 5)
        self.assertEqual([r["amount"] for r in results], [1000, 1001, 1002, 1003, 1004])
        self.assertEqual([r["id"] for r in results], ["0", "1", "2", "3", "4"])


class RetrievalEvaluation(unittest.TestCase):
    def test_hierarchy_metrics_separate_heading_hit_from_leaf_miss(self):
        metrics = hierarchy_metrics([
            {"reference": "85369090", "codes": ["85369010", "85369090"]},
            {"reference": "85443000", "codes": ["87089900"]},
        ])
        self.assertEqual(metrics["heading"]["top1"], 0.5)
        self.assertEqual(metrics["tariff_item"]["top1"], 0.0)
        self.assertEqual(metrics["tariff_item"]["candidate_recall"], 0.5)

    def test_gold_csv_preserves_leading_zero_and_requires_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gold.csv"
            path.write_text("item_description,gold_cth,label_source\nlive horse,01012100,officer review\n", encoding="utf-8")
            examples, reference = load_examples(path)
            self.assertEqual(reference, "gold_cth")
            self.assertEqual(examples.iloc[0].gold_cth, "01012100")
            path.write_text("item_description,gold_cth\nlive horse,01012100\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "label_source"):
                load_examples(path)


class NotificationChunks(unittest.TestCase):
    def test_long_text_is_bounded_and_preserved_without_overlap(self):
        text = "\n\n".join(f"Section {i}. " + "a" * 80 for i in range(50))
        chunks = list(_chunks_and_tfidf.split_notification(text, chunk_size=100, overlap=0))
        self.assertTrue(all(len(c) <= 100 for c in chunks))
        self.assertEqual("".join("".join(chunks).split()), "".join(text.split()))

    def test_unbroken_text_and_overlap_make_progress(self):
        chunks = list(_chunks_and_tfidf.split_notification("x" * 350, chunk_size=100, overlap=20))
        self.assertEqual([len(c) for c in chunks], [100, 100, 100, 100, 30])
        with self.assertRaises(ValueError):
            list(_chunks_and_tfidf.split_notification("abc", chunk_size=20, overlap=20))


if __name__ == "__main__":
    unittest.main()
