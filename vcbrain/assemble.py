"""Assemble handover records from enriched signals + §5 scores.

Two record kinds share one feature-snapshot builder:
  * inference request  — observation_date = today, cutoff = today (live signals)
  * training snapshot  — observation_date = YC batch date, cutoff = batch date
                         (point-in-time filtered; leakage-prone fields nulled)

Training outcome labels are derived from the YC directory `status`
(Active/Acquired/Public/Inactive), which is collected *after* the cutoff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from . import scoring
from .config import TODAY
from .context import MarketContext
from .evidence import EvidenceRegistry
from .sources import github as gh_src
from .sources import hackernews as hn_src
from .sources import llm as llm_src
from .sources import producthunt as ph_src
from .util import (days_between, opportunity_id, parse_date, snapshot_id)

_YC_STAGE_TO_ENUM = {"Early": "seed", "Growth": "series_b", "Public": "series_c_plus"}


@dataclass
class RawBundle:
    """All network I/O for one company, fetched once (this is what parallelises)."""

    github: gh_src.GithubRaw = field(default_factory=gh_src.GithubRaw)
    hn_hits: list = field(default_factory=list)
    ph_live: ph_src.PHSignals = field(default_factory=ph_src.PHSignals)
    llm: llm_src.LLMEstimates = field(default_factory=llm_src.LLMEstimates)
    errors: list = field(default_factory=list)


def fetch_raw(company, *, use_github=True, use_llm=True) -> RawBundle:
    """Perform all fetches for a company. Never raises; records errors instead."""
    b = RawBundle()
    if use_github:
        try:
            b.github = gh_src.fetch(company)
        except Exception as e:  # defensive: one source must not sink the record
            b.errors.append(f"github: {e!r}")
    try:
        b.hn_hits = hn_src.fetch(company)
    except Exception as e:
        b.errors.append(f"hn: {e!r}")
    try:
        b.ph_live = ph_src.enrich(company)
    except Exception as e:
        b.errors.append(f"producthunt: {e!r}")
    if use_llm:
        try:
            b.llm = llm_src.enrich(company)
        except Exception as e:
            b.errors.append(f"llm: {e!r}")
    return b


def _collect_null_paths(obj, prefix, out):
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            _collect_null_paths(v, path, out)
        elif v is None:
            out.append(path)


@dataclass
class SnapshotResult:
    features: dict
    axes: dict            # raw axis values for trend computation
    source_ids: list


def build_feature_snapshot(
    company,
    raw: RawBundle,
    ev: EvidenceRegistry,
    ctx: MarketContext,
    *,
    observation_date: str,
    cutoff_date: str,
    live: bool,
    prior_axes: Optional[dict] = None,
) -> SnapshotResult:
    """Build one nested feature snapshot dict (no ids/screening-trends yet)."""
    sid = company.startup_id
    cutoff = parse_date(cutoff_date)
    cutoff_year = cutoff.year if cutoff else None
    source_ids: list[str] = []

    def reg(**kw):
        source_ids.append(ev.add(startup_id=sid, **kw))

    # --- directory evidence (always) ---
    reg(channel="yc", source_type="internal_investment_record",
        document_name=f"YC directory: {company.name}",
        excerpt=(company.one_liner or company.description or company.name)[:300]
        + f" | batch={company.batch}, status={company.yc_status}, stage={company.yc_stage}.",
        source_uri=company.directory_url, document_date=company.founding_date,
        verification_status="document_verified", confidence="high")

    # --- source signals (point-in-time filtered) ---
    gh = gh_src.signals(raw.github, cutoff)
    hn = hn_src.signals(raw.hn_hits, cutoff)
    ph = raw.ph_live  # PH aggregate signal (launch-dated; treated as footprint proxy)
    for sig, ch, stype in ((gh, "github", "company_website"), (hn, "hackernews", "other"), (ph, "producthunt", "other")):
        if getattr(sig, "available", False):
            for excerpt, uri, ddate in sig.evidence:
                reg(channel=ch, source_type=stype, document_name=f"{ch}:{company.name}",
                    excerpt=excerpt, source_uri=uri, document_date=ddate, confidence="medium")

    # --- §5.6 derived signals ---
    oss = scoring.open_source_activity(gh)
    foot = scoring.public_footprint(gh, hn, ph)
    # LLM estimates only for live/inference (training would risk leakage via updated text)
    llm = raw.llm if (live and raw.llm.available) else llm_src.LLMEstimates()
    prop = scoring.proprietary(None, oss, llm.defensibility_rubric)
    cd_val, n_comp = ctx.competitor_density(company, cutoff_year if not live else None)
    comp_density = scoring.Scored(cd_val, "medium" if cd_val is not None else "low",
                                  f"sat(n_competitors={n_comp} in '{company.subindustry or company.sector}',50)",
                                  1.0 if cd_val is not None else 0.0)
    climate = scoring.Scored(ctx.funding_climate_index(company), "low",
                             "sector deals / trailing-8q peak (YC cadence proxy)", 1.0)
    net = scoring.Scored(ctx.network_centrality(company), "low",
                         "normalized degree centrality in YC sourcing graph (§6)", 1.0)
    soft = scoring.Scored(llm.soft_skill_estimate, "low", "LLM rubric over public text") if llm.soft_skill_estimate is not None else scoring.Scored(None, "low")

    for s in (oss, foot, prop, comp_density, climate, net):
        if s.value is not None:
            reg(channel="computed", source_type="manual_research",
                document_name=f"computed:{s.note[:40]}", excerpt=f"{s.note} = {round(s.value,4)} (coverage={round(s.coverage,2)})",
                document_date=cutoff_date, confidence=s.confidence)

    # --- §5.1 / §5.2 founder scores (cold-start: mostly footprint+network) ---
    fs = scoring.founder_score_persistent(footprint=foot, network=net, pedigree=llm.pedigree)
    fa = scoring.founder_axis(fs=fs, fmf=llm.founder_market_fit, team_size=(company.team_size if live else None),
                              soft=soft.value, single_founder=None)
    # --- §5.3 market axis ---
    tam = llm.tam_usd if live else None
    market_enum, market_scored = scoring.market_axis(tam_usd=tam, competitor_density=comp_density, climate=climate)
    # --- §5.4 idea-vs-market ---
    ivm = scoring.idea_vs_market(proprietary_s=prop, competitor_density=comp_density, founder_axis_s=fa)

    for s, note in ((fs, "founder_score_persistent §5.1"), (fa, "founder_axis §5.2"),
                    (market_scored, "market_axis §5.3"), (ivm, "idea_vs_market §5.4")):
        if s.value is not None:
            reg(channel="computed", source_type="internal_investment_record",
                document_name=f"axis:{note}", excerpt=f"{note}: value={round(s.value,2)}, {s.note}, coverage={round(s.coverage,2)}",
                document_date=observation_date, confidence=s.confidence)

    # --- trends (§5.5): compare to prior snapshot axes ---
    def tr(now, key):
        return scoring.trend(now, (prior_axes or {}).get(key))

    axes = {"founder_axis": fa.value, "market_axis": market_enum if market_enum != "unknown" else None,
            "idea_vs_market": ivm.value, "founder_score_persistent": fs.value}

    # --- financials: stage/last round ---
    if live:
        current_stage = _YC_STAGE_TO_ENUM.get(company.yc_stage or "", "unknown")
        last_round_date = None
    else:
        current_stage = "seed"                 # YC program participation at batch
        last_round_date = company.founding_date  # the YC investment event

    features = {
        "screening": {
            "founder_score_persistent": _r(fs.value),
            "founder_axis": _r(fa.value),
            "founder_axis_trend": tr(fa.value, "founder_axis"),
            "market_axis": market_enum,
            "market_axis_trend": tr(market_enum if market_enum != "unknown" else None, "market_axis"),
            "idea_vs_market": _r(ivm.value),
            "idea_vs_market_trend": tr(ivm.value, "idea_vs_market"),
        },
        "traction": {
            "arr_usd": None, "revenue_growth_rate_yoy": None,
            "customer_count": None, "churn_rate_annual": None,
        },
        "financials": {
            "burn_rate_usd_monthly": None, "runway_months": None,
            "current_stage": current_stage,
            "last_round_size_usd": None, "last_round_date": last_round_date,
            "total_funding_to_date_usd": None, "cash_balance_usd": None,
        },
        "team": {
            "founder_prior_exits": None, "founder_prior_startups": None,
            "single_founder_flag": None,
            "team_size": (company.team_size if live else None),
            "technical_founder_flag": None, "founder_industry_experience_years": None,
            "proprietary_score": _r(prop.value, 3), "patent_count": None,
            "open_source_activity_score": _r(oss.value, 3),
        },
        "market": {
            "tam_usd": tam, "competitor_density": _r(comp_density.value, 3),
            "sector": company.sector, "geography": company.geography,
            "funding_climate_index": _r(climate.value, 3),
        },
        "cold_start": {
            "public_footprint_score": _r(foot.value, 3),
            "network_centrality": _r(net.value, 3),
            "soft_skill_estimate": _r(soft.value, 3),
        },
    }
    return SnapshotResult(features=features, axes=axes, source_ids=sorted(set(source_ids)))


def _r(v, ndigits=2):
    return round(v, ndigits) if isinstance(v, (int, float)) else v


def finalize_record(company, snap: SnapshotResult, *, observation_date: str, cutoff_date: str,
                    stage: str, include_outcome_fields: bool = False) -> dict:
    sid = company.startup_id
    oid = opportunity_id(sid, observation_date)
    snap_id = snapshot_id(sid, observation_date, stage)
    missing: list[str] = []
    _collect_null_paths(snap.features, "", missing)
    rec = {
        "startup_id": sid,
        "opportunity_id": oid,
        "snapshot_id": snap_id,
        "observation_date": observation_date,
        "data_cutoff_date": cutoff_date,
        **snap.features,
        "source_ids": snap.source_ids,
        "missing_fields": sorted(missing),
        "contradicted_fields": [],
        "_meta": {"company": company.name, "accelerator": company.accelerator,
                  "batch": company.batch, "yc_status": company.yc_status},
    }
    return rec


# --- outcome labels from YC directory status ------------------------------
def build_outcomes(company, ev: EvidenceRegistry, *, snap_id: str, oid: str,
                   observation_date: str) -> dict:
    sid = company.startup_id
    obs = parse_date(observation_date) or company.founding_date
    obs_d = parse_date(observation_date) or TODAY
    end = TODAY
    censor_days = max(0, days_between(obs_d, end))
    status = (company.yc_status or "Active").lower()

    src = ev.add(startup_id=sid, channel="yc", source_type="internal_monitoring_record",
                 document_name=f"YC status: {company.name}",
                 excerpt=f"YC directory status = '{company.yc_status}' as of {end.isoformat()} "
                         f"(observation_end). Used for outcome labelling.",
                 source_uri=company.directory_url, document_date=end.isoformat(),
                 verification_status="document_verified", confidence="high")

    # defaults: censored / still observed
    next_round = {"next_round_observed": False, "next_round_stage": None,
                  "next_round_date": None, "next_round_size_usd": None,
                  "progressed_within_24_months": None}
    next_event = {"next_event_type": "none_observed", "next_event_date": None,
                  "time_to_next_event_days": censor_days, "next_event_observed": False}
    failure = {"failure_observed": False, "failure_date": None, "failure_definition": None,
               "time_to_failure_days": censor_days, "failure_event_observed": False}
    exit_block = {"exit_type": "no_exit_observed", "exit_date": None, "exit_verified": False,
                  "exit_valuation_usd": None, "exit_transaction_value_usd": None,
                  "exit_value_type": None, "exit_value_disclosed": False, "exit_value_verified": False}
    still_observed = True

    if status == "public":
        exit_block.update(exit_type="ipo", exit_verified=True)
    elif status == "acquired":
        exit_block.update(exit_type="acquisition", exit_verified=True)
    elif status == "inactive":
        # YC "Inactive" is a curated editorial signal (stronger than a dead site),
        # but exact shutdown date is not available -> event flagged, timing censored.
        failure.update(failure_observed=True, failure_definition="shutdown_confirmed",
                       failure_event_observed=True)
        still_observed = False

    return {
        "startup_id": sid,
        "opportunity_id": oid,
        "snapshot_id": snap_id,
        "next_round": next_round,
        "next_event": next_event,
        "failure": failure,
        "growth": None,                 # M4: no two comparable revenue points available
        "m4_included": False,
        "exit": exit_block,
        "outcome_observation_end_date": end.isoformat(),
        "company_still_observed": still_observed,
        "source_ids": [src],
        "missing_fields": [p for p, v in (("exit.exit_valuation_usd", exit_block["exit_valuation_usd"]),
                                          ("growth", None)) if v is None],
        "contradicted_fields": [],
    }
