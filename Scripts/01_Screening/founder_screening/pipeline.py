from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .collectors import COLLECTORS
from .http import PoliteHttpClient
from .models import Candidate, CollectedCandidate, Evidence, FOUNDER_FEATURE_PATHS, normalize_name, stable_id, utc_now

LOGGER = logging.getLogger(__name__)


def _unique(values: Iterable[Any]) -> list[Any]:
    result = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


@dataclass
class CandidateStore:
    candidates: dict[str, Candidate] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)
    _strong_keys: dict[str, str] = field(default_factory=dict)
    _source_names: dict[str, str] = field(default_factory=dict)

    def _keys(self, item: Candidate) -> tuple[list[str], list[str]]:
        strong = [f"url:{url.lower().rstrip('/')}" for url in item.profile_urls if url]
        login = item.open_source.get("github_login")
        if login:
            strong.append(f"github:{login.lower()}")
        for affiliation in item.affiliations:
            strong.append(f"name_aff:{normalize_name(item.full_name)}|{normalize_name(affiliation)}")
        source_names = [f"{source}:{normalize_name(item.full_name)}" for source in item.discovery_sources]
        return strong, source_names

    def add(self, collected: CollectedCandidate) -> bool:
        incoming = collected.candidate
        # Source IDs are canonicalized only after the final candidate ID is known.
        incoming.source_ids = []
        strong, source_names = self._keys(incoming)
        existing_id = next((self._strong_keys[k] for k in strong if k in self._strong_keys), None)
        if existing_id is None:
            existing_id = next((self._source_names[k] for k in source_names if k in self._source_names), None)
        if existing_id is None:
            existing_id = stable_id("candidate", incoming.full_name, *(incoming.affiliations or incoming.discovery_sources))
            incoming.candidate_id = existing_id
            self.candidates[existing_id] = incoming
            created = True
        else:
            self._merge(self.candidates[existing_id], incoming)
            created = False
        current = self.candidates[existing_id]
        current.last_seen_at = utc_now()
        for evidence in collected.evidence:
            evidence.candidate_id = existing_id
            evidence.source_id = stable_id("source", existing_id, evidence.source_uri, evidence.evidence_excerpt[:80])
            self.evidence[evidence.source_id] = evidence
            if evidence.source_id not in current.source_ids:
                current.source_ids.append(evidence.source_id)
        final_strong, final_source_names = self._keys(current)
        for key in final_strong:
            self._strong_keys[key] = existing_id
        for key in final_source_names:
            self._source_names[key] = existing_id
        return created

    @staticmethod
    def _merge(target: Candidate, source: Candidate):
        for attr in ("aliases", "affiliations", "founded_companies", "profile_urls", "discovery_sources", "entrepreneurial_signals", "source_ids"):
            setattr(target, attr, _unique(getattr(target, attr) + getattr(source, attr)))
        for attr in ("headline", "current_role", "location", "geography"):
            left, right = getattr(target, attr), getattr(source, attr)
            if left is None and right is not None:
                setattr(target, attr, right)
            elif left and right and left != right:
                field_path = attr
                if field_path not in target.contradicted_fields:
                    target.contradicted_fields.append(field_path)
        for key in ("papers", "categories", "coauthors"):
            target.research[key] = _unique(target.research.get(key, []) + source.research.get(key, []))
        target.research["coauthors"] = [
            name for name in target.research["coauthors"]
            if normalize_name(name) != normalize_name(target.full_name)
        ]
        for key in ("projects", "awards"):
            target.hackathons[key] = _unique(target.hackathons.get(key, []) + source.hackathons.get(key, []))
        for key in ("github_login", "public_repos", "followers"):
            if target.open_source.get(key) is None and source.open_source.get(key) is not None:
                target.open_source[key] = source.open_source[key]
        for key in ("owned_repositories", "contributed_repositories"):
            target.open_source[key] = _unique(target.open_source.get(key, []) + source.open_source.get(key, []))


def validate_candidate(candidate: Candidate) -> list[str]:
    errors = []
    if not candidate.candidate_id or not candidate.full_name:
        errors.append("candidate_id and full_name are required")
    for path in FOUNDER_FEATURE_PATHS:
        field = path.split(".")[-1]
        if field not in candidate.founder_features:
            errors.append(f"missing key {path}")
        elif candidate.founder_features[field] is None and path not in candidate.missing_fields:
            errors.append(f"null field absent from missing_fields: {path}")
    if not candidate.source_ids:
        errors.append("at least one source_id is required")
    if not candidate.founded_companies:
        errors.append("at least one publicly verified founded company is required")
    if not candidate.company_name or not candidate.company_url:
        errors.append("company_name and company_url are required")
    if not candidate.founder_role or "founder" not in candidate.founder_role.lower():
        errors.append("founder_role must explicitly contain Founder or Co-founder")
    if not candidate.founder_relationship_evidence_url:
        errors.append("founder_relationship_evidence_url is required")
    for index, company in enumerate(candidate.founded_companies):
        required = ("company_name", "company_url", "founder_role", "relationship_evidence_url")
        missing = [field for field in required if not company.get(field)]
        if missing:
            errors.append(f"founded_companies[{index}] missing required fields: {missing}")
        if company.get("founder_role") and "founder" not in company["founder_role"].lower():
            errors.append(f"founded_companies[{index}].founder_role is not a founder role")
    return errors


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def run(config: dict[str, Any], output_dir: Path, max_candidates: int = 2000, selected_sources: list[str] | None = None) -> dict[str, Any]:
    if max_candidates < 1 or max_candidates > 2000:
        raise ValueError("max_candidates must be between 1 and 2000")
    sources_config = config.get("sources", {})
    names = selected_sources or [name for name, value in sources_config.items() if value.get("enabled", False)]
    unknown = set(names) - set(COLLECTORS)
    if unknown:
        raise ValueError(f"unknown sources: {sorted(unknown)}")
    client = PoliteHttpClient(
        user_agent=config.get("http", {}).get("user_agent", "LARS-Founder-Screening/0.1 (research prototype)"),
        timeout_seconds=int(config.get("http", {}).get("timeout_seconds", 30)),
        default_delay_seconds=float(config.get("http", {}).get("default_delay_seconds", 1.0)),
    )
    store = CandidateStore()
    errors, counts = [], Counter()
    started = utc_now()
    for name in names:
        if len(store.candidates) >= max_candidates:
            break
        source_config = sources_config.get(name, {})
        quota = min(int(source_config.get("quota", max_candidates)), max_candidates - len(store.candidates))
        collector = COLLECTORS[name](client, source_config)
        overfetch = max(1, int(source_config.get("overfetch_factor", 3)))
        source_new = 0
        LOGGER.info("Collecting up to %d unique candidates from %s", quota, name)
        try:
            for collected in collector.collect(quota * overfetch):
                created = store.add(collected)
                counts[name] += 1
                if created:
                    source_new += 1
                if source_new >= quota or len(store.candidates) >= max_candidates:
                    break
        except Exception as exc:
            LOGGER.exception("Collector %s failed", name)
            errors.append({"source": name, "error": str(exc)})
    validation_errors = []
    for candidate in store.candidates.values():
        for error in validate_candidate(candidate):
            validation_errors.append({"candidate_id": candidate.candidate_id, "error": error})
    if validation_errors:
        raise ValueError(f"candidate validation failed: {validation_errors[:10]}")
    candidates = sorted(store.candidates.values(), key=lambda item: item.candidate_id)
    evidence = sorted(store.evidence.values(), key=lambda item: item.source_id)
    write_jsonl(output_dir / "sourcing_candidates.jsonl", (item.to_dict() for item in candidates))
    write_jsonl(output_dir / "evidence_registry.jsonl", (item.to_dict() for item in evidence))
    report = {
        "run_id": stable_id("crawl", started),
        "started_at": started,
        "completed_at": utc_now(),
        "requested_max_candidates": max_candidates,
        "unique_candidates": len(candidates),
        "evidence_records": len(evidence),
        "raw_records_by_source": dict(counts),
        "sources_attempted": names,
        "errors": errors,
        "outputs": ["sourcing_candidates.jsonl", "evidence_registry.jsonl"],
    }
    write_jsonl(output_dir / "crawl_runs.jsonl", [report])
    return report
