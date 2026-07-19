from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .io import read_jsonl, write_jsonl

GROUPS = ("screening", "traction", "financials", "team", "market", "cold_start")
TECH_TERMS = {"ai", "ml", "software", "engineering", "engineer", "robotics", "data", "computer", "developer", "technical", "security", "infrastructure", "hardware"}


def _clamp(value: float, low=0.0, high=1.0):
    return max(low, min(high, value))


def _mean(values: list[Any]) -> float | None:
    values = [float(v) for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"https://{url}").hostname or ""
    return host.removeprefix("www.").lower() or None


def _id(prefix: str, key: str) -> str:
    return f"{prefix}_{hashlib.sha1(f'{prefix}::{key}'.encode()).hexdigest()[:12]}"


def _first(rows: list[dict[str, Any]], *path: str) -> Any:
    for row in rows:
        value: Any = row
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        if value is not None:
            return value
    return None


def _normalize_geography(value: str | None) -> str:
    aliases = {"United States of America": "united_states", "United States": "united_states", "USA": "united_states",
               "United Kingdom": "united_kingdom", "UK": "united_kingdom"}
    value = aliases.get(value or "", value or "unknown")
    return "_".join(value.strip().lower().replace(",", " ").split())


def _rank_number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value).replace(",", ""))
    return float(match.group()) if match else None

def _founder_estimate(row: dict[str, Any]) -> dict[str, Any]:
    ff = row.get("founder_features") or {}
    skills = (row.get("skills") or {}).get("items") or []
    skill_names = " ".join(str(x.get("name", "")).lower() for x in skills)
    skill_count = int((row.get("skills") or {}).get("documented_skill_count") or len(skills))
    papers = (row.get("research") or {}).get("papers") or []
    projects = (row.get("hackathons") or {}).get("projects") or []
    profiles = row.get("profile_urls") or []
    sources = row.get("source_ids") or []
    signals = row.get("entrepreneurial_signals") or []
    affiliations = row.get("affiliations") or []
    os_data = row.get("open_source") or {}
    followers = float(os_data.get("followers") or 0)
    repos = float(os_data.get("public_repos") or len(os_data.get("owned_repositories") or []))
    technical = ff.get("technical_founder_flag")
    if technical is None:
        technical = bool(papers or projects or repos or any(term in skill_names for term in TECH_TERMS))
    footprint = ff.get("public_footprint_score")
    if footprint is None:
        footprint = _clamp(0.08 * len(sources) + 0.12 * len(profiles) + 0.06 * skill_count + 0.08 * len(papers) + 0.05 * len(signals))
    network = ff.get("network_centrality")
    if network is None:
        coauthors = len((row.get("research") or {}).get("coauthors") or [])
        network = _clamp(0.1 + 0.08 * len(affiliations) + 0.06 * len(profiles) + 0.025 * coauthors + 0.035 * math.log1p(followers))
    career = row.get("career_history") or {}
    exits = ff.get("founder_prior_exits")
    if exits is None:
        exits = career.get("verified_prior_exit_count")
    exits = float(exits or 0)
    startups = ff.get("founder_prior_startups")
    if startups is None:
        startups = max(0, len(row.get("founded_companies") or []) - 1)
    rank = _rank_number((row.get("education") or {}).get("best_qs_world_rank"))
    education = 0.0 if rank is None else _clamp((500 - rank) / 500)
    axis = ff.get("founder_axis")
    if axis is None:
        axis = _clamp(0.30 + 0.16 * float(technical) + 0.16 * footprint + 0.12 * network + 0.10 * min(exits, 2) + 0.08 * education + 0.025 * min(skill_count, 5)) * 100
    persistent = ff.get("founder_score_persistent")
    if persistent is None:
        persistent = _clamp(axis / 100 * 0.8 + min(exits, 2) * 0.1) * 100
    open_source = ff.get("open_source_activity_score")
    if open_source is None:
        open_source = _clamp(0.12 * math.log1p(repos) + 0.04 * math.log1p(followers) + 0.12 * float(technical))
    proprietary = ff.get("proprietary_score")
    if proprietary is None:
        proprietary = _clamp(0.25 + 0.3 * float(technical) + 0.08 * len(papers) + 0.06 * len(projects))
    soft_skill = ff.get("soft_skill_estimate")
    if soft_skill is None:
        role = str(row.get("founder_role") or row.get("current_role") or "").lower()
        soft_skill = _clamp(0.45 + 0.12 * ("ceo" in role) + 0.06 * ("co-founder" in role) + 0.03 * len(signals))
    return {"axis": axis, "persistent": persistent, "technical": technical, "footprint": footprint,
            "network": network, "exits": exits, "startups": float(startups), "open_source": open_source,
            "proprietary": proprietary, "soft_skill": soft_skill}


def founder_candidates_to_requests(candidates: list[dict[str, Any]], observation_date: str | None = None) -> list[dict[str, Any]]:
    observed = observation_date or date.today().isoformat()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        key = _domain(row.get("company_url")) or (row.get("company_name") or "").strip().casefold()
        if key:
            grouped[key].append(row)
    sectors = {}
    for key, rows in grouped.items():
        companies = _first(rows, "founded_companies")
        company = companies[0] if isinstance(companies, list) and companies else {}
        sectors[key] = company.get("subindustry") or company.get("industry") or "unknown"
    sector_counts = Counter(sectors.values())
    max_sector_count = max(sector_counts.values(), default=1)
    output = []
    for key, rows in sorted(grouped.items()):
        estimates = [_founder_estimate(r) for r in rows]
        sid, stage = _id("startup", key), "seed"
        sector = sectors[key]
        founder_axis = _mean([x["axis"] for x in estimates]) or 50.0
        persistent = _mean([x["persistent"] for x in estimates]) or founder_axis
        density = sector_counts[sector] / max_sector_count
        technical_share = _mean([float(x["technical"]) for x in estimates]) or 0.0
        idea_match = _clamp(0.35 + 0.004 * founder_axis + 0.18 * technical_share - 0.12 * density) * 100
        record = {
            "startup_id": sid, "opportunity_id": _id("opportunity", f"{sid}::{observed}"),
            "snapshot_id": f"{sid}_{observed}_{stage}", "observation_date": observed, "data_cutoff_date": observed,
            "company_name": rows[0].get("company_name") or key,
            "candidate_ids": [r["candidate_id"] for r in rows if r.get("candidate_id")],
            "screening": {"founder_score_persistent": round(persistent, 4), "founder_axis": round(founder_axis, 4),
                "founder_axis_trend": "stable", "market_axis": "neutral", "market_axis_trend": "stable",
                "idea_vs_market": round(idea_match, 4), "idea_vs_market_trend": "stable"},
            "traction": {"arr_usd": None, "revenue_growth_rate_yoy": None, "customer_count": None, "churn_rate_annual": None},
            "financials": {"burn_rate_usd_monthly": None, "runway_months": None, "current_stage": stage,
                "last_round_size_usd": None, "last_round_date": None, "total_funding_to_date_usd": None, "cash_balance_usd": None},
            "team": {"founder_prior_exits": round(sum(x["exits"] for x in estimates), 4),
                "founder_prior_startups": round(sum(x["startups"] for x in estimates), 4), "single_founder_flag": len(rows) == 1,
                "team_size": None, "technical_founder_flag": any(x["technical"] for x in estimates),
                "founder_industry_experience_years": None, "proprietary_score": round(_mean([x["proprietary"] for x in estimates]) or 0, 4),
                "patent_count": None, "open_source_activity_score": round(_mean([x["open_source"] for x in estimates]) or 0, 4)},
            "market": {"tam_usd": None, "competitor_density": round(density, 4), "sector": sector,
                "geography": _normalize_geography(_first(rows, "geography")), "funding_climate_index": 0.65},
            "cold_start": {"public_footprint_score": round(_mean([x["footprint"] for x in estimates]) or 0, 4),
                "network_centrality": round(_mean([x["network"] for x in estimates]) or 0, 4),
                "soft_skill_estimate": round(_mean([x["soft_skill"] for x in estimates]) or 0, 4)},
            "source_ids": sorted({s for r in rows for s in (r.get("source_ids") or [])}),
            "estimated_fields": ["screening.*", "team.founder_prior_exits", "team.founder_prior_startups",
                "team.technical_founder_flag", "team.proprietary_score", "team.open_source_activity_score",
                "market.competitor_density", "market.funding_climate_index", "cold_start.*"],
            "missing_fields": [],
            "contradicted_fields": sorted({f for r in rows for f in (r.get("contradicted_fields") or [])}),
        }
        record["missing_fields"] = [f"{g}.{k}" for g in GROUPS for k, v in record[g].items() if v is None]
        output.append(record)
    return output


def prepare_founder_requests(input_path: str | Path, output_path: str | Path, observation_date: str | None = None) -> int:
    rows = founder_candidates_to_requests(read_jsonl(input_path), observation_date)
    write_jsonl(output_path, rows)
    return len(rows)
