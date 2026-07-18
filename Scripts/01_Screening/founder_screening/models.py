from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


FOUNDER_FEATURE_PATHS = (
    "founder_features.founder_score_persistent",
    "founder_features.founder_axis",
    "founder_features.founder_axis_trend",
    "founder_features.founder_prior_exits",
    "founder_features.founder_prior_startups",
    "founder_features.single_founder_flag",
    "founder_features.technical_founder_flag",
    "founder_features.founder_industry_experience_years",
    "founder_features.proprietary_score",
    "founder_features.patent_count",
    "founder_features.open_source_activity_score",
    "founder_features.public_footprint_score",
    "founder_features.network_centrality",
    "founder_features.soft_skill_estimate",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_name(name: str) -> str:
    value = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def stable_id(prefix: str, *parts: str) -> str:
    raw = "|".join(normalize_name(part) for part in parts if part)
    return f"{prefix}_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


@dataclass
class Evidence:
    source_id: str
    candidate_id: str
    source_type: str
    document_name: str
    source_uri: str
    document_date: str | None
    collected_at: str
    location: str | None
    evidence_excerpt: str
    verification_status: str = "document_verified"
    confidence: str = "medium"
    raw_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Candidate:
    candidate_id: str
    full_name: str
    given_name: str | None = None
    family_name: str | None = None
    aliases: list[str] = field(default_factory=list)
    headline: str | None = None
    current_role: str | None = None
    affiliations: list[str] = field(default_factory=list)
    location: str | None = None
    geography: str | None = None
    profile_urls: list[str] = field(default_factory=list)
    discovery_sources: list[str] = field(default_factory=list)
    entrepreneurial_signals: list[dict[str, Any]] = field(default_factory=list)
    research: dict[str, Any] = field(default_factory=lambda: {"papers": [], "categories": [], "coauthors": []})
    hackathons: dict[str, Any] = field(default_factory=lambda: {"projects": [], "awards": []})
    open_source: dict[str, Any] = field(default_factory=lambda: {
        "github_login": None, "public_repos": None, "followers": None,
        "owned_repositories": [], "contributed_repositories": [],
    })
    founder_features: dict[str, Any] = field(default_factory=lambda: {
        "founder_score_persistent": None,
        "founder_axis": None,
        "founder_axis_trend": None,
        "founder_prior_exits": None,
        "founder_prior_startups": None,
        "single_founder_flag": None,
        "technical_founder_flag": None,
        "founder_industry_experience_years": None,
        "proprietary_score": None,
        "patent_count": None,
        "open_source_activity_score": None,
        "public_footprint_score": None,
        "network_centrality": None,
        "soft_skill_estimate": None,
    })
    source_ids: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=lambda: list(FOUNDER_FEATURE_PATHS))
    contradicted_fields: list[str] = field(default_factory=list)
    first_seen_at: str = field(default_factory=utc_now)
    last_seen_at: str = field(default_factory=utc_now)

    @classmethod
    def from_name(cls, full_name: str, source_key: str, affiliation: str = "") -> "Candidate":
        parts = full_name.strip().split()
        return cls(
            candidate_id=stable_id("candidate", full_name, affiliation or source_key),
            full_name=full_name.strip(),
            given_name=parts[0] if parts else None,
            family_name=parts[-1] if len(parts) > 1 else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CollectedCandidate:
    candidate: Candidate
    evidence: list[Evidence]

