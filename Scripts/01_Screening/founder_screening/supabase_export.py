from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import stable_id


TABLE_FILES = {
    "founders": "founders.supabase.jsonl",
    "founder_companies": "founder_companies.supabase.jsonl",
    "founder_company_roles": "founder_company_roles.supabase.jsonl",
    "founder_education": "founder_education.supabase.jsonl",
    "founder_skills": "founder_skills.supabase.jsonl",
    "founder_exits": "founder_exits.supabase.jsonl",
    "founder_reported_exit_claims": "founder_reported_exit_claims.supabase.jsonl",
    "founder_evidence": "founder_evidence.supabase.jsonl",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            count += 1
    return count


def _record_token(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _company_id(company: dict[str, Any]) -> str:
    identity = (
        company.get("company_url")
        or company.get("company_profile_url")
        or company.get("company_name")
        or "unknown-company"
    )
    return stable_id("company", str(identity))


def _founder_row(candidate: dict[str, Any]) -> dict[str, Any]:
    education = candidate.get("education") or {}
    career = candidate.get("career_history") or {}
    skills = candidate.get("skills") or {}
    return {
        "candidate_id": candidate["candidate_id"],
        "full_name": candidate["full_name"],
        "given_name": candidate.get("given_name"),
        "family_name": candidate.get("family_name"),
        "aliases": candidate.get("aliases") or [],
        "headline": candidate.get("headline"),
        "current_founder_role": candidate.get("current_role"),
        "affiliations": candidate.get("affiliations") or [],
        "location": candidate.get("location"),
        "geography": candidate.get("geography"),
        "company_name": candidate.get("company_name"),
        "company_url": candidate.get("company_url"),
        "founder_role": candidate.get("founder_role"),
        "founder_relationship_evidence_url": candidate.get("founder_relationship_evidence_url"),
        "profile_urls": candidate.get("profile_urls") or [],
        "discovery_sources": candidate.get("discovery_sources") or [],
        "entrepreneurial_signals": candidate.get("entrepreneurial_signals") or [],
        "research": candidate.get("research") or {},
        "hackathons": candidate.get("hackathons") or {},
        "open_source": candidate.get("open_source") or {},
        "founder_features": candidate.get("founder_features") or {},
        "missing_fields": candidate.get("missing_fields") or [],
        "contradicted_fields": candidate.get("contradicted_fields") or [],
        "has_bachelor": education.get("has_bachelor"),
        "has_master": education.get("has_master"),
        "has_phd": education.get("has_phd"),
        "highest_documented_level": education.get("highest_documented_level"),
        "highest_ranked_institution": education.get("highest_ranked_institution"),
        "best_qs_world_rank": (
            str(education["best_qs_world_rank"])
            if education.get("best_qs_world_rank") is not None else None
        ),
        "qs_edition": education.get("qs_edition"),
        "verified_prior_exit_count": career.get("verified_prior_exit_count"),
        "reported_prior_exit_count": career.get("reported_prior_exit_count"),
        "documented_skill_count": skills.get("documented_skill_count") or 0,
        "first_seen_at": candidate.get("first_seen_at"),
        "last_seen_at": candidate.get("last_seen_at"),
        "raw_record": candidate,
    }


def build_tables(
    candidates: list[dict[str, Any]], evidence: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    candidate_ids = {item["candidate_id"] for item in candidates}
    if len(candidate_ids) != len(candidates):
        raise ValueError("candidate_id values must be unique")
    invalid_links = sorted({item.get("candidate_id") for item in evidence} - candidate_ids)
    if invalid_links:
        raise ValueError(f"evidence contains unknown candidate_id values: {invalid_links[:5]}")

    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLE_FILES}
    companies: dict[str, dict[str, Any]] = {}
    roles: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        candidate_id = candidate["candidate_id"]
        tables["founders"].append(_founder_row(candidate))
        for index, company in enumerate(candidate.get("founded_companies") or []):
            company_id = _company_id(company)
            companies[company_id] = {
                "company_id": company_id,
                "company_name": company.get("company_name") or candidate.get("company_name") or "Unknown",
                "company_url": company.get("company_url"),
                "company_profile_url": company.get("company_profile_url"),
                "status": company.get("status"),
                "batch": company.get("batch"),
                "industry": company.get("industry"),
                "subindustry": company.get("subindustry"),
                "launched_at": company.get("launched_at"),
                "raw_record": company,
            }
            roles[(candidate_id, company_id)] = {
                "candidate_id": candidate_id,
                "company_id": company_id,
                "founder_role": company.get("founder_role") or candidate.get("founder_role"),
                "relationship_evidence_url": company.get("relationship_evidence_url"),
                "is_primary": index == 0,
                "raw_record": company,
            }

        education = candidate.get("education") or {}
        for degree in education.get("degrees") or []:
            tables["founder_education"].append({
                "education_id": stable_id("education", candidate_id, _record_token(degree)),
                "candidate_id": candidate_id,
                "level": degree.get("level"),
                "degree_name": degree.get("degree_name"),
                "institution": degree.get("institution"),
                "status": degree.get("status"),
                "completed": degree.get("completed"),
                "qs_world_rank": (
                    str(degree["qs_world_rank"])
                    if degree.get("qs_world_rank") is not None else None
                ),
                "qs_edition": degree.get("qs_edition"),
                "education_source_url": degree.get("education_source_url"),
                "qs_source_url": degree.get("qs_source_url"),
                "evidence_excerpt": degree.get("evidence_excerpt"),
                "raw_record": degree,
            })

        for skill in (candidate.get("skills") or {}).get("items") or []:
            tables["founder_skills"].append({
                "founder_skill_id": stable_id("founder_skill", candidate_id, _record_token(skill)),
                "candidate_id": candidate_id,
                "skill_name": skill["name"],
                "evidence_type": skill.get("evidence_type"),
                "evidence_url": skill.get("evidence_url"),
                "evidence_excerpt": skill.get("evidence_excerpt"),
                "raw_record": skill,
            })

        career = candidate.get("career_history") or {}
        for exit_record in career.get("exits") or []:
            tables["founder_exits"].append({
                "exit_id": stable_id("exit", candidate_id, _record_token(exit_record)),
                "candidate_id": candidate_id,
                "company_name": exit_record.get("company_name"),
                "outcome": exit_record["outcome"],
                "source_status": exit_record.get("source_status"),
                "evidence_url": exit_record.get("evidence_url"),
                "evidence_excerpt": exit_record.get("evidence_excerpt"),
                "raw_record": exit_record,
            })
        for claim in career.get("reported_exit_claims") or []:
            tables["founder_reported_exit_claims"].append({
                "claim_id": stable_id("exit_claim", candidate_id, _record_token(claim)),
                "candidate_id": candidate_id,
                "reported_count": claim["count"],
                "source_url": claim.get("source_url"),
                "evidence_excerpt": claim.get("evidence_excerpt"),
                "verification_status": claim.get("verification_status") or "founder_reported",
                "raw_record": claim,
            })

    tables["founder_companies"] = list(companies.values())
    tables["founder_company_roles"] = list(roles.values())
    tables["founder_evidence"] = [{
        "source_id": item["source_id"],
        "candidate_id": item["candidate_id"],
        "source_type": item.get("source_type"),
        "document_name": item.get("document_name"),
        "source_uri": item.get("source_uri"),
        "document_date": item.get("document_date"),
        "collected_at": item.get("collected_at"),
        "location": item.get("location"),
        "evidence_excerpt": item.get("evidence_excerpt"),
        "verification_status": item.get("verification_status"),
        "confidence": item.get("confidence"),
        "raw_metadata": item.get("raw_metadata") or {},
        "raw_record": item,
    } for item in evidence]

    id_fields = {
        "founders": ("candidate_id",),
        "founder_companies": ("company_id",),
        "founder_company_roles": ("candidate_id", "company_id"),
        "founder_education": ("education_id",),
        "founder_skills": ("founder_skill_id",),
        "founder_exits": ("exit_id",),
        "founder_reported_exit_claims": ("claim_id",),
        "founder_evidence": ("source_id",),
    }
    for table, fields in id_fields.items():
        keys = [tuple(row[field] for field in fields) for row in tables[table]]
        if len(keys) != len(set(keys)):
            raise ValueError(f"duplicate primary key generated for {table}")
    return tables


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_bundle(input_dir: Path, output_dir: Path, *, write: bool = True) -> dict[str, Any]:
    candidates = read_jsonl(input_dir / "sourcing_candidates.jsonl")
    evidence = read_jsonl(input_dir / "evidence_registry.jsonl")
    tables = build_tables(candidates, evidence)
    manifest: dict[str, Any] = {
        "format": "lars-supabase-jsonl-v1",
        "generated_at": _utc_now(),
        "source_directory": str(input_dir),
        "migration": "supabase/migrations/202607180001_founder_screening.sql",
        "network_requests": False,
        "tables": [],
    }
    for table, file_name in TABLE_FILES.items():
        item = {"table": table, "file": file_name, "records": len(tables[table])}
        if write:
            path = output_dir / file_name
            write_jsonl(path, tables[table])
            item["sha256"] = _sha256(path)
        manifest["tables"].append(item)
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return manifest
