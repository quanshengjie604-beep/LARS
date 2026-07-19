from __future__ import annotations

from typing import Any

import pandas as pd

from .constants import FEATURE_GROUPS


def get_path(record: dict[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def flatten_features(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for record in records:
        row: dict[str, Any] = {}
        for group in FEATURE_GROUPS:
            values = record.get(group) or {}
            if isinstance(values, dict):
                row.update({f"{group}__{key}": value for key, value in values.items()})
        row["quality__missing_count"] = len(record.get("missing_fields") or [])
        row["quality__contradicted_count"] = len(record.get("contradicted_fields") or [])
        row["quality__source_count"] = len(record.get("source_ids") or [])
        rows.append(row)
    return pd.DataFrame(rows)


def targets(outcomes: list[dict[str, Any]], path: str) -> pd.Series:
    return pd.Series([get_path(row, path) for row in outcomes], dtype="object")


def join_by_snapshot(features: list[dict[str, Any]], outcomes: list[dict[str, Any]]):
    outcome_by_id = {row["snapshot_id"]: row for row in outcomes}
    joined_features, joined_outcomes = [], []
    for row in features:
        outcome = outcome_by_id.get(row.get("snapshot_id"))
        if outcome is not None:
            joined_features.append(row)
            joined_outcomes.append(outcome)
    return joined_features, joined_outcomes

