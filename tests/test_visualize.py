from __future__ import annotations

import unittest

from vcbrain.visualize import render_html


class VisualizationTests(unittest.TestCase):
    def test_deterministic_and_escaped(self):
        companies = [{"startup_id": "x", "name": "<script>alert(1)</script>"}]
        rounds = [{
            "startup_id": "x", "company_name": "<script>alert(1)</script>",
            "date": "2025-01-01", "date_basis": "announcement", "amount_usd": None,
            "source_names": ["HN"], "source_urls": ["javascript:alert(1)"],
        }]
        first = render_html(companies, rounds)
        second = render_html(list(reversed(companies)), list(reversed(rounds)))
        self.assertEqual(first, second)
        self.assertNotIn("<script>alert(1)</script>", first)
        self.assertNotIn('href="javascript:', first)
        self.assertIn("not found", first)


if __name__ == "__main__":
    unittest.main()

