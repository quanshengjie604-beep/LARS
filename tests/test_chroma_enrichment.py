import sys
from pathlib import Path


SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from export_chroma import founder_record, validate_records


def test_enrichment_is_included_in_founder_document_and_metadata():
    candidate = {
        "candidate_id": "candidate_1",
        "full_name": "Maya Chen",
        "founder_role": "Co-Founder",
        "company_name": "Example AI",
        "company_url": "https://example.test",
        "founder_relationship_evidence_url": "https://evidence.test",
        "founded_companies": [{"status": "Inactive"}],
        "education": {
            "degrees": [{
                "level": "phd",
                "institution": "Stanford University",
                "qs_world_rank": 2,
                "qs_edition": 2027,
            }],
            "has_bachelor": None,
            "has_master": None,
            "has_phd": True,
            "highest_documented_level": "phd",
            "highest_ranked_institution": "Stanford University",
            "best_qs_world_rank": 2,
            "qs_edition": 2027,
        },
        "career_history": {"verified_prior_exit_count": 1},
        "skills": {"documented_skill_count": 2, "items": [{"name": "Python"}, {"name": "robotics"}]},
    }
    record = founder_record(candidate, [])
    assert "Education: phd | Stanford University | QS 2027: 2" in record["document"]
    assert "Documented skills: Python, robotics" in record["document"]
    assert record["metadata"]["has_phd"] is True
    assert record["metadata"]["verified_prior_exit_count"] == 1
    assert record["metadata"]["documented_skills"] == ["Python", "robotics"]
    assert not validate_records([record])
