"""Leakage-aware feature snapshots and outcome labels."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping

from .context import CohortContext
from .evidence import make_evidence
from .models import Company, Evidence, FundingRound
from .postprocess import apply_latest_disclosures, recompute_missing_fields
from . import scoring
from .util import parse_date, stable_id

FEATURE_FIELDS = (
    "founder_score_persistent", "founder_axis", "founder_axis_trend", "market_axis",
    "market_axis_trend", "idea_vs_market", "idea_vs_market_trend", "arr_usd",
    "revenue_growth_rate_yoy", "customer_count", "churn_rate_annual",
    "burn_rate_usd_monthly", "runway_months", "current_stage", "last_round_size_usd",
    "last_round_date", "total_funding_to_date_usd", "cash_balance_usd",
    "founder_prior_exits", "founder_prior_startups", "single_founder_flag", "team_size",
    "technical_founder_flag", "founder_industry_experience_years", "proprietary_score",
    "patent_count", "open_source_activity_score", "tam_usd", "competitor_density",
    "sector", "geography", "funding_climate_index", "public_footprint_score",
    "network_centrality", "soft_skill_estimate",
)


@dataclass(slots=True)
class SnapshotBuild:
    record: dict[str, Any]
    axes: dict[str, float | str | None]
    evidence: list[Evidence]


def _eligible_source_ids(
    source_ids: Iterable[str], evidence_by_id: Mapping[str, Evidence], cutoff: date
) -> list[str]:
    eligible: list[str] = []
    for source_id in source_ids:
        evidence = evidence_by_id.get(source_id)
        if evidence is None:
            continue
        document_date = parse_date(evidence.document_date)
        if document_date is not None and document_date <= cutoff:
            eligible.append(source_id)
    return sorted(set(eligible))


def _score_evidence(
    company: Company,
    *,
    field: str,
    score: scoring.Score,
    cutoff: date,
    collected_at: str,
) -> Evidence | None:
    if score.value is None:
        return None
    excerpt = json.dumps(
        {
            "field": field,
            "formula": score.formula,
            "value": round(score.value, 6),
            "coverage": round(score.coverage, 6),
            "inputs": score.inputs,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return make_evidence(
        startup_id=company.startup_id,
        channel="computed",
        source_type="internal_investment_record",
        document_name=f"Derived {field} ({score.formula})",
        excerpt=excerpt,
        source_uri=None,
        document_date=cutoff.isoformat(),
        collected_at=collected_at,
        location=field,
        verification_status="estimated",
        confidence=score.confidence,
    )


def _stage_from_round(round_: FundingRound | None) -> str:
    stage = round_.stage if round_ else None
    return stage if stage in {"pre_seed", "seed", "series_a", "series_b", "series_c_plus"} else "unknown"


def build_snapshot(
    company: Company,
    *,
    cutoff: date,
    live: bool,
    rounds: Iterable[FundingRound],
    evidence: Iterable[Evidence],
    signals: Mapping[str, Any],
    context: CohortContext,
    disclosures: Iterable[Mapping[str, Any]],
    collected_at: str,
    previous_axes: Mapping[str, float | str | None] | None = None,
) -> SnapshotBuild:
    evidence_list = list(evidence)
    evidence_by_id = {item.source_id: item for item in evidence_list}
    field_sources: dict[str, list[str]] = {}
    feature_quality: dict[str, dict[str, Any]] = {}
    contradicted_fields: set[str] = set()

    company_sources = _eligible_source_ids(company.source_ids, evidence_by_id, cutoff)
    eligible_rounds: list[tuple[FundingRound, list[str]]] = []
    for round_ in rounds:
        round_date = parse_date(round_.date)
        if round_date is None or round_date > cutoff:
            continue
        eligible = _eligible_source_ids(round_.source_ids, evidence_by_id, cutoff)
        if eligible:
            eligible_rounds.append((round_, eligible))
    eligible_rounds.sort(key=lambda item: (item[0].date, item[0].round_id))
    last_pair = eligible_rounds[-1] if eligible_rounds else None
    last_round = last_pair[0] if last_pair else None

    record: dict[str, Any] = {field: None for field in FEATURE_FIELDS}
    record.update(
        founder_axis_trend="unknown",
        market_axis="unknown",
        market_axis_trend="unknown",
        idea_vs_market_trend="unknown",
        current_stage=_stage_from_round(last_round),
    )
    if last_pair:
        record["last_round_date"] = last_round.date
        record["last_round_size_usd"] = None if last_round.contradicted else last_round.amount_usd
        field_sources["last_round_date"] = last_pair[1]
        if record["last_round_size_usd"] is not None:
            field_sources["last_round_size_usd"] = last_pair[1]
        if last_round.contradicted:
            contradicted_fields.add("last_round_size_usd")

    equity_rounds = [pair for pair in eligible_rounds if pair[0].instrument not in {"debt", "grant"}]
    if equity_rounds and all(pair[0].amount_usd is not None and not pair[0].contradicted for pair in equity_rounds):
        record["total_funding_to_date_usd"] = sum(float(pair[0].amount_usd or 0) for pair in equity_rounds)
        if record["total_funding_to_date_usd"].is_integer():
            record["total_funding_to_date_usd"] = int(record["total_funding_to_date_usd"])
        field_sources["total_funding_to_date_usd"] = sorted(
            {source_id for _, source_ids in equity_rounds for source_id in source_ids}
        )
    elif any(pair[0].contradicted for pair in equity_rounds):
        contradicted_fields.add("total_funding_to_date_usd")

    # Current directory attributes have a current knowledge date. They are only
    # admitted when their evidence itself is cutoff-eligible.
    if company_sources:
        record["sector"] = company.sectors[0] if company.sectors else None
        record["geography"] = company.geography
        if live:
            record["team_size"] = company.team_size
            record["single_founder_flag"] = len(company.founders) == 1 if company.founders else None
        for field in ("sector", "geography", "team_size", "single_founder_flag"):
            if record[field] is not None:
                field_sources[field] = company_sources

    # Apply only exact, dated, definition-compatible disclosures before scoring.
    record_for_apply = {
        **record,
        "startup_id": company.startup_id,
        "data_cutoff_date": cutoff.isoformat(),
        "contradicted_fields": sorted(contradicted_fields),
    }
    record_for_apply, applied = apply_latest_disclosures(record_for_apply, disclosures, cutoff)
    for field in FEATURE_FIELDS:
        record[field] = record_for_apply.get(field)
    for audit in applied:
        field_sources[audit["field"]] = audit["supporting_source_ids"]

    historical_mentions = sum(
        1
        for item in evidence_list
        if item.startup_id == company.startup_id
        and item.channel in {"hackernews", "producthunt"}
        and parse_date(item.document_date) is not None
        and parse_date(item.document_date) <= cutoff
    )
    live_signals = signals if live else {}
    oss = scoring.open_source_activity(live_signals)
    footprint = scoring.public_footprint(live_signals, dated_mentions=historical_mentions)
    prop = scoring.proprietary(patent_count=record["patent_count"], oss=oss)
    network = context.network.get(company.startup_id) if live and company_sources else None
    founder = scoring.founder_score(
        prior_exits=record["founder_prior_exits"],
        prior_startups=record["founder_prior_startups"],
        experience_years=record["founder_industry_experience_years"],
        technical=record["technical_founder_flag"],
        pedigree=None,
        footprint=footprint,
        network=network,
    )
    founder_opportunity = scoring.founder_axis(
        founder,
        founder_market_fit=None,
        team_size=record["team_size"],
        soft_skill=record["soft_skill_estimate"],
        single_founder=record["single_founder_flag"],
    )
    density = context.competitor_density(company) if record["sector"] else None
    climate = context.funding_climate(company, cutoff) if record["sector"] else None
    market_label, market_score = scoring.market_axis(
        tam_usd=record["tam_usd"], competitor_density=density, funding_climate=climate
    )
    idea = scoring.idea_vs_market(
        proprietary_score=prop,
        competitor_density=density,
        revenue_growth=record["revenue_growth_rate_yoy"],
        churn=record["churn_rate_annual"],
        founder_axis_score=founder_opportunity,
    )
    score_fields = {
        "open_source_activity_score": oss,
        "public_footprint_score": footprint,
        "proprietary_score": prop,
        "founder_score_persistent": founder,
        "founder_axis": founder_opportunity,
        "market_axis": market_score,
        "idea_vs_market": idea,
    }
    record.update(
        open_source_activity_score=round(oss.value, 6) if oss.value is not None else None,
        public_footprint_score=round(footprint.value, 6) if footprint.value is not None else None,
        proprietary_score=round(prop.value, 6) if prop.value is not None else None,
        network_centrality=round(network, 6) if network is not None else None,
        founder_score_persistent=round(founder.value, 4) if founder.value is not None else None,
        founder_axis=round(founder_opportunity.value, 4) if founder_opportunity.value is not None else None,
        competitor_density=round(density, 6) if density is not None else None,
        funding_climate_index=round(climate, 6) if climate is not None else None,
        market_axis=market_label,
        idea_vs_market=round(idea.value, 4) if idea.value is not None else None,
    )
    if record["network_centrality"] is not None:
        field_sources["network_centrality"] = company_sources
        feature_quality["network_centrality"] = {"confidence": "low", "coverage": 1.0,
                                                   "formula": "plan-6-degree-centrality-v1"}

    prior = previous_axes or {}
    record["founder_axis_trend"] = scoring.trend(record["founder_axis"], prior.get("founder_axis"))
    record["market_axis_trend"] = scoring.trend(record["market_axis"], prior.get("market_axis"))
    record["idea_vs_market_trend"] = scoring.trend(record["idea_vs_market"], prior.get("idea_vs_market"))

    derived_evidence: list[Evidence] = []
    for field, score in score_fields.items():
        feature_quality[field] = {
            "coverage": round(score.coverage, 6),
            "confidence": score.confidence,
            "formula": score.formula,
        }
        item = _score_evidence(company, field=field, score=score, cutoff=cutoff, collected_at=collected_at)
        if item:
            derived_evidence.append(item)
            field_sources[field] = [item.source_id]

    identifiers = {
        "startup_id": company.startup_id,
        "opportunity_id": stable_id("opportunity", company.startup_id, cutoff.isoformat()),
        "snapshot_id": f"{company.startup_id}_{cutoff.isoformat()}_{record['current_stage']}",
        "observation_date": cutoff.isoformat(),
        "data_cutoff_date": cutoff.isoformat(),
        "company_name": company.name,
        "domain": company.domain,
        "accelerators": company.accelerators,
    }
    final = {
        **identifiers,
        **record,
        "source_ids": sorted({source_id for values in field_sources.values() for source_id in values}),
        "field_source_ids": {key: sorted(set(values)) for key, values in sorted(field_sources.items())},
        "feature_quality": dict(sorted(feature_quality.items())),
        "contradicted_fields": sorted(contradicted_fields),
    }
    final = recompute_missing_fields(final, FEATURE_FIELDS)
    axes = {
        "founder_axis": record["founder_axis"],
        "market_axis": record["market_axis"],
        "idea_vs_market": record["idea_vs_market"],
    }
    return SnapshotBuild(final, axes, derived_evidence)


# Directory statuses are point-in-time (observed as of the run) and undated. We read
# the status VALUE (text after the final ":"), never the prefix: "a16z listed investment
# stage:" and "Pear current stage:" both mix real funding stages (Seed, Series A) with
# terminal outcomes (IPO, M&A, Acquired), so keying on the prefix would mislabel exits.
# The value is normalized (case-folded; ampersand/punctuation/whitespace flattened) and
# matched by whole-word keyword, so realistic phrasings ("Acquired by X", "M & A",
# "Acquired.") are caught, not just the clean closed-set tokens. Matching stays
# conservative: unknown vocabulary yields no label rather than a guessed one. exit_type
# and failure_definition targets follow the enums in ngboost_data_requirements.md.
_EXIT_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bipo\b"), "ipo"),
    (re.compile(r"\bpublic(?:ly)?\b"), "ipo"),
    (re.compile(r"\bspac\b"), "ipo"),            # a (de-)SPAC listing takes the company public
    (re.compile(r"\bde spac\b"), "ipo"),
    (re.compile(r"\bacqui\w*"), "acquisition"),  # acquire/acquired/acquisition/acqui-hired
    (re.compile(r"\bmerg\w*"), "acquisition"),   # merger/merged
    (re.compile(r"\bm and a\b"), "acquisition"),  # "M&A" / "M & A" normalize to "m and a"
    (re.compile(r"\bbought\b"), "acquisition"),
    (re.compile(r"\bexit(?:s|ed)?\b"), "acquisition"),  # a16z generic flag; a specific type wins
)
# failure_definition enum: {ceased_operations, dissolved, bankruptcy, shutdown_confirmed,
# other}. Ordered by specificity so the first match on a value is the strongest reading;
# "inactive" is a catch-all directory flag -> "other".
_FAILURE_KEYWORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bbankrupt\w*"), "bankruptcy"),
    (re.compile(r"\bliquidat\w*"), "dissolved"),
    (re.compile(r"\bdissolved\b"), "dissolved"),
    (re.compile(r"\bdead\b"), "shutdown_confirmed"),
    (re.compile(r"\bdefunct\b"), "shutdown_confirmed"),
    (re.compile(r"\bshut ?down\b"), "shutdown_confirmed"),
    (re.compile(r"\bceased\b"), "ceased_operations"),
    (re.compile(r"\bwound down\b"), "ceased_operations"),
    (re.compile(r"\bwinding down\b"), "ceased_operations"),
    (re.compile(r"\bno longer operating\b"), "ceased_operations"),
    (re.compile(r"\bsunset\b"), "ceased_operations"),
    (re.compile(r"\binactive\b"), "other"),
)
# Precedence when several tokens coexist: a more definitive/specific outcome wins.
_EXIT_TYPE_PRIORITY = {"ipo": 0, "acquisition": 1}
_FAILURE_DEFINITION_PRIORITY = {
    "bankruptcy": 0, "dissolved": 1, "shutdown_confirmed": 2, "ceased_operations": 3, "other": 4,
}


def _normalize_status_value(status: str) -> str:
    """Value after the final ':', case-folded with ampersand/punctuation/whitespace flattened."""
    value = status.rsplit(":", 1)[-1].casefold().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", value).strip()


@dataclass(slots=True)
class StatusOutcome:
    """Terminal exit/failure signal derived from point-in-time directory statuses."""

    exit_type: str | None  # "ipo" | "acquisition" | None
    failure_definition: str | None  # ngboost failure enum member | None
    matched_statuses: list[str]  # exact status strings that drove the chosen label

    @property
    def failure(self) -> bool:
        return self.failure_definition is not None


def classify_status_outcome(statuses: Iterable[str]) -> StatusOutcome:
    """Classify directory statuses into a terminal exit/failure signal.

    Returns the classification only; the caller decides how to encode dates because a
    directory status is undated (a current observation, not a dated event). An exit takes
    priority over a failure when both appear (a specific liquidity event is a stronger,
    more specific claim than a catch-all inactive/dead flag).
    """
    exit_hits: list[tuple[str, str]] = []  # (exit_type, status)
    failure_hits: list[tuple[str, str]] = []  # (failure_definition, status)
    for status in statuses:
        if not isinstance(status, str):
            continue
        value = _normalize_status_value(status)
        matched_types = {exit_type for pattern, exit_type in _EXIT_KEYWORDS if pattern.search(value)}
        if matched_types:
            exit_hits.extend((exit_type, status) for exit_type in matched_types)
            continue
        definition = next(
            (definition for pattern, definition in _FAILURE_KEYWORDS if pattern.search(value)), None
        )
        if definition is not None:
            failure_hits.append((definition, status))
    if exit_hits:
        chosen = min((exit_type for exit_type, _ in exit_hits), key=lambda item: _EXIT_TYPE_PRIORITY.get(item, 99))
        matched = sorted({status for exit_type, status in exit_hits if exit_type == chosen})
        return StatusOutcome(chosen, None, matched)
    if failure_hits:
        chosen = min((defn for defn, _ in failure_hits), key=lambda item: _FAILURE_DEFINITION_PRIORITY.get(item, 99))
        matched = sorted({status for defn, status in failure_hits if defn == chosen})
        return StatusOutcome(None, chosen, matched)
    return StatusOutcome(None, None, [])


def _outcome_enrichment_evidence(
    enrichment: Any,
    *,
    startup_id: str,
    location: str,
    as_of: date,
    collected_at: str,
) -> Evidence:
    """Evidence record backing a web-search LLM failure determination.

    The verdict is model-derived from live web search, so it is recorded as
    estimated/unverified — not a dated, independently verified transaction — mirroring
    how an undated directory-status failure is treated. A dead website alone is not
    sufficient proof (ngboost_data_requirements.md §5.3); the excerpt carries the
    model's cited justification so a reviewer can see what the claim rests on.
    """
    reason = (getattr(enrichment, "status_reason", "") or "").strip()
    model = getattr(enrichment, "model", "llm") or "llm"
    failure_definition = getattr(enrichment, "failure_definition", None) or "other"
    excerpt = (
        f"Web-search LLM ({model}) determination through {as_of.isoformat()}: the company "
        f"has no exit/failure signal in the collected directories, and a live web search "
        f"indicates it is dissolved/no longer operating (failure_definition={failure_definition}). "
        f"Model justification: {reason or 'not provided'}. This is a model-derived, "
        "web-sourced signal recorded as estimated and unverified, not an independently "
        "verified transaction; the failure date is the confirmed ceased date when available, "
        "otherwise the observation horizon."
    )
    return make_evidence(
        startup_id=startup_id,
        channel="openai_web_search",
        source_type="manual_research",
        document_name=f"Web-search LLM outcome determination ({model})",
        excerpt=excerpt,
        source_uri=None,
        document_date=as_of.isoformat(),
        collected_at=collected_at,
        location=location,
        verification_status="estimated",
        confidence="low",
    )


def build_training_records(
    company: Company,
    *,
    rounds: Iterable[FundingRound],
    all_evidence: Iterable[Evidence],
    signals: Mapping[str, Any],
    context: CohortContext,
    disclosures: Iterable[Mapping[str, Any]],
    as_of: date,
    collected_at: str,
    outcome_enrichment: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Evidence]]:
    company_rounds = sorted(
        [round_ for round_ in rounds if round_.startup_id == company.startup_id and parse_date(round_.date)],
        key=lambda item: (item.date, item.round_id),
    )
    evidence_list = list(all_evidence)
    features: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    derived: list[Evidence] = []
    prior_axes: dict[str, Any] | None = None
    # A terminal exit/failure read off the company's current (undated) directory status.
    # It is applied per snapshot below using as_of as the observation horizon, never
    # backdated into the historical feature snapshots.
    status_outcome = classify_status_outcome(company.statuses)
    # When the directory carries no exit/failure token at all, there is no information
    # about whether the company is alive. An optional web-search LLM verdict may supply
    # a confirmed dissolution for exactly those companies; a directory-status terminal
    # signal is more specific and always takes precedence over it.
    llm_failure = (
        outcome_enrichment is not None
        and getattr(outcome_enrichment, "failure_observed", False)
        and status_outcome.exit_type is None
        and not status_outcome.failure
    )
    # One post-round snapshot per distinct date. Same-day entries cannot be a
    # strictly later next round and are represented as one transition point.
    observation_dates = sorted({round_.date for round_ in company_rounds if parse_date(round_.date) <= as_of})
    # A company with no funding events yields no snapshot, so an LLM-confirmed failure
    # would have nowhere to live. Anchor one snapshot at the founding date (Jan 1 of
    # founded_year) when known, giving a meaningful founding->failure duration. Without
    # a founded_year there is no defensible observation date, so the company is skipped.
    if not observation_dates and llm_failure and company.founded_year:
        founded = date(company.founded_year, 1, 1)
        if founded < as_of:
            observation_dates = [founded.isoformat()]
    for observation in observation_dates:
        cutoff = parse_date(observation)
        if cutoff is None:
            continue
        build = build_snapshot(
            company,
            cutoff=cutoff,
            live=False,
            rounds=company_rounds,
            evidence=evidence_list,
            signals=signals,
            context=context,
            disclosures=disclosures,
            collected_at=collected_at,
            previous_axes=prior_axes,
        )
        features.append(build.record)
        derived.extend(build.evidence)
        prior_axes = build.axes

        next_round = next((item for item in company_rounds if parse_date(item.date) > cutoff), None)
        censor_days = max(0, (as_of - cutoff).days)
        next_date = parse_date(next_round.date) if next_round else None
        progressed = (
            (next_date - cutoff).days <= 730
            if next_date is not None
            else False if as_of >= cutoff + timedelta(days=730) else None
        )
        censor_evidence = make_evidence(
            startup_id=company.startup_id,
            channel="computed",
            source_type="internal_monitoring_record",
            document_name=f"Censoring record through {as_of.isoformat()}",
            excerpt=(
                f"Outcome collection window ended {as_of.isoformat()}; the company remained present "
                "in the collected directory cohort. This is a censoring record, not proof of operations."
            ),
            source_uri=None,
            document_date=as_of.isoformat(),
            collected_at=collected_at,
            location=build.record["snapshot_id"],
            verification_status="estimated",
            confidence="low",
        )
        derived.append(censor_evidence)
        outcome_source_ids = [censor_evidence.source_id]
        if next_round:
            outcome_source_ids.extend(next_round.source_ids)

        # A directory status is undated, so a terminal outcome is only attributable to
        # the strictly-post-cutoff window when that window is non-empty (censor_days > 0).
        # The event date is recorded as the observation horizon (as_of) and flagged
        # estimated/unverified; the exact-date and value fields stay null.
        has_exit = status_outcome.exit_type is not None and censor_days > 0
        has_failure = status_outcome.failure and censor_days > 0
        terminal = has_exit or has_failure
        if terminal:
            terminal_kind = f"an exit ({status_outcome.exit_type})" if has_exit else "a failure"
            status_evidence = make_evidence(
                startup_id=company.startup_id,
                channel="computed",
                source_type="internal_monitoring_record",
                document_name=f"Directory-status outcome determination through {as_of.isoformat()}",
                excerpt=(
                    f"Directory status(es) {', '.join(status_outcome.matched_statuses)} observed as of "
                    f"{as_of.isoformat()} indicate {terminal_kind}. The status is point-in-time and "
                    "undated; the event date is recorded as the observation horizon and the outcome is "
                    "marked estimated and unverified, not a dated, independently verified transaction."
                ),
                source_uri=None,
                document_date=as_of.isoformat(),
                collected_at=collected_at,
                location=build.record["snapshot_id"],
                verification_status="estimated",
                confidence="low",
            )
            derived.append(status_evidence)
            outcome_source_ids.append(status_evidence.source_id)

        # Web-search LLM verdict, applied only to the strictly-post-cutoff window and
        # only when the directory carried no terminal signal of its own. It fills the
        # failure fields that would otherwise stay null for a company we had no
        # information about. A confirmed ceased date is used when it falls after the
        # cutoff; otherwise the observation horizon dates the (estimated) failure.
        llm_failure_here = llm_failure and censor_days > 0
        llm_failure_date: str | None = None
        if llm_failure_here:
            ceased = parse_date(getattr(outcome_enrichment, "failure_date", None))
            llm_failure_date = (
                ceased.isoformat() if ceased is not None and ceased > cutoff else as_of.isoformat()
            )
            llm_evidence = _outcome_enrichment_evidence(
                outcome_enrichment,
                startup_id=company.startup_id,
                location=build.record["snapshot_id"],
                as_of=as_of,
                collected_at=collected_at,
            )
            derived.append(llm_evidence)
            outcome_source_ids.append(llm_evidence.source_id)

        failure_observed = has_failure or llm_failure_here
        failure_date = (
            as_of.isoformat() if has_failure else llm_failure_date if llm_failure_here else None
        )
        failure_definition = (
            status_outcome.failure_definition
            if has_failure
            else (getattr(outcome_enrichment, "failure_definition", None) or "other")
            if llm_failure_here
            else None
        )
        terminal = terminal or llm_failure_here
        # Right-censored survival duration: measure to the failure event when one was
        # observed, otherwise to the censoring horizon (as_of, via censor_days). Keying
        # this off failure_observed/failure_date keeps the duration consistent with the
        # event fields instead of always reporting the full observation window — a
        # censored row (no failure) now carries the censoring time, and an observed
        # failure carries cutoff -> failure_date, not cutoff -> as_of.
        failure_dt = parse_date(failure_date) if failure_observed else None
        time_to_failure_days = (
            max(0, (failure_dt - cutoff).days) if failure_dt is not None else censor_days
        )
        outcome = {
            "startup_id": company.startup_id,
            "opportunity_id": build.record["opportunity_id"],
            "snapshot_id": build.record["snapshot_id"],
            "next_round_observed": next_round is not None,
            "next_round_stage": next_round.stage if next_round else None,
            "next_round_date": next_round.date if next_round else None,
            "next_round_size_usd": next_round.amount_usd if next_round and not next_round.contradicted else None,
            "progressed_within_24_months": progressed,
            "next_event_type": "funding_round" if next_round else "none_observed",
            "next_event_date": next_round.date if next_round else None,
            "time_to_next_event_days": (next_date - cutoff).days if next_date else censor_days,
            "next_event_observed": next_round is not None,
            "failure_observed": failure_observed,
            "failure_date": failure_date,
            "failure_definition": failure_definition,
            "time_to_failure_days": time_to_failure_days,
            "failure_event_observed": failure_observed,
            "revenue_start_date": None,
            "revenue_start_usd": None,
            "revenue_end_date": None,
            "revenue_end_usd": None,
            "growth_measurement_type": None,
            "annual_growth_factor": None,
            "m4_included": False,
            "m4_exclusion_reason": "two_comparable_revenue_observations_not_found",
            "exit_type": status_outcome.exit_type if has_exit else "no_exit_observed",
            "exit_date": as_of.isoformat() if has_exit else None,
            "exit_verified": False,
            "exit_valuation_usd": None,
            "exit_transaction_value_usd": None,
            "exit_value_type": None,
            "exit_value_disclosed": False,
            "exit_value_verified": False,
            "outcome_observation_end_date": as_of.isoformat(),
            "company_still_observed": not terminal,
            "source_ids": sorted(set(outcome_source_ids)),
        }
        outcomes.append(outcome)
    return features, outcomes, derived
