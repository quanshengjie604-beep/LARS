"""Small pure helpers: §5 math primitives, weighted-present averaging, ids, dates."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import date, datetime, timezone
from typing import Iterable, Optional


# --- §5 math helpers ------------------------------------------------------
def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def nlog(x: float, lo: float, hi: float) -> float:
    """Log-scale a positive money/count value into [0, 1] between decades lo..hi."""
    return clamp((math.log10(max(x, 1)) - lo) / (hi - lo), 0.0, 1.0)


def sat(x: float, k: float) -> float:
    """Saturating count: x/k clamped to [0, 1]; k is the saturation point."""
    return clamp(x / k, 0.0, 1.0)


def weighted_present(pairs: Iterable[tuple[Optional[float], float]]) -> tuple[Optional[float], float, int, int]:
    """Weighted average that DROPS null inputs and renormalizes remaining weights.

    A missing input widens uncertainty; it never scores as zero (plan §5).

    Returns (value_or_None, coverage_fraction, n_present, n_total) where
    coverage = present_weight / total_weight. Callers lower confidence in
    proportion to how many inputs were null.
    """
    total_w = 0.0
    present_w = 0.0
    acc = 0.0
    n_present = 0
    n_total = 0
    for val, w in pairs:
        n_total += 1
        total_w += w
        if val is None:
            continue
        acc += val * w
        present_w += w
        n_present += 1
    if present_w == 0:
        return None, 0.0, 0, n_total
    coverage = present_w / total_w if total_w else 0.0
    return acc / present_w, coverage, n_present, n_total


def mean_present(values: Iterable[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def confidence_from_coverage(coverage: float, base: str = "medium") -> str:
    """Map an input-coverage fraction to a confidence label."""
    if coverage >= 0.75:
        return "high" if base != "low" else "medium"
    if coverage >= 0.4:
        return "medium" if base != "low" else "low"
    return "low"


# --- ids ------------------------------------------------------------------
def stable_hash(*parts: str) -> str:
    h = hashlib.sha1("::".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return h[:12]


def startup_id(name: str, domain: Optional[str] = None) -> str:
    key = (domain or "").strip().lower() or slugify(name)
    return "startup_" + stable_hash("startup", key)


def opportunity_id(sid: str, observation_date: str) -> str:
    return "opportunity_" + stable_hash("opp", sid, observation_date)


def snapshot_id(sid: str, observation_date: str, stage: str) -> str:
    return f"{sid}_{observation_date}_{stage or 'unknown'}"


def source_id(*parts: str) -> str:
    return "source_" + stable_hash("source", *[str(p) for p in parts])


# --- text/normalisation ---------------------------------------------------
def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "unknown"


def domain_of(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.match(r"^\s*(?:https?://)?(?:www\.)?([^/\s?#]+)", url)
    if not m:
        return None
    return m.group(1).lower()


# --- dates ----------------------------------------------------------------
def epoch_to_date(ts: Optional[int]) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # last resort: leading 10 chars
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def days_between(a: date, b: date) -> int:
    return (b - a).days


# YC batch -> approximate (start_date, founding_year). Batches: "Summer 2024",
# "Winter 2024", "Spring 2024", "Fall 2024".
_SEASON_MONTH = {"winter": "01", "spring": "03", "summer": "06", "fall": "09"}


def batch_to_date(batch: Optional[str]) -> tuple[Optional[str], Optional[int]]:
    if not batch:
        return None, None
    m = re.match(r"([A-Za-z]+)\s+(\d{4})", batch.strip())
    if not m:
        return None, None
    season, year = m.group(1).lower(), int(m.group(2))
    month = _SEASON_MONTH.get(season, "01")
    return f"{year}-{month}-01", year
