from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .constants import FEATURE_GROUPS, ID_FIELDS


class FeatureRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    startup_id: str | None = None
    opportunity_id: str | None = None
    snapshot_id: str | None = None
    observation_date: date | None = None
    data_cutoff_date: date | None = None
    source_ids: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    contradicted_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def cutoff_not_after_observation(self):
        if self.observation_date and self.data_cutoff_date and self.data_cutoff_date > self.observation_date:
            raise ValueError("data_cutoff_date cannot be after observation_date")
        return self


class ValidationReport(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    record_count: int = 0


def _paths(value: dict[str, Any], prefix: str = "") -> set[str]:
    result = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else key
        result.add(path)
        if isinstance(child, dict):
            result |= _paths(child, path)
    return result


def validate_features(records: list[dict[str, Any]], live: bool = False) -> ValidationReport:
    errors, warnings, seen = [], [], set()
    for idx, raw in enumerate(records, 1):
        try:
            parsed = FeatureRecord.model_validate(raw)
        except ValidationError as exc:
            errors.append(f"record {idx}: {exc.errors(include_url=False)}")
            continue
        if live and any(k in raw for k in ("next_round", "next_event", "failure", "growth", "exit")):
            errors.append(f"record {idx}: inference input contains outcome fields")
        if parsed.snapshot_id:
            if parsed.snapshot_id in seen:
                errors.append(f"record {idx}: duplicate snapshot_id {parsed.snapshot_id}")
            seen.add(parsed.snapshot_id)
        missing_paths = set(parsed.missing_fields)
        for group in FEATURE_GROUPS:
            values = raw.get(group, {}) or {}
            if not isinstance(values, dict):
                errors.append(f"record {idx}: {group} must be an object")
                continue
            for field, value in values.items():
                if value is None and f"{group}.{field}" not in missing_paths:
                    warnings.append(f"record {idx}: null {group}.{field} absent from missing_fields")
        unknown_missing = missing_paths - _paths(raw)
        # Missing fields are allowed to be omitted, so this is informational only.
        if unknown_missing:
            warnings.append(f"record {idx}: {len(unknown_missing)} declared fields are omitted")
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings, record_count=len(records))


def validate_join(features: list[dict[str, Any]], outcomes: list[dict[str, Any]]) -> ValidationReport:
    errors, warnings = [], []
    feature_ids = {r.get("snapshot_id") for r in features if r.get("snapshot_id")}
    outcome_ids = set()
    required_groups = ("next_round", "next_event", "failure", "growth", "exit")
    for idx, row in enumerate(outcomes, 1):
        missing_ids = [k for k in ID_FIELDS[:3] if not row.get(k)]
        if missing_ids:
            errors.append(f"outcome {idx}: missing identifiers {missing_ids}")
        sid = row.get("snapshot_id")
        if sid not in feature_ids:
            errors.append(f"outcome {idx}: snapshot_id has no feature record: {sid}")
        if sid in outcome_ids:
            errors.append(f"outcome {idx}: duplicate snapshot_id {sid}")
        outcome_ids.add(sid)
        for group in required_groups:
            if not isinstance(row.get(group), dict):
                errors.append(f"outcome {idx}: missing object {group}")
        if not row.get("outcome_observation_end_date"):
            errors.append(f"outcome {idx}: missing outcome_observation_end_date")
    unused = feature_ids - outcome_ids
    if unused:
        warnings.append(f"{len(unused)} feature snapshots have no outcome record")
    return ValidationReport(valid=not errors, errors=errors, warnings=warnings, record_count=len(outcomes))
