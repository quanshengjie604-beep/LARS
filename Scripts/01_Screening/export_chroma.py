from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_INPUT = Path("screening_handover/founder_sourcing")
DEFAULT_OUTPUT = Path("screening_handover/chroma")
SCALAR_TYPES = (str, int, float, bool)


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


def _metadata(**values: Any) -> dict[str, Any]:
    """Keep only metadata types accepted by current Chroma collections."""
    result: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, SCALAR_TYPES):
            result[key] = value
        elif isinstance(value, list) and all(isinstance(item, SCALAR_TYPES) for item in value):
            result[key] = value
        else:
            raise TypeError(f"unsupported Chroma metadata value for {key}: {type(value).__name__}")
    return result


def _company(candidate: dict[str, Any]) -> dict[str, Any]:
    companies = candidate.get("founded_companies") or []
    return companies[0] if companies else {}


def founder_record(candidate: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    company = _company(candidate)
    education = candidate.get("education") or {}
    career = candidate.get("career_history") or {}
    skills = candidate.get("skills") or {}
    evidence_excerpts = [item.get("evidence_excerpt") for item in evidence if item.get("evidence_excerpt")]
    document_lines = [
        f"Founder: {candidate['full_name']}",
        f"Role: {candidate['founder_role']}",
        f"Company: {candidate['company_name']}",
    ]
    optional_lines = [
        ("Company status", company.get("status")),
        ("YC batch", company.get("batch")),
        ("Industry", company.get("industry")),
        ("Subindustry", company.get("subindustry")),
        ("Location", candidate.get("location")),
        ("Geography", candidate.get("geography")),
        ("Public profile", candidate.get("headline")),
    ]
    document_lines.extend(f"{label}: {value}" for label, value in optional_lines if value)
    for degree in education.get("degrees") or []:
        details = [degree.get("level"), degree.get("institution")]
        if degree.get("qs_world_rank") is not None:
            details.append(f"QS {degree.get('qs_edition')}: {degree['qs_world_rank']}")
        document_lines.append("Education: " + " | ".join(str(item) for item in details if item))
    if career.get("verified_prior_exit_count") is not None:
        document_lines.append(f"Verified prior exits: {career['verified_prior_exit_count']}")
    if skills.get("items"):
        document_lines.append("Documented skills: " + ", ".join(item["name"] for item in skills["items"]))
    document_lines.extend(f"Verified evidence: {excerpt}" for excerpt in evidence_excerpts)
    document_lines.extend([
        f"Company URL: {candidate['company_url']}",
        f"Founder relationship evidence: {candidate['founder_relationship_evidence_url']}",
    ])
    metadata = _metadata(
        record_type="founder",
        candidate_id=candidate["candidate_id"],
        full_name=candidate["full_name"],
        given_name=candidate.get("given_name"),
        family_name=candidate.get("family_name"),
        founder_role=candidate.get("founder_role"),
        company_name=candidate.get("company_name"),
        company_url=candidate.get("company_url"),
        founder_evidence_url=candidate.get("founder_relationship_evidence_url"),
        company_status=company.get("status"),
        yc_batch=company.get("batch"),
        industry=company.get("industry"),
        subindustry=company.get("subindustry"),
        company_launched_at=company.get("launched_at"),
        location=candidate.get("location"),
        geography=candidate.get("geography"),
        discovery_sources=candidate.get("discovery_sources"),
        source_ids=candidate.get("source_ids"),
        profile_urls=candidate.get("profile_urls"),
        company_active=company.get("status") == "Active" if company.get("status") else None,
        has_bachelor=education.get("has_bachelor"),
        has_master=education.get("has_master"),
        has_phd=education.get("has_phd"),
        highest_documented_level=education.get("highest_documented_level"),
        highest_ranked_institution=education.get("highest_ranked_institution"),
        best_qs_world_rank=education.get("best_qs_world_rank"),
        qs_edition=education.get("qs_edition"),
        verified_prior_exit_count=career.get("verified_prior_exit_count"),
        documented_skill_count=skills.get("documented_skill_count"),
        documented_skills=[item["name"] for item in skills.get("items") or []],
    )
    return {
        "id": candidate["candidate_id"],
        "document": "\n".join(document_lines),
        "metadata": metadata,
        "uri": candidate["founder_relationship_evidence_url"],
    }


def evidence_record(evidence: dict[str, Any]) -> dict[str, Any]:
    company = evidence.get("raw_metadata") or {}
    context = [
        evidence.get("evidence_excerpt"),
        f"Document: {evidence.get('document_name')}" if evidence.get("document_name") else None,
        f"Company: {company.get('company_name')}" if company.get("company_name") else None,
        f"Source: {evidence.get('source_uri')}" if evidence.get("source_uri") else None,
    ]
    metadata = _metadata(
        record_type="founder_evidence",
        source_id=evidence["source_id"],
        candidate_id=evidence.get("candidate_id"),
        source_type=evidence.get("source_type"),
        document_name=evidence.get("document_name"),
        source_uri=evidence.get("source_uri"),
        document_date=evidence.get("document_date"),
        collected_at=evidence.get("collected_at"),
        evidence_location=evidence.get("location"),
        verification_status=evidence.get("verification_status"),
        confidence=evidence.get("confidence"),
        company_name=company.get("company_name"),
        company_url=company.get("company_url"),
        founder_role=company.get("founder_role"),
        company_status=company.get("status"),
        yc_batch=company.get("batch"),
        industry=company.get("industry"),
        subindustry=company.get("subindustry"),
        company_launched_at=company.get("launched_at"),
    )
    return {
        "id": evidence["source_id"],
        "document": "\n".join(item for item in context if item),
        "metadata": metadata,
        "uri": evidence.get("source_uri"),
    }


def validate_records(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for index, record in enumerate(records):
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            errors.append(f"record {index}: non-empty string id is required")
        elif record_id in seen:
            errors.append(f"record {index}: duplicate id {record_id}")
        else:
            seen.add(record_id)
        if not isinstance(record.get("document"), str) or not record["document"].strip():
            errors.append(f"record {index}: non-empty document is required")
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            errors.append(f"record {index}: metadata must be an object")
            continue
        for key, value in metadata.items():
            valid = isinstance(value, SCALAR_TYPES) or (
                isinstance(value, list) and value and all(isinstance(item, SCALAR_TYPES) for item in value)
            )
            if not valid:
                errors.append(f"record {index}: unsupported metadata {key}={value!r}")
        if record.get("uri") is not None and not isinstance(record["uri"], str):
            errors.append(f"record {index}: uri must be a string or null")
    return errors


def export(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    candidates = read_jsonl(input_dir / "sourcing_candidates.jsonl")
    evidence = read_jsonl(input_dir / "evidence_registry.jsonl")
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in evidence:
        by_candidate[item["candidate_id"]].append(item)
    founder_records = [
        founder_record(candidate, by_candidate.get(candidate["candidate_id"], []))
        for candidate in candidates
    ]
    evidence_records = [evidence_record(item) for item in evidence]
    errors = validate_records(founder_records) + validate_records(evidence_records)
    if errors:
        raise ValueError(f"Chroma export validation failed: {errors[:20]}")
    founder_file = output_dir / "founders.chroma.jsonl"
    evidence_file = output_dir / "founder_evidence.chroma.jsonl"
    founder_count = write_jsonl(founder_file, founder_records)
    evidence_count = write_jsonl(evidence_file, evidence_records)
    manifest = {
        "format": "chroma-record-jsonl-v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "embedding_policy": "documents_only; collection embedding function computes embeddings",
        "collections": [
            {"suggested_name": "lars_founders", "file": founder_file.name, "records": founder_count},
            {"suggested_name": "lars_founder_evidence", "file": evidence_file.name, "records": evidence_count},
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Convert founder screening JSONL to Chroma-ready records")
    result.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    print(json.dumps(export(args.input_dir, args.output_dir), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
