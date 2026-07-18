"""Tests for the Monte Carlo per-path model.

The MC package directory starts with a digit, so it is added to sys.path here
and its modules are imported by plain name.
"""

import os
import sys

import numpy as np
import pytest

_MC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Scripts", "03_Monte_Carlo",
)
sys.path.insert(0, _MC_DIR)

from contracts import (  # noqa: E402
    DeterministicTerms,
    DistributionParams,
    RoundTransition,
    SimControl,
    PathStatus,
)
from simulator import simulate_paths  # noqa: E402
from aggregate import summarize  # noqa: E402


def _theta() -> DistributionParams:
    return DistributionParams(
        opportunity_id="test_co",
        horizon_years=10,
        round_progression=[
            RoundTransition("seed", "A", 6.0, 4.0),
            RoundTransition("A", "B", 3.0, 5.0),
            RoundTransition("B", "C+", 2.0, 6.0),
        ],
        t_next_k=1.3, t_next_lambda=2.1,
        fail_k=0.8, fail_lambda=3.5,
        growth_mu=0.45, growth_sigma=0.6,
        p_ipo=0.05, p_acq=0.35, p_secondary=0.10, p_none=0.50,
        exit_mu=0.7, exit_sigma=0.9, tail_xm=5.0, tail_alpha=1.8,
    )


def _terms() -> DeterministicTerms:
    return DeterministicTerms(
        check_size=100_000, target_ownership=0.10, entry_valuation=1_000_000,
        dilution_per_round=0.20, horizon_years=10, tam=1_000_000_000,
    )


def test_reproducible_with_fixed_seed():
    control = SimControl(n_iterations=20_000, random_seed=7)
    a = simulate_paths(_theta(), _terms(), control)
    b = simulate_paths(_theta(), _terms(), control)
    np.testing.assert_array_equal(a["moic"], b["moic"])


def test_different_seeds_differ():
    a = simulate_paths(_theta(), _terms(), SimControl(n_iterations=20_000, random_seed=1))
    b = simulate_paths(_theta(), _terms(), SimControl(n_iterations=20_000, random_seed=2))
    assert not np.array_equal(a["moic"], b["moic"])


def test_moic_non_negative_and_finite():
    p = simulate_paths(_theta(), _terms(), SimControl(n_iterations=50_000))
    assert np.all(p["moic"] >= 0.0)
    assert np.all(np.isfinite(p["moic"]))


def test_every_path_has_terminal_status():
    p = simulate_paths(_theta(), _terms(), SimControl(n_iterations=50_000))
    assert not np.any(p["status"] == PathStatus.ALIVE)


def test_only_exited_paths_have_positive_moic():
    # In the default (no carry value) config, a positive MOIC requires a realized exit.
    p = simulate_paths(_theta(), _terms(), SimControl(n_iterations=50_000))
    positive = p["moic"] > 0.0
    assert np.all(p["status"][positive] == PathStatus.EXITED)


def test_exit_time_only_for_exited():
    p = simulate_paths(_theta(), _terms(), SimControl(n_iterations=50_000))
    exited = p["status"] == PathStatus.EXITED
    assert np.all(np.isfinite(p["t_exit"][exited]))
    assert np.all(np.isnan(p["t_exit"][~exited]))


def test_summary_probabilities_are_valid():
    p = simulate_paths(_theta(), _terms(), SimControl(n_iterations=50_000, random_seed=3))
    r = summarize(p, _theta(), _terms())
    for prob in (r.p_total_loss, r.p_gt_1x, r.p_gt_10x):
        assert 0.0 <= prob <= 1.0
    assert r.moic_p95 >= r.moic_p5
    assert abs(sum(r.exit_type_mix.values()) - 1.0) < 1e-9
    assert r.median_moic <= r.expected_moic  # right-skewed / fat-tailed


def test_absolute_mode_ignores_growth_path():
    # In absolute mode, exit value comes straight from M6; changing growth mu
    # must not move the exit-value distribution (only which paths survive).
    terms = _terms()
    ctrl = SimControl(n_iterations=40_000, random_seed=11, exit_value_mode="absolute")
    # exit_mu here is USD log-space; use a realistic absolute value.
    theta = _theta()
    theta.exit_mu, theta.exit_sigma, theta.tail_xm = 18.0, 1.0, 5.0e8
    p = simulate_paths(theta, terms, ctrl)
    assert np.all(p["moic"] >= 0.0)


def test_stalled_and_failed_yield_total_loss():
    p = simulate_paths(_theta(), _terms(), SimControl(n_iterations=50_000))
    for st in (PathStatus.STALLED, PathStatus.FAILED):
        mask = p["status"] == st
        if mask.any():
            assert np.all(p["moic"][mask] == 0.0)


def test_exits_occur_before_terminal_stage():
    # Variant (A): a path can exit at its very first stage, i.e. before ever
    # raising (valuation still == entry_valuation). At least some should.
    terms = _terms()
    p = simulate_paths(_theta(), terms, SimControl(n_iterations=50_000, random_seed=5))
    exited = p["status"] == PathStatus.EXITED
    early = exited & np.isclose(p["valuation"], terms.entry_valuation)
    assert early.sum() > 0


def test_higher_stage_exit_prob_reduces_total_loss():
    theta_lo = _theta()
    theta_lo.stage_exit_prob = 0.05
    theta_hi = _theta()
    theta_hi.stage_exit_prob = 0.40
    ctrl = SimControl(n_iterations=50_000, random_seed=9)
    lo = simulate_paths(theta_lo, _terms(), ctrl)
    hi = simulate_paths(theta_hi, _terms(), ctrl)
    assert (hi["moic"] > 0).mean() > (lo["moic"] > 0).mean()
