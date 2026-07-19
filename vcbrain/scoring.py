"""Deterministic plan.md section 5 scoring formulas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .util import clamp, mean_present, nlog, sat, weighted_present


@dataclass(slots=True)
class Score:
    value: float | None
    coverage: float
    confidence: str
    formula: str
    inputs: dict[str, Any]


def _confidence(coverage: float, *, ceiling: str = "high") -> str:
    if coverage >= 0.75 and ceiling == "high":
        return "high"
    if coverage >= 0.4 and ceiling in {"high", "medium"}:
        return "medium"
    return "low"


def open_source_activity(signals: Mapping[str, Any]) -> Score:
    stars = signals.get("github_stars_current")
    forks = signals.get("github_forks_current")
    components = {
        "stars": nlog(float(stars), 1, 4) if stars is not None else None,
        "forks": nlog(float(forks), 0, 3) if forks is not None else None,
        "contributors": None,
        "commits_last_90d": None,
    }
    value, coverage = weighted_present(
        [(components["stars"], 0.25), (components["forks"], 0.25), (None, 0.25), (None, 0.25)]
    )
    return Score(value, coverage, _confidence(coverage, ceiling="medium"), "plan-5.6-open-source-v1", components)


def public_footprint(signals: Mapping[str, Any], *, dated_mentions: int = 0) -> Score:
    hn_ph = dated_mentions or int(signals.get("hn_mention_count") or 0) + int(
        signals.get("producthunt_launch_count") or 0
    )
    components = {
        "twitter_followers": None,
        "github_followers": None,
        "paper_count": None,
        "hn_ph_mentions": sat(float(hn_ph), 20) if hn_ph else None,
    }
    value, coverage = weighted_present([(component, 0.25) for component in components.values()])
    return Score(value, coverage, "low", "plan-5.6-public-footprint-v1", components)


def proprietary(*, patent_count: int | None, oss: Score, defensibility: float | None = None) -> Score:
    inputs = {
        "patent_count": sat(float(patent_count), 5) if patent_count is not None else None,
        "open_source_activity": oss.value,
        "defensibility_rubric": defensibility,
    }
    value, coverage = weighted_present(
        [(inputs["patent_count"], 0.4), (inputs["open_source_activity"], 0.3), (defensibility, 0.3)]
    )
    # Upstream OSS coverage still matters; do not upgrade a one-input score.
    effective_coverage = coverage * (oss.coverage if oss.value is not None and patent_count is None else 1.0)
    return Score(value, effective_coverage, _confidence(effective_coverage, ceiling="medium"), "plan-5.6-proprietary-v1", inputs)


def founder_score(
    *,
    prior_exits: int | None,
    prior_startups: int | None,
    experience_years: float | None,
    technical: bool | None,
    pedigree: float | None,
    footprint: Score,
    network: float | None,
) -> Score:
    inputs = {
        "exits": sat(float(prior_exits), 2) if prior_exits is not None else None,
        "startups": sat(float(prior_startups), 3) if prior_startups is not None else None,
        "experience": sat(float(experience_years), 10) if experience_years is not None else None,
        "technical": float(technical) if technical is not None else None,
        "pedigree": pedigree,
        "footprint": footprint.value,
        "network": network,
    }
    weights = {"exits": .25, "startups": .10, "experience": .15, "technical": .10,
               "pedigree": .15, "footprint": .15, "network": .10}
    value, coverage = weighted_present((inputs[key], weight) for key, weight in weights.items())
    if value is not None:
        value *= 100
    # Propagate deliberately low-confidence public-footprint/network inputs.
    ceiling = "medium" if any(inputs[key] is not None for key in ("exits", "experience", "technical")) else "low"
    return Score(value, coverage, _confidence(coverage, ceiling=ceiling), "plan-5.1-founder-score-v1", inputs)


def founder_axis(
    persistent: Score,
    *,
    founder_market_fit: float | None,
    team_size: int | None,
    soft_skill: float | None,
    single_founder: bool | None,
) -> Score:
    inputs = {
        "persistent": persistent.value / 100 if persistent.value is not None else None,
        "founder_market_fit": founder_market_fit,
        "team_adequacy": sat(float(team_size), 8) if team_size is not None else None,
        "soft_skill": soft_skill,
    }
    value, coverage = weighted_present(
        [(inputs["persistent"], .45), (founder_market_fit, .25), (inputs["team_adequacy"], .15), (soft_skill, .15)]
    )
    if value is not None:
        value = clamp(100 * value - (8 if single_founder is True else 0), 0, 100)
    effective = coverage * (persistent.coverage if persistent.value is not None else 1.0)
    return Score(value, effective, _confidence(effective, ceiling="medium"), "plan-5.2-founder-axis-v1", inputs)


def market_axis(
    *, tam_usd: float | None, competitor_density: float | None, funding_climate: float | None
) -> tuple[str, Score]:
    inputs = {
        "tam": nlog(tam_usd, 8, 12) if tam_usd is not None else None,
        "crowd": 1 - competitor_density if competitor_density is not None else None,
        "climate": funding_climate,
    }
    value, coverage = weighted_present([(inputs["tam"], .45), (inputs["crowd"], .30), (inputs["climate"], .25)])
    score = value * 100 if value is not None else None
    label = "unknown" if score is None else "bull" if score >= 66 else "bear" if score <= 40 else "neutral"
    return label, Score(score, coverage, _confidence(coverage, ceiling="medium"), "plan-5.3-market-axis-v1", inputs)


def idea_vs_market(
    *,
    proprietary_score: Score,
    competitor_density: float | None,
    revenue_growth: float | None,
    churn: float | None,
    founder_axis_score: Score,
) -> Score:
    growth_component = sat(revenue_growth, 1.0) if revenue_growth is not None else None
    churn_component = 1 - clamp(churn / .4) if churn is not None else None
    traction = mean_present([growth_component, churn_component])
    inputs = {
        "proprietary": proprietary_score.value,
        "crowd": 1 - competitor_density if competitor_density is not None else None,
        "traction": traction,
        "pivot": founder_axis_score.value / 100 if founder_axis_score.value is not None else None,
    }
    value, coverage = weighted_present(
        [(inputs["proprietary"], .35), (inputs["crowd"], .20), (traction, .25), (inputs["pivot"], .20)]
    )
    if value is not None:
        value *= 100
    upstream = min(
        [item for item in (proprietary_score.coverage, founder_axis_score.coverage) if item > 0] or [1.0]
    )
    effective = coverage * upstream
    return Score(value, effective, _confidence(effective, ceiling="medium"), "plan-5.4-idea-market-v1", inputs)


def trend(current: float | str | None, previous: float | str | None) -> str:
    if current is None or previous is None:
        return "unknown"
    enum = {"bear": 0.0, "neutral": 50.0, "bull": 100.0,
            "declining": 0.0, "stable": 50.0, "improving": 100.0}
    current_value = enum.get(current, float(current) if isinstance(current, (int, float)) else None)
    previous_value = enum.get(previous, float(previous) if isinstance(previous, (int, float)) else None)
    if current_value is None or previous_value is None:
        return "unknown"
    delta = (current_value - previous_value) / 100
    return "improving" if delta > .05 else "declining" if delta < -.05 else "stable"
