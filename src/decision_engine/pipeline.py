from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from .constants import MODEL_SPECS
from .features import flatten_features, get_path, join_by_snapshot
from .io import read_jsonl, write_jsonl
from .models import ProbabilisticEstimator
from .validation import validate_features, validate_join


@dataclass
class ModelBundle:
    model_id: str
    estimator: ProbabilisticEstimator
    feature_columns: list[str]
    trained_at: str
    training_rows: int


def _training_rows(model_id: str, features, outcomes):
    spec = MODEL_SPECS[model_id]
    selected_f, y, event = [], [], []
    for feature, outcome in zip(features, outcomes):
        if model_id == "m6":
            value = get_path(outcome, spec["targets"][0])
            if value is None:
                value = get_path(outcome, spec["targets"][1])
        elif spec["kind"] == "survival":
            value = get_path(outcome, spec["duration"])
        else:
            value = get_path(outcome, spec["target"])
        observed = get_path(outcome, spec["event"]) if spec["kind"] == "survival" else None
        # M4/M6 require genuine positive numeric labels; censored M1 rows have null labels.
        valid = value is not None and (spec["kind"] not in {"positive_regression", "survival"} or float(value) > 0)
        if valid and (spec["kind"] != "survival" or observed is not None):
            selected_f.append(feature); y.append(value)
            if spec["kind"] == "survival":
                event.append(bool(observed))
    return selected_f, y, event or None


def train_all(config: dict[str, Any]) -> dict[str, Any]:
    data = config["data"]
    features = read_jsonl(data["training_features"])
    outcomes = read_jsonl(data["training_outcomes"])
    feature_report = validate_features(features)
    join_report = validate_join(features, outcomes)
    if not feature_report.valid or not join_report.valid:
        raise ValueError({"features": feature_report.model_dump(), "join": join_report.model_dump()})
    features, outcomes = join_by_snapshot(features, outcomes)
    model_dir = Path(config["project"]["model_dir"])
    model_dir.mkdir(parents=True, exist_ok=True)
    minimum_rows = int(config["training"].get("minimum_rows", 20))
    manifest: dict[str, Any] = {"trained": {}, "skipped": {}, "warnings": feature_report.warnings + join_report.warnings}
    now = datetime.now(timezone.utc).isoformat()
    for model_id in config["training"].get("models", MODEL_SPECS):
        selected, y, event = _training_rows(model_id, features, outcomes)
        if len(selected) < minimum_rows:
            manifest["skipped"][model_id] = f"{len(selected)} usable rows; minimum is {minimum_rows}"
            continue
        kind = MODEL_SPECS[model_id]["kind"]
        if kind in {"classification", "multiclass"} and len(set(y)) < 2:
            manifest["skipped"][model_id] = "target contains fewer than two classes"
            continue
        X = flatten_features(selected)
        estimator = ProbabilisticEstimator(
            kind=kind,
            random_state=int(config["project"].get("random_state", 42)),
            strict_backend=bool(config["training"].get("strict_backend", False)),
        ).fit(X, np.asarray(y), event)
        bundle = ModelBundle(model_id, estimator, list(X.columns), now, len(X))
        path = model_dir / f"{model_id}.joblib"
        joblib.dump(bundle, path)
        manifest["trained"][model_id] = {"path": str(path), "rows": len(X), "backend": estimator.backend}
    joblib.dump(manifest, model_dir / "manifest.joblib")
    return manifest


def _confidence(record: dict[str, Any]) -> dict[str, Any]:
    missing = len(record.get("missing_fields") or [])
    contradicted = len(record.get("contradicted_fields") or [])
    sources = len(record.get("source_ids") or [])
    score = max(0.0, min(1.0, 1.0 - 0.025 * missing - 0.15 * contradicted + min(sources, 5) * 0.02))
    level = "high" if score >= 0.8 else "medium" if score >= 0.55 else "low"
    return {"level": level, "score": round(score, 4), "missing_field_count": missing, "contradicted_field_count": contradicted, "source_count": sources}


def infer_all(config: dict[str, Any], output_path: str | Path) -> list[dict[str, Any]]:
    records = read_jsonl(config["data"]["inference_requests"])
    report = validate_features(records, live=True)
    if not report.valid:
        raise ValueError(report.model_dump())
    X = flatten_features(records)
    model_dir = Path(config["project"]["model_dir"])
    predictions: dict[str, list[dict[str, Any]]] = {}
    model_paths = [model_dir / f"m{i}.joblib" for i in range(1, 7)]
    for path in (path for path in model_paths if path.exists()):
        bundle: ModelBundle = joblib.load(path)
        aligned = X.reindex(columns=bundle.feature_columns)
        predictions[bundle.model_id] = bundle.estimator.predict_distribution(
            aligned,
            list(config["inference"].get("quantiles", [0.1, 0.5, 0.9])),
            list(config["inference"].get("survival_horizons_days", [365, 730, 1825])),
        )
    output = []
    for idx, record in enumerate(records):
        output.append({
            "startup_id": record.get("startup_id"),
            "opportunity_id": record.get("opportunity_id"),
            "snapshot_id": record.get("snapshot_id"),
            "company_name": record.get("company_name"),
            "candidate_ids": record.get("candidate_ids", []),
            "prediction_generated_at": datetime.now(timezone.utc).isoformat(),
            "models": {model_id: rows[idx] for model_id, rows in predictions.items()},
            "model_confidence": _confidence(record),
            "data_quality_warnings": [w for w in report.warnings if w.startswith(f"record {idx + 1}:")],
        })
    write_jsonl(output_path, output)
    return output
