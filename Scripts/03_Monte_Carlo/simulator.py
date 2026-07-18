"""The per-path dynamic model: a competing-risks, semi-Markov stage-jump process.

Given one theta (DistributionParams, H2) and the thesis terms (DeterministicTerms),
this walks N companies through their life path and returns one MOIC per path.

Time advances in *jumps* at financing events, not continuously. On every path,
two clocks race -- a progression clock (M2) and a failure clock (M3) -- while a
raise gate (M1) decides whether the next round even happens:

    for each stage transition still active:
        M1  raise gate      p ~ Beta ; raised ~ Bernoulli(p)   (else -> STALLED)
        M2  advance time    t += Weibull                        (else check death)
        M3  failure check   t > t_fail                          (-> FAILED)
            horizon check   t >= horizon                        (-> CENSORED)
        --  dilute          ownership *= (1 - dilution)
        M4  grow            valuation *= (1 + LogNormal)
    -- time to exit         t += Weibull (M2), re-check death/horizon
    M5  exit type           Categorical                         (none -> CENSORED)
    M6  exit value          heavy-tailed (anchored or absolute), capped by tam
        payoff              MOIC = ownership * exit_val / check_size

Everything is vectorized over the N paths; the only Python loop is over the
handful of stage transitions, gated by a boolean `active` mask.
"""

from __future__ import annotations

import numpy as np

from contracts import (
    DeterministicTerms,
    DistributionParams,
    PathStatus,
    SimControl,
    EXIT_NONE,
    EXIT_UNRESOLVED,
)
from sampling import (
    draw_beta_bernoulli,
    draw_lognormal,
    draw_lognormal_pareto,
    draw_weibull,
    sample_categorical,
)


def simulate_paths(
    params: DistributionParams,
    terms: DeterministicTerms,
    control: SimControl,
) -> dict[str, np.ndarray]:
    """Run N Monte Carlo paths. Returns per-path arrays (all shape (N,)).

    Keys: moic, t_exit, exit_type, status, ownership, valuation.
    """

    # ------------------------------------------------------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------------------------------------------------------
    rng = np.random.default_rng(control.random_seed)    # Draw random seed
    n = control.n_iterations
    horizon = terms.horizon_years

    # ------------------------------------------------------------------------------------------------------------------
    # Set up initial state for all paths
    # ------------------------------------------------------------------------------------------------------------------
    # path state (spec 5, the dynamic state carried along each path)
    ownership = np.full(n, terms.target_ownership, dtype=float)
    valuation = np.full(n, terms.entry_valuation, dtype=float)
    t = np.zeros(n, dtype=float)
    status = np.zeros(n, dtype=np.int8)          # PathStatus.ALIVE
    active = np.ones(n, dtype=bool)

    # ------------------------------------------------------------------------------------------------------------------
    # Draw the characteristic lifespan of the startup
    # ------------------------------------------------------------------------------------------------------------------
    # M3 -- one global failure clock per path, drawn at t=0
    # MVP simplification: not reset when a round is raised.
    t_fail = draw_weibull(rng, params.fail_k, params.fail_lambda, n)

    def _kill_by_time() -> None:
        """Apply the failure and horizon deadlines to currently-active paths."""
        nonlocal active
        failed = active & (t > t_fail)
        status[failed] = PathStatus.FAILED
        active &= ~failed
        censored = active & (t >= horizon)
        status[censored] = PathStatus.CENSORED
        active &= ~censored

    # ------------------------------------------------------------------------------------------------------------------
    # Walk the funding rounds
    # ------------------------------------------------------------------------------------------------------------------
    for tr in params.round_progression:

        # --------------------------------------------------------------------------------------------------------------
        # Check which startups will not raise another round
        # --------------------------------------------------------------------------------------------------------------
        # M1: raise gate for paths still in play.
        raised, _ = draw_beta_bernoulli(rng, tr.series_alpha, tr.series_beta, n)
        stalled = active & ~raised
        status[stalled] = PathStatus.STALLED
        active &= raised

        # --------------------------------------------------------------------------------------------------------------
        # Advance all active startups (all startups, which raised another round) in time
        # --------------------------------------------------------------------------------------------------------------
        # M2: time advances only for paths that raised.
        dt = draw_weibull(rng, params.t_next_k, params.t_next_lambda, n)
        t = np.where(active, t + dt, t)
        _kill_by_time()

        # --------------------------------------------------------------------------------------------------------------
        # With time, startups loose ownership and increase their valuation
        # --------------------------------------------------------------------------------------------------------------
        # Dilution + M4 growth apply to survivors of this round.
        ownership = np.where(active, ownership * (1.0 - terms.dilution_per_round), ownership)
        g = draw_lognormal(rng, params.growth_mu, params.growth_sigma, n)
        valuation = np.where(active, valuation * (1.0 + g), valuation)

    # --- time-to-exit for companies that completed the progression ---------
    # M2 is "time to next round / exit": the final leg is the time to liquidity.
    dt_exit = draw_weibull(rng, params.t_next_k, params.t_next_lambda, n)
    t = np.where(active, t + dt_exit, t)
    _kill_by_time()

    # --- resolve the exit (M5, M6) -----------------------------------------
    exit_type = np.full(n, EXIT_UNRESOLVED, dtype=np.int8)
    moic = np.zeros(n, dtype=float)

    if active.any():
        probs = np.array(
            [params.p_ipo, params.p_acq, params.p_secondary, params.p_none], dtype=float
        )
        drawn = sample_categorical(rng, probs, n)
        exit_type = np.where(active, drawn, EXIT_UNRESOLVED).astype(np.int8)

        real_exit = active & (exit_type != EXIT_NONE)
        no_liquidity = active & (exit_type == EXIT_NONE)
        status[real_exit] = PathStatus.EXITED
        status[no_liquidity] = PathStatus.CENSORED  # stayed private, no liquidity

        # M6 exit value.
        heavy = draw_lognormal_pareto(
            rng, params.exit_mu, params.exit_sigma, params.tail_xm, params.tail_alpha, n
        )
        if control.exit_value_mode == "anchored":
            # Exit value = accumulated private valuation * heavy-tailed multiple,
            # so the growth path (M4) actually drives the payoff.
            exit_val = valuation * heavy
        elif control.exit_value_mode == "absolute":
            # Spec-literal: M6 is the exit value in USD, independent of the path.
            exit_val = heavy
        else:
            raise ValueError(f"unknown exit_value_mode: {control.exit_value_mode!r}")

        if terms.tam is not None:
            exit_val = np.minimum(exit_val, terms.tam)  # capped by market size

        proceeds = ownership * exit_val
        path_moic = proceeds / terms.check_size
        moic = np.where(real_exit, path_moic, moic)

    # --- optional carrying value for horizon survivors ---------------------
    if control.horizon_carry_value:
        carry = status == PathStatus.CENSORED
        carry_val = valuation.copy()
        if terms.tam is not None:
            carry_val = np.minimum(carry_val, terms.tam)
        carry_moic = ownership * carry_val / terms.check_size
        moic = np.where(carry, carry_moic, moic)

    # t is only meaningful as an exit time for realized exits.
    t_exit = np.where(status == PathStatus.EXITED, t, np.nan)

    return {
        "moic": moic,
        "t_exit": t_exit,
        "exit_type": exit_type,
        "status": status,
        "ownership": ownership,
        "valuation": valuation,
    }
