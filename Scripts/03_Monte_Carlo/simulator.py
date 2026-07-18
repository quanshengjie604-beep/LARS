"""The per-path dynamic model: a competing-risks, semi-Markov stage-jump process.

Given one theta (DistributionParams, H2) and the thesis terms (DeterministicTerms),
this walks N companies through their life path and returns one MOIC per path.

Time advances in *jumps* at financing events, not continuously. On every path,
three competing forces act at each stage the company reaches: it may die (M3),
have a liquidity event / exit (per-stage hazard + M5/M6), or raise the next round
(M1) and press on. This is model variant (A): an exit can occur at ANY stage, not
only after reaching the terminal stage.

    for each stage the company reaches (current stage, then each raised-into stage):
        M2  advance time    t += Weibull                         (else check death)
        M3  failure check   t > t_fail                           (-> FAILED)
            horizon check   t >= horizon                         (-> CENSORED)
        --  exit hazard     Bernoulli(p_exit) among survivors    (-> EXITED)
        M5  exit type       Categorical(ipo/acq/secondary)         given an exit
        M6  exit value      heavy-tailed (anchored or absolute), capped by tam
            payoff          MOIC = ownership * exit_val / check_size
        M1  raise gate      p ~ Beta ; raised ~ Bernoulli(p)     (else -> STALLED)
        --  dilute + grow   ownership *= (1-dil) ; valuation *= (1+LogNormal[M4])
    -- terminal stage: one last exit hazard; non-exiters stay private (CENSORED)

The per-stage exit hazard `p_exit` is either supplied on theta
(`stage_exit_prob`) or derived from M5's `p_none` so that surviving all K exit
opportunities without a liquidity event has probability p_none:

        (1 - p_exit) ** K == p_none   ->   p_exit = 1 - p_none ** (1/K)

That keeps M5 fully used: `p_none` sets how often exits happen, the other three
set the exit-type mix.

Everything is vectorized over the N paths; the only Python loop is over the
handful of stages, gated by a boolean `active` mask.
"""

from __future__ import annotations

import numpy as np

from contracts import (
    DeterministicTerms,
    DistributionParams,
    PathStatus,
    SimControl,
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

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    rng = np.random.default_rng(control.random_seed)
    n = control.n_iterations
    horizon = terms.horizon_years

    # ------------------------------------------------------------------
    # Initialize paths
    # ------------------------------------------------------------------
    # Path state (spec 5, the dynamic state carried along each path).
    ownership = np.full(n, terms.target_ownership, dtype=float)
    valuation = np.full(n, terms.entry_valuation, dtype=float)
    t = np.zeros(n, dtype=float)
    status = np.zeros(n, dtype=np.int8)          # PathStatus.ALIVE
    active = np.ones(n, dtype=bool)

    moic = np.zeros(n, dtype=float)
    exit_type = np.full(n, EXIT_UNRESOLVED, dtype=np.int8)
    t_exit = np.full(n, np.nan, dtype=float)
    t_end = np.full(n, np.nan, dtype=float)      # termination time for EVERY path

    # Stage trajectory: which stage a path reached, and when it entered each one.
    # stage index 0 = current_stage; +1 per successful raise. Column j holds the
    # time the path entered stage j (nan if never reached); column 0 is t = 0.
    n_transitions = len(params.round_progression)
    stage_reached = np.zeros(n, dtype=np.int16)
    stage_entry_t = np.full((n, n_transitions + 1), np.nan, dtype=float)
    stage_entry_t[:, 0] = 0.0

    # ------------------------------------------------------------------
    # Spin up runway (characteristic lifespan of a startup)
    # ------------------------------------------------------------------
    # M3 -- one global failure clock per path ("characteristic lifespan").
    # MVP simplification: not reset when a round is raised.
    t_fail = draw_weibull(rng, params.fail_k, params.fail_lambda, n)

    # Per-stage exit hazard. Number of exit opportunities K =
    # the current stage plus one per raised-into stage.
    k_opportunities = len(params.round_progression) + 1
    if params.stage_exit_prob is not None:
        p_exit = float(params.stage_exit_prob)
    else:
        p_exit = 1.0 - params.p_none ** (1.0 / k_opportunities)

    # Exit-type mix given that an exit occurs: M5 over {ipo, acq, secondary}.
    real_type_probs = np.array(
        [params.p_ipo, params.p_acq, params.p_secondary], dtype=float
    )
    can_exit = p_exit > 0.0 and real_type_probs.sum() > 0.0

    def _kill_by_time() -> None:
        """Apply the failure and horizon deadlines to currently-active paths."""
        nonlocal active
        failed = active & (t > t_fail)
        status[failed] = PathStatus.FAILED
        t_end[failed] = t_fail[failed]          # died when cash ran out
        active &= ~failed
        censored = active & (t >= horizon)
        status[censored] = PathStatus.CENSORED
        t_end[censored] = horizon               # still alive at the horizon
        active &= ~censored

    def _resolve_exits() -> None:
        """Per-stage liquidity event: exiters realize an MOIC and leave the pool."""
        nonlocal active
        if not can_exit or not active.any():
            return
        exiting = active & (rng.random(n) < p_exit)
        # Draw type/value for all n (cheap, keeps RNG stream position stable),
        # apply only to exiters.
        types = sample_categorical(rng, real_type_probs, n)  # 0 ipo,1 acq,2 secondary
        heavy = draw_lognormal_pareto(
            rng, params.exit_mu, params.exit_sigma, params.tail_xm, params.tail_alpha, n
        )
        if not exiting.any():
            return
        if control.exit_value_mode == "anchored":
            exit_val = valuation * heavy      # M4 growth drives the payoff
        elif control.exit_value_mode == "absolute":
            exit_val = heavy                  # spec-literal: M6 is USD directly
        else:
            raise ValueError(f"unknown exit_value_mode: {control.exit_value_mode!r}")
        if terms.tam is not None:
            exit_val = np.minimum(exit_val, terms.tam)   # capped by market size
        path_moic = ownership * exit_val / terms.check_size

        exit_type[exiting] = types[exiting]
        t_exit[exiting] = t[exiting]
        t_end[exiting] = t[exiting]
        moic[exiting] = path_moic[exiting]
        status[exiting] = PathStatus.EXITED
        active &= ~exiting

    # ------------------------------------------------------------------
    # Walk the stages: at each stage the company may die, exit, or raise on.
    # ------------------------------------------------------------------
    for j, tr in enumerate(params.round_progression):
        # Time spent at the current stage before the next event.
        dt = draw_weibull(rng, params.t_next_k, params.t_next_lambda, n)
        t = np.where(active, t + dt, t)
        _kill_by_time()

        # Liquidity event at the current stage (uses the valuation reached so far).
        _resolve_exits()

        # M1 raise gate for the survivors that did not exit.
        raised, _ = draw_beta_bernoulli(rng, tr.series_alpha, tr.series_beta, n)
        stalled = active & ~raised
        status[stalled] = PathStatus.STALLED
        t_end[stalled] = t[stalled]             # could not raise the next round
        active &= raised

        # Record the stage the raisers just entered (for stage-segmented plots).
        stage_reached[active] = j + 1
        stage_entry_t[active, j + 1] = t[active]

        # Dilution + M4 growth apply to those who raised (now at the next stage).
        ownership = np.where(active, ownership * (1.0 - terms.dilution_per_round), ownership)
        g = draw_lognormal(rng, params.growth_mu, params.growth_sigma, n)
        valuation = np.where(active, valuation * (1.0 + g), valuation)

    # ------------------------------------------------------------------
    # Terminal stage: one last exit chance; survivors stay private.
    # ------------------------------------------------------------------
    dt = draw_weibull(rng, params.t_next_k, params.t_next_lambda, n)
    t = np.where(active, t + dt, t)
    _kill_by_time()
    _resolve_exits()
    survivors = active.copy()
    status[survivors] = PathStatus.CENSORED   # reached terminal, never got liquidity
    t_end[survivors] = t[survivors]
    active[:] = False

    # ------------------------------------------------------------------
    # Optional carrying value for companies that never realized a liquidity event.
    # ------------------------------------------------------------------
    if control.horizon_carry_value:
        carry = status == PathStatus.CENSORED
        carry_val = valuation.copy()
        if terms.tam is not None:
            carry_val = np.minimum(carry_val, terms.tam)
        carry_moic = ownership * carry_val / terms.check_size
        moic = np.where(carry, carry_moic, moic)

    return {
        "moic": moic,
        "t_exit": t_exit,
        "t_end": t_end,
        "exit_type": exit_type,
        "status": status,
        "ownership": ownership,
        "valuation": valuation,
        "stage_reached": stage_reached,
        "stage_entry_t": stage_entry_t,
    }
