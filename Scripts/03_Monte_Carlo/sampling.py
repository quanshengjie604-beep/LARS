"""Vectorized distribution draws for the Monte Carlo layer.

Every function draws a full (N,) array in one call -- the simulator never loops
over individual paths. All randomness flows through a single numpy Generator so
a memo is reproducible from its seed (spec 5.2).
"""

from __future__ import annotations

import numpy as np


def draw_weibull(rng: np.random.Generator, k: float, lam: float, size: int) -> np.ndarray:
    """Weibull(k, lambda): k = shape, lambda = scale (years). Used for M2 / M3.

    numpy's Generator.weibull draws with scale 1, so we multiply by lambda.
    k < 1 front-loads the hazard (early death); k > 1 means risk rises with age.
    """
    return lam * rng.weibull(k, size)


def draw_beta_bernoulli(
    rng: np.random.Generator, alpha: float, beta: float, size: int
) -> tuple[np.ndarray, np.ndarray]:
    """M1 raise gate, done the honest way.

    Draw the success probability p ~ Beta(alpha, beta) *per path*, then a
    Bernoulli(p) outcome. This propagates the Beta's confidence (low alpha+beta
    -> wide p -> wider return distribution) instead of collapsing to the mean,
    which the spec (3.2) explicitly warns against.

    Returns (raised_bool, p_drawn).
    """
    p = rng.beta(alpha, beta, size)
    raised = rng.random(size) < p
    return raised, p


def draw_lognormal(rng: np.random.Generator, mu: float, sigma: float, size: int) -> np.ndarray:
    """LogNormal(mu, sigma) in log-space; median = exp(mu). Used for M4 growth."""
    return rng.lognormal(mu, sigma, size)


def draw_pareto(rng: np.random.Generator, x_m: float, alpha: float, size: int) -> np.ndarray:
    """Classic Pareto: minimum x_m, tail index alpha (all draws >= x_m).

    numpy's Generator.pareto is the Lomax (Pareto II) form, so we shift+scale:
    (pareto(alpha) + 1) * x_m recovers the classic Pareto.
    """
    return (rng.pareto(alpha, size) + 1.0) * x_m


def draw_lognormal_pareto(
    rng: np.random.Generator,
    mu: float,
    sigma: float,
    x_m: float,
    alpha: float,
    size: int,
) -> np.ndarray:
    """M6 -- LogNormal body spliced to a Pareto tail above x_m.

    The "normal" exit outcomes come from the LogNormal; any draw that lands
    above the threshold x_m is replaced by a Pareto draw, fattening the right
    tail so the rare 100x outcome is not undervalued (spec 3.2). A pure
    LogNormal under-tails and systematically undervalues the outliers that
    actually drive VC returns.

    This is a simple splice (adequate for the MVP); a fully continuous body/tail
    mixture is a later refinement.
    """
    out = rng.lognormal(mu, sigma, size)
    tail_mask = out > x_m
    n_tail = int(tail_mask.sum())
    if n_tail:
        out[tail_mask] = draw_pareto(rng, x_m, alpha, n_tail)
    return out


def sample_categorical(
    rng: np.random.Generator, probs: np.ndarray, size: int
) -> np.ndarray:
    """M5 -- vectorized categorical sample returning integer codes in [0, len(probs))."""
    cum = np.cumsum(probs / probs.sum())
    u = rng.random(size)
    return np.searchsorted(cum, u).astype(np.int8)
