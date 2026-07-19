from __future__ import annotations

import json
import unittest
from pathlib import Path

from vcbrain.sources.directories import (
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


if __name__ == "__main__":
    unittest.main()

