from decision_engine.founder_adapter import founder_candidates_to_requests


def test_candidates_are_aggregated_by_company_without_fabricating_missing_values():
    candidates = [
        {"candidate_id": "c1", "company_name": "Example", "company_url": "https://example.com", "geography": "US",
         "source_ids": ["s1"], "founder_features": {"founder_axis": 80}, "founded_companies": [{"industry": "B2B"}]},
        {"candidate_id": "c2", "company_name": "Example", "company_url": "https://www.example.com",
         "source_ids": ["s2"], "founder_features": {"founder_axis": 60}},
    ]
    rows = founder_candidates_to_requests(candidates, "2026-07-18")
    assert len(rows) == 1
    assert rows[0]["screening"]["founder_axis"] == 70
    assert rows[0]["team"]["single_founder_flag"] is False
    assert rows[0]["traction"]["arr_usd"] is None
    assert "traction.arr_usd" in rows[0]["missing_fields"]
