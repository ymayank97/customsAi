"""Regression coverage for product facts previously discarded by cleaning."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from backend.app.core.text_cleaning import (
    clean_description,
    normalize_for_matching,
    token_set_for_overlap,
)
from backend.app.services import lexical_index


class ProductFacts(unittest.TestCase):
    def test_voltage_and_dimensions_do_not_collapse(self):
        first = "Insulated copper cable 1000 V 0.5 mm"
        second = "Insulated copper cable 500 V 1.5 mm"
        self.assertEqual(clean_description(first), first.lower())
        self.assertEqual(clean_description(second), second.lower())
        self.assertNotEqual(normalize_for_matching(first), normalize_for_matching(second))

    def test_measurements_grades_and_identifiers_survive(self):
        for description in (
            "Steel plate 6MM grade 304",
            "Motor 1000V 1500W 50Hz",
            "Vessel capacity 1.5 L weight 500 KG",
            "Model ABC-99/A part no XYZ-123/X4 serial 12345678",
            "Cotton 60% polyester 40%",
            "Film width 1,000 mm thickness 0.05 mm",
            "Cable voltage <= 1000 V diameter 0.5-1.5 mm",
            "Sheet 10 x 20 x 0.5 mm",
        ):
            with self.subTest(description=description):
                self.assertEqual(clean_description(description), description.lower())

    def test_origin_brand_and_use_do_not_swallow_product_facts(self):
        for description in (
            "Made in China stainless steel grade 304",
            "Country of origin India copper cable 500V",
            "Brand: ACME cotton 60% polyester 40%",
            "Rubber gasket for industrial use",
        ):
            with self.subTest(description=description):
                cleaned = clean_description(description)
                self.assertIn(description.lower().replace(":", ""), cleaned)

    def test_explicit_declaration_codes_and_counts_are_removed(self):
        for code in ("HSN: 85444210", "CTH: 8544 42 10", "HS CODE: 85.44.42.10"):
            with self.subTest(code=code):
                cleaned = clean_description(f"{code} cable 24 V 0.5 mm QTY: 50 NOS AS PER INVOICE")
                self.assertEqual(cleaned, "cable 24 v 0.5 mm")

    def test_code_removal_stops_before_a_following_specification(self):
        self.assertEqual(clean_description("CTH 85444210 1000 V copper cable"), "1000 v copper cable")
        self.assertEqual(clean_description("CHAPTER 85 24 V copper cable"), "24 v copper cable")
        self.assertEqual(clean_description("HS CODE 5208 60% cotton"), "60% cotton")

    def test_matching_preserves_decimals_percentages_and_short_units(self):
        tokens = set(normalize_for_matching("Cable 6 V 0.5 mm cotton 60%").split())
        self.assertTrue({"6", "v", "0.5", "mm", "60%"} <= tokens)
        self.assertIn("6", token_set_for_overlap("Steel plate 6 mm"))
        self.assertNotEqual(normalize_for_matching("plate 0.5 mm"), normalize_for_matching("plate 0.8 mm"))

    def test_cleaning_is_idempotent_and_handles_empty_input(self):
        for description in (
            None, "", "   ", 1000,
            "HSN: 85444210 Cable (1000V), .5 mm; 60% copper. 100 PCS",
            "MODEL ABC-99/A <= 1000 V 0.5-1.5 mm",
        ):
            with self.subTest(description=description):
                cleaned = clean_description(description)
                self.assertEqual(clean_description(cleaned), cleaned)


class SpecificationRetrieval(unittest.TestCase):
    def test_retrieval_distinguishes_cables_by_decimal_dimension(self):
        descriptions = ["Insulated copper cable 500 V 0.5 mm", "Insulated copper cable 500 V 0.8 mm"]
        master = pd.DataFrame([
            {"cth": "85444210", "description": descriptions[0]},
            {"cth": "85444290", "description": descriptions[1]},
        ])
        with patch.object(lexical_index.pd, "read_parquet", return_value=master), \
             patch.object(lexical_index, "_master_fingerprint", return_value=(0, 0)):
            index = lexical_index.LexicalIndex(lexical_index.build_index())
        for description, expected in zip(descriptions, master["cth"]):
            with self.subTest(description=description):
                self.assertEqual(index.search(description, k=1)[0][0], expected)


if __name__ == "__main__":
    unittest.main()
