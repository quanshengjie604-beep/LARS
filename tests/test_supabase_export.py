import json
import sys
from pathlib import Path


SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from founder_screening.supabase_export import build_tables, export_bundle
from upload_supabase import validate_bundle


def candidate():
    return {
        "candidate_id": "candidate_1",
        "full_name": "Maya Chen",
        "given_name": "Maya",
        "family_name": "Chen",
        "company_name": "Example AI",
        "company_url": "https://example.test",
        "founder_role": "Co-founder",
        "founder_relationship_evidence_url": "https://example.test/team",
        "founded_companies": [{
            "company_name": "Example AI",
            "company_url": "https://example.test",
            "company_profile_url": "https://example.test/team",
            "founder_role": "Co-founder",
            "relationship_evidence_url": "https://example.test/team",
            "status": "Active",
            "launched_at": "2025-01-02",
        }],
        "education": {
            "degrees": [{
                "level": "phd", "institution": "Stanford University",
                "status": "completed", "completed": True, "qs_world_rank": 3,
            }],
            "has_bachelor": None,
            "has_master": None,
            "has_phd": True,
            "highest_documented_level": "phd",
            "highest_ranked_institution": "Stanford University",
            "best_qs_world_rank": 3,
            "qs_edition": 2027,
        },
        "skills": {
            "items": [{"name": "robotics", "evidence_type": "public_bio_term"}],
            "documented_skill_count": 1,
        },
        "career_history": {
            "verified_prior_exit_count": 1,
            "reported_prior_exit_count": 2,
            "exits": [{"company_name": "OldCo", "outcome": "acquisition"}],
            "reported_exit_claims": [{
                "count": 2, "source_url": "https://example.test/team",
                "evidence_excerpt": "2 exits", "verification_status": "founder_reported",
            }],
        },
        "first_seen_at": "2026-01-01T00:00:00Z",
        "last_seen_at": "2026-01-02T00:00:00Z",
    }


def evidence():
    return {
        "source_id": "source_1",
        "candidate_id": "candidate_1",
        "source_type": "company_website",
        "source_uri": "https://example.test/team",
        "verification_status": "founder_reported",
        "confidence": "medium",
        "raw_metadata": {"field": "education"},
    }


def test_normalized_tables_preserve_raw_records_and_evidence_granularity():
    tables = build_tables([candidate()], [evidence()])
    assert len(tables["founders"]) == 1
    assert tables["founders"][0]["best_qs_world_rank"] == "3"
    assert tables["founders"][0]["raw_record"]["full_name"] == "Maya Chen"
    assert len(tables["founder_companies"]) == 1
    assert len(tables["founder_company_roles"]) == 1
    assert len(tables["founder_education"]) == 1
    assert len(tables["founder_skills"]) == 1
    assert len(tables["founder_exits"]) == 1
    assert len(tables["founder_reported_exit_claims"]) == 1
    assert len(tables["founder_evidence"]) == 1


def test_bundle_manifest_checksums_and_relationships_validate(tmp_path):
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "bundle"
    input_dir.mkdir()
    (input_dir / "sourcing_candidates.jsonl").write_text(
        json.dumps(candidate()) + "\n", encoding="utf-8"
    )
    (input_dir / "evidence_registry.jsonl").write_text(
        json.dumps(evidence()) + "\n", encoding="utf-8"
    )
    manifest = export_bundle(input_dir, output_dir)
    loaded_manifest, tables = validate_bundle(output_dir)
    assert manifest["format"] == "lars-supabase-jsonl-v1"
    assert loaded_manifest["tables"][0]["sha256"]
    assert tables["founders"][0]["candidate_id"] == "candidate_1"


def test_migration_enables_rls_and_revokes_frontend_roles():
    migration = (
        Path(__file__).parents[1]
        / "supabase" / "migrations" / "202607180001_founder_screening.sql"
    ).read_text(encoding="utf-8")
    for table in (
        "founders", "founder_companies", "founder_company_roles", "founder_education",
        "founder_skills", "founder_exits", "founder_reported_exit_claims", "founder_evidence",
    ):
        assert f"alter table public.{table} enable row level security" in migration
        assert f"revoke all on public.{table} from anon, authenticated" in migration
