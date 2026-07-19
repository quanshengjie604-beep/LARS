"""Build a deterministic, shuffled augmented dataset for the six-model demo."""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
from pathlib import Path

FEATURE_GROUPS = ("screening", "traction", "financials", "team", "market", "cold_start")


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def jitter_feature(row, rng):
    for group in FEATURE_GROUPS:
        for key, value in (row.get(group) or {}).items():
            if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if "score" in key or "axis" in key:
                row[group][key] = round(max(0.0, min(100.0, value + rng.gauss(0, 4.0))), 4)
            elif value >= 0:
                row[group][key] = round(max(0.0, value * math.exp(rng.gauss(0, 0.12))), 4)
            else:
                row[group][key] = round(value + rng.gauss(0, max(abs(value) * 0.08, 0.01)), 4)


def make_outcome(source, feature, rng):
    row = copy.deepcopy(source)
    score = float((feature.get("screening") or {}).get("founder_axis") or 50.0)
    exit_type = (row.get("exit") or {}).get("exit_type", "no_exit_observed")
    progressed_probability = max(0.08, min(0.92, 0.2 + score / 140 + (0.18 if exit_type != "no_exit_observed" else -0.08)))
    progressed = rng.random() < progressed_probability
    row["next_round"].update({
        "next_round_observed": progressed, "next_round_stage": "series_a" if progressed else None,
        "next_round_date": None, "next_round_size_usd": round((2_000_000 + score * 100_000) * math.exp(rng.gauss(0, 0.3)), -3) if progressed else None,
        "progressed_within_24_months": progressed,
    })
    event_days = max(60, int(rng.lognormvariate(math.log(650 if progressed else 1500), 0.35)))
    row["next_event"].update({"next_event_type": "funding_round" if progressed else "none_observed",
        "next_event_date": None, "time_to_next_event_days": event_days, "next_event_observed": progressed})
    failed = exit_type == "no_exit_observed" and not progressed and rng.random() < max(0.08, (65 - score) / 100)
    row["failure"].update({"failure_observed": failed, "failure_date": None,
        "failure_definition": "shutdown_confirmed" if failed else None,
        "time_to_failure_days": max(90, int(rng.lognormvariate(math.log(900 if failed else 1900), 0.3))),
        "failure_event_observed": failed})
    growth = round(max(0.25, math.exp((score - 50) / 90 + rng.gauss(0, 0.22)) * (1.2 if progressed else 0.82)), 4)
    year = int(feature["observation_date"][:4])
    start_revenue = round(rng.lognormvariate(math.log(1_000_000), 0.5), -2)
    row["growth"] = {"revenue_start_date": f"{year}-01-01", "revenue_start_usd": start_revenue,
        "revenue_end_date": f"{year + 1}-01-01", "revenue_end_usd": round(start_revenue * growth, -2),
        "growth_measurement_type": "annualized_revenue", "annual_growth_factor": growth}
    row["m4_included"] = True
    if exit_type != "no_exit_observed":
        multiplier = {"acquisition": 1.0, "ipo": 4.0, "secondary": 2.0}.get(exit_type, 1.0)
        value = round((25_000_000 + score * 2_000_000) * multiplier * math.exp(rng.gauss(0, 0.35)), -3)
        row["exit"].update({"exit_valuation_usd": value, "exit_transaction_value_usd": value,
            "exit_value_type": "transaction_value" if exit_type == "acquisition" else "ipo_market_cap",
            "exit_value_disclosed": True, "exit_value_verified": False})
    row.pop("demo_synthetic", None)
    row.pop("synthetic_fields", None)
    row["source_ids"] = list(dict.fromkeys((row.get("source_ids") or []) + ["source_demo_augmented_v2"]))
    return row


def build(features, outcomes, target_rows=1000, seed=42):
    rng = random.Random(seed)
    paired = [(f, o) for f, o in zip(features, outcomes)]
    generated = []
    for index in range(target_rows):
        base_feature, base_outcome = copy.deepcopy(rng.choice(paired))
        suffix = f"aug_{index:05d}"
        startup_id = f"{base_feature['startup_id']}_{suffix}"
        opportunity_id = f"{base_feature['opportunity_id']}_{suffix}"
        snapshot_id = f"{base_feature['snapshot_id']}_{suffix}"
        base_feature.update({"startup_id": startup_id, "opportunity_id": opportunity_id, "snapshot_id": snapshot_id})
        jitter_feature(base_feature, rng)
        outcome = make_outcome(base_outcome, base_feature, rng)
        outcome.update({"startup_id": startup_id, "opportunity_id": opportunity_id, "snapshot_id": snapshot_id})
        generated.append((base_feature, outcome))
    feature_rows = [pair[0] for pair in generated]
    outcome_rows = [pair[1] for pair in generated]
    rng.shuffle(feature_rows)
    rng.shuffle(outcome_rows)
    return feature_rows, outcome_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", default="screening_handover/training_features.jsonl")
    parser.add_argument("--outcomes", default="screening_handover/training_outcomes.jsonl")
    parser.add_argument("--feature-output", default="screening_handover/demo_training_features.jsonl")
    parser.add_argument("--outcome-output", default="screening_handover/demo_training_outcomes.jsonl")
    parser.add_argument("--rows", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    features, outcomes = build(read_jsonl(Path(args.features)), read_jsonl(Path(args.outcomes)), args.rows, args.seed)
    write_jsonl(Path(args.feature_output), features)
    write_jsonl(Path(args.outcome_output), outcomes)
    print(json.dumps({"features": args.feature_output, "outcomes": args.outcome_output, "records": len(features), "seed": args.seed}))


if __name__ == "__main__":
    main()
