from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from vcbrain.config import Settings
from vcbrain.mini import FixtureFetcher, assess_mini_result
from vcbrain.pipeline import CollectionConfig, collect
from vcbrain.util import parse_date

FIXTURES = Path(__file__).parent / "fixtures"


class MiniPipelineTests(unittest.TestCase):
    def run_pipeline(self, concurrency: int):
        settings = Settings(
            as_of=date(2026, 7, 18),
            cache_dir=Path(tempfile.gettempdir()) / "vcbrain-test-cache",
            output_dir=Path(tempfile.gettempdir()) / "unused-vcbrain-output",
            concurrency=concurrency,
            producthunt_token="fixture",
            startx_api_key="fixture",
            max_company_age_years=None,
        )
        config = CollectionConfig(
            settings=settings,
            yc_export=FIXTURES / "yc_authorized.json",
            limit=None,
            pear_pace_seconds=0,
            collected_at="2026-07-18T12:00:00Z",
            write_outputs=False,
        )
        return asyncio.run(collect(config, fetcher=FixtureFetcher(FIXTURES)))

    def test_end_to_end_and_cutoff_invariant(self):
        result = self.run_pipeline(8)
        assessment = assess_mini_result(result)
        self.assertTrue(assessment["ok"], assessment)
        acme = next(row for row in result.companies if row["name"] == "Acme AI")
        self.assertEqual(len([row for row in result.companies if row["domain"] == "acme.ai"]), 1)
        self.assertEqual(set(acme["accelerators"]), {
            "Andreessen Horowitz (a16z)", "Pear VC", "StartX", "Y Combinator"
        })
        live = next(row for row in result.inference_requests if row["company_name"] == "Acme AI")
        self.assertEqual(live["arr_usd"], 1_200_000)
        self.assertIsNone(live["burn_rate_usd_monthly"])
        history = [row for row in result.training_features if row["company_name"] == "Acme AI"]
        self.assertTrue(history)
        self.assertTrue(all(row["arr_usd"] is None for row in history))
        evidence = {row["source_id"]: row for row in result.evidence}
        for snapshot in [*result.training_features, *result.inference_requests]:
            cutoff = parse_date(snapshot["data_cutoff_date"])
            for source_id in snapshot["source_ids"]:
                document_date = parse_date(evidence[source_id]["document_date"])
                self.assertLessEqual(document_date, cutoff)

    def test_completion_order_does_not_change_core_outputs(self):
        slow = self.run_pipeline(1)
        fast = self.run_pipeline(8)
        for field in (
            "companies", "funding_rounds", "inference_requests", "training_features",
            "training_outcomes", "evidence", "financial_disclosures", "enrichment_queue",
        ):
            self.assertEqual(
                json.dumps(getattr(slow, field), sort_keys=True),
                json.dumps(getattr(fast, field), sort_keys=True),
                field,
            )


if __name__ == "__main__":
    unittest.main()

