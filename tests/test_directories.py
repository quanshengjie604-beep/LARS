from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path

from vcbrain.sources.directories import (
    _find_yc_scraper,
    _load_yc_scraper_extract,
    fetch_yc_algolia,
    parse_a16z_portfolio_html,
    parse_a16z_records,
    parse_pear_records,
    parse_pear_taxonomy,
    parse_startx_page,
    parse_startx_records,
    parse_yc_json,
    parse_yc_records,
)

FIXTURES = Path(__file__).parent / "fixtures"


class DirectoryParserTests(unittest.TestCase):
    def test_yc_export_keeps_cohort_separate_from_founding(self):
        rows = parse_yc_json((FIXTURES / "yc_authorized.json").read_text())
        companies = parse_yc_records(rows)
        acme = next(company for company in companies if company.name == "Acme AI")
        self.assertEqual(acme.founded_year, 2024)
        self.assertEqual(acme.batches, ["Winter 2025"])
        self.assertEqual(acme.team_size, 6)

    def test_a16z_data_company_json(self):
        rows = parse_a16z_portfolio_html((FIXTURES / "a16z_portfolio.html").read_text())
        self.assertEqual(len(rows), 2)
        companies = parse_a16z_records(rows)
        acme = next(company for company in companies if company.name == "Acme AI")
        self.assertEqual(acme.external_ids["a16z_initial_investment_date"], "2025-01-15")
        self.assertIn("Andreessen Horowitz (a16z)", acme.accelerators)

    def test_startx_consider_page(self):
        payload = json.loads((FIXTURES / "startx_companies.json").read_text())
        rows, next_link = parse_startx_page(payload)
        self.assertIsNone(next_link)
        rocket = next(company for company in parse_startx_records(rows) if company.name == "RocketOps")
        self.assertEqual(rocket.team_size, 8)

    def test_pear_taxonomy_stage_does_not_create_round(self):
        taxonomies = {
            "first_investment": parse_pear_taxonomy(json.loads((FIXTURES / "pear_first_investment.json").read_text())),
            "current_stage": parse_pear_taxonomy(json.loads((FIXTURES / "pear_current_stage.json").read_text())),
            "pear_vc_company_sector": parse_pear_taxonomy(json.loads((FIXTURES / "pear_sector.json").read_text())),
        }
        rows = json.loads((FIXTURES / "pear_companies.json").read_text())
        eco = next(company for company in parse_pear_records(rows, taxonomies) if company.name == "EcoGrid")
        self.assertIn("Pear first-investment stage: Pre-Seed", eco.statuses)
        self.assertIn("Pear VC", eco.accelerators)


class YcAlgoliaAutoScrapeTests(unittest.TestCase):
    STAMP = "2026-07-18T00:00:00Z"

    def _records(self):
        # Field names match algolia_extractor.normalize() output verbatim.
        return [
            {
                "company_id": 123,
                "company_name": "Acme AI",
                "slug": "acme-ai",
                "url": "https://www.ycombinator.com/companies/acme-ai",
                "batch": "Winter 2025",
                "year_founded": 2024,
                "team_size": 6,
                "long_description": "AI copilots for warehouses.",
                "website": "https://acme.ai",
                "status": "Active",
                "industry": "B2B",
            },
            {
                "company_id": 456,
                "company_name": "Globex",
                "slug": "globex",
                "url": "https://www.ycombinator.com/companies/globex",
                "batch": "Summer 2024",
                "year_founded": 2023,
                "team_size": 12,
                "website": "https://globex.example",
            },
        ]

    def test_auto_scrape_uses_injected_extractor(self):
        captured = {}

        def fake_extract(recent=None, batches_filter=None):
            captured["recent"] = recent
            captured["batches_filter"] = batches_filter
            return self._records()

        result = asyncio.run(
            fetch_yc_algolia(
                collected_at=self.STAMP,
                recent=2,
                batches=["Winter 2025", "Summer 2024"],
                cache=False,
                extract_fn=fake_extract,
            )
        )

        # The extractor is driven with the requested bounds.
        self.assertEqual(captured["recent"], 2)
        self.assertEqual(captured["batches_filter"], ["Winter 2025", "Summer 2024"])

        self.assertEqual(result.source, "yc")
        self.assertEqual(result.run.status, "ok")
        self.assertEqual(result.run.records, 2)

        acme = next(company for company in result.companies if company.name == "Acme AI")
        self.assertIn("Y Combinator", acme.accelerators)
        # Cohort is retained as a batch label, never converted to a founding date.
        self.assertEqual(acme.batches, ["Winter 2025"])
        self.assertEqual(acme.founded_year, 2024)
        self.assertEqual(acme.team_size, 6)

        # Every company carries directory-membership evidence from the scrape.
        self.assertEqual(len(result.evidence), 2)
        self.assertTrue(all(item.channel == "yc_algolia_directory" for item in result.evidence))
        self.assertTrue(all(item.startup_id in {c.startup_id for c in result.companies} for item in result.evidence))

    def test_missing_scraper_is_structured_skip(self):
        result = asyncio.run(
            fetch_yc_algolia(
                collected_at=self.STAMP,
                scraper_path="/nonexistent/algolia_extractor.py",
                cache=False,
            )
        )
        self.assertEqual(result.run.status, "skipped")
        self.assertEqual(result.run.skipped_reason, "yc_algolia_scraper_not_found")
        self.assertEqual(result.companies, [])
        self.assertEqual(result.evidence, [])

    def test_scrape_error_is_isolated_as_source_error(self):
        def boom(recent=None, batches_filter=None):
            raise RuntimeError("algolia down")

        result = asyncio.run(
            fetch_yc_algolia(collected_at=self.STAMP, cache=False, extract_fn=boom)
        )
        self.assertEqual(result.run.status, "error")
        self.assertTrue(any("algolia down" in error for error in result.run.errors))
        self.assertEqual(result.companies, [])

    def test_bundled_scraper_is_discoverable_and_loadable(self):
        path = _find_yc_scraper(None)
        if path is None:
            self.skipTest("bundled yc-scraper/algolia_extractor.py not present")
        extract = _load_yc_scraper_extract(path)
        self.assertTrue(callable(extract))


if __name__ == "__main__":
    unittest.main()

