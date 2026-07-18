"""Deterministic §5 scoring formulas.

Every function returns a `Scored` (value, confidence, formula-note, coverage) so
the caller can attach the formula-as-evidence and propagate confidence. Any null
input is dropped from its weighted average and the remaining weights renormalise
(plan §5): a missing input widens uncertainty, it does not score as zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import WEIGHTS
from .util import clamp, confidence_from_coverage, mean_present, nlog, sat, weighted_present


@dataclass
class Scored:
    value: Optional[float]
    confidence: str = "low"
    note: str = ""
    coverage: float = 0.0


@dataclass
class DerivedSignals:
    """The §5.6 0-1 signals, computed from raw source data."""

    open_source_activity_score: Scored = field(default_factory=lambda: Scored(None))
    proprietary_score: Scored = field(default_factory=lambda: Scored(None))
    public_footprint_score: Scored = field(default_factory=lambda: Scored(None))
    competitor_density: Scored = field(default_factory=lambda: Scored(None))
    funding_climate_index: Scored = field(default_factory=lambda: Scored(None))
    soft_skill_estimate: Scored = field(default_factory=lambda: Scored(None))
    network_centrality: Scored = field(default_factory=lambda: Scored(None))


# --- §5.6 derived signals -------------------------------------------------
def open_source_activity(gh) -> Scored:
    if not gh or not gh.available:
        return Scored(None, "low", "no GitHub footprint located")
    commits_proxy = None
    if gh.recent_push_days is not None:
        # freshness proxy in place of commit count: pushed today -> 1, ~200d -> 0
        commits_proxy = clamp(1 - gh.recent_push_days / 200.0, 0.0, 1.0)
    parts = [
        nlog(gh.stars_total, 1, 4) if gh.stars_total is not None else None,
        nlog(gh.forks_total, 0, 3) if gh.forks_total is not None else None,
        sat(gh.repo_count, 20) if gh.repo_count is not None else None,
        commits_proxy,
    ]
    val = mean_present(parts)
    cov = sum(1 for p in parts if p is not None) / len(parts)
    return Scored(val, confidence_from_coverage(cov), "mean(nlog stars, nlog forks, sat repos, freshness)", cov)


def public_footprint(gh, hn, ph, twitter_followers=None, paper_count=None) -> Scored:
    parts = [
        nlog(twitter_followers, 2, 6) if twitter_followers is not None else None,
        nlog(gh.followers, 1, 4) if (gh and gh.followers is not None) else None,
        sat(paper_count, 10) if paper_count is not None else None,
        sat((hn.mentions or 0) + (ph.launches or 0), 20) if (hn and hn.available) or (ph and ph.available) else None,
    ]
    val = mean_present(parts)
    cov = sum(1 for p in parts if p is not None) / len(parts)
    # cold-start footprint is low-confidence by mandate (§4.6)
    conf = "low" if cov < 0.5 else "medium"
    return Scored(val, conf, "mean(nlog twitter, nlog gh_followers, sat papers, sat hn+ph mentions)", cov)


def proprietary(patent_count, oss: Scored, defensibility_rubric=None) -> Scored:
    pairs = [
        (sat(patent_count, 5) if patent_count is not None else None, 0.4),
        (oss.value, 0.3),
        (defensibility_rubric, 0.3),
    ]
    val, cov, _, _ = weighted_present(pairs)
    return Scored(val, confidence_from_coverage(cov), "0.4*sat(patents,5)+0.3*oss+0.3*rubric (renorm)", cov)


# --- §5.1 Founder Score (persistent) --------------------------------------
def founder_score_persistent(
    *, prior_exits=None, prior_startups=None, industry_years=None, technical_flag=None,
    pedigree=None, footprint: Scored = None, network: Scored = None,
) -> Scored:
    w = WEIGHTS.founder_score
    pairs = [
        (sat(prior_exits, 2) if prior_exits is not None else None, w["exits"]),
        (sat(prior_startups, 3) if prior_startups is not None else None, w["startups"]),
        (sat(industry_years, 10) if industry_years is not None else None, w["exp"]),
        ((1.0 if technical_flag else 0.0) if technical_flag is not None else None, w["tech"]),
        (pedigree, w["pedigree"]),
        (footprint.value if footprint else None, w["footprint"]),
        (network.value if network else None, w["network"]),
    ]
    val, cov, n, _ = weighted_present(pairs)
    if val is None:
        return Scored(None, "low", "no founder inputs present", 0.0)
    return Scored(100 * val, confidence_from_coverage(cov), "§5.1 weighted-present founder score", cov)


# --- §5.2 Founder axis (per-opportunity) ----------------------------------
def founder_axis(*, fs: Scored, fmf=None, team_size=None, soft=None, single_founder=None) -> Scored:
    w = WEIGHTS.founder_axis
    team_adeq = sat(team_size, 8) if team_size is not None else None
    pairs = [
        (fs.value / 100.0 if fs and fs.value is not None else None, w["fs"]),
        (fmf, w["fmf"]),
        (team_adeq, w["team_adeq"]),
        (soft, w["soft"]),
    ]
    val, cov, _, _ = weighted_present(pairs)
    if val is None:
        return Scored(None, "low", "no founder-axis inputs present", 0.0)
    score = 100 * val
    if single_founder:
        score -= WEIGHTS.single_founder_penalty
    return Scored(clamp(score, 0, 100), confidence_from_coverage(cov), "§5.2 founder axis (renorm, single-founder penalty)", cov)


# --- §5.3 Market axis -----------------------------------------------------
def market_axis(*, tam_usd=None, competitor_density: Scored = None, climate: Scored = None):
    w = WEIGHTS.market_axis
    tam_norm = nlog(tam_usd, 8, 12) if tam_usd is not None else None
    crowd = (1 - competitor_density.value) if (competitor_density and competitor_density.value is not None) else None
    clim = climate.value if climate else None
    pairs = [(tam_norm, w["tam"]), (crowd, w["crowd"]), (clim, w["climate"])]
    val, cov, _, _ = weighted_present(pairs)
    if val is None:
        return "unknown", Scored(None, "low", "no market inputs present", 0.0)
    m = 100 * val
    if m >= WEIGHTS.market_bull_threshold:
        enum = "bull"
    elif m <= WEIGHTS.market_bear_threshold:
        enum = "bear"
    else:
        enum = "neutral"
    return enum, Scored(m, confidence_from_coverage(cov), "§5.3 continuous m -> {bull,neutral,bear}", cov)


# --- §5.4 Idea-vs-Market --------------------------------------------------
def idea_vs_market(*, proprietary_s: Scored = None, competitor_density: Scored = None,
                   revenue_growth=None, churn=None, founder_axis_s: Scored = None) -> Scored:
    w = WEIGHTS.idea_vs_market
    if revenue_growth is not None or churn is not None:
        g = sat(revenue_growth, 1.0) if revenue_growth is not None else None
        ch = (1 - clamp(churn / 0.4, 0, 1)) if churn is not None else None
        traction = mean_present([g, ch])
    else:
        traction = None
    crowd = (1 - competitor_density.value) if (competitor_density and competitor_density.value is not None) else None
    pivot = founder_axis_s.value / 100.0 if (founder_axis_s and founder_axis_s.value is not None) else None
    pairs = [
        (proprietary_s.value if proprietary_s else None, w["proprietary"]),
        (crowd, w["crowd"]),
        (traction, w["traction"]),
        (pivot, w["pivot"]),
    ]
    val, cov, _, _ = weighted_present(pairs)
    if val is None:
        return Scored(None, "low", "no idea-vs-market inputs present", 0.0)
    return Scored(100 * val, confidence_from_coverage(cov), "§5.4 idea-vs-market (renorm)", cov)


# --- §5.5 Trends ----------------------------------------------------------
_ENUM_LEVEL = {"bear": 0.0, "neutral": 50.0, "bull": 100.0,
               "declining": 0.0, "stable": 50.0, "improving": 100.0}


def trend(v_now, v_prev) -> str:
    """Compare current axis value to the previous snapshot for the same startup."""
    if v_now is None or v_prev is None:
        return "unknown"
    a = _ENUM_LEVEL.get(v_now, v_now) if isinstance(v_now, str) else v_now
    b = _ENUM_LEVEL.get(v_prev, v_prev) if isinstance(v_prev, str) else v_prev
    if a is None or b is None:
        return "unknown"
    delta = (a - b) / 100.0
    if delta > WEIGHTS.trend_delta:
        return "improving"
    if delta < -WEIGHTS.trend_delta:
        return "declining"
    return "stable"
