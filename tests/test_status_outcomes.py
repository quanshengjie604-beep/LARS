from __future__ import annotations

import unittest
from datetime import date

from vcbrain.assemble import build_training_records, classify_status_outcome
from vcbrain.context import CohortContext
from vcbrain.models import Company, FundingRound

AS_OF = date(2026, 7, 18)


def _company(statuses: list[str]) -> Company:
    return Company(name="Acme AI", website="https://acme.ai", statuses=statuses)


def _round(company: Company, when: str = "2020-01-01") -> FundingRound:
    return FundingRound(
        startup_id=company.startup_id,
        company_name=company.name,
        date=when,
        date_basis="announced",
        stage="Seed",
        amount_usd=1_000_000,
        source_ids=["source_round_1"],
    )


def _outcomes(statuses: list[str], *, dates: list[str] | None = None) -> list[dict]:
    company = _company(statuses)
    rounds = [_round(company, when) for when in (dates or ["2020-01-01"])]
    context = CohortContext([company], rounds)
    _, outcomes, derived = build_training_records(
        company,
        rounds=rounds,
        all_evidence=[],
        signals={},
        context=context,
        disclosures=[],
        as_of=AS_OF,
        collected_at="2026-07-18T12:00:00Z",
    )
    # Every source_id an outcome cites must resolve against emitted derived evidence.
    derived_ids = {ev.source_id for ev in derived}
    for outcome in outcomes:
        for sid in outcome["source_ids"]:
            if sid.startswith("source_round"):
                continue
            assert sid in derived_ids, (sid, derived_ids)
    return outcomes


class ClassifyStatusOutcomeTests(unittest.TestCase):
    def test_value_token_not_prefix(self):
        # "stage:" prefixes still carry real exits in the value token.
        self.assertEqual(classify_status_outcome(["a16z listed investment stage: IPO"]).exit_type, "ipo")
        self.assertEqual(classify_status_outcome(["Pear current stage: Acquired"]).exit_type, "acquisition")
        # Real funding stages under the same prefixes are not exits.
        self.assertIsNone(classify_status_outcome(["Pear current stage: Series A"]).exit_type)
        self.assertFalse(classify_status_outcome(["Pear current stage: Series A"]).failure)

    def test_enum_mapping(self):
        self.assertEqual(classify_status_outcome(["YC status: Acquired"]).exit_type, "acquisition")
        self.assertEqual(classify_status_outcome(["YC status: Public"]).exit_type, "ipo")
        self.assertEqual(classify_status_outcome(["a16z listed investment stage: M&A"]).exit_type, "acquisition")
        self.assertEqual(classify_status_outcome(["a16z listed investment stage: SPAC"]).exit_type, "ipo")
        self.assertEqual(classify_status_outcome(["a16z status: Exits"]).exit_type, "acquisition")

    def test_failure_and_active(self):
        self.assertTrue(classify_status_outcome(["YC status: Inactive"]).failure)
        active = classify_status_outcome(["YC status: Active", "YC stage: Growth"])
        self.assertIsNone(active.exit_type)
        self.assertFalse(active.failure)

    def test_exit_beats_failure_and_ipo_beats_acquisition(self):
        both = classify_status_outcome(["YC status: Inactive", "Pear current stage: Acquired"])
        self.assertEqual(both.exit_type, "acquisition")
        self.assertFalse(both.failure)
        mixed = classify_status_outcome(["YC status: Public", "YC status: Acquired"])
        self.assertEqual(mixed.exit_type, "ipo")


class BuildOutcomeTests(unittest.TestCase):
    def test_acquisition_sets_dated_estimated_exit(self):
        (outcome,) = _outcomes(["YC status: Acquired"])
        self.assertEqual(outcome["exit_type"], "acquisition")
        self.assertEqual(outcome["exit_date"], AS_OF.isoformat())  # non-null iff exit_type != sentinel
        self.assertFalse(outcome["exit_verified"])
        self.assertFalse(outcome["exit_value_disclosed"])
        self.assertIsNone(outcome["exit_valuation_usd"])
        self.assertFalse(outcome["failure_observed"])
        self.assertFalse(outcome["company_still_observed"])

    def test_inactive_sets_failure_invariants(self):
        (outcome,) = _outcomes(["YC status: Inactive"])
        self.assertTrue(outcome["failure_observed"])
        self.assertTrue(outcome["failure_event_observed"])  # must move together
        self.assertEqual(outcome["failure_date"], AS_OF.isoformat())
        self.assertEqual(outcome["failure_definition"], "other")
        self.assertEqual(outcome["exit_type"], "no_exit_observed")
        self.assertFalse(outcome["company_still_observed"])

    def test_active_is_unchanged_baseline(self):
        (outcome,) = _outcomes(["YC status: Active"])
        self.assertEqual(outcome["exit_type"], "no_exit_observed")
        self.assertIsNone(outcome["exit_date"])
        self.assertFalse(outcome["failure_observed"])
        self.assertFalse(outcome["failure_event_observed"])
        self.assertTrue(outcome["company_still_observed"])

    def test_terminal_label_applies_to_every_snapshot(self):
        outcomes = _outcomes(["YC status: Acquired"], dates=["2019-01-01", "2021-01-01"])
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(o["exit_type"] == "acquisition" for o in outcomes))

    def test_same_day_as_of_round_stays_censored(self):
        # censor_days == 0 -> no strictly-post window, so no terminal attribution.
        (outcome,) = _outcomes(["YC status: Acquired"], dates=[AS_OF.isoformat()])
        self.assertEqual(outcome["exit_type"], "no_exit_observed")
        self.assertTrue(outcome["company_still_observed"])


if __name__ == "__main__":
    unittest.main()
