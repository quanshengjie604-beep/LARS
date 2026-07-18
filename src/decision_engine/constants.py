FEATURE_GROUPS = ("screening", "traction", "financials", "team", "market", "cold_start")
ID_FIELDS = ("startup_id", "opportunity_id", "snapshot_id", "observation_date", "data_cutoff_date")
META_FIELDS = set(ID_FIELDS) | {"source_ids", "missing_fields", "contradicted_fields"}

MODEL_SPECS = {
    "m1": {"kind": "classification", "target": "next_round.progressed_within_24_months"},
    "m2": {"kind": "survival", "duration": "next_event.time_to_next_event_days", "event": "next_event.next_event_observed"},
    "m3": {"kind": "survival", "duration": "failure.time_to_failure_days", "event": "failure.failure_event_observed"},
    "m4": {"kind": "positive_regression", "target": "growth.annual_growth_factor"},
    "m5": {"kind": "multiclass", "target": "exit.exit_type"},
    "m6": {"kind": "positive_regression", "targets": ("exit.exit_valuation_usd", "exit.exit_transaction_value_usd")},
}

