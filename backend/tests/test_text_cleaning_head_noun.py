"""Regression tests for head-noun extraction used in cross-encoder reranking."""
from __future__ import annotations

import unittest

from backend.app.core.text_cleaning import boost_head_noun, extract_head_noun


class HeadNounExtraction(unittest.TestCase):
    def test_connector_wire_cable_removes_boilerplate(self):
        q = "CONNECTOR (CONTACT ELEMENT FOR WIRE AND CABLE) FOR CAPTIVE CONSUMPTION"
        head, mod = extract_head_noun(q)
        self.assertEqual(head, "connector contact element")
        self.assertEqual(mod, "wire and cable")

    def test_boost_formats_head_then_full(self):
        q = "RUBBER GASKET FOR WASHING MACHINE"
        b = boost_head_noun(q)
        self.assertIn("rubber gasket", b)
        self.assertTrue(b.startswith("rubber gasket."))
        self.assertIn("for washing machine", b)

    def test_articles_of_plastic_for_packing_keeps_of_in_head(self):
        head, mod = extract_head_noun("ARTICLES OF PLASTIC FOR PACKING")
        self.assertEqual(head, "articles of plastic")
        self.assertEqual(mod, "packing")

    def test_parts_of_steel_no_false_split(self):
        head, mod = extract_head_noun("PARTS OF STEEL")
        self.assertEqual(mod, "")
        self.assertEqual(head, "parts of steel")

    def test_bearing_of_rubber_for_automobile(self):
        head, mod = extract_head_noun("BEARING OF RUBBER FOR AUTOMOBILE")
        self.assertEqual(head, "bearing of rubber")
        self.assertEqual(mod, "automobile")

    def test_used_in_pattern(self):
        head, mod = extract_head_noun("PCB ASSEMBLY USED IN INVERTER")
        self.assertEqual(head, "pcb assembly")
        self.assertEqual(mod, "inverter")

    def test_no_modifier_returns_cleaned_only(self):
        head, mod = extract_head_noun("STEEL PLATE 6MM")
        self.assertEqual(mod, "")
        self.assertEqual(head, "steel plate 6mm")


if __name__ == "__main__":
    unittest.main()
