"""
schema.py
=========

Canonical definition of the **NGBoost live inference request** and a strict
normaliser that turns any loosely-structured dict (e.g. the raw JSON an LLM
produces) into a schema-clean, NGBoost-ready inference request.

The authoritative field list is Section 9 ("Live inference-request example") of
``docs/requirements/ngboost-data-requirements.md``. Every input feature is
nullable/optional; the golden rules enforced here are:

* Never fabricate: an un-findable value becomes ``null`` (not ``0``).
* Every defined field is always present (filled with ``null`` if missing).
* Enums only ever hold an allowed value, otherwise ``null`` (or ``unknown``
  where the schema defines that sentinel).
* ``missing_fields`` is recomputed to list every ``null`` feature path.

Keeping this independent of FastAPI and of the OpenAI client means it can be
unit-tested on its own and reused by any caller that needs a valid request.
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Iterable

# --------------------------------------------------------------------------
# Allowed enum values (from the data-requirements doc)
# --------------------------------------------------------------------------
TREND_VALUES = ("improving", "stable", "declining", "unknown")
MARKET_AXIS_VALUES = ("bull", "neutral", "bear", "unknown")
STAGE_VALUES = ("pre_seed", "seed", "series_a", "series_b", "series_c_plus", "unknown")

# --------------------------------------------------------------------------
# Feature-group layout. Each entry: field name -> ("kind", extra)
#   kind one of: float, int, bool, str, date, enum, score01, score0100
#   For enum, `extra` is the tuple of allowed values.
#   score01 / score0100 are floats clamped to [0,1] / [0,100].
# --------------------------------------------------------------------------
_GROUPS: dict[str, dict[str, tuple[str, Any]]] = {
    "screening": {
        "founder_score_persistent": ("score0100", None),
        "founder_axis": ("score0100", None),
        "founder_axis_trend": ("enum", TREND_VALUES),
        "market_axis": ("enum", MARKET_AXIS_VALUES),
        "market_axis_trend": ("enum", TREND_VALUES),
        "idea_vs_market": ("score0100", None),
        "idea_vs_market_trend": ("enum", TREND_VALUES),
    },
    "traction": {
        "arr_usd": ("float", None),
        "revenue_growth_rate_yoy": ("float", None),
        "customer_count": ("int", None),
        "churn_rate_annual": ("float", None),
    },
    "financials": {
        "burn_rate_usd_monthly": ("float", None),
        "runway_months": ("float", None),
        "current_stage": ("enum", STAGE_VALUES),
        "last_round_size_usd": ("float", None),
        "last_round_date": ("date", None),
        "total_funding_to_date_usd": ("float", None),
        "cash_balance_usd": ("float", None),
    },
    "team": {
        "founder_prior_exits": ("int", None),
        "founder_prior_startups": ("int", None),
        "single_founder_flag": ("bool", None),
        "team_size": ("int", None),
        "technical_founder_flag": ("bool", None),
        "founder_industry_experience_years": ("float", None),
        "proprietary_score": ("score01", None),
        "patent_count": ("int", None),
        "open_source_activity_score": ("score01", None),
    },
    "market": {
        "tam_usd": ("float", None),
        "competitor_density": ("score01", None),
        "sector": ("str", None),
        "geography": ("str", None),
        "funding_climate_index": ("score01", None),
    },
    "cold_start": {
        "public_footprint_score": ("score01", None),
        "network_centrality": ("score01", None),
        "soft_skill_estimate": ("score01", None),
    },
}

# Ordered list of every feature path, e.g. "traction.arr_usd".
FEATURE_PATHS: tuple[str, ...] = tuple(
    f"{group}.{field}" for group, fields in _GROUPS.items() for field in fields
)


# --------------------------------------------------------------------------
# Coercion helpers — each returns the cleaned value or None (never raises).
# --------------------------------------------------------------------------
def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() in {"null", "none", "n/a", "unknown", "?"}
    return False


def _to_float(value: Any) -> float | None:
    if _is_blank(value):
        return None
    if isinstance(value, bool):  # avoid True -> 1.0
        return None
    if isinstance(value, (int, float)):
        return None if (isinstance(value, float) and math.isnan(value)) else float(value)
    if isinstance(value, str):
        # Strip currency symbols, thousands separators, stray words.
        cleaned = re.sub(r"[,_$€£\s]", "", value.strip())
        m = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if m:
            try:
                return float(m.group())
            except ValueError:
                return None
    return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return None if f is None else int(round(f))


def _to_bool(value: Any) -> bool | None:
    if _is_blank(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "y", "1"}:
            return True
        if v in {"false", "no", "n", "0"}:
            return False
    return None


def _to_str(value: Any) -> str | None:
    if _is_blank(value):
        return None
    return str(value).strip()


def _to_date(value: Any) -> str | None:
    """Return a ``YYYY-MM-DD`` string or None."""
    if _is_blank(value):
        return None
    if isinstance(value, str):
        m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
        if m:
            y, mo, d = (int(g) for g in m.groups())
            try:
                return date(y, mo, d).isoformat()
            except ValueError:
                return None
        # A bare year -> pin to Jan 1 so downstream date parsing still works.
        m = re.fullmatch(r"\s*(\d{4})\s*", value)
        if m:
            return f"{m.group(1)}-01-01"
    return None


def _to_enum(value: Any, allowed: Iterable[str]) -> str | None:
    if _is_blank(value):
        return None
    v = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    allowed = tuple(allowed)
    if v in allowed:
        return v
    # A few forgiving aliases for the stage / market enums.
    aliases = {
        "preseed": "pre_seed", "pre_seed_": "pre_seed",
        "seriesa": "series_a", "a": "series_a",
        "seriesb": "series_b", "b": "series_b",
        "seriesc": "series_c_plus", "series_c": "series_c_plus", "c": "series_c_plus",
        "growth": "series_c_plus", "late": "series_c_plus",
        "bullish": "bull", "bearish": "bear", "flat": "neutral",
        "up": "improving", "down": "declining", "steady": "stable",
    }
    return aliases.get(v) if aliases.get(v) in allowed else None


def _coerce(value: Any, kind: str, extra: Any) -> Any:
    if kind == "float":
        return _to_float(value)
    if kind == "int":
        return _to_int(value)
    if kind == "bool":
        return _to_bool(value)
    if kind == "str":
        return _to_str(value)
    if kind == "date":
        return _to_date(value)
    if kind == "enum":
        return _to_enum(value, extra)
    if kind == "score01":
        f = _to_float(value)
        if f is None:
            return None
        # Accept 0-100 inputs for a 0-1 field by rescaling.
        if f > 1.0 and f <= 100.0:
            f = f / 100.0
        return max(0.0, min(1.0, f))
    if kind == "score0100":
        f = _to_float(value)
        if f is None:
            return None
        # Accept 0-1 inputs for a 0-100 field by rescaling.
        if 0.0 <= f <= 1.0:
            f = f * 100.0
        return max(0.0, min(100.0, f))
    return None


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def empty_feature_groups() -> dict[str, dict[str, Any]]:
    """Return the six feature groups with every field set to ``None``."""
    return {group: {field: None for field in fields} for group, fields in _GROUPS.items()}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return slug or "unknown"


def make_ids(
    name: str,
    stage: str | None,
    observation_date: str,
    startup_id: str | None = None,
    opportunity_id: str | None = None,
) -> dict[str, str]:
    """Build the identifier block. Deterministic from the person's name + date."""
    base = _slugify(name)
    stage_slug = (stage or "unknown")
    return {
        "startup_id": startup_id or f"startup_{base}",
        "opportunity_id": opportunity_id or f"opportunity_{base}",
        "snapshot_id": f"startup_{base}_{observation_date}_{stage_slug}",
    }


def normalize_inference_request(
    raw: dict[str, Any] | None,
    *,
    name: str = "",
    observation_date: str | None = None,
    startup_id: str | None = None,
    opportunity_id: str | None = None,
) -> dict[str, Any]:
    """Turn a loose dict into a valid, NGBoost-ready inference request.

    Parameters
    ----------
    raw:
        Any dict shaped roughly like an inference request. May be nested
        (``{"traction": {"arr_usd": ...}}``) or flat (``{"arr_usd": ...}``);
        both are accepted. Extra/unknown keys are ignored.
    name:
        Person / company name, used to derive identifiers and the snapshot id.
    observation_date / *_id:
        Overrides; default to today's date and name-derived identifiers.

    Returns
    -------
    dict
        A schema-clean inference request. Every defined field is present,
        ``missing_fields`` lists every ``null`` feature path, and no outcome
        fields are included.
    """
    raw = raw or {}
    obs_date = _to_date(observation_date) or date.today().isoformat()

    # Accept either nested groups or a flat mapping of field -> value.
    def _lookup(group: str, field: str) -> Any:
        grp = raw.get(group)
        if isinstance(grp, dict) and field in grp:
            return grp.get(field)
        # Fall back to a flat key at the top level.
        return raw.get(field)

    groups = empty_feature_groups()
    for group, fields in _GROUPS.items():
        for field, (kind, extra) in fields.items():
            groups[group][field] = _coerce(_lookup(group, field), kind, extra)

    # Derive current_stage first so identifiers can use it.
    stage = groups["financials"]["current_stage"]
    ids = make_ids(name, stage, obs_date, startup_id, opportunity_id)

    # Recompute missing_fields from the cleaned values (source of truth).
    missing = [path for path in FEATURE_PATHS
               if groups[path.split(".")[0]][path.split(".")[1]] is None]

    # Preserve any source ids / contradictions the caller supplied.
    source_ids = raw.get("source_ids") or []
    if not isinstance(source_ids, list):
        source_ids = []
    contradicted = raw.get("contradicted_fields") or []
    if not isinstance(contradicted, list):
        contradicted = []

    request: dict[str, Any] = {
        **ids,
        "observation_date": obs_date,
        "data_cutoff_date": obs_date,
        **groups,
        "source_ids": [str(s) for s in source_ids],
        "missing_fields": missing,
        "contradicted_fields": [str(c) for c in contradicted],
    }
    return request


# JSON-schema description handed to the LLM so it knows exactly what to fill.
# (Kept as a plain dict so callers can embed it in a prompt or a structured
# response format without importing anything.)
def llm_field_guide() -> dict[str, Any]:
    """A compact, human-readable description of every field, for prompting."""
    return {
        "identifiers": ["startup_id", "opportunity_id", "snapshot_id",
                        "observation_date", "data_cutoff_date"],
        "enums": {
            "trend_fields": list(TREND_VALUES),
            "market_axis": list(MARKET_AXIS_VALUES),
            "current_stage": list(STAGE_VALUES),
        },
        "groups": {group: list(fields) for group, fields in _GROUPS.items()},
        "rules": [
            "Every feature is optional and nullable. Use null when a value "
            "cannot be found from public sources. Never guess a number.",
            "Do not equate missing with zero: arr_usd=0 means verified "
            "pre-revenue; arr_usd=null means unknown.",
            "Money in USD, rates as fractions (0.65 = 65%), dates YYYY-MM-DD, "
            "0-1 or 0-100 scores within range.",
            "Return ONLY a JSON object with the six feature groups "
            "(screening, traction, financials, team, market, cold_start).",
        ],
    }


if __name__ == "__main__":
    import json

    demo = normalize_inference_request(
        {
            "financials": {"current_stage": "Seed", "runway_months": "10 months"},
            "traction": {"arr_usd": "$0", "customer_count": "3"},
            "team": {"single_founder_flag": "true", "proprietary_score": 62},
            "market": {"sector": "Developer Tools", "geography": "Germany"},
        },
        name="Ada Lovelace",
    )
    print(json.dumps(demo, indent=2))
