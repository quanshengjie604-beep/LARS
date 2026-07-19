"""Runtime settings; environment variables are read only when settings are built."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(slots=True)
class Settings:
    as_of: date = field(default_factory=date.today)
    cache_dir: Path = Path(".vcbrain_cache")
    output_dir: Path = Path("screening_handover")
    cache_ttl_seconds: int = 7 * 24 * 60 * 60
    timeout_seconds: float = 25.0
    max_retries: int = 3
    concurrency: int = 32
    per_host_concurrency: int = 6
    user_agent: str = "vcbrain/0.2 startup-research"
    producthunt_token: str | None = None
    github_token: str | None = None
    sec_user_agent: str | None = None
    startx_api_key: str | None = None
    max_company_age_years: int | None = 10

    @classmethod
    def from_env(cls) -> "Settings":
        as_of = date.fromisoformat(os.environ.get("VCBRAIN_AS_OF", date.today().isoformat()))
        max_age_raw = os.environ.get("VCBRAIN_MAX_COMPANY_AGE_YEARS", "10")
        return cls(
            as_of=as_of,
            cache_dir=Path(os.environ.get("VCBRAIN_CACHE_DIR", ".vcbrain_cache")),
            output_dir=Path(os.environ.get("VCBRAIN_OUTPUT_DIR", "screening_handover")),
            cache_ttl_seconds=int(os.environ.get("VCBRAIN_CACHE_TTL", str(7 * 24 * 60 * 60))),
            timeout_seconds=float(os.environ.get("VCBRAIN_HTTP_TIMEOUT", "25")),
            max_retries=int(os.environ.get("VCBRAIN_HTTP_RETRIES", "3")),
            concurrency=int(os.environ.get("VCBRAIN_CONCURRENCY", "32")),
            per_host_concurrency=int(os.environ.get("VCBRAIN_PER_HOST_CONCURRENCY", "6")),
            user_agent=os.environ.get("VCBRAIN_USER_AGENT", "vcbrain/0.2 startup-research"),
            producthunt_token=os.environ.get("PRODUCTHUNT_TOKEN"),
            github_token=os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN"),
            sec_user_agent=os.environ.get("SEC_USER_AGENT"),
            startx_api_key=os.environ.get("STARTX_API_KEY") or os.environ.get("CONSIDER_API_KEY"),
            max_company_age_years=None if max_age_raw.casefold() == "none" else int(max_age_raw),
        )
