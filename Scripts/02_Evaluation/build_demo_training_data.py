"""Build deterministic synthetic outcome labels for the six-model demo only."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def build(features, outcomes):
    by_snapshot = {row["snapshot_id"]: row for row in features}
    result = []
    for index, source in enumerate(outcomes):
        row = copy.deepcopy(source)
        feature = by_snapshot[row["snapshot_id"]]
        score = float((feature.get("screening") or {}).get("founder_axis") or 50.0)
        exit_type = (row.get("exit") or {}).get("exit_type", "no_exit_observed")
        progressed = exit_type != "no_exit_observed" or score >= 70
        row["next_round"].update({
            "next_round_observed": progressed,
            "next_round_stage": "series_a" if progressed else None,
            "next_round_date": None,
            "next_round_size_usd": (2_000_000 + score * 100_000) if progressed else None,
            "progressed_within_24_months": progressed,
        })
        growth_factor = round(max(0.35, math.exp((score - 50) / 90) * (1.25 if progressed else 0.8)), 4)
        observed = feature["observation_date"]
        start_year = int(observed[:4])
        row["growth"] = {
            "revenue_start_date": f"{start_year}-01-01", "revenue_start_usd": 1_000_000.0,
            "revenue_end_date": f"{start_year + 1}-01-01", "revenue_end_usd": 1_000_000.0 * growth_factor,
            "growth_measurement_type": "annualized_revenue", "annual_growth_factor": growth_factor,
        }
        row["m4_included"] = True
        if exit_type != "no_exit_observed":
            multiplier = {"acquisition": 1.0, "ipo": 4.0, "secondary": 2.0}.get(exit_type, 1.0)
            value = round((25_000_000 + score * 2_000_000) * multiplier, -3)
            row["exit"].update({"exit_valuation_usd": value, "exit_transaction_value_usd": value,
                "exit_value_type": "transaction_value" if exit_type == "acquisition" else "ipo_market_cap",
                "exit_value_disclosed": True, "exit_value_verified": False})
        row["demo_synthetic"] = True
        row["synthetic_fields"] = ["next_round.*", "growth.*"] + (["exit.exit_valuation_usd", "exit.exit_transaction_value_usd"] if exit_type != "no_exit_observed" else [])
        row["source_ids"] = list(dict.fromkeys((row.get("source_ids") or []) + ["source_demo_synthetic_v1"]))
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", default="screening_handover/training_features.jsonl")
    parser.add_argument("--outcomes", default="screening_handover/training_outcomes.jsonl")
    parser.add_argument("--output", default="screening_handover/demo_training_outcomes.jsonl")
    args = parser.parse_args()
    rows = build(read_jsonl(Path(args.features)), read_jsonl(Path(args.outcomes)))
    write_jsonl(Path(args.output), rows)
    print(json.dumps({"output": args.output, "records": len(rows), "demo_synthetic": True}))


if __name__ == "__main__":
    main()
