"""Run the full MC pipeline over a predictions JSONL file -> SimulationResults.

    python Scripts/03_Monte_Carlo/run_pipeline.py
    python Scripts/03_Monte_Carlo/run_pipeline.py \
        --input artifacts/demo_founder_predictions.jsonl \
        --output artifacts/demo_mc_results.jsonl \
        --iterations 10000 --seed 42 --limit 50

For each prediction record this:
    1. adapts it to a DistributionParams theta   (theta_from_prediction)
    2. pairs it with DeterministicTerms           (default_terms / thesis engine)
    3. simulates N life paths                      (simulate_paths)
    4. aggregates them into an H3 SimulationResult (summarize)
and writes one SimulationResult JSON object per line to the output file.

Because this package directory starts with a digit ("03_Monte_Carlo"), modules
import each other by plain name and rely on the script's own directory being on
sys.path (automatic when Python runs the file directly). Run from the repo root.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from contracts import SimControl
from simulator import simulate_paths
from aggregate import summarize
from theta_from_prediction import (
    default_terms,
    load_predictions,
    theta_from_prediction,
)

# Repo root = three levels up from this file (Scripts/03_Monte_Carlo/<file>).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_INPUT = os.path.join(_REPO_ROOT, "artifacts", "scrape_demo_predictions.jsonl")
_DEFAULT_OUTPUT = os.path.join(_REPO_ROOT, "artifacts", "demo_mc_results.jsonl")


def run(
    input_path: str,
    output_path: str,
    n_iterations: int,
    seed: int,
    limit: int | None,
) -> int:
    """Execute the pipeline; return the number of results written."""
    # M6 is emitted in USD, so exit values are absolute (not a growth multiple).
    control = SimControl(
        n_iterations=n_iterations,
        random_seed=seed,
        exit_value_mode="absolute",
    )
    terms = default_terms()

    written = 0
    t0 = time.perf_counter()
    with open(output_path, "w", encoding="utf-8") as out:
        for i, record in enumerate(load_predictions(input_path)):
            if limit is not None and i >= limit:
                break
            params = theta_from_prediction(record)
            paths = simulate_paths(params, terms, control)
            result = summarize(paths, params, terms)

            row = dict(result.__dict__)
            row["company_name"] = record.get("company_name")
            row["startup_id"] = record.get("startup_id")
            out.write(json.dumps(row) + "\n")
            written += 1

            if written % 100 == 0:
                print(f"  ... {written} opportunities simulated", file=sys.stderr)

    dt = time.perf_counter() - t0
    print(
        f"Wrote {written} SimulationResults -> {output_path} "
        f"({n_iterations:,} paths each, {dt:.1f}s)"
    )
    return written


def _peek(output_path: str, n: int = 3) -> None:
    """Print the first n result rows so a run is self-verifying."""
    print(f"\nFirst {n} results:")
    with open(output_path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i >= n:
                break
            r = json.loads(line)
            print(
                f"  {r.get('company_name') or r['opportunity_id']:<24} "
                f"E[MOIC]={r['expected_moic']:8.2f}  "
                f"med={r['median_moic']:7.2f}  "
                f"P(>1x)={r['p_gt_1x']:.2f}  P(>10x)={r['p_gt_10x']:.2f}  "
                f"P(loss)={r['p_total_loss']:.2f}  conf={r['confidence_flag']}"
            )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", default=_DEFAULT_INPUT, help="predictions JSONL path")
    ap.add_argument("--output", default=_DEFAULT_OUTPUT, help="results JSONL path")
    ap.add_argument("--iterations", type=int, default=10_000, help="MC paths per opportunity")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=None, help="cap number of opportunities")
    args = ap.parse_args()

    print(f"Input : {args.input}")
    print(f"Output: {args.output}")
    written = run(args.input, args.output, args.iterations, args.seed, args.limit)
    if written:
        _peek(args.output)


if __name__ == "__main__":
    main()
