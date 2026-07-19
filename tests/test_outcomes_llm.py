from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import httpx

from vcbrain.assemble import build_training_records
from vcbrain.context import CohortContext
from vcbrain.models import Company, FundingRound
from vcbrain import outcomes_llm
from vcbrain.outcomes_llm import (
    DEFAULT_MODELS,
    OutcomeEnrichment,
    _amount_from_text,
    _collect_gemini_text,
    _extract_json_block,
    _parse_verdict,
    enrich_outcomes,
    resolve_api_key,
)

AS_OF = date(2026, 7, 18)


def _company(statuses: list[str] | None = None, name: str = "Ghost Robotics") -> Company:
    return Company(name=name, website="https://ghost.example", statuses=statuses or [])


def _round(company: Company, when: str = "2021-01-01") -> FundingRound:
    return FundingRound(
        startup_id=company.startup_id,
        company_name=company.name,
        date=when,
        date_basis="announced",
        stage="Seed",
        amount_usd=2_000_000,
        source_ids=["source_round_seed"],
    )


def _outcomes(company: Company, enrichment: OutcomeEnrichment | None, *, when: str = "2021-01-01"):
    rounds = [_round(company, when)]
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
        outcome_enrichment=enrichment,
    )
    derived_ids = {ev.source_id for ev in derived}
    # Every non-round source an outcome cites must resolve against emitted evidence.
    for outcome in outcomes:
        for sid in outcome["source_ids"]:
            if sid.startswith("source_round"):
                continue
            assert sid in derived_ids, (sid, derived_ids)
    return outcomes, derived


class ParseVerdictTests(unittest.TestCase):
    def test_amount_from_text(self):
        self.assertEqual(_amount_from_text("$1.2M"), 1_200_000)
        self.assertEqual(_amount_from_text("2 million"), 2_000_000)
        self.assertEqual(_amount_from_text("$500k"), 500_000)
        self.assertEqual(_amount_from_text("1.5 billion"), 1_500_000_000)
        self.assertIsNone(_amount_from_text("undisclosed"))

    def test_extract_json_block_fenced(self):
        text = 'Here is a table...\n\n```json\n{"status": "dissolved", "funding_rounds": []}\n```'
        parsed = _extract_json_block(text)
        self.assertEqual(parsed["status"], "dissolved")

    def test_extract_json_block_bare(self):
        text = 'prose {"status": "operating", "funding_rounds": []}'
        parsed = _extract_json_block(text)
        self.assertEqual(parsed["status"], "operating")

    def test_parse_dissolved_with_ceased_date(self):
        answer = (
            "Table omitted.\n```json\n"
            '{"status": "dissolved", "status_reason": "TechCrunch reports shutdown",'
            ' "ceased_date": "2024-03-01", "failure_kind": "bankruptcy",'
            ' "funding_rounds": [{"date": "2021-05-02", "stage": "Seed",'
            ' "amount_usd": null, "amount_text": "$3M"}]}\n```'
        )
        verdict = _parse_verdict("startup_x", "gpt-4o-mini", "q", answer, as_of=AS_OF)
        self.assertTrue(verdict.failure_observed)
        self.assertEqual(verdict.status, "dissolved")
        self.assertEqual(verdict.failure_definition, "bankruptcy")
        self.assertEqual(verdict.failure_date, "2024-03-01")
        self.assertEqual(verdict.funding_rounds[0]["amount_usd"], 3_000_000)
        self.assertEqual(verdict.funding_rounds[0]["date"], "2021-05-02")

    def test_parse_operating_is_not_failure(self):
        answer = '```json\n{"status": "operating", "funding_rounds": []}\n```'
        verdict = _parse_verdict("startup_y", "gpt-4o-mini", "q", answer, as_of=AS_OF)
        self.assertFalse(verdict.failure_observed)
        self.assertIsNone(verdict.failure_date)
        self.assertIsNone(verdict.failure_definition)

    def test_parse_unparseable_is_unknown_not_failure(self):
        verdict = _parse_verdict("startup_z", "gpt-4o-mini", "q", "no json here at all", as_of=AS_OF)
        self.assertEqual(verdict.status, "unknown")
        self.assertFalse(verdict.failure_observed)

    def test_dissolved_without_ceased_date_uses_horizon(self):
        answer = '```json\n{"status": "dissolved", "failure_kind": "dissolved", "funding_rounds": []}\n```'
        verdict = _parse_verdict("startup_w", "gpt-4o-mini", "q", answer, as_of=AS_OF)
        self.assertTrue(verdict.failure_observed)
        self.assertEqual(verdict.failure_date, AS_OF.isoformat())
        self.assertEqual(verdict.failure_definition, "dissolved")


class NoKeyGuardTests(unittest.TestCase):
    def test_no_api_key_returns_empty(self):
        # Explicit empty api_key short-circuits before any network call.
        self.assertEqual(enrich_outcomes([("s1", "Acme")], as_of=AS_OF, api_key=""), {})

    def test_empty_targets_returns_empty(self):
        self.assertEqual(enrich_outcomes([], as_of=AS_OF, api_key="sk-test"), {})


class BuildOutcomeEnrichmentTests(unittest.TestCase):
    def _dissolved(self, definition="bankruptcy", failure_date="2024-03-01") -> OutcomeEnrichment:
        return OutcomeEnrichment(
            startup_id="ignored",
            status="dissolved",
            failure_observed=True,
            failure_date=failure_date,
            failure_definition=definition,
            status_reason="press coverage of shutdown",
        )

    def test_dissolved_populates_failure_fields(self):
        company = _company(statuses=[])  # no directory signal at all
        (outcome,), derived = _outcomes(company, self._dissolved())
        self.assertTrue(outcome["failure_observed"])
        self.assertTrue(outcome["failure_event_observed"])
        self.assertEqual(outcome["failure_definition"], "bankruptcy")
        # ceased date is after the 2021 cutoff, so it is used verbatim.
        self.assertEqual(outcome["failure_date"], "2024-03-01")
        self.assertFalse(outcome["company_still_observed"])
        self.assertEqual(outcome["exit_type"], "no_exit_observed")
        # A backing evidence record exists and is cited.
        self.assertTrue(any(ev.channel == "openai_web_search" for ev in derived))

    def test_ceased_date_before_cutoff_falls_back_to_horizon(self):
        company = _company(statuses=[])
        (outcome,), _ = _outcomes(company, self._dissolved(failure_date="2019-01-01"))
        # 2019 is before the 2021 round cutoff, so the horizon dates the failure.
        self.assertEqual(outcome["failure_date"], AS_OF.isoformat())

    def test_observed_failure_time_measures_to_failure_date(self):
        # An LLM-confirmed dissolution dated after the cutoff makes time_to_failure_days
        # measure cutoff (2021-01-01) -> failure_date (2024-03-01), NOT the censoring
        # horizon. It must be strictly shorter than the full observation window.
        company = _company(statuses=[])
        (outcome,), _ = _outcomes(company, self._dissolved(failure_date="2024-03-01"))
        self.assertTrue(outcome["failure_observed"])
        self.assertEqual(outcome["failure_date"], "2024-03-01")
        self.assertEqual(
            outcome["time_to_failure_days"], (date(2024, 3, 1) - date(2021, 1, 1)).days
        )
        censor_days = (AS_OF - date(2021, 1, 1)).days
        self.assertLess(outcome["time_to_failure_days"], censor_days)

    def test_censored_row_time_uses_observation_horizon(self):
        # No failure observed -> the duration is the censoring time, cutoff -> as_of.
        company = _company(statuses=[])
        enrichment = OutcomeEnrichment(
            startup_id="ignored",
            status="operating",
            failure_observed=False,
            failure_date=None,
            failure_definition=None,
            status_reason="still shipping",
        )
        (outcome,), _ = _outcomes(company, enrichment)
        self.assertFalse(outcome["failure_observed"])
        self.assertEqual(
            outcome["time_to_failure_days"], (AS_OF - date(2021, 1, 1)).days
        )

    def test_operating_keeps_censored_baseline(self):
        company = _company(statuses=[])
        enrichment = OutcomeEnrichment(
            startup_id="ignored",
            status="operating",
            failure_observed=False,
            failure_date=None,
            failure_definition=None,
            status_reason="still shipping",
        )
        (outcome,), _ = _outcomes(company, enrichment)
        self.assertFalse(outcome["failure_observed"])
        self.assertIsNone(outcome["failure_date"])
        self.assertIsNone(outcome["failure_definition"])
        self.assertTrue(outcome["company_still_observed"])

    def test_directory_status_failure_takes_precedence_over_llm(self):
        # A directory-status failure already labels the company; the LLM verdict must
        # not override it (its definition, not the LLM's, is kept).
        company = _company(statuses=["YC status: Inactive"])
        (outcome,), _ = _outcomes(company, self._dissolved(definition="bankruptcy"))
        self.assertTrue(outcome["failure_observed"])
        self.assertEqual(outcome["failure_definition"], "other")  # from "Inactive"
        self.assertEqual(outcome["failure_date"], AS_OF.isoformat())

    def test_directory_status_exit_takes_precedence_over_llm(self):
        # An exit is a stronger, more specific claim than an LLM dissolution guess.
        company = _company(statuses=["YC status: Acquired"])
        (outcome,), _ = _outcomes(company, self._dissolved())
        self.assertEqual(outcome["exit_type"], "acquisition")
        self.assertFalse(outcome["failure_observed"])

    def test_same_day_round_stays_censored_even_with_llm(self):
        # censor_days == 0 -> no strictly-post window, so no terminal attribution.
        company = _company(statuses=[])
        (outcome,), _ = _outcomes(company, self._dissolved(), when=AS_OF.isoformat())
        self.assertFalse(outcome["failure_observed"])
        self.assertTrue(outcome["company_still_observed"])


class RoundlessAnchorTests(unittest.TestCase):
    """A company with no funding rounds is anchored at its founding date."""

    def _build(self, *, founded_year, enrichment):
        company = Company(
            name="Ghost Co",
            website="https://ghost.example",
            statuses=[],
            founded_year=founded_year,
        )
        context = CohortContext([company], [])
        _, outcomes, derived = build_training_records(
            company,
            rounds=[],  # no funding events at all
            all_evidence=[],
            signals={},
            context=context,
            disclosures=[],
            as_of=AS_OF,
            collected_at="2026-07-18T12:00:00Z",
            outcome_enrichment=enrichment,
        )
        return outcomes, derived

    def _dissolved(self):
        return OutcomeEnrichment(
            startup_id="ignored",
            status="dissolved",
            failure_observed=True,
            failure_date="2024-05-01",
            failure_definition="dissolved",
            status_reason="registry shows dissolution",
        )

    def test_roundless_dissolved_gets_anchored_row(self):
        outcomes, derived = self._build(founded_year=2020, enrichment=self._dissolved())
        self.assertEqual(len(outcomes), 1)
        outcome = outcomes[0]
        self.assertTrue(outcome["failure_observed"])
        self.assertEqual(outcome["failure_definition"], "dissolved")
        self.assertEqual(outcome["failure_date"], "2024-05-01")
        # founding 2020-01-01 -> failure_date 2024-05-01, not the censoring horizon
        self.assertEqual(
            outcome["time_to_failure_days"], (date(2024, 5, 1) - date(2020, 1, 1)).days
        )
        self.assertFalse(outcome["company_still_observed"])
        # evidence backing the determination resolves.
        derived_ids = {ev.source_id for ev in derived}
        self.assertTrue(all(s in derived_ids for s in outcome["source_ids"]))

    def test_roundless_without_founded_year_is_skipped(self):
        # No defensible observation date -> no synthetic row.
        outcomes, _ = self._build(founded_year=None, enrichment=self._dissolved())
        self.assertEqual(outcomes, [])

    def test_roundless_operating_is_skipped(self):
        # Only a confirmed failure creates an anchor; an operating verdict does not.
        operating = OutcomeEnrichment(
            startup_id="ignored",
            status="operating",
            failure_observed=False,
            failure_date=None,
            failure_definition=None,
            status_reason="active",
        )
        outcomes, _ = self._build(founded_year=2020, enrichment=operating)
        self.assertEqual(outcomes, [])


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Records the single POST an enrichment run makes and returns a canned payload."""

    def __init__(self, payload):
        self._payload = payload
        self.calls: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, params=None, json=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json or {}})
        return _FakeResp(self._payload)


_DISSOLVED_JSON = (
    "Table...\n```json\n"
    '{"status": "dissolved", "status_reason": "press", "ceased_date": "2024-01-01",'
    ' "failure_kind": "bankruptcy", "funding_rounds": ['
    '{"date": "2021-01-01", "stage": "Seed", "amount_usd": 1000000, "amount_text": "$1M"}]}\n```'
)


class ProviderDispatchTests(unittest.TestCase):
    def test_gemini_is_the_default_provider(self):
        payload = {"candidates": [{"content": {"parts": [{"text": _DISSOLVED_JSON}]}}]}
        client = _FakeClient(payload)
        with mock.patch.object(outcomes_llm.httpx, "Client", return_value=client):
            out = enrich_outcomes([("s1", "Acme")], as_of=AS_OF, api_key="gkey")
        verdict = out["s1"]
        self.assertEqual(verdict.model, DEFAULT_MODELS["gemini"])
        self.assertTrue(verdict.failure_observed)
        self.assertEqual(verdict.funding_rounds[0]["amount_usd"], 1_000_000)
        # Gemini endpoint, key header, and web-search grounding tool are used.
        call = client.calls[0]
        self.assertIn("generativelanguage.googleapis.com", call["url"])
        self.assertEqual(call["headers"].get("x-goog-api-key"), "gkey")
        self.assertEqual(call["json"]["tools"][0], {"google_search": {}})

    def test_openai_provider_when_selected(self):
        payload = {"output_text": '```json\n{"status": "operating", "funding_rounds": []}\n```'}
        client = _FakeClient(payload)
        with mock.patch.object(outcomes_llm.httpx, "Client", return_value=client):
            out = enrich_outcomes([("s1", "Acme")], as_of=AS_OF, provider="openai", api_key="okey")
        verdict = out["s1"]
        self.assertEqual(verdict.model, DEFAULT_MODELS["openai"])
        self.assertEqual(verdict.status, "operating")
        call = client.calls[0]
        self.assertEqual(call["url"], outcomes_llm.OPENAI_RESPONSES_URL)
        self.assertEqual(call["headers"].get("Authorization"), "Bearer okey")
        self.assertEqual(call["json"]["tools"][0], {"type": "web_search"})
        # Forced JSON schema structured output is requested.
        fmt = call["json"]["text"]["format"]
        self.assertEqual(fmt["type"], "json_schema")
        self.assertTrue(fmt["strict"])
        self.assertEqual(fmt["schema"]["type"], "object")

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            enrich_outcomes([("s1", "Acme")], as_of=AS_OF, provider="claude", api_key="k")

    def test_collect_gemini_text_flattens_parts(self):
        payload = {
            "candidates": [
                {"content": {"parts": [{"text": "one"}, {"text": "two"}]}},
            ]
        }
        self.assertEqual(_collect_gemini_text(payload), "one\ntwo")


class PydanticValidationTests(unittest.TestCase):
    def test_valid_payload_validates(self):
        verdict = outcomes_llm._validate_verdict(
            {
                "status": "dissolved",
                "status_reason": "shut down",
                "ceased_date": "2024-01-01",
                "failure_kind": "bankruptcy",
                "funding_rounds": [
                    {"date": "2021-01-01", "stage": "Seed", "amount_usd": 1000000, "amount_text": "$1M"}
                ],
            }
        )
        self.assertEqual(verdict.status, "dissolved")
        self.assertEqual(verdict.failure_kind, "bankruptcy")
        self.assertEqual(verdict.funding_rounds[0].amount_usd, 1_000_000)

    def test_synonym_status_is_normalized_before_validation(self):
        # "closed" is not a schema Literal, but is folded to "dissolved" first.
        verdict = outcomes_llm._validate_verdict({"status": "closed"})
        self.assertEqual(verdict.status, "dissolved")

    def test_unknown_failure_kind_is_dropped(self):
        verdict = outcomes_llm._validate_verdict({"status": "dissolved", "failure_kind": "nonsense"})
        self.assertIsNone(verdict.failure_kind)

    def test_extra_keys_are_ignored(self):
        verdict = outcomes_llm._validate_verdict({"status": "operating", "surprise": 1})
        self.assertEqual(verdict.status, "operating")

    def test_bad_type_raises_validation_error(self):
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            outcomes_llm._validate_verdict({"status": "operating", "funding_rounds": "not-a-list"})

    def test_parse_verdict_degrades_to_unknown_on_bad_schema(self):
        # funding_rounds as a string fails validation -> neutral verdict, error recorded.
        answer = '```json\n{"status": "operating", "funding_rounds": "oops"}\n```'
        verdict = _parse_verdict("s", "m", "q", answer, as_of=AS_OF)
        self.assertEqual(verdict.status, "unknown")
        self.assertFalse(verdict.failure_observed)
        self.assertIsNotNone(verdict.error)


class ResolveApiKeyTests(unittest.TestCase):
    def test_provider_selects_env_var(self):
        with mock.patch.dict(
            outcomes_llm.os.environ,
            {"GEMINI_API_KEY": "g", "OPENAI_API_KEY": "o"},
            clear=True,
        ):
            self.assertEqual(resolve_api_key(provider="gemini"), "g")
            self.assertEqual(resolve_api_key(provider="openai"), "o")

    def test_explicit_empty_key_disables_fallback(self):
        with mock.patch.dict(outcomes_llm.os.environ, {"GEMINI_API_KEY": "g"}, clear=True):
            self.assertIsNone(resolve_api_key("", provider="gemini"))


class _SeqClient:
    """Returns a queued sequence of ``httpx.Response`` objects across POSTs."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, params=None, json=None):
        self.calls += 1
        return self._responses.pop(0)


class RateLimiterTests(unittest.TestCase):
    def test_spaces_starts_by_min_interval(self):
        # 60 rpm -> one start per second. With a frozen clock, each acquire after
        # the first must sleep out the growing reservation gap.
        limiter = outcomes_llm._RateLimiter(60.0)
        sleeps: list[float] = []
        with mock.patch.object(outcomes_llm.time, "monotonic", return_value=0.0), mock.patch.object(
            outcomes_llm.time, "sleep", side_effect=sleeps.append
        ):
            limiter.acquire()  # first slot is free
            limiter.acquire()  # waits ~1s
            limiter.acquire()  # waits ~2s
        self.assertEqual(len(sleeps), 2)
        self.assertAlmostEqual(sleeps[0], 1.0, places=6)
        self.assertAlmostEqual(sleeps[1], 2.0, places=6)

    def test_zero_rpm_disables_throttling(self):
        limiter = outcomes_llm._RateLimiter(0)
        with mock.patch.object(outcomes_llm.time, "sleep") as slept:
            limiter.acquire()
            limiter.acquire()
        slept.assert_not_called()


class RetryBackoffTests(unittest.TestCase):
    def test_honours_retry_after_header(self):
        req = httpx.Request("POST", "https://x")
        resp = httpx.Response(429, headers={"Retry-After": "7"}, request=req)
        self.assertEqual(outcomes_llm._retry_after_seconds(resp, attempt=3), 7.0)

    def test_falls_back_to_capped_exponential_backoff(self):
        with mock.patch.object(outcomes_llm.random, "uniform", return_value=0.0):
            self.assertEqual(outcomes_llm._retry_after_seconds(None, attempt=0), 1.0)
            self.assertEqual(outcomes_llm._retry_after_seconds(None, attempt=2), 4.0)
            self.assertEqual(
                outcomes_llm._retry_after_seconds(None, attempt=20),
                outcomes_llm._MAX_BACKOFF_SECONDS,
            )


class RetryOn429Tests(unittest.TestCase):
    def test_rate_limited_then_succeeds(self):
        req = httpx.Request("POST", "https://generativelanguage.googleapis.com")
        rate_limited = httpx.Response(
            429, headers={"Retry-After": "0"}, request=req, text="slow down"
        )
        payload = {"candidates": [{"content": {"parts": [{"text": _DISSOLVED_JSON}]}}]}
        ok = httpx.Response(200, request=req, json=payload)
        client = _SeqClient([rate_limited, ok])
        with mock.patch.object(outcomes_llm.httpx, "Client", return_value=client), mock.patch.object(
            outcomes_llm.time, "sleep"
        ):
            out = enrich_outcomes([("s1", "Acme")], as_of=AS_OF, api_key="gkey")
        # The 429 was retried and the second attempt's verdict is what we keep.
        self.assertEqual(client.calls, 2)
        self.assertTrue(out["s1"].failure_observed)
        self.assertIsNone(out["s1"].error)

    def test_gives_up_after_max_retries(self):
        req = httpx.Request("POST", "https://generativelanguage.googleapis.com")
        responses = [
            httpx.Response(429, headers={"Retry-After": "0"}, request=req, text="nope")
            for _ in range(outcomes_llm._MAX_RETRIES + 1)
        ]
        client = _SeqClient(responses)
        with mock.patch.object(outcomes_llm.httpx, "Client", return_value=client), mock.patch.object(
            outcomes_llm.time, "sleep"
        ):
            out = enrich_outcomes([("s1", "Acme")], as_of=AS_OF, api_key="gkey")
        # Initial attempt + _MAX_RETRIES retries, then a neutral error verdict.
        self.assertEqual(client.calls, outcomes_llm._MAX_RETRIES + 1)
        self.assertFalse(out["s1"].failure_observed)
        self.assertIn("429", out["s1"].error or "")


if __name__ == "__main__":
    unittest.main()
