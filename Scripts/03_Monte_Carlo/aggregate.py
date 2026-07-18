"""Aggregate the N per-path outcomes into the H3 SimulationResult contract."""

from __future__ import annotations

import numpy as np

from contracts import (
    DeterministicTerms,
    DistributionParams,
    PathStatus,
    SimulationResult,
    EXIT_IPO,
    EXIT_ACQ,
    EXIT_SECONDARY,
)


def summarize(
    paths: dict[str, np.ndarray],
    params: DistributionParams,
    terms: DeterministicTerms,
) -> SimulationResult:
    """Turn per-path arrays (from simulate_paths) into decision-ready aggregates."""
    moic = paths["moic"]
    t_exit = paths["t_exit"]
    exit_type = paths["exit_type"]
    status = paths["status"]
    n = moic.size

    exited = status == PathStatus.EXITED
    exit_times = t_exit[exited]
    median_exit = float(np.median(exit_times)) if exit_times.size else float("nan")

    # Exit-type mix over all paths: realized IPO / acq / secondary shares, with
    # "none" absorbing every non-exit (failed, stalled, no-liquidity, censored).
    ipo = float(np.mean(exit_type == EXIT_IPO))
    acq = float(np.mean(exit_type == EXIT_ACQ))
    sec = float(np.mean(exit_type == EXIT_SECONDARY))
    mix = {"ipo": ipo, "acq": acq, "secondary": sec, "none": 1.0 - (ipo + acq + sec)}

    assumptions: list[str] = []
    if terms.tam is None:
        assumptions.append("TAM not supplied -- exit valuation left uncapped")
    if terms.liquidation_pref is None:
        assumptions.append("Liquidation preference not disclosed -- ignored in base case")
    assumptions.append(
        f"Cap table not disclosed -- assumed {terms.dilution_per_round:.0%}/round dilution"
    )

    return SimulationResult(
        opportunity_id=params.opportunity_id,
        expected_moic=float(np.mean(moic)),
        median_moic=float(np.median(moic)),
        p_total_loss=float(np.mean(moic == 0.0)),
        p_gt_1x=float(np.mean(moic > 1.0)),
        p_gt_10x=float(np.mean(moic >= 10.0)),
        moic_p5=float(np.percentile(moic, 5)),
        moic_p95=float(np.percentile(moic, 95)),
        median_exit_years=median_exit,
        exit_type_mix=mix,
        confidence_flag=params.confidence_flag,
        assumptions_flagged=assumptions,
        n_iterations=n,
    )
