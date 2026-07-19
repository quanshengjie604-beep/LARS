import json
import sys
import zipfile
from pathlib import Path


SCREENING_DIR = Path(__file__).parents[1] / "Scripts" / "01_Screening"
sys.path.insert(0, str(SCREENING_DIR))

from founder_screening.enrichment import (
    InstitutionMatcher,
    enrich_candidate,
    extract_education,
    extract_exits,
    extract_skills,
    load_qs_rankings_xlsx,
)


def rankings():
    return {
        "Massachusetts Institute of Technology (MIT)": 1,
        "Stanford University": 3,
        "Harvard University": 5,
    }


def candidate(headline=""):
    return {
        "candidate_id": "candidate_1",
        "full_name": "Maya Chen",
        "headline": headline,
        "company_name": "Example AI",
        "founder_relationship_evidence_url": "https://evidence.test/founder",
        "founded_companies": [{
            "company_name": "Example AI",
            "status": "Active",
            "relationship_evidence_url": "https://evidence.test/founder",
        }],
        "founder_features": {"founder_prior_exits": None},
        "missing_fields": ["founder_features.founder_prior_exits"],
        "source_ids": ["source_original"],
        "open_source": {"owned_repositories": []},
        "research": {"categories": []},
    }


def test_explicit_degrees_and_exact_qs_rank_are_extracted():
    result = extract_education(
        "She holds a BS and MS in Computer Science from Stanford University. Later received a PhD from MIT.",
        "https://evidence.test/founder",
        InstitutionMatcher(rankings()),
    )
    assert result["has_bachelor"] is True
    assert result["has_master"] is True
    assert result["has_phd"] is True
    assert result["best_qs_world_rank"] == 1
    assert result["highest_ranked_institution"] == "Massachusetts Institute of Technology (MIT)"
    assert {item["qs_world_rank"] for item in result["degrees"]} == {1, 3}


def test_unknown_education_remains_null_not_false():
    result = extract_education("Founder and product builder.", None, InstitutionMatcher(rankings()))
    assert result["has_bachelor"] is None
    assert result["has_master"] is None
    assert result["has_phd"] is None


def test_inactive_company_counts_as_verified_exit_but_active_does_not_imply_zero():
    active = extract_exits(candidate())
    assert active["verified_prior_exit_count"] is None
    inactive_candidate = candidate()
    inactive_candidate["founded_companies"][0]["status"] = "Inactive"
    inactive = extract_exits(inactive_candidate)
    assert inactive["verified_prior_exit_count"] == 1
    assert inactive["exits"][0]["outcome"] == "closed_or_inactive"


def test_skills_are_explicit_terms_and_unique():
    item = candidate("Built machine learning systems in Python; prior machine learning research in robotics.")
    result = extract_skills(item)
    assert {entry["name"] for entry in result["items"]} == {"machine learning", "robotics", "Python"}
    assert result["documented_skill_count"] == 3


def test_enrichment_updates_only_verified_nonzero_exit_feature():
    item = candidate("BS in Computer Science from Stanford. Machine learning in Python.")
    item["founded_companies"][0]["status"] = "Inactive"
    enriched, evidence = enrich_candidate(item, InstitutionMatcher(rankings()))
    assert enriched["founder_features"]["founder_prior_exits"] == 1
    assert "founder_features.founder_prior_exits" not in enriched["missing_fields"]
    assert enriched["education"]["best_qs_world_rank"] == 3
    assert enriched["skills"]["documented_skill_count"] == 3
    assert len(enriched["source_ids"]) > 1
    assert any(item["location"] == "qs_rank" for item in evidence)


def test_qs_xlsx_reader_uses_rank_and_name_columns(tmp_path):
    path = tmp_path / "qs.xlsx"
    rows = []
    for index in range(1, 1002):
        rank = index
        name = f"University {index}"
        rows.append(
            f'<row r="{index + 3}"><c r="B{index + 3}"><v>{rank}</v></c>'
            f'<c r="D{index + 3}" t="inlineStr"><is><t>{name}</t></is></c></row>'
        )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(rows)}</sheetData></worksheet>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    loaded = load_qs_rankings_xlsx(path)
    assert loaded["University 1"] == 1
    assert loaded["University 1001"] == 1001
