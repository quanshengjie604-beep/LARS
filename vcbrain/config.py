"""Global configuration, constants, and environment wiring.

Everything the pipeline needs to run is derived here so the rest of the code
stays free of magic numbers. Values map directly to the specifications in
`plan.md` (§5 formula constants) and `ngboost_data_requirements.md`.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

# --- Point-in-time anchor -------------------------------------------------
# The challenge/handover docs fix "today" at 2026-07-18. We honour that so the
# outcome-observation-end date and censoring maths are reproducible; override
# with VCBRAIN_TODAY=YYYY-MM-DD if you re-run later.
TODAY: date = date.fromisoformat(os.environ.get("VCBRAIN_TODAY", "2026-07-18"))

# "at most 10 years old" -> founded on/after this cutoff (batch/founding year).
MAX_AGE_YEARS = int(os.environ.get("VCBRAIN_MAX_AGE_YEARS", "10"))
MIN_FOUNDING_YEAR = TODAY.year - MAX_AGE_YEARS  # 2016 for a 2026 run


def _find_ca_bundle() -> str | None:
    """macOS python.org builds often lack a CA bundle on the default path.

    Probe the common locations so HTTPS verification works out of the box.
    """
    for cand in (
        os.environ.get("SSL_CERT_FILE"),
        "/etc/ssl/cert.pem",
        "/private/etc/ssl/cert.pem",
        "/usr/local/etc/openssl@3/cert.pem",
        "/usr/local/etc/openssl/cert.pem",
    ):
        if cand and os.path.exists(cand):
            return cand
    try:  # certifi may be present in some environments
        import certifi  # type: ignore

        return certifi.where()
    except Exception:
        return None


CA_BUNDLE = _find_ca_bundle()


def ssl_context() -> ssl.SSLContext:
    if CA_BUNDLE:
        return ssl.create_default_context(cafile=CA_BUNDLE)
    return ssl.create_default_context()


# --- API credentials (all optional) --------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
PRODUCTHUNT_TOKEN = os.environ.get("PRODUCTHUNT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = os.environ.get("VCBRAIN_LLM_MODEL", "claude-sonnet-5")

USER_AGENT = "vcbrain/0.1 (+https://github.com/; VC Brain screening pipeline)"

# --- Filesystem ----------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get("VCBRAIN_CACHE_DIR", os.path.join(ROOT, ".vcbrain_cache"))
OUTPUT_DIR = os.environ.get("VCBRAIN_OUTPUT_DIR", os.path.join(ROOT, "screening_handover"))

CACHE_TTL_SECONDS = int(os.environ.get("VCBRAIN_CACHE_TTL", str(7 * 24 * 3600)))

# --- Concurrency / rate limits -------------------------------------------
MAX_WORKERS = int(os.environ.get("VCBRAIN_WORKERS", "16"))

# Per-host requests/second caps. Unauthenticated GitHub search is ~10/min, so
# we throttle hard unless a token is present.
RATE_LIMITS = {
    "api.github.com": 0.5 if GITHUB_TOKEN else 0.15,
    "hn.algolia.com": 5.0,
    "yc-oss.github.io": 20.0,
    "api.producthunt.com": 1.0,
    "api.anthropic.com": 2.0,
    "_default": 3.0,
}

HTTP_TIMEOUT = int(os.environ.get("VCBRAIN_HTTP_TIMEOUT", "30"))
HTTP_RETRIES = 3


@dataclass
class Weights:
    """§5 scoring weights, isolated so they can be tuned without touching logic."""

    # 5.1 Founder Score (persistent)
    founder_score: dict = field(default_factory=lambda: {
        "exits": 0.25, "startups": 0.10, "exp": 0.15, "tech": 0.10,
        "pedigree": 0.15, "footprint": 0.15, "network": 0.10,
    })
    # 5.2 Founder axis
    founder_axis: dict = field(default_factory=lambda: {
        "fs": 0.45, "fmf": 0.25, "team_adeq": 0.15, "soft": 0.15,
    })
    single_founder_penalty = 8.0
    # 5.3 Market axis (sam/som dropped per plan -> renormalized weights)
    market_axis: dict = field(default_factory=lambda: {
        "tam": 0.45, "crowd": 0.30, "climate": 0.25,
    })
    market_bull_threshold = 66.0
    market_bear_threshold = 40.0
    # 5.4 Idea-vs-Market
    idea_vs_market: dict = field(default_factory=lambda: {
        "proprietary": 0.35, "crowd": 0.20, "traction": 0.25, "pivot": 0.20,
    })
    # 5.5 Trend thresholds
    trend_delta = 0.05


WEIGHTS = Weights()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
