"""Canonical records shared by directory and enrichment adapters."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from .util import domain_of, normalized_name, stable_id, unique_sorted


@dataclass(slots=True)
class Company:
    name: str
    startup_id: str = ""
    domain: str | None = None
    website: str | None = None
    aliases: list[str] = field(default_factory=list)
    description: str | None = None
    accelerators: list[str] = field(default_factory=list)
    batches: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    geography: str | None = None
    founded_year: int | None = None
    team_size: int | None = None
    founders: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    directory_urls: list[str] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.domain = self.domain or domain_of(self.website)
        if not self.startup_id:
            identity = self.domain or normalized_name(self.name)
            self.startup_id = stable_id("startup", identity)

    @property
    def identity_key(self) -> str:
        return f"domain:{self.domain}" if self.domain else f"name:{normalized_name(self.name)}"

    def to_dict(self) -> dict[str, Any]:
        from .util import json_ready

        return json_ready(self)


@dataclass(slots=True)
class Evidence:
    source_id: str
    startup_id: str
    source_type: str
    document_name: str
    source_uri: str | None
    document_date: str | None
    collected_at: str
    location: str | None
    evidence_excerpt: str
    verification_status: str
    confidence: str
    channel: str | None = None
    content_hash: str | None = None

    def to_dict(self, *, extended: bool = False) -> dict[str, Any]:
        from .util import json_ready

        row = json_ready(self)
        if not extended:
            row.pop("channel", None)
            row.pop("content_hash", None)
        return row


@dataclass(slots=True)
class FundingRound:
    startup_id: str
    company_name: str
    date: str
    date_basis: str
    stage: str | None = None
    raw_stage: str | None = None
    amount_usd: int | float | None = None
    amount_original: int | float | None = None
    currency: str | None = None
    instrument: str | None = None
    announced_date: str | None = None
    close_date: str | None = None
    filed_date: str | None = None
    source_ids: list[str] = field(default_factory=list)
    source_names: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    headline: str | None = None
    verification_status: str = "unverified"
    confidence: str = "low"
    contradicted: bool = False
    round_id: str = ""

    def __post_init__(self) -> None:
        if not self.round_id:
            amount_key = self.amount_usd if self.amount_usd is not None else self.amount_original
            self.round_id = stable_id(
                "round", self.startup_id, self.date, self.stage or "unknown", amount_key or "undisclosed"
            )

    def to_dict(self) -> dict[str, Any]:
        from .util import json_ready

        return json_ready(self)


@dataclass(slots=True)
class SourceRun:
    source: str
    status: str
    started_at: str
    finished_at: str
    requests: int = 0
    cache_hits: int = 0
    records: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        from .util import json_ready

        return json_ready(self)


@dataclass(slots=True)
class DirectoryResult:
    source: str
    companies: list[Company] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    run: SourceRun | None = None


@dataclass(slots=True)
class EnrichmentResult:
    source: str
    startup_id: str
    evidence: list[Evidence] = field(default_factory=list)
    funding_rounds: list[FundingRound] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def merge_companies(companies: Iterable[Company]) -> list[Company]:
    """Resolve duplicates by non-shared domain first and normalized name second."""
    groups: list[list[Company]] = []
    domain_index: dict[str, int] = {}
    name_index: dict[str, int] = {}

    for company in companies:
        name_key = normalized_name(company.name)
        index = domain_index.get(company.domain or "") if company.domain else None
        if index is None:
            index = name_index.get(name_key)
        if index is None:
            index = len(groups)
            groups.append([])
        groups[index].append(company)
        if company.domain:
            domain_index[company.domain] = index
        if name_key:
            name_index[name_key] = index

    merged: list[Company] = []
    for group in groups:
        # Prefer a record with a real domain, then the richest description/name.
        primary = max(group, key=lambda item: (bool(item.domain), len(item.description or ""), len(item.name)))
        domains = unique_sorted(item.domain for item in group)
        canonical_domain = primary.domain or (domains[0] if domains else None)
        identity = canonical_domain or normalized_name(primary.name)
        row = replace(
            primary,
            startup_id=stable_id("startup", identity),
            domain=canonical_domain,
            aliases=unique_sorted([item.name for item in group] + [a for item in group for a in item.aliases]),
            accelerators=unique_sorted(a for item in group for a in item.accelerators),
            batches=unique_sorted(a for item in group for a in item.batches),
            sectors=unique_sorted(a for item in group for a in item.sectors),
            founders=unique_sorted(a for item in group for a in item.founders),
            statuses=unique_sorted(a for item in group for a in item.statuses),
            directory_urls=unique_sorted(a for item in group for a in item.directory_urls),
            source_ids=unique_sorted(a for item in group for a in item.source_ids),
            external_ids={key: value for item in group for key, value in sorted(item.external_ids.items())},
            founded_year=min((item.founded_year for item in group if item.founded_year), default=None),
            team_size=next((item.team_size for item in group if item.team_size is not None), None),
            geography=next((item.geography for item in group if item.geography), None),
            website=next((item.website for item in group if item.domain == canonical_domain), primary.website),
        )
        merged.append(row)
    return sorted(merged, key=lambda item: (item.name.casefold(), item.startup_id))
