"""Asynchronous list-scale startup discovery, enrichment, and handover output."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from tqdm import tqdm

from .assemble import FEATURE_FIELDS, build_snapshot, build_training_records
from .config import Settings
from .outcomes_llm import DEFAULT_REQUESTS_PER_MINUTE, enrich_outcomes, resolve_api_key
from .context import CohortContext
from .datadict import render_data_dictionary
from .evidence import dedupe_evidence, make_evidence
from .extract import dedupe_funding_rounds
from .http import AsyncHttpClient, Fetcher
from .models import Company, DirectoryResult, EnrichmentResult, Evidence, FundingRound, SourceRun, merge_companies
from .postprocess import extract_financial_disclosures, postprocess_snapshots
from .sources import (
    fetch_a16z_directory,
    fetch_pear_directory,
    fetch_startx_directory,
    fetch_yc_algolia,
    load_yc_export,
)
from .sources.enrichment import enrich_github, enrich_hackernews, enrich_producthunt
from .sources.sec import collect_form_d
from .util import parse_date, utc_now_iso
from .visualize import write_html


@dataclass(slots=True)
class CollectionConfig:
    settings: Settings = field(default_factory=Settings.from_env)
    directories: tuple[str, ...] = ("a16z", "pear", "yc", "startx")
    yc_export: Path | None = None
    use_yc_scraper: bool = True
    yc_scraper_path: Path | None = None
    yc_recent: int | None = None
    yc_batches: tuple[str, ...] = ()
    sec_form_d_zips: tuple[Path, ...] = ()
    limit: int | None = 25
    mode: str = "both"  # inference | training | both
    use_hackernews: bool = True
    use_producthunt: bool = True
    use_github: bool = True
    pear_pace_seconds: float = 10.0
    output_dir: Path | None = None
    collected_at: str | None = None
    write_outputs: bool = True
    # Web-search LLM enrichment of company funding rounds and exit/failure outcomes.
    # Runs for EVERY company by default (not conditioned on missing directory signal),
    # and its funding rounds replace the scraped ones. None means "auto": run when the
    # provider's API key is set and a live network fetcher is in use; the fixture
    # pipeline never enriches, keeping the deterministic tests offline. Set to False to
    # opt out entirely.
    enrich_outcomes_llm: bool | None = None
    outcomes_llm_provider: str = "gemini"  # "gemini" (default) or "openai"
    outcomes_llm_model: str | None = None
    outcomes_llm_max_workers: int = 8
    outcomes_llm_rpm: float = DEFAULT_REQUESTS_PER_MINUTE  # cap on request start rate

    def __post_init__(self) -> None:
        if self.mode not in {"inference", "training", "both"}:
            raise ValueError("mode must be inference, training, or both")
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit must be non-negative or None")


@dataclass(slots=True)
class CollectionResult:
    companies: list[dict[str, Any]]
    funding_rounds: list[dict[str, Any]]
    inference_requests: list[dict[str, Any]]
    training_features: list[dict[str, Any]]
    training_outcomes: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    financial_disclosures: list[dict[str, Any]]
    quality_report: dict[str, Any]
    enrichment_queue: list[dict[str, Any]]
    source_runs: list[dict[str, Any]]
    summary: dict[str, Any]
    output_dir: Path | None = None


def _canonicalize_evidence(companies: Sequence[Company], evidence: Iterable[Evidence]) -> list[Evidence]:
    source_to_startup = {
        source_id: company.startup_id for company in companies for source_id in company.source_ids
    }
    rows = []
    for item in evidence:
        canonical = source_to_startup.get(item.source_id)
        rows.append(replace(item, startup_id=canonical) if canonical and canonical != item.startup_id else item)
    return dedupe_evidence(rows)


def _a16z_initial_investments(
    companies: Iterable[Company], *, as_of: date, collected_at: str
) -> tuple[list[FundingRound], list[Evidence]]:
    rounds: list[FundingRound] = []
    evidence: list[Evidence] = []
    for company in companies:
        raw_date = company.external_ids.get("a16z_initial_investment_date")
        event_date = parse_date(raw_date)
        if event_date is None or event_date > as_of:
            continue
        source_url = company.external_ids.get("a16z_announcement_url") or next(
            (url for url in company.directory_urls if "a16z.com" in url), "https://a16z.com/portfolio/"
        )
        raw_stage = company.external_ids.get("a16z_initial_stage")
        item = make_evidence(
            startup_id=company.startup_id,
            channel="a16z_portfolio",
            source_type="financing_document",
            document_name=f"a16z initial investment listing: {company.name}",
            excerpt=(
                f"The current a16z portfolio record reports its initial a16z investment date as "
                f"{event_date.isoformat()}. This is an investor-first-investment event, not a complete "
                "company funding round and no amount is asserted."
            ),
            source_uri=source_url,
            document_date=as_of.isoformat(),  # knowledge date of retrospective directory observation
            collected_at=collected_at,
            verification_status="document_verified",
            confidence="medium",
        )
        evidence.append(item)
        rounds.append(
            FundingRound(
                startup_id=company.startup_id,
                company_name=company.name,
                date=event_date.isoformat(),
                date_basis="investor_first_investment",
                stage=None,
                raw_stage=raw_stage,
                amount_usd=None,
                amount_original=None,
                currency=None,
                source_ids=[item.source_id],
                source_names=["a16z portfolio"],
                source_urls=[source_url],
                headline=item.evidence_excerpt,
                verification_status="document_verified",
                confidence="medium",
            )
        )
    return rounds, evidence


def _llm_funding_rounds(
    companies: Iterable[Company],
    enrichments: Mapping[str, Any],
    *,
    as_of: date,
    collected_at: str,
) -> tuple[list[FundingRound], list[Evidence]]:
    """Build funding rounds (and backing evidence) from the web-search LLM verdict.

    Funding rounds come exclusively from the LLM here — the scraped funding-round
    collection is ignored. Each round is recorded as an estimated/unverified,
    web-sourced signal; only rounds dated on or before ``as_of`` are kept.
    """
    rounds: list[FundingRound] = []
    evidence: list[Evidence] = []
    by_id = {company.startup_id: company for company in companies}
    for startup_id, enrichment in enrichments.items():
        company = by_id.get(startup_id)
        if company is None:
            continue
        model = getattr(enrichment, "model", "llm") or "llm"
        for row in getattr(enrichment, "funding_rounds", None) or []:
            event_date = parse_date(row.get("date"))
            if event_date is None or event_date > as_of:
                continue
            amount = row.get("amount_usd")
            amount_text = row.get("amount_text")
            stage = row.get("stage")
            headline = (
                f"Web-search LLM ({model}) reports a funding round for {company.name} "
                f"dated {event_date.isoformat()}"
                + (f", stage {stage}" if stage else "")
                + (f", amount {amount_text}" if amount_text else "")
                + ". This is a model-derived, web-sourced signal recorded as estimated "
                "and unverified, not an independently verified transaction."
            )
            item = make_evidence(
                startup_id=company.startup_id,
                channel="llm_web_search_funding",
                source_type="manual_research",
                document_name=f"Web-search LLM funding round ({model}): {company.name}",
                excerpt=headline,
                source_uri=None,
                document_date=as_of.isoformat(),
                collected_at=collected_at,
                verification_status="estimated",
                confidence="low",
            )
            company.source_ids = sorted({*company.source_ids, item.source_id})
            evidence.append(item)
            rounds.append(
                FundingRound(
                    startup_id=company.startup_id,
                    company_name=company.name,
                    date=event_date.isoformat(),
                    date_basis="announced",
                    stage=None,
                    raw_stage=(str(stage).strip() if stage else None),
                    amount_usd=amount if isinstance(amount, (int, float)) else None,
                    amount_original=None,
                    currency=None,
                    source_ids=[item.source_id],
                    source_names=[f"web-search LLM ({model})"],
                    source_urls=[],
                    headline=headline,
                    verification_status="estimated",
                    confidence="low",
                )
            )
    return rounds, evidence


def _directory_description_evidence(
    companies: Iterable[Company], *, as_of: date, collected_at: str
) -> list[Evidence]:
    """Retain current directory descriptions as founder-reported text claims."""
    rows: list[Evidence] = []
    for company in companies:
        if not company.description:
            continue
        item = make_evidence(
                startup_id=company.startup_id,
                channel="directory_description",
                source_type="company_website",
                document_name=f"Current directory description: {company.name}",
                excerpt=company.description,
                source_uri=company.directory_urls[0] if company.directory_urls else company.website,
                document_date=as_of.isoformat(),
                collected_at=collected_at,
                verification_status="founder_reported",
                confidence="low",
            )
        company.source_ids = sorted({*company.source_ids, item.source_id})
        rows.append(item)
    return rows


async def _discover(
    config: CollectionConfig, fetcher: Fetcher, *, collected_at: str
) -> list[DirectoryResult]:
    selected = {source.casefold() for source in config.directories}
    jobs: list[Awaitable[DirectoryResult]] = []
    if "yc" in selected:
        if config.yc_export is not None:
            jobs.append(load_yc_export(config.yc_export, settings=config.settings, collected_at=collected_at))
        elif config.use_yc_scraper:
            # No authorised export supplied: auto-run the bundled Algolia scraper.
            jobs.append(
                fetch_yc_algolia(
                    settings=config.settings,
                    collected_at=collected_at,
                    scraper_path=config.yc_scraper_path,
                    recent=config.yc_recent,
                    batches=config.yc_batches or None,
                )
            )
        else:
            # Scraper disabled and no export: a structured skip, not a scrape.
            jobs.append(load_yc_export(None, settings=config.settings, collected_at=collected_at))
    if "a16z" in selected:
        jobs.append(fetch_a16z_directory(fetcher, settings=config.settings, collected_at=collected_at))
    if "startx" in selected:
        jobs.append(
            fetch_startx_directory(
                fetcher,
                api_key=config.settings.startx_api_key,
                settings=config.settings,
                collected_at=collected_at,
            )
        )
    if "pear" in selected:
        jobs.append(
            fetch_pear_directory(
                fetcher,
                settings=config.settings,
                collected_at=collected_at,
                pace_seconds=config.pear_pace_seconds,
            )
        )
    results = await asyncio.gather(*jobs, return_exceptions=True)
    normalized: list[DirectoryResult] = []
    with tqdm(total=len(results), desc="Discovering companies from directories", unit="source") as pbar:
        for result in results:
            if isinstance(result, DirectoryResult):
                normalized.append(result)
            else:
                source = "directory_unknown"
                normalized.append(
                    DirectoryResult(
                        source=source,
                        run=SourceRun(
                            source=source,
                            status="error",
                            started_at=collected_at,
                            finished_at=collected_at,
                            errors=[str(result)],
                        ),
                    )
                )
            pbar.update(1)
    return sorted(normalized, key=lambda item: item.source)


async def _enrich_companies(
    companies: Sequence[Company], config: CollectionConfig, fetcher: Fetcher, *, collected_at: str
) -> tuple[list[EnrichmentResult], list[SourceRun]]:
    enabled: list[str] = []
    skipped: list[SourceRun] = []
    if config.use_hackernews:
        enabled.append("hackernews")
    if config.use_producthunt and config.settings.producthunt_token:
        enabled.append("producthunt")
    elif config.use_producthunt:
        skipped.append(SourceRun("producthunt", "skipped", collected_at, collected_at,
                                 skipped_reason="PRODUCTHUNT_TOKEN not configured"))
    explicit_github = [company for company in companies if company.external_ids.get("github") or company.external_ids.get("github_org")]
    if config.use_github and explicit_github:
        enabled.append("github")
    elif config.use_github:
        skipped.append(SourceRun("github", "skipped", collected_at, collected_at,
                                 skipped_reason="no explicit directory-provided GitHub organization IDs"))

    queue: asyncio.Queue[tuple[Company, str] | None] = asyncio.Queue(maxsize=max(2, config.settings.concurrency * 2))
    results: list[EnrichmentResult] = []

    # Count total enrichment tasks
    total_tasks = sum(
        1 for company in companies
        for source in enabled
        if not (source == "producthunt" and not company.website)
        and not (source == "github" and company not in explicit_github)
    )
    pbar = tqdm(total=total_tasks, desc="Enriching company data", unit="task")

    async def producer() -> None:
        for company in companies:
            for source in enabled:
                if source == "producthunt" and not company.website:
                    continue
                if source == "github" and company not in explicit_github:
                    continue
                await queue.put((company, source))
        for _ in range(max(1, min(config.settings.concurrency, len(companies) * max(1, len(enabled))))):
            await queue.put(None)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                company, source = item
                try:
                    if source == "hackernews":
                        value = await enrich_hackernews(
                            fetcher, company, settings=config.settings, collected_at=collected_at
                        )
                    elif source == "producthunt":
                        value = await enrich_producthunt(
                            fetcher, company, settings=config.settings, collected_at=collected_at
                        )
                    else:
                        value = await enrich_github(
                            fetcher, company, settings=config.settings, collected_at=collected_at
                        )
                    results.append(value)
                except Exception as exc:
                    results.append(EnrichmentResult(source, company.startup_id, errors=[str(exc)]))
            finally:
                pbar.update(1)
                queue.task_done()

    worker_count = max(1, min(config.settings.concurrency, len(companies) * max(1, len(enabled))))
    workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
    await producer()
    await queue.join()
    await asyncio.gather(*workers)
    pbar.close()
    results.sort(key=lambda item: (item.startup_id, item.source))

    source_runs = list(skipped)
    for source in enabled:
        source_results = [item for item in results if item.source == source]
        errors = sorted(error for item in source_results for error in item.errors)
        records = sum(len(item.evidence) for item in source_results)
        source_runs.append(
            SourceRun(
                source=source,
                status="partial" if errors and records else "error" if errors else "ok",
                started_at=collected_at,
                finished_at=collected_at,
                requests=len(source_results),
                records=records,
                errors=errors[:200],
            )
        )
    return results, sorted(source_runs, key=lambda item: item.source)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _write_json(path: Path, value: Any) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    _atomic_text(path, text)


def write_collection_outputs(result: CollectionResult, output_dir: Path, *, as_of: date) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "companies.jsonl", result.companies)
    _write_jsonl(output_dir / "funding_rounds.jsonl", result.funding_rounds)
    _write_jsonl(output_dir / "inference_requests.jsonl", result.inference_requests)
    _write_jsonl(output_dir / "training_features.jsonl", result.training_features)
    _write_jsonl(output_dir / "training_outcomes.jsonl", result.training_outcomes)
    _write_jsonl(output_dir / "evidence_registry.jsonl", result.evidence)
    _write_jsonl(output_dir / "financial_disclosures.jsonl", result.financial_disclosures)
    _write_jsonl(output_dir / "enrichment_queue.jsonl", result.enrichment_queue)
    _write_json(output_dir / "quality_report.json", result.quality_report)
    _write_json(output_dir / "source_runs.json", result.source_runs)
    _write_json(output_dir / "collection_summary.json", result.summary)
    _atomic_text(
        output_dir / "data_dictionary.md",
        render_data_dictionary(
            as_of=as_of,
            sources=[str(row.get("source")) for row in result.source_runs if row.get("status") != "skipped"],
        ),
    )
    write_html(
        result.companies,
        result.funding_rounds,
        result.quality_report,
        result.source_runs,
        output_dir / "scrape_report.html",
        title="Startup funding collection",
    )


def _fixture_outcome_enrichments(
    fetcher: Fetcher, targets: Sequence[Company]
) -> dict[str, Any]:
    """Map a FixtureFetcher's canned, name-keyed verdicts onto target startup_ids.

    Keeps the offline pipeline network-free while still exercising the LLM-derived
    funding-round and outcome path. Returns ``{}`` when the fixture supplies none.
    """
    from .outcomes_llm import OutcomeEnrichment

    canned = getattr(fetcher, "outcome_enrichments", None)
    if not canned:
        return {}
    results: dict[str, Any] = {}
    for company in targets:
        verdict = canned.get(company.name)
        if verdict is None:
            continue
        results[company.startup_id] = replace(verdict, startup_id=company.startup_id) if isinstance(
            verdict, OutcomeEnrichment
        ) else verdict
    return results


async def _enrich_outcomes_llm(
    config: CollectionConfig, targets: Sequence[Company], fetcher: Fetcher
) -> dict[str, Any]:
    """Query the web-search LLM for the funding/exit outcome of EVERY company.

    Unlike the previous behaviour, this always queries the LLM for every company —
    it is not conditioned on the directory lacking an exit/failure signal. It runs
    with an API key present (Gemini by default, OpenAI when selected) and never
    against the offline fixture fetcher (so the deterministic tests stay
    network-free). The blocking, thread-pooled call is offloaded so the event loop
    is not held.
    """
    # Explicit opt-out wins.
    if config.enrich_outcomes_llm is False:
        return {}
    # The fixture fetcher must never trigger a network call; gate by class name to
    # avoid importing mini.py (which imports this module). It may, however, supply
    # canned per-company verdicts so the offline pipeline still exercises the
    # LLM-rounds/outcome path deterministically.
    if type(fetcher).__name__ == "FixtureFetcher":
        return _fixture_outcome_enrichments(fetcher, targets)
    if not resolve_api_key(provider=config.outcomes_llm_provider):
        return {}

    pending = [
        (company.startup_id, company.name)
        for company in targets
        if company.name
    ]
    if not pending:
        return {}
    return await asyncio.to_thread(
        enrich_outcomes,
        pending,
        as_of=config.settings.as_of,
        provider=config.outcomes_llm_provider,
        model=config.outcomes_llm_model,
        max_workers=config.outcomes_llm_max_workers,
        requests_per_minute=config.outcomes_llm_rpm,
    )


async def _collect_with_fetcher(config: CollectionConfig, fetcher: Fetcher) -> CollectionResult:
    collected_at = config.collected_at or utc_now_iso()
    directory_results = await _discover(config, fetcher, collected_at=collected_at)
    discovered = [company for result in directory_results for company in result.companies]
    raw_description_evidence = _directory_description_evidence(
        discovered, as_of=config.settings.as_of, collected_at=collected_at
    )
    cohort = merge_companies(discovered)
    targets = cohort[: config.limit] if config.limit else cohort
    target_ids = {company.startup_id for company in targets}

    directory_evidence = _canonicalize_evidence(
        cohort, [item for result in directory_results for item in result.evidence] + raw_description_evidence
    )
    directory_evidence = [item for item in directory_evidence if item.startup_id in target_ids]
    directory_runs = [result.run for result in directory_results if result.run]

    enrichment_results, enrichment_runs = await _enrich_companies(
        targets, config, fetcher, collected_at=collected_at
    )
    enrichment_evidence = [item for result in enrichment_results for item in result.evidence]
    signals: dict[str, dict[str, Any]] = {company.startup_id: {} for company in targets}
    for enrichment in enrichment_results:
        signals.setdefault(enrichment.startup_id, {}).update(enrichment.signals)

    # SEC Form D rounds are intentionally discarded — funding rounds come from the
    # web-search LLM only — but the filing evidence is retained.
    _sec_rounds, sec_evidence, sec_run = await collect_form_d(
        targets, config.sec_form_d_zips, collected_at=collected_at
    )

    # Funding rounds and exit/failure outcomes come from the web-search LLM for every
    # company; the scraped funding-round collection is ignored entirely.
    outcome_enrichments = await _enrich_outcomes_llm(config, targets, fetcher)
    llm_rounds, llm_round_evidence = _llm_funding_rounds(
        targets, outcome_enrichments, as_of=config.settings.as_of, collected_at=collected_at
    )

    all_evidence = dedupe_evidence(
        [*directory_evidence, *enrichment_evidence, *sec_evidence, *llm_round_evidence]
    )
    rounds = dedupe_funding_rounds(llm_rounds)
    rounds = [round_ for round_ in rounds if (parse_date(round_.date) or config.settings.as_of) <= config.settings.as_of]
    context = CohortContext(targets, rounds)
    disclosure_rows = extract_financial_disclosures([item.to_dict(extended=True) for item in all_evidence])

    inference: list[dict[str, Any]] = []
    training_features: list[dict[str, Any]] = []
    training_outcomes: list[dict[str, Any]] = []
    derived_evidence: list[Evidence] = []
    with tqdm(total=len(targets), desc="Building company snapshots", unit="company") as pbar:
        for company in targets:
            company_rounds = [round_ for round_ in rounds if round_.startup_id == company.startup_id]
            company_disclosures = [
                row for row in disclosure_rows if row.get("startup_id") == company.startup_id
            ]
            if config.mode in {"training", "both"}:
                features, outcomes, derived = build_training_records(
                    company,
                    rounds=company_rounds,
                    all_evidence=all_evidence,
                    signals=signals.get(company.startup_id, {}),
                    context=context,
                    disclosures=company_disclosures,
                    as_of=config.settings.as_of,
                    collected_at=collected_at,
                    outcome_enrichment=outcome_enrichments.get(company.startup_id),
                )
                training_features.extend(features)
                training_outcomes.extend(outcomes)
                derived_evidence.extend(derived)
            if config.mode in {"inference", "both"}:
                build = build_snapshot(
                    company,
                    cutoff=config.settings.as_of,
                    live=True,
                    rounds=company_rounds,
                    evidence=all_evidence,
                    signals=signals.get(company.startup_id, {}),
                    context=context,
                    disclosures=company_disclosures,
                    collected_at=collected_at,
                )
                inference.append(build.record)
                derived_evidence.extend(build.evidence)
            pbar.update(1)

    all_evidence = dedupe_evidence([*all_evidence, *derived_evidence])
    source_runs = sorted([*directory_runs, *enrichment_runs, sec_run], key=lambda item: item.source)
    all_features = [*training_features, *inference]
    post = postprocess_snapshots(
        all_features,
        [item.to_dict(extended=True) for item in all_evidence],
        [item.to_dict() for item in source_runs],
        tracked_fields=FEATURE_FIELDS,
    )
    by_snapshot = {row["snapshot_id"]: row for row in post["snapshots"]}
    training_features = [by_snapshot[row["snapshot_id"]] for row in training_features]
    inference = [by_snapshot[row["snapshot_id"]] for row in inference]
    training_features.sort(key=lambda row: (row["startup_id"], row["observation_date"], row["snapshot_id"]))
    training_outcomes.sort(key=lambda row: (row["startup_id"], row["snapshot_id"]))
    inference.sort(key=lambda row: (row["startup_id"], row["snapshot_id"]))

    summary = {
        "as_of": config.settings.as_of.isoformat(),
        "collected_at": collected_at,
        "companies_discovered": len(cohort),
        "companies_processed": len(targets),
        "funding_rounds": len(rounds),
        "inference_requests": len(inference),
        "training_features": len(training_features),
        "training_outcomes": len(training_outcomes),
        "evidence_records": len(all_evidence),
        "financial_disclosures": len(post["financial_disclosures"]),
        "enrichment_tasks": len(post["enrichment_queue"]),
        "source_status": {item.source: item.status for item in source_runs},
    }
    output_dir = config.output_dir or config.settings.output_dir
    result = CollectionResult(
        companies=[company.to_dict() for company in targets],
        funding_rounds=[round_.to_dict() for round_ in rounds],
        inference_requests=inference,
        training_features=training_features,
        training_outcomes=training_outcomes,
        evidence=[item.to_dict() for item in all_evidence],
        financial_disclosures=post["financial_disclosures"],
        quality_report=post["assessment"],
        enrichment_queue=post["enrichment_queue"],
        source_runs=[item.to_dict() for item in source_runs],
        summary=summary,
        output_dir=output_dir if config.write_outputs else None,
    )
    if config.write_outputs:
        write_collection_outputs(result, output_dir, as_of=config.settings.as_of)
    return result


async def collect(config: CollectionConfig, *, fetcher: Fetcher | None = None) -> CollectionResult:
    """Collect a startup list; source and company I/O execute concurrently."""
    if fetcher is not None:
        return await _collect_with_fetcher(config, fetcher)
    async with AsyncHttpClient(config.settings) as client:
        return await _collect_with_fetcher(config, client)
