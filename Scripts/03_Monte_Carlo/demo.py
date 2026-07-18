"""Runnable demo: synthetic theta -> per-path model -> H3 aggregates.

    python Scripts/03_Monte_Carlo/demo.py

Because this package directory starts with a digit ("03_Monte_Carlo"), the
modules import each other by plain name and rely on the script's own directory
being on sys.path (which Python does automatically when you run the file
directly). Run from the repo root as shown above.
"""

from __future__ import annotations

import json
import os

from contracts import (
    DeterministicTerms,
    DistributionParams,
    RoundTransition,
    SimControl,
)
from aggregate import summarize
from simulator import simulate_paths
from Utilities.plot_paths import plot_paths, plot_moic_bars, _stage_names_from


def synthetic_theta() -> DistributionParams:
    """A plausible seed-stage opportunity (theta the evaluation layer would emit).

    NOTE: exit params are in *multiple* space because the demo runs in the
    default "anchored" mode (exit_val = valuation * multiple). tail_xm = 5.0
    means the Pareto tail takes over above a 5x exit multiple.
    """
    return DistributionParams(
        opportunity_id="startup_0421",
        horizon_years=10,
        round_progression=[
            RoundTransition("seed", "A", series_alpha=6.0, series_beta=4.0),
            RoundTransition("A", "B", series_alpha=3.0, series_beta=5.0),
            RoundTransition("B", "C+", series_alpha=2.0, series_beta=6.0),
        ],
        t_next_k=1.3, t_next_lambda=2.1,
        fail_k=0.8, fail_lambda=3.5,
        growth_mu=0.45, growth_sigma=0.6,
        p_ipo=0.05, p_acq=0.35, p_secondary=0.10, p_none=0.50,
        exit_mu=0.7, exit_sigma=0.9, tail_xm=5.0, tail_alpha=1.8,
        confidence_flag="medium",
    )


def thesis_terms() -> DeterministicTerms:
    return DeterministicTerms(
        check_size=100_000,
        target_ownership=0.10,
        entry_valuation=1_000_000,
        dilution_per_round=0.20,
        horizon_years=10,
        tam=1_000_000_000,
    )


def main() -> None:
    params = synthetic_theta()
    terms = thesis_terms()
    n_paths = 100_000
    control = SimControl(n_iterations=n_paths, random_seed=42)

    paths = simulate_paths(params, terms, control)
    result = summarize(paths, params, terms)

    print(json.dumps(result.__dict__, indent=2))

    # Validation plot of the simulated life paths.
    import matplotlib
    matplotlib.use("Agg")  # headless: just write the PNG

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    out = os.path.join(repo_root, "Output", "mc_paths.png")
    paths_fraction = n_paths * 0.05
    plot_paths(
        paths,
        horizon=terms.horizon_years,
        stage_names=_stage_names_from(params),
        n_show=int(paths_fraction),
        sort_by="moic",
        title=f"Monte Carlo company life paths ({params.opportunity_id})",
        save_path=out,
    )

    out_moic = os.path.join(repo_root, "Output", "mc_moic.png")
    plot_moic_bars(
        paths,
        n_show=n_paths,
        sort_by="moic",
        benchmark=30.0,
        title=f"Monte Carlo MOIC by path ({params.opportunity_id})",
        save_path=out_moic,
    )


if __name__ == "__main__":
    main()
