from decision_engine.validation import validate_features, validate_join


def feature(snapshot="s1"):
    return {"startup_id": "startup_1", "opportunity_id": "opp_1", "snapshot_id": snapshot,
            "observation_date": "2024-01-01", "data_cutoff_date": "2024-01-01",
            "traction": {"arr_usd": None}, "missing_fields": ["traction.arr_usd"]}


def outcome(snapshot="s1"):
    return {"startup_id": "startup_1", "opportunity_id": "opp_1", "snapshot_id": snapshot,
            "next_round": {}, "next_event": {}, "failure": {}, "growth": {}, "exit": {},
            "outcome_observation_end_date": "2026-01-01"}


def test_valid_feature_and_join():
    assert validate_features([feature()]).valid
    assert validate_join([feature()], [outcome()]).valid


def test_live_rejects_outcomes_and_future_cutoff():
    with_outcome = feature(); with_outcome["exit"] = {}
    future_cutoff = feature("s2"); future_cutoff["data_cutoff_date"] = "2024-02-01"
    outcome_report = validate_features([with_outcome], live=True)
    cutoff_report = validate_features([future_cutoff], live=True)
    assert any("outcome" in error for error in outcome_report.errors)
    assert not cutoff_report.valid
