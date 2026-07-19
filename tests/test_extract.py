from __future__ import annotations

import unittest

from vcbrain.extract import dedupe_funding_rounds, extract_funding_round
from vcbrain.models import FundingRound


class FundingExtractionTests(unittest.TestCase):
    def extract(self, text: str):
        return extract_funding_round(
            company_name="Acme AI",
            startup_id="startup_acme",
            text=text,
            document_date="2025-04-03",
            date_basis="announcement",
            source_name="fixture",
            source_url="https://example.test/story",
            source_id="source_1",
        )

    def test_round_amount_is_not_valuation(self):
        round_ = self.extract("Acme AI raises $5M seed round at a $50M valuation")
        self.assertIsNotNone(round_)
        self.assertEqual(round_.amount_usd, 5_000_000)
        self.assertEqual(round_.stage, "seed")

    def test_total_to_date_is_not_round_amount(self):
        self.assertIsNone(self.extract("Acme AI announces funding bringing total raised to $12M"))

    def test_non_usd_is_preserved_without_conversion(self):
        round_ = self.extract("Acme AI raises €3M seed financing")
        self.assertEqual(round_.amount_original, 3_000_000)
        self.assertEqual(round_.currency, "EUR")
        self.assertIsNone(round_.amount_usd)

    def test_launch_is_not_funding(self):
        self.assertIsNone(self.extract("Launch: Acme AI ships a new workflow product"))

    def test_conflicting_same_round_is_retained_as_conflict(self):
        first = FundingRound(
            "startup_acme", "Acme AI", "2025-04-03", "announcement",
            stage="seed", amount_usd=5_000_000, amount_original=5_000_000,
            currency="USD", source_ids=["a"], source_names=["one"],
        )
        second = FundingRound(
            "startup_acme", "Acme AI", "2025-04-03", "announcement",
            stage="seed", amount_usd=6_000_000, amount_original=6_000_000,
            currency="USD", source_ids=["b"], source_names=["two"],
        )
        [merged] = dedupe_funding_rounds([first, second])
        self.assertTrue(merged.contradicted)
        self.assertIsNone(merged.amount_usd)
        self.assertEqual(merged.source_ids, ["a", "b"])


if __name__ == "__main__":
    unittest.main()

