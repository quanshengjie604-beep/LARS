"""H2 adapter: prediction records (m1..m6) -> DistributionParams (theta).

The evaluation layer emits one JSON object per opportunity (see
``artifacts/demo_founder_predictions.jsonl``) with six named sub-models:

    m1  categorical{True, False}                 P(raise the next round)
    m2  survival_curve {day -> P(survive)}       time to next round / progression
    m3  survival_curve {day -> P(survive)}       company survival / failure timing
    m4  lognormal{log_mu, log_sigma}             per-round growth multiple
    m5  categorical{acquisition, ipo,            exit type
                    secondary, no_exit_observed}
    m6  lognormal{log_mu, log_sigma}             exit valuation (USD)

The simulator, however, consumes a single ``DistributionParams`` whose fields are
specific *parameterizations* (Beta per stage, Weibull(k, lambda), a 4-way exit
categorical, a LogNormal+Pareto tail). This module is the connecting element:
it maps each prediction record onto that contract, model-for-model, resolving the
four shape mismatches:

    #1  m1 P(raise)            -> a Beta(alpha, beta) per stage transition
    #2  m2 / m3 survival curve -> Weibull(k, lambda) fit to the curve points
    #3  m5 4-way categorical   -> {p_ipo, p_acq, p_secondary, p_none}
    #4  m6 LogNormal (USD)     -> LogNormal body + Pareto tail, run in
                                  ``exit_value_mode="absolute"``

``DeterministicTerms`` (check size, ownership, entry valuation, TAM) is NOT part
of a prediction -- it comes from the thesis engine. ``default_terms()`` supplies
a documented placeholder; every assumption it makes is surfaced by the
aggregator in ``SimulationResult.assumptions_flagged``.
"""

from __future__ import annotations

import json
from typing import Iterator

import numpy as np

from contracts import (
    DeterministicTerms,
    DistributionParams,
    RoundTransition,
)

# --------------------------------------------------------------------------
# Tunable adapter defaults (documented so they can be overridden per thesis).
# --------------------------------------------------------------------------
DAYS_PER_YEAR = 365.25

# #1 -- concentration (total pseudo-count) used to turn a single P(raise) into a
# Beta(alpha, beta). Higher = more confident / narrower p. The file gives one
# raise probability, so the same Beta is applied to every stage transition.
DEFAULT_BETA_CONCENTRATION = 10.0

# Default financing ladder used to expand the single m1 probability into a
# per-stage progression. Three transitions (four stages).
DEFAULT_STAGES = ("seed", "series_a", "series_b", "series_c")

# #2 -- fallbacks when a survival curve carries no usable event information
# (e.g. every point == 1.0, as in the placeholder demo data). k = 1.0 is a
# memoryless (exponential) hazard. The two survival models live on very
# different time scales, so they get different no-signal fallbacks:
#   m2 (time-to-next-round): a realistic financing cadence, so progression still
#      happens within the horizon instead of stalling out to censorship.
#   m3 (company lifespan): a long lambda, so "no failure signal" reads as
#      long-lived rather than manufacturing early deaths.
DEFAULT_WEIBULL_K = 1.0
DEFAULT_TNEXT_LAMBDA = 1.75    # years between rounds when the curve has no signal
DEFAULT_FAIL_LAMBDA = 100.0    # years to failure when the curve has no signal

# #4 -- the file provides no Pareto tail, so we splice one on above a high
# multiple of the LogNormal median. With a small sigma the threshold is
# essentially never crossed (no artificial tail); with a wide sigma it fattens
# only the extreme right tail that drives VC returns.
DEFAULT_TAIL_FACTOR = 10.0   # tail takes over above TAIL_FACTOR * median exit value
DEFAULT_TAIL_ALPHA = 1.8     # tail index (~1.5-2.0 for VC power-law outcomes)

DEFAULT_HORIZON_YEARS = 10

_PROB_EPS = 1e-6  # clamp probabilities off the {0, 1} boundary for valid Betas


# --------------------------------------------------------------------------
# #1  m1 categorical P(raise) -> Beta ladder
# --------------------------------------------------------------------------
def _beta_ladder(
    p_raise: float,
    stages: tuple[str, ...],
    concentration: float = DEFAULT_BETA_CONCENTRATION,
) -> list[RoundTransition]:
    """Expand one raise probability into a Beta(alpha, beta) per stage transition.

    alpha = p * kappa (pseudo-count of "raised"), beta = (1 - p) * kappa. The
    same p is used for every transition because the model emits a single number.
    """
    p = float(np.clip(p_raise, _PROB_EPS, 1.0 - _PROB_EPS))
    alpha = p * concentration
    beta = (1.0 - p) * concentration
    return [
        RoundTransition(
            from_stage=stages[i],
            to_stage=stages[i + 1],
            series_alpha=alpha,
            series_beta=beta,
        )
        for i in range(len(stages) - 1)
    ]


# --------------------------------------------------------------------------
# #2  survival_curve -> Weibull(k, lambda)
# --------------------------------------------------------------------------
def _weibull_from_survival(
    survival_by_day: dict[str, float],
    *,
    default_k: float = DEFAULT_WEIBULL_K,
    default_lambda: float = DEFAULT_FAIL_LAMBDA,
) -> tuple[float, float]:
    """Fit Weibull(k, lambda) in years to survival points S(t) = exp(-(t/lam)^k).

    Linearizes as ln(-ln S) = k*ln(t) - k*ln(lam) and least-squares fits the
    slope/intercept. Falls back gracefully when < 2 usable points exist
    (points with 0 < S < 1); an all-1.0 curve carries no event signal and
    returns the long-lambda default.
    """
    pts: list[tuple[float, float]] = []
    for day_str, s in survival_by_day.items():
        try:
            t = float(day_str) / DAYS_PER_YEAR
            s = float(s)
        except (TypeError, ValueError):
            continue
        if t > 0.0 and 0.0 < s < 1.0:
            pts.append((t, s))

    if len(pts) >= 2:
        x = np.log([t for t, _ in pts])
        y = np.log(-np.log([s for _, s in pts]))
        k, intercept = np.polyfit(x, y, 1)  # slope = k, intercept = -k*ln(lam)
        lam = float(np.exp(-intercept / k)) if k != 0.0 else default_lambda
    elif len(pts) == 1:
        # Single point -> assume exponential (k = 1): S = exp(-t/lam).
        t, s = pts[0]
        k = default_k
        lam = -t / np.log(s)
    else:
        k, lam = default_k, default_lambda

    # Guard against degenerate / non-physical fits.
    if not np.isfinite(k) or k <= 0.0:
        k = default_k
    if not np.isfinite(lam) or lam <= 0.0:
        lam = default_lambda
    return float(k), float(lam)


# --------------------------------------------------------------------------
# #3  m5 4-way -> {p_ipo, p_acq, p_secondary, p_none}
# --------------------------------------------------------------------------
def _exit_mix(m5_params: dict[str, float]) -> dict[str, float]:
    """Map {acquisition, ipo, secondary, no_exit_observed} to the contract's 4-way exit mix.

    The M5 exit-type model now emits a secondary class alongside ipo /
    acquisition / no_exit_observed; it is read straight through here. ``secondary``
    is defaulted to 0.0 so older 3-way records (no secondary key) still map
    cleanly. Probabilities are renormalized defensively.
    """
    p_ipo = float(m5_params.get("ipo", 0.0))
    p_acq = float(m5_params.get("acquisition", 0.0))
    p_secondary = float(m5_params.get("secondary", 0.0))
    p_none = float(m5_params.get("no_exit_observed", 0.0))

    total = p_ipo + p_acq + p_secondary + p_none
    if total > 0.0:
        p_ipo, p_acq, p_secondary, p_none = (
            p_ipo / total,
            p_acq / total,
            p_secondary / total,
            p_none / total,
        )
    return {
        "p_ipo": p_ipo,
        "p_acq": p_acq,
        "p_secondary": p_secondary,
        "p_none": p_none,
    }


# --------------------------------------------------------------------------
# The connecting element: one prediction record -> one theta
# --------------------------------------------------------------------------
def theta_from_prediction(
    record: dict,
    *,
    stages: tuple[str, ...] = DEFAULT_STAGES,
    horizon_years: int = DEFAULT_HORIZON_YEARS,
    beta_concentration: float = DEFAULT_BETA_CONCENTRATION,
    tail_factor: float = DEFAULT_TAIL_FACTOR,
    tail_alpha: float = DEFAULT_TAIL_ALPHA,
) -> DistributionParams:
    """Convert one prediction record (a parsed JSONL line) into DistributionParams.

    Assumes the six-model schema in the demo file. Missing sub-models fall back
    to non-informative defaults rather than raising, so a partially-populated
    record still yields a runnable theta.
    """
    models = record.get("models", {})

    m1 = models.get("m1", {}).get("parameters", {})
    m2 = models.get("m2", {}).get("parameters", {}).get("survival_probability_by_day", {})
    m3 = models.get("m3", {}).get("parameters", {}).get("survival_probability_by_day", {})
    m4 = models.get("m4", {}).get("parameters", {})
    m5 = models.get("m5", {}).get("parameters", {})
    m6 = models.get("m6", {}).get("parameters", {})

    # #1 raise gate -> Beta ladder
    p_raise = float(m1.get("True", 0.5))
    round_progression = _beta_ladder(p_raise, stages, beta_concentration)

    # #2 progression + failure survival curves -> Weibull (distinct no-signal scales)
    t_next_k, t_next_lambda = _weibull_from_survival(m2, default_lambda=DEFAULT_TNEXT_LAMBDA)
    fail_k, fail_lambda = _weibull_from_survival(m3, default_lambda=DEFAULT_FAIL_LAMBDA)

    # m4 growth: LogNormal params map directly (log-space).
    growth_mu = float(m4.get("log_mu", 0.0))
    growth_sigma = float(m4.get("log_sigma", 1e-3))

    # #3 exit type mix
    mix = _exit_mix(m5)

    # #4 exit valuation (USD): LogNormal body + a spliced Pareto tail.
    exit_mu = float(m6.get("log_mu", 0.0))
    exit_sigma = float(m6.get("log_sigma", 1e-3))
    exit_median = float(np.exp(exit_mu))
    tail_xm = exit_median * tail_factor  # tail only above TAIL_FACTOR x median

    return DistributionParams(
        opportunity_id=record.get("opportunity_id", record.get("startup_id", "unknown")),
        horizon_years=horizon_years,
        round_progression=round_progression,
        t_next_k=t_next_k,
        t_next_lambda=t_next_lambda,
        fail_k=fail_k,
        fail_lambda=fail_lambda,
        growth_mu=growth_mu,
        growth_sigma=growth_sigma,
        p_ipo=mix["p_ipo"],
        p_acq=mix["p_acq"],
        p_secondary=mix["p_secondary"],
        p_none=mix["p_none"],
        exit_mu=exit_mu,
        exit_sigma=exit_sigma,
        tail_xm=tail_xm,
        tail_alpha=tail_alpha,
        confidence_flag=record.get("model_confidence", {}).get("level", "medium"),
    )


def default_terms(horizon_years: int = DEFAULT_HORIZON_YEARS) -> DeterministicTerms:
    """Placeholder thesis terms (H1/D side-input, not part of a prediction).

    These are seed-stage defaults; override per opportunity when cap-table terms
    are known. Whatever is assumed here is echoed into
    SimulationResult.assumptions_flagged by the aggregator.
    """
    return DeterministicTerms(
        check_size=100_000,
        target_ownership=0.10,
        entry_valuation=1_000_000,
        dilution_per_round=0.20,
        horizon_years=horizon_years,
        tam=1_000_000_000,
    )


def load_predictions(path: str) -> Iterator[dict]:
    """Yield one parsed record per non-blank line of a predictions JSONL file."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
