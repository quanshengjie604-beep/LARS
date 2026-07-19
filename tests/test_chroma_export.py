import json
import sys
from pathlib import Path


SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from export_chroma import export, founder_record, validate_records
from upload_chroma import upsert_file


def candidate():
    return {
        "candidate_id": "candidate_1",
        "full_name": "Maya Chen",
        "given_name": "Maya",
        "family_name": "Chen",
        "headline": "Robotics researcher and company builder.",
        "location": "Boston, MA, USA",
        "geography": "United States of America",
        "company_name": "Example Robotics",
        "company_url": "https://example.test",
        "founder_role": "Co-Founder & CTO",
        "founder_relationship_evidence_url": "https://evidence.test/company",
        "founded_companies": [{
            "company_name": "Example Robotics",
            "company_url": "https://example.test",
            "founder_role": "Co-Founder & CTO",
            "relationship_evidence_url": "https://evidence.test/company",
            "status": "Active",
            "batch": "Spring 2026",
            "industry": "Industrials",
            "subindustry": "Robotics",
            "launched_at": "2026-03-01",
        }],
        "discovery_sources": ["yc"],
        "source_ids": ["source_1"],
        "profile_urls": [],
    }


def evidence():
    return {
        "source_id": "source_1",
        "candidate_id": "candidate_1",
        "source_type": "manual_research",
        "document_name": "Example Robotics company profile",
        "source_uri": "https://evidence.test/company",
        "document_date": None,
        "collected_at": "2026-07-18T00:00:00Z",
        "location": "founder card",
        "evidence_excerpt": "Maya Chen is publicly listed as Co-Founder & CTO of Example Robotics.",
        "verification_status": "document_verified",
        "confidence": "high",
        "raw_metadata": candidate()["founded_companies"][0],
    }


def test_founder_record_is_chroma_compatible():
    record = founder_record(candidate(), [evidence()])
    assert record["id"] == "candidate_1"
    assert "Verified evidence:" in record["document"]
    assert record["metadata"]["company_name"] == "Example Robotics"
    assert not validate_records([record])
    assert all(value is not None for value in record["metadata"].values())


def test_export_writes_two_valid_collections(tmp_path):
    input_dir = tmp_path / "source"
    output_dir = tmp_path / "chroma"
    input_dir.mkdir()
    (input_dir / "sourcing_candidates.jsonl").write_text(
        json.dumps(candidate()) + "\n", encoding="utf-8"
    )
    (input_dir / "evidence_registry.jsonl").write_text(
        json.dumps(evidence()) + "\n", encoding="utf-8"
    )
    manifest = export(input_dir, output_dir)
    assert [item["records"] for item in manifest["collections"]] == [1, 1]
    for name in ("founders.chroma.jsonl", "founder_evidence.chroma.jsonl"):
        records = [json.loads(line) for line in (output_dir / name).read_text(encoding="utf-8").splitlines()]
        assert not validate_records(records)


def test_uploader_does_not_treat_evidence_uri_as_external_data(tmp_path):
    class Collection:
        name = "test"

        def __init__(self):
            self.calls = []

        def upsert(self, **kwargs):
            self.calls.append(kwargs)

    record = founder_record(candidate(), [evidence()])
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    collection = Collection()
    assert upsert_file(collection, path, batch_size=100) == 1
    assert "uris" not in collection.calls[0]
    assert collection.calls[0]["metadatas"][0]["founder_evidence_url"] == record["uri"]