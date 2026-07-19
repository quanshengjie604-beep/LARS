from __future__ import annotations

import unittest

from vcbrain.postprocess import apply_latest_disclosures, extract_financial_disclosures


class PostprocessTests(unittest.TestCase):
    def setUp(self):
        self.evidence = [{
            "source_id": "s1",
            "startup_id": "startup_acme",
            "source_type": "company_website",
            "document_name": "Acme update",
            "source_uri": "https://acme.test/update",
            "document_date": "2025-05-01",
            "evidence_excerpt": "Acme reached $1.2M ARR and 25 paying customers.",
            "verification_status": "founder_reported",
            "confidence": "medium",
        }]

    def test_explicit_disclosure_fills_only_after_knowledge_date(self):
        disclosures = extract_financial_disclosures(self.evidence)
        old = {
            "startup_id": "startup_acme", "data_cutoff_date": "2025-04-30",
            "arr_usd": None, "customer_count": None, "contradicted_fields": [],
        }
        new = {**old, "data_cutoff_date": "2025-05-01"}
        old_result, _ = apply_latest_disclosures(old, disclosures)
        new_result, audit = apply_latest_disclosures(new, disclosures)
        self.assertIsNone(old_result["arr_usd"])
        self.assertEqual(new_result["arr_usd"], 1_200_000)
        self.assertEqual(new_result["customer_count"], 25)
        self.assertEqual({row["field"] for row in audit}, {"arr_usd", "customer_count"})

    def test_projection_is_never_canonical(self):
        evidence = [{**self.evidence[0], "source_id": "s2", "evidence_excerpt": "Acme expects to reach $5M ARR."}]
        self.assertEqual(extract_financial_disclosures(evidence), [])


if __name__ == "__main__":
    unittest.main()

