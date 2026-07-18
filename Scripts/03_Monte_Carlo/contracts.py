"""Handover contracts for the Monte Carlo layer.

These mirror the pipeline spec (Information/vc-brain-mc-pipeline.md):

    H2  DistributionParams   Evaluation -> Simulation   (the theta the six
                                                          NGBoost-style models emit)
    --  DeterministicTerms   Thesis Engine side-input   (converts a path to MOIC)
    H3  SimulationResult     Simulation -> Output       (decision-ready aggregates)

Pinning them as dataclasses decouples MC development from the (unbuilt)
evaluation layer: the simulator only ever sees a DistributionParams object,
so it can be developed and tested against hand-written / synthetic theta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class PathStatus(IntEnum):
    """Terminal state of a single simulated company path."""
    ALIVE = 0        # transient only; never recorded
    FAILED = 1       # failure clock (M3) fired -> ran out of cash
    STALLED = 2      # could not raise the next round (M1 gate) -> no liquidity
    CENSORED = 3     # alive at horizon, or exit type == none -> no realized exit
    EXITED = 4       # realized a liquidity event (M5 != none)


# Exit-type codes (order matters: used for categorical sampling of M5).
EXIT_IPO, EXIT_ACQ, EXIT_SECONDARY, EXIT_NONE = 0, 1, 2, 3
EXIT_UNRESOLVED = -1  # path died / stalled before an exit was ever sampled


@dataclass
class RoundTransition:
    """One stage transition with its M1 (Beta) raise-probability params."""
    from_stage: str
    to_stage: str
    series_alpha: float   # Beta alpha  (pseudo-count of "raised")
    series_beta: float    # Beta beta   (pseudo-count of "did not raise")


@dataclass
class DistributionParams:
    """H2 -- everything the evaluation layer emits for one opportunity (theta).

    One instance fully parameterizes the per-path model. Values are the
    distribution *parameters*, never point estimates.
    """
    opportunity_id: str
    horizon_years: float

    # M1 -- P(progress to next round), one Beta per stage transition.
    round_progression: list[RoundTransition]

    # M2 -- time to next round / exit, Weibull(k, lambda) in years.
    t_next_k: float
    t_next_lambda: float

    # M3 -- failure timing, Weibull(k, lambda) in years.
    fail_k: float
    fail_lambda: float

    # M4 -- revenue/ARR growth per round, LogNormal(mu, sigma) in log-space.
    growth_mu: float
    growth_sigma: float

    # M5 -- exit type, Categorical over (ipo, acq, secondary, none).
    p_ipo: float
    p_acq: float
    p_secondary: float
    p_none: float

    # M6 -- exit valuation: LogNormal body + Pareto tail.
    #   In "anchored" mode these describe an exit *multiple* on the path's
    #   accumulated valuation; in "absolute" mode they describe the exit
    #   value in USD directly (see SimControl.exit_value_mode).
    exit_mu: float
    exit_sigma: float
    tail_xm: float        # threshold at which the Pareto tail takes over
    tail_alpha: float     # tail index (~1.5-2.0 for VC power-law outcomes)

    # Per-stage exit hazard: P(a liquidity event occurs at a stage the company
    # reaches, before it attempts the next round). Enables exits at any stage,
    # not just the terminal one. If None, it is derived from M5's p_none so that
    # the compounded no-exit probability across all K opportunities equals p_none
    # (see simulator). The exit *type* given an exit is drawn from M5 restricted
    # to {ipo, acq, secondary}.
    stage_exit_prob: float | None = None

    confidence_flag: str = "medium"   # high / medium / low (from attribution)


@dataclass
class DeterministicTerms:
    """Thesis-Engine side-input D. Not predicted -- set by thesis / round structure.

    Converts a sampled outcome path into an MOIC. Missing cap-table terms fall
    back to defaults here and MUST be surfaced in SimulationResult.assumptions_flagged
    rather than silently used.
    """
    check_size: float
    target_ownership: float
    entry_valuation: float
    dilution_per_round: float = 0.20     # standard schedule when cap table undisclosed
    liquidation_pref: float | None = None  # ignored in base case if None (+flag)
    horizon_years: int = 10
    # tam is technically a FeatureVector field (H1); it is passed through to the
    # simulator because it caps the exit-valuation ceiling. None -> no cap.
    tam: float | None = None


@dataclass
class SimControl:
    """Simulation control parameters (spec 5.2) plus model-choice switches."""
    n_iterations: int = 100_000
    random_seed: int = 42
    period_length: float = 1.0           # growth compounding period, years (reserved)
    correlation_mode: str = "independent"  # independent | copula (MVP: independent)

    # --- model-defining switches (see the design decisions doc) ---
    # "anchored": exit_val = accumulated_valuation * heavy-tailed multiple (M4 has teeth)
    # "absolute": exit_val ~ M6 directly, independent of the growth path (spec-literal)
    exit_value_mode: str = "anchored"
    # Alive-at-horizon companies: False -> realize 0; True -> carry last mark.
    horizon_carry_value: bool = False


@dataclass
class SimulationResult:
    """H3 -- decision-ready aggregates over the N simulated paths."""
    opportunity_id: str
    expected_moic: float
    median_moic: float
    p_total_loss: float
    p_gt_1x: float
    p_gt_10x: float
    moic_p5: float
    moic_p95: float
    median_exit_years: float
    exit_type_mix: dict[str, float]
    confidence_flag: str
    assumptions_flagged: list[str] = field(default_factory=list)
    n_iterations: int = 0
