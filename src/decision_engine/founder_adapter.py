from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .io import read_jsonl, write_jsonl

GROUPS = ("screening", "traction", "financials", "team", "market", "cold_start")


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


def founder_candidates_to_requests(candidates: list[dict[str, Any]], observation_date: str | None = None) -> list[dict[str, Any]]:
    observed = observation_date or date.today().isoformat()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        key = _domain(row.get("company_url")) or (row.get("company_name") or "").strip().casefold()
        if key:
            grouped[key].append(row)
    output = []
    for key, rows in sorted(grouped.items()):
        ff = [r.get("founder_features") or {} for r in rows]
        sid, stage = _id("startup", key), "unknown"
        companies = _first(rows, "founded_companies")
        company = companies[0] if isinstance(companies, list) and companies else {}
        record = {
            "startup_id": sid, "opportunity_id": _id("opportunity", f"{sid}::{observed}"),
            "snapshot_id": f"{sid}_{observed}_{stage}", "observation_date": observed, "data_cutoff_date": observed,
            "company_name": rows[0].get("company_name") or key,
            "candidate_ids": [r["candidate_id"] for r in rows if r.get("candidate_id")],
            "screening": {
                "founder_score_persistent": _mean([x.get("founder_score_persistent") for x in ff]),
                "founder_axis": _mean([x.get("founder_axis") for x in ff]),
                "founder_axis_trend": _first(rows, "founder_features", "founder_axis_trend"),
                "market_axis": None, "market_axis_trend": None, "idea_vs_market": None, "idea_vs_market_trend": None},
            "traction": {"arr_usd": None, "revenue_growth_rate_yoy": None, "customer_count": None, "churn_rate_annual": None},
            "financials": {"burn_rate_usd_monthly": None, "runway_months": None, "current_stage": stage,
                "last_round_size_usd": None, "last_round_date": None, "total_funding_to_date_usd": None, "cash_balance_usd": None},
            "team": {
                "founder_prior_exits": _mean([x.get("founder_prior_exits") for x in ff]),
                "founder_prior_startups": _mean([x.get("founder_prior_startups") for x in ff]),
                "single_founder_flag": len(rows) == 1, "team_size": None,
                "technical_founder_flag": _first(rows, "founder_features", "technical_founder_flag"),
                "founder_industry_experience_years": _mean([x.get("founder_industry_experience_years") for x in ff]),
                "proprietary_score": _mean([x.get("proprietary_score") for x in ff]),
                "patent_count": _mean([x.get("patent_count") for x in ff]),
                "open_source_activity_score": _mean([x.get("open_source_activity_score") for x in ff])},
            "market": {"tam_usd": None, "competitor_density": None,
                "sector": company.get("subindustry") or company.get("industry"),
                "geography": _first(rows, "geography"), "funding_climate_index": None},
            "cold_start": {
                "public_footprint_score": _mean([x.get("public_footprint_score") for x in ff]),
                "network_centrality": _mean([x.get("network_centrality") for x in ff]),
                "soft_skill_estimate": _mean([x.get("soft_skill_estimate") for x in ff])},
            "source_ids": sorted({s for r in rows for s in (r.get("source_ids") or [])}),
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
