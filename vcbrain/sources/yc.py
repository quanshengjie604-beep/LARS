"""Y Combinator directory — the primary accelerator seed source.

Uses the yc-oss/api static JSON mirror (https://github.com/yc-oss/api), which
exposes the public YC company directory with no auth. Each record carries the
fields we build the seed `Company` from, plus `status`/`stage` which anchor the
point-in-time outcome labels.

This module is the reference implementation of an "accelerator directory" seed;
`Company` is source-agnostic so other accelerators can be added the same way.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .. import config
from ..http_client import get_json
from ..util import batch_to_date, domain_of, slugify, startup_id

YC_ALL = "https://yc-oss.github.io/api/companies/all.json"
YC_META = "https://yc-oss.github.io/api/meta.json"

# Normalised sector taxonomy (YC `industry` -> our snake_case taxonomy).
_SECTOR_MAP = {
    "b2b": "b2b_software",
    "consumer": "consumer",
    "education": "education",
    "fintech": "fintech",
    "healthcare": "healthcare",
    "industrials": "industrials",
    "government": "govtech",
    "real estate and construction": "real_estate_construction",
    "unspecified": "unknown",
}

# Region string -> normalised geography.
_GEO_MAP = {
    "united states": "united_states",
    "germany": "germany",
    "united kingdom": "united_kingdom",
    "canada": "canada",
    "india": "india",
    "france": "france",
    "remote": "remote",
}

_STAGE_MAP = {"early": "seed", "growth": "series_b", "public": "series_c_plus"}


@dataclass
class Company:
    """Source-agnostic seed record for one startup."""

    name: str
    accelerator: str = "Y Combinator"
    batch: Optional[str] = None
    founding_year: Optional[int] = None
    founding_date: Optional[str] = None       # YYYY-MM-DD, approx from batch
    website: Optional[str] = None
    domain: Optional[str] = None
    one_liner: Optional[str] = None
    description: Optional[str] = None
    sector: Optional[str] = None
    subindustry: Optional[str] = None
    tags: list = field(default_factory=list)
    geography: Optional[str] = None
    team_size: Optional[int] = None           # CURRENT headcount (leakage-prone)
    yc_status: Optional[str] = None           # Active / Acquired / Public / Inactive
    yc_stage: Optional[str] = None            # Early / Growth
    top_company: bool = False
    directory_url: Optional[str] = None
    slug: Optional[str] = None
    raw: dict = field(default_factory=dict)

    @property
    def startup_id(self) -> str:
        return startup_id(self.name, self.domain)


def _norm_geo(all_locations: Optional[str], regions: list) -> Optional[str]:
    hay = " ".join([all_locations or ""] + [str(r) for r in (regions or [])]).lower()
    for key, val in _GEO_MAP.items():
        if key in hay:
            return val
    # fall back to last comma-separated token of the location string
    if all_locations:
        tail = all_locations.split(",")[-1].strip().lower()
        if tail:
            return slugify(tail).replace("-", "_")
    return None


def _to_company(rec: dict) -> Company:
    industry = (rec.get("industry") or "").strip().lower()
    fdate, fyear = batch_to_date(rec.get("batch"))
    website = rec.get("website")
    return Company(
        name=rec.get("name") or "Unknown",
        batch=rec.get("batch"),
        founding_year=fyear,
        founding_date=fdate,
        website=website,
        domain=domain_of(website),
        one_liner=rec.get("one_liner"),
        description=rec.get("long_description") or rec.get("one_liner"),
        sector=_SECTOR_MAP.get(industry, slugify(industry).replace("-", "_") if industry else "unknown"),
        subindustry=rec.get("subindustry"),
        tags=rec.get("tags") or [],
        geography=_norm_geo(rec.get("all_locations"), rec.get("regions") or []),
        team_size=rec.get("team_size") if rec.get("team_size") else None,
        yc_status=rec.get("status"),
        yc_stage=rec.get("stage"),
        top_company=bool(rec.get("top_company")),
        directory_url=rec.get("url"),
        slug=rec.get("slug"),
        raw=rec,
    )


def fetch_directory() -> list[dict]:
    """Return the raw YC company list (cached)."""
    data = get_json(YC_ALL)
    if not isinstance(data, list):
        raise RuntimeError("Unexpected YC directory payload")
    return data


def seed_companies(
    *,
    limit: Optional[int] = None,
    batches: Optional[list[str]] = None,
    sectors: Optional[list[str]] = None,
    geographies: Optional[list[str]] = None,
    min_founding_year: int = config.MIN_FOUNDING_YEAR,
    include_status: Optional[list[str]] = None,
    top_only: bool = False,
) -> list[Company]:
    """Seed a filtered, age-capped list of companies from the YC directory.

    The 10-year age cap (`min_founding_year`) is applied *here* so no downstream
    stage ever sees an out-of-range company.
    """
    raw = fetch_directory()
    companies: list[Company] = []
    for rec in raw:
        c = _to_company(rec)
        # Age filter: founded on/after the cutoff year (batch year proxy).
        if c.founding_year is None or c.founding_year < min_founding_year:
            continue
        if c.founding_year > config.TODAY.year:
            continue
        if batches and (c.batch or "") not in batches:
            continue
        if sectors and (c.sector or "") not in sectors:
            continue
        if geographies and (c.geography or "") not in geographies:
            continue
        if include_status and (c.yc_status or "") not in include_status:
            continue
        if top_only and not c.top_company:
            continue
        companies.append(c)

    # Deterministic ordering: top companies first, then newest, then name.
    companies.sort(key=lambda c: (not c.top_company, -(c.founding_year or 0), c.name.lower()))
    if limit:
        companies = companies[:limit]
    return companies
