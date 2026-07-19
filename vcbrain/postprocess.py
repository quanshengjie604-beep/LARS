"""Deterministic, evidence-only postprocessing for flat startup snapshots.

The functions in this module operate on ordinary dictionaries and have no
package or third-party dependencies.  They deliberately separate three ideas:

* an explicit disclosure found in an evidence excerpt;
* a canonical snapshot value that may be filled from that disclosure; and
* a quality/enrichment recommendation when the disclosure is missing, stale,
  ambiguous, contradicted, or unsupported.

No function estimates a financial value.  Unit normalization is limited to
mechanical conversions (for example, ``$2M`` to ``2_000_000`` and ``45%`` to
``0.45``).  Projections, third-party estimates, non-exact bounds, values with
an incompatible definition, and non-USD money amounts are retained as
disclosures but are never written into canonical ``*_usd`` fields.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlparse


__all__ = [
    "FINANCIAL_FIELDS",
    "extract_financial_disclosures",
    "apply_latest_disclosures",
    "recompute_missing_fields",
    "assess_data_quality",
    "build_enrichment_queue",
    "postprocess_snapshots",
]


# Flat canonical fields handled by the evidence extractor.  ``revenue_usd`` is
# intentionally separate from ARR: generic revenue must never populate ARR.
FINANCIAL_FIELDS: tuple[str, ...] = (
    "arr_usd",
    "revenue_usd",
    "customer_count",
    "revenue_growth_rate_yoy",
    "runway_months",
    "burn_rate_usd_monthly",
    "cash_balance_usd",
)

# ``revenue_usd`` is an optional extension rather than part of the NGBoost
# handover contract.  It is extracted when explicitly annual, but is not added
# to missingness/queue reports unless the caller's flat schema actually carries
# that field (or explicitly requests it via ``tracked_fields``).
_DEFAULT_TRACKED_FINANCIAL_FIELDS = tuple(
    field for field in FINANCIAL_FIELDS if field != "revenue_usd"
)

_PRIVATE_FINANCIAL_FIELDS = {
    "arr_usd",
    "revenue_usd",
    "runway_months",
    "burn_rate_usd_monthly",
    "cash_balance_usd",
}
_SPARSE_PUBLIC_FIELDS = {"customer_count", "revenue_growth_rate_yoy"}

_ADMIN_FIELDS = {
    "missing_fields",
    "missing_reasons",
    "missing_reason_codes",
    "contradicted_fields",
    "source_ids",
    "field_evidence",
    "field_source_ids",
    "errors",
}

# Public, machine-readable reason/action codes used by the quality report and
# enrichment queue.
_REASON_ACTION = {
    "private_financial_disclosure": "request_founder_financials",
    "sparse_public_disclosure": "search_primary_disclosures",
    "source_pipeline_failure": "retry_or_repair_source",
    "contradicted_disclosures": "resolve_contradictions",
    "stale_disclosure": "refresh_evidence",
    "missing_supporting_evidence": "verify_primary_source",
    "low_coverage": "enrich_from_additional_sources",
}

_SOURCE_TARGETS = {
    "arr_usd": ["company_website", "press_release", "founder_documents"],
    "revenue_usd": ["company_website", "regulatory_filing", "founder_documents"],
    "customer_count": ["company_website", "press_release", "founder_documents"],
    "revenue_growth_rate_yoy": ["company_website", "press_release", "founder_documents"],
    "runway_months": ["financial_statement", "founder_documents", "cap_table"],
    "burn_rate_usd_monthly": ["financial_statement", "founder_documents"],
    "cash_balance_usd": ["financial_statement", "bank_or_payment_record", "founder_documents"],
}

_ENTITY_KEYS = ("startup_id", "company_id", "canonical_company_id")

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_MAGNITUDE = r"(?:k|thousand|m|mn|million|b|bn|billion)?"
_QUALIFIER = r"(?:approximately|approx\.?|about|around|nearly|over|more\s+than|at\s+least|up\s+to|~)?"
_CURRENCY = r"(?:US\$|USD|\$|EUR|€|GBP|£)"
_MONEY = (
    rf"(?P<qualifier>{_QUALIFIER})\s*(?P<currency>{_CURRENCY})\s*"
    rf"(?P<number>{_NUMBER})\s*(?P<magnitude>{_MAGNITUDE})\b"
)
_COUNT = (
    rf"(?P<qualifier>{_QUALIFIER})\s*(?P<number>{_NUMBER})\s*"
    rf"(?P<magnitude>{_MAGNITUDE})\b"
)

_ARR_LABEL = r"(?P<label>ARR|annual(?:ized)?\s+recurring\s+revenue)"
_REVENUE_LABEL = (
    r"(?P<label>annual\s+revenue|yearly\s+revenue|monthly\s+recurring\s+revenue|"
    r"monthly\s+revenue|MRR|revenue)"
)
_CONNECTOR = r"(?:was|is|of|at|reached|hit|total(?:l)?ed|stood\s+at|reported|:|=)?"

_ARR_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b{_ARR_LABEL}\b\s*{_CONNECTOR}\s*{_MONEY}",
        rf"{_MONEY}\s*(?:in|of)?\s*\b{_ARR_LABEL}\b",
    )
)
_REVENUE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b{_REVENUE_LABEL}\b\s*{_CONNECTOR}\s*{_MONEY}",
        rf"{_MONEY}\s*(?:in|of)?\s*\b{_REVENUE_LABEL}\b",
        rf"\b(?:generated|booked|recorded)\s*{_MONEY}\s+(?:in|of)\s+{_REVENUE_LABEL}\b",
    )
)
_CUSTOMER_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b(?:has|had|serves?|reached|counts?|with)\s+{_COUNT}\s+"
        r"(?P<paying>paying\s+)?(?:customers?|clients?)\b",
        rf"{_COUNT}\s+(?P<paying>paying\s+)?(?:customers?|clients?)\b",
        r"\b(?P<paying>paying\s+)?(?:customers?|clients?)\s*"
        rf"(?:was|were|reached|total(?:l)?ed|:|=)?\s*{_COUNT}",
    )
)
_GROWTH_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?P<basis>ARR|revenue)\s+(?:growth\s+(?:was|of|reached)|grew|increased|was\s+up)"
        rf"\s*(?:by\s+)?(?P<qualifier>{_QUALIFIER})\s*(?P<percent>{_NUMBER})\s*%"
        r"\s*(?P<period>year[-\s]?over[-\s]?year|yoy|annually|over\s+the\s+(?:past|last)\s+year)?",
        rf"(?P<qualifier>{_QUALIFIER})\s*(?P<percent>{_NUMBER})\s*%\s*"
        r"(?P<period>year[-\s]?over[-\s]?year|yoy|annual(?:ly)?)?\s*"
        r"(?P<basis>ARR|revenue)\s+growth\b",
    )
)
_RUNWAY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b(?:cash\s+)?runway\s*(?:was|is|of|at|:|=)?\s*{_COUNT}\s*"
        r"(?P<duration_unit>months?|years?)\b",
        rf"{_COUNT}\s*(?P<duration_unit>months?|years?)\s+of\s+(?:cash\s+)?runway\b",
    )
)
_BURN_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\b(?P<label>monthly\s+(?:cash\s+)?burn|(?:cash\s+)?burn\s+rate|cash\s+burn|burn)\b"
        rf"\s*{_CONNECTOR}\s*{_MONEY}\s*(?P<frequency>per\s+month|/\s*month|monthly|per\s+year|/\s*year|annually)?",
        rf"{_MONEY}\s*(?P<frequency>per\s+month|/\s*month|monthly|per\s+year|/\s*year|annually)"
        r"\s+(?P<label>(?:cash\s+)?burn(?:\s+rate)?)\b",
    )
)
_CASH_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?P<label>cash\s+balance|cash\s+on\s+hand|cash\s+reserves?|cash\s+position)\b"
        rf"\s*{_CONNECTOR}\s*{_MONEY}",
        rf"\b(?:has|had|holds?|reported)\s+{_MONEY}\s+(?:in\s+)?cash\b",
    )
)

_PROJECTION_RE = re.compile(
    r"\b(?:project(?:ed|s)?|forecast(?:ed|s)?|estimate(?:d|s)?|target(?:ed|s)?|"
    r"aims?\s+to|plans?\s+to|expects?\s+to|expected(?:\s+to)?|on\s+track\s+to|"
    r"set\s+to|could\s+reach|would\s+reach|will\s+reach|"
    r"potential(?:ly)?|pro\s+forma)\b",
    re.IGNORECASE,
)
_NON_COMPANY_RE = re.compile(
    r"\b(?:total\s+addressable\s+market|addressable\s+market|market\s+size|TAM)\b",
    re.IGNORECASE,
)


def _parse_date(value: Any) -> Optional[date]:
    """Parse an ISO-like date without inventing missing precision."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(_stable_json(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _entity_value(record: Mapping[str, Any]) -> Optional[str]:
    for key in _ENTITY_KEYS:
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def _entity_matches(snapshot: Mapping[str, Any], disclosure: Mapping[str, Any]) -> bool:
    snapshot_entity = _entity_value(snapshot)
    disclosure_entity = _entity_value(disclosure)
    if snapshot_entity is None:
        return True
    return disclosure_entity == snapshot_entity


def _excerpt(record: Mapping[str, Any]) -> str:
    value = record.get("evidence_excerpt", record.get("excerpt", ""))
    return str(value or "").strip()


def _sentence_window(text: str, start: int, end: int) -> str:
    """Return the surrounding sentence used for conservative rejection checks."""
    left = max(text.rfind(".", 0, start), text.rfind(";", 0, start), text.rfind("\n", 0, start))
    candidates = [pos for token in (".", ";", "\n") if (pos := text.find(token, end)) >= 0]
    right = min(candidates) if candidates else len(text)
    return text[left + 1 : right]


def _is_rejected_context(text: str, start: int, end: int) -> bool:
    window = _sentence_window(text, start, end)
    # Projections and market-size statements are not company financial facts.
    return bool(_PROJECTION_RE.search(window) or _NON_COMPANY_RE.search(window))


def _operator(qualifier: Any) -> str:
    text = str(qualifier or "").strip().lower().replace(".", "")
    if text in {"over", "more than", "at least"}:
        return "gte"
    if text == "up to":
        return "lte"
    if text in {"approximately", "approx", "about", "around", "nearly", "~"}:
        return "approx"
    return "eq"


def _scaled_number(raw_number: str, raw_magnitude: Any) -> float:
    number = float(raw_number.replace(",", ""))
    magnitude = str(raw_magnitude or "").strip().lower()
    multiplier = {
        "": 1.0,
        "k": 1_000.0,
        "thousand": 1_000.0,
        "m": 1_000_000.0,
        "mn": 1_000_000.0,
        "million": 1_000_000.0,
        "b": 1_000_000_000.0,
        "bn": 1_000_000_000.0,
        "billion": 1_000_000_000.0,
    }[magnitude]
    return number * multiplier


def _currency(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if text in {"$", "US$", "USD"}:
        return "USD"
    if text in {"€", "EUR"}:
        return "EUR"
    if text in {"£", "GBP"}:
        return "GBP"
    return text or "unknown"


def _base_disclosure(
    evidence: Mapping[str, Any],
    excerpt: str,
    match: re.Match[str],
    *,
    metric: str,
    canonical_field: Optional[str],
    value: Any,
    unit: str,
    definition: str,
    operator: str,
    currency: Optional[str] = None,
    definition_eligible: bool = True,
) -> dict[str, Any]:
    """Build one immutable disclosure-shaped dictionary."""
    if operator == "eq" and excerpt[match.end() : match.end() + 2].lstrip().startswith("+"):
        operator = "gte"
    verification = str(evidence.get("verification_status") or "unverified").lower()
    eligible = (
        canonical_field is not None
        and definition_eligible
        and operator == "eq"
        and verification not in {"estimated", "contradicted"}
        and (currency in (None, "USD"))
        and _parse_date(evidence.get("document_date")) is not None
    )
    source_id = evidence.get("source_id")
    disclosure_id = _stable_id(
        "disclosure",
        source_id,
        evidence.get("document_date"),
        metric,
        value,
        match.start(),
        match.group(0),
    )
    result: dict[str, Any] = {
        "disclosure_id": disclosure_id,
        "metric": metric,
        "canonical_field": canonical_field,
        "value": value,
        "unit": unit,
        "currency": currency,
        "definition": definition,
        "operator": operator,
        "eligible_for_canonical": eligible,
        "raw_value": match.group(0),
        "match_text": match.group(0),
        "evidence_excerpt": excerpt,
        "source_id": source_id,
        "source_uri": evidence.get("source_uri"),
        "source_type": evidence.get("source_type"),
        "source_name": _source_name(evidence),
        "document_name": evidence.get("document_name"),
        "document_date": evidence.get("document_date"),
        "verification_status": evidence.get("verification_status"),
        "confidence": evidence.get("confidence"),
        "_span": (match.start(), match.end()),
    }
    for key in _ENTITY_KEYS + ("snapshot_id", "opportunity_id"):
        if evidence.get(key) not in (None, ""):
            result[key] = evidence.get(key)
    return result


def _money_disclosures(
    evidence: Mapping[str, Any],
    excerpt: str,
    patterns: Sequence[re.Pattern[str]],
    *,
    metric: str,
    canonical_field: Optional[str],
    definition_for: Any,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in patterns:
        for match in pattern.finditer(excerpt):
            if _is_rejected_context(excerpt, match.start(), match.end()):
                continue
            currency = _currency(match.groupdict().get("currency"))
            value = _scaled_number(match.group("number"), match.groupdict().get("magnitude"))
            definition, eligible_definition, resolved_field = definition_for(match, excerpt)
            rows.append(
                _base_disclosure(
                    evidence,
                    excerpt,
                    match,
                    metric=metric,
                    canonical_field=resolved_field if resolved_field is not None else canonical_field,
                    value=value,
                    unit="currency",
                    definition=definition,
                    operator=_operator(match.groupdict().get("qualifier")),
                    currency=currency,
                    definition_eligible=eligible_definition,
                )
            )
    return rows


def _arr_definition(match: re.Match[str], _: str) -> tuple[str, bool, Optional[str]]:
    return "annual_recurring_revenue", True, "arr_usd"


def _revenue_definition(match: re.Match[str], excerpt: str) -> tuple[str, bool, Optional[str]]:
    label = str(match.groupdict().get("label") or "revenue").lower()
    context = _sentence_window(excerpt, match.start(), match.end()).lower()
    if "recurring" in label or label == "mrr":
        return "monthly_recurring_revenue", False, None
    if "monthly" in label:
        return "monthly_revenue", False, None
    annual_context = bool(
        "annual" in label
        or "yearly" in label
        or re.search(r"\b(?:fy\s*)?20\d{2}\b", context)
        or re.search(r"\b(?:last|past|fiscal)\s+year\b", context)
    )
    if annual_context:
        return "annual_revenue", True, "revenue_usd"
    return "revenue_period_unspecified", False, None


def _burn_definition(match: re.Match[str], _: str) -> tuple[str, bool, Optional[str]]:
    label = str(match.groupdict().get("label") or "").lower()
    frequency = str(match.groupdict().get("frequency") or "").lower().replace(" ", "")
    monthly = "monthly" in label or frequency in {"permonth", "/month", "monthly"}
    annual = frequency in {"peryear", "/year", "annually"}
    if monthly:
        return "monthly_cash_burn", True, "burn_rate_usd_monthly"
    if annual:
        return "annual_cash_burn", False, None
    return "burn_period_unspecified", False, None


def _cash_definition(match: re.Match[str], _: str) -> tuple[str, bool, Optional[str]]:
    return "cash_balance", True, "cash_balance_usd"


def extract_financial_disclosures(
    evidence_records: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Extract explicit company financial disclosures from evidence excerpts.

    Supported metrics are ARR, revenue, customer count, year-over-year growth,
    runway, burn, and cash.  Each result retains its full excerpt, source,
    document date, metric definition, raw match, comparison operator, and
    canonical-eligibility flag.

    The extractor is intentionally conservative:

    * evidence marked ``estimated`` is ignored;
    * projections, forecasts, targets, TAM, and market-size statements are
      ignored;
    * bounds and approximations are retained but are not canonical-eligible;
    * generic revenue without an annual definition is not treated as ARR or
      annual revenue;
    * unspecified customers are retained, but only explicitly *paying*
      customers can populate ``customer_count``; and
    * non-USD values are retained without FX conversion and cannot populate a
      ``*_usd`` field.

    Results are deduplicated and sorted deterministically.
    """
    extracted: list[dict[str, Any]] = []
    for evidence in evidence_records:
        excerpt = _excerpt(evidence)
        if not excerpt:
            continue
        if str(evidence.get("verification_status") or "").lower() == "estimated":
            continue

        candidates: list[dict[str, Any]] = []
        # ARR runs before generic revenue; overlapping generic-revenue matches
        # are removed below so ARR can never be silently relabelled.
        candidates.extend(
            _money_disclosures(
                evidence,
                excerpt,
                _ARR_PATTERNS,
                metric="arr",
                canonical_field="arr_usd",
                definition_for=_arr_definition,
            )
        )
        candidates.extend(
            _money_disclosures(
                evidence,
                excerpt,
                _REVENUE_PATTERNS,
                metric="revenue",
                canonical_field=None,
                definition_for=_revenue_definition,
            )
        )

        for pattern in _CUSTOMER_PATTERNS:
            for match in pattern.finditer(excerpt):
                if _is_rejected_context(excerpt, match.start(), match.end()):
                    continue
                context = _sentence_window(excerpt, match.start(), match.end()).lower()
                if re.search(r"\b(?:potential|prospective|target|registered)\s+(?:customers?|users?)\b", context):
                    continue
                value_float = _scaled_number(match.group("number"), match.groupdict().get("magnitude"))
                if not value_float.is_integer():
                    continue
                paying = bool(match.groupdict().get("paying"))
                candidates.append(
                    _base_disclosure(
                        evidence,
                        excerpt,
                        match,
                        metric="customers",
                        canonical_field="customer_count",
                        value=int(value_float),
                        unit="count",
                        definition="paying_customers" if paying else "customers_payment_status_unspecified",
                        operator=_operator(match.groupdict().get("qualifier")),
                        definition_eligible=paying,
                    )
                )

        for pattern in _GROWTH_PATTERNS:
            for match in pattern.finditer(excerpt):
                if _is_rejected_context(excerpt, match.start(), match.end()):
                    continue
                period = str(match.groupdict().get("period") or "").lower()
                yoy = bool(period and ("year" in period or "yoy" in period or "annual" in period))
                basis = str(match.groupdict().get("basis") or "revenue").lower()
                candidates.append(
                    _base_disclosure(
                        evidence,
                        excerpt,
                        match,
                        metric="growth",
                        canonical_field="revenue_growth_rate_yoy",
                        value=float(match.group("percent").replace(",", "")) / 100.0,
                        unit="fraction_per_year" if yoy else "fraction_period_unspecified",
                        definition=f"{basis}_growth_yoy" if yoy else f"{basis}_growth_period_unspecified",
                        operator=_operator(match.groupdict().get("qualifier")),
                        definition_eligible=yoy,
                    )
                )

        for pattern in _RUNWAY_PATTERNS:
            for match in pattern.finditer(excerpt):
                if _is_rejected_context(excerpt, match.start(), match.end()):
                    continue
                raw_value = _scaled_number(match.group("number"), match.groupdict().get("magnitude"))
                duration_unit = str(match.group("duration_unit")).lower()
                months = raw_value * (12.0 if duration_unit.startswith("year") else 1.0)
                candidates.append(
                    _base_disclosure(
                        evidence,
                        excerpt,
                        match,
                        metric="runway",
                        canonical_field="runway_months",
                        value=months,
                        unit="months",
                        definition="cash_runway",
                        operator=_operator(match.groupdict().get("qualifier")),
                    )
                )

        candidates.extend(
            _money_disclosures(
                evidence,
                excerpt,
                _BURN_PATTERNS,
                metric="burn",
                canonical_field=None,
                definition_for=_burn_definition,
            )
        )
        candidates.extend(
            _money_disclosures(
                evidence,
                excerpt,
                _CASH_PATTERNS,
                metric="cash",
                canonical_field="cash_balance_usd",
                definition_for=_cash_definition,
            )
        )

        # Remove generic revenue matches that overlap an ARR match, then remove
        # duplicate pattern hits.  Other metric overlaps are retained because a
        # sentence can legitimately disclose, for example, ARR and growth.
        arr_spans = [tuple(row["_span"]) for row in candidates if row["metric"] == "arr"]
        seen: set[tuple[Any, ...]] = set()
        for row in candidates:
            span = tuple(row["_span"])
            if row["metric"] == "revenue" and any(
                max(span[0], arr_span[0]) < min(span[1], arr_span[1]) for arr_span in arr_spans
            ):
                continue
            key = (
                row.get("source_id"),
                row.get("document_date"),
                row["metric"],
                row.get("canonical_field"),
                _stable_json(row["value"]),
                span,
            )
            if key in seen:
                continue
            seen.add(key)
            extracted.append(row)

    extracted.sort(
        key=lambda row: (
            str(_entity_value(row) or ""),
            str(row.get("document_date") or ""),
            str(row.get("source_id") or ""),
            str(row.get("metric") or ""),
            tuple(row.get("_span") or (0, 0)),
            str(row.get("disclosure_id") or ""),
        )
    )
    for row in extracted:
        row.pop("_span", None)
    return extracted


def _cutoff_for(snapshot: Mapping[str, Any], cutoff_date: Any = None) -> Optional[date]:
    raw = cutoff_date
    if raw is None:
        raw = snapshot.get("data_cutoff_date", snapshot.get("observation_date"))
    return _parse_date(raw)


def _numeric_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not (math.isfinite(float(left)) and math.isfinite(float(right))):
            return False
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


def apply_latest_disclosures(
    snapshot: Mapping[str, Any],
    disclosures: Iterable[Mapping[str, Any]],
    cutoff_date: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fill null flat snapshot fields from the latest eligible disclosures.

    The input mapping is not mutated.  A field is eligible only when it already
    exists in the flat snapshot and its value is exactly ``None``.  The source
    document must have a valid date on or before the snapshot cutoff, match the
    snapshot entity, and have ``eligible_for_canonical=true``.

    If multiple latest-date sources disagree, the field remains null.  Equal
    corroborating values are safe and all supporting source IDs are returned in
    the application audit row.  The function returns ``(snapshot, applied)``;
    ``applied`` is separate so evidence metadata is not mixed into model fields.
    """
    updated = dict(snapshot)
    cutoff = _cutoff_for(snapshot, cutoff_date)
    if cutoff is None:
        return updated, []

    contradicted = {str(field) for field in (snapshot.get("contradicted_fields") or [])}
    by_field: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for disclosure in disclosures:
        field = disclosure.get("canonical_field")
        if not isinstance(field, str) or field not in updated or updated.get(field) is not None:
            continue
        if field in contradicted or not disclosure.get("eligible_for_canonical"):
            continue
        if not _entity_matches(snapshot, disclosure):
            continue
        document_date = _parse_date(disclosure.get("document_date"))
        if document_date is None or document_date > cutoff:
            continue
        by_field[field].append(disclosure)

    applied: list[dict[str, Any]] = []
    for field in sorted(by_field):
        candidates = by_field[field]
        latest_date = max(_parse_date(row.get("document_date")) for row in candidates)
        latest = [row for row in candidates if _parse_date(row.get("document_date")) == latest_date]
        distinct_values: list[Any] = []
        for row in latest:
            if not any(_numeric_equal(row.get("value"), value) for value in distinct_values):
                distinct_values.append(row.get("value"))
        if len(distinct_values) != 1:
            continue
        selected = sorted(latest, key=lambda row: str(row.get("disclosure_id") or ""))[0]
        updated[field] = selected.get("value")
        applied.append(
            {
                "field": field,
                "value": selected.get("value"),
                "document_date": latest_date.isoformat(),
                "definition": selected.get("definition"),
                "supporting_source_ids": sorted(
                    {str(row["source_id"]) for row in latest if row.get("source_id") not in (None, "")}
                ),
                "supporting_disclosure_ids": sorted(
                    {
                        str(row["disclosure_id"])
                        for row in latest
                        if row.get("disclosure_id") not in (None, "")
                    }
                ),
            }
        )
    return updated, applied


def _tracked_fields(
    snapshots: Sequence[Mapping[str, Any]],
    tracked_fields: Optional[Iterable[str]],
) -> list[str]:
    if tracked_fields is not None:
        return sorted({str(field) for field in tracked_fields if str(field)})
    fields: set[str] = set(_DEFAULT_TRACKED_FINANCIAL_FIELDS)
    for snapshot in snapshots:
        for key, value in snapshot.items():
            if key in _ADMIN_FIELDS or key.startswith("_"):
                continue
            if value is None or isinstance(value, (str, int, float, bool)):
                fields.add(str(key))
        for field in snapshot.get("missing_fields") or []:
            # This module is flat by design; dotted paths belong to a nested
            # schema and are left to that schema's own missingness walker.
            if isinstance(field, str) and field and "." not in field:
                fields.add(field)
    return sorted(fields)


def recompute_missing_fields(
    snapshot: Mapping[str, Any],
    tracked_fields: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Return a copy with a deterministic ``missing_fields`` list.

    When ``tracked_fields`` is omitted, scalar top-level fields, known financial
    fields, and existing flat missing-field names are considered.  Operational
    metadata (evidence lists, errors, and contradiction lists) is excluded.
    Absent tracked fields and fields whose value is ``None`` are both missing;
    zero, ``False``, and empty strings remain observed values.
    """
    updated = dict(snapshot)
    fields = _tracked_fields([snapshot], tracked_fields)
    updated["missing_fields"] = sorted(field for field in fields if updated.get(field) is None)
    return updated


def _source_name(record: Mapping[str, Any]) -> str:
    for key in ("source_name", "source", "channel", "adapter", "_channel"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value).strip().lower()
    uri = record.get("source_uri") or record.get("url")
    if isinstance(uri, str) and uri:
        host = (urlparse(uri).hostname or "").lower()
        if host:
            return host.removeprefix("www.")
    document_name = record.get("document_name")
    if isinstance(document_name, str) and ":" in document_name:
        prefix = document_name.split(":", 1)[0].strip().lower()
        if prefix and len(prefix) <= 40:
            return prefix
    source_type = record.get("source_type")
    return str(source_type or "unknown").strip().lower()


def _attempt_success(attempt: Mapping[str, Any]) -> bool:
    if isinstance(attempt.get("success"), bool):
        return bool(attempt["success"])
    status_code = attempt.get("status_code", attempt.get("http_status"))
    try:
        if status_code is not None:
            return 200 <= int(status_code) < 400
    except (TypeError, ValueError):
        pass
    status = str(attempt.get("status") or attempt.get("result") or "").strip().lower()
    return status in {"ok", "success", "succeeded", "cached", "not_modified", "complete", "completed"}


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _source_health(
    evidence_records: Sequence[Mapping[str, Any]],
    disclosures: Sequence[Mapping[str, Any]],
    source_attempts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    evidence_counts = Counter(_source_name(row) for row in evidence_records)
    disclosure_counts = Counter(_source_name(row) for row in disclosures)
    attempts_by_source: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for attempt in source_attempts:
        attempts_by_source[_source_name(attempt)].append(attempt)

    sources = sorted(set(evidence_counts) | set(disclosure_counts) | set(attempts_by_source))
    rows: list[dict[str, Any]] = []
    for source in sources:
        attempts = attempts_by_source.get(source, [])
        successes = sum(1 for attempt in attempts if _attempt_success(attempt))
        failures = len(attempts) - successes
        request_count = 0
        cache_hits = 0
        reported_records = 0
        latencies: list[float] = []
        error_codes: Counter[str] = Counter()
        for attempt in attempts:
            for key, accumulator in (
                ("requests", "requests"),
                ("cache_hits", "cache_hits"),
                ("records", "records"),
            ):
                try:
                    count = max(0, int(attempt.get(key) or 0))
                except (TypeError, ValueError):
                    count = 0
                if accumulator == "requests":
                    request_count += count
                elif accumulator == "cache_hits":
                    cache_hits += count
                else:
                    reported_records += count
            latency = attempt.get("latency_ms", attempt.get("elapsed_ms"))
            try:
                if latency is not None and float(latency) >= 0:
                    latencies.append(float(latency))
            except (TypeError, ValueError):
                pass
            if not _attempt_success(attempt):
                code = attempt.get("error_code") or attempt.get("status") or attempt.get("result") or "unknown_error"
                error_codes[str(code).lower()] += 1
            errors = attempt.get("errors")
            if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
                for error in errors:
                    text = str(error or "unknown_error").strip().lower()
                    # Preserve a stable, compact code when SourceRun contains
                    # human-readable exception messages.
                    code = text.split(":", 1)[0].replace(" ", "_")[:80] or "unknown_error"
                    error_codes[code] += 1

        if not attempts:
            status = "observed_only" if evidence_counts[source] else "not_attempted"
            success_rate = None
        else:
            success_rate = successes / len(attempts)
            if success_rate >= 0.9 and failures == 0:
                status = "healthy" if evidence_counts[source] else "healthy_no_yield"
            elif success_rate >= 0.5:
                status = "degraded"
            else:
                status = "failing"
        rows.append(
            {
                "source": source,
                "status": status,
                "attempted": len(attempts),
                "succeeded": successes,
                "failed": failures,
                "requests": request_count,
                "cache_hits": cache_hits,
                "records_reported": reported_records,
                "success_rate": round(success_rate, 6) if success_rate is not None else None,
                "evidence_items": evidence_counts[source],
                "financial_disclosures": disclosure_counts[source],
                "disclosure_yield_per_success": (
                    round(disclosure_counts[source] / successes, 6) if successes else None
                ),
                "disclosure_yield_per_request": (
                    round(disclosure_counts[source] / request_count, 6) if request_count else None
                ),
                "latency_ms_median": round(median(latencies), 3) if latencies else None,
                "latency_ms_p95": round(_percentile(latencies, 0.95), 3) if latencies else None,
                "error_codes": [
                    {"code": code, "count": count} for code, count in sorted(error_codes.items())
                ],
            }
        )
    return rows


def _matching_disclosures(
    snapshot: Mapping[str, Any],
    field: str,
    disclosures: Sequence[Mapping[str, Any]],
    *,
    require_value_match: bool,
) -> list[Mapping[str, Any]]:
    cutoff = _cutoff_for(snapshot)
    if cutoff is None:
        return []
    rows: list[Mapping[str, Any]] = []
    for disclosure in disclosures:
        if disclosure.get("canonical_field") != field or not disclosure.get("eligible_for_canonical"):
            continue
        if not _entity_matches(snapshot, disclosure):
            continue
        document_date = _parse_date(disclosure.get("document_date"))
        if document_date is None or document_date > cutoff:
            continue
        if require_value_match and not _numeric_equal(snapshot.get(field), disclosure.get("value")):
            continue
        rows.append(disclosure)
    return rows


def _missing_reason_counts(snapshots: Sequence[Mapping[str, Any]], field: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for snapshot in snapshots:
        if snapshot.get(field) is not None:
            continue
        reasons = snapshot.get("missing_reasons", snapshot.get("missing_reason_codes", {}))
        if not isinstance(reasons, Mapping):
            continue
        value = reasons.get(field)
        if isinstance(value, Mapping):
            value = value.get("reason_code", value.get("code"))
        if isinstance(value, (list, tuple, set)):
            for item in value:
                counts[str(item).lower()] += 1
        elif value not in (None, ""):
            counts[str(value).lower()] += 1
    return counts


def _detected_disclosure_conflicts(
    disclosures: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for disclosure in disclosures:
        entity = _entity_value(disclosure)
        field = disclosure.get("canonical_field")
        document_date = _parse_date(disclosure.get("document_date"))
        if entity is None or not isinstance(field, str) or document_date is None:
            continue
        if not disclosure.get("eligible_for_canonical"):
            continue
        groups[(entity, field, document_date.isoformat())].append(disclosure)

    conflicts: list[dict[str, Any]] = []
    for (entity, field, document_date), rows in sorted(groups.items()):
        values: list[Any] = []
        for row in rows:
            if not any(_numeric_equal(row.get("value"), value) for value in values):
                values.append(row.get("value"))
        if len(values) <= 1:
            continue
        conflicts.append(
            {
                "entity_id": entity,
                "field": field,
                "document_date": document_date,
                "values": sorted(values, key=_stable_json),
                "source_ids": sorted(
                    {str(row["source_id"]) for row in rows if row.get("source_id") not in (None, "")}
                ),
                "disclosure_ids": sorted(
                    {
                        str(row["disclosure_id"])
                        for row in rows
                        if row.get("disclosure_id") not in (None, "")
                    }
                ),
            }
        )
    return conflicts


def _reason_and_action(
    field: str,
    *,
    missing_count: int,
    missing_reasons: Counter[str],
    contradiction_rate: float,
    stale_rate: float,
) -> tuple[str, str]:
    source_failures = sum(
        count
        for reason, count in missing_reasons.items()
        if reason in {"source_unavailable", "source_error", "parse_failed", "timeout", "rate_limited"}
    )
    if missing_count and source_failures / missing_count >= 0.5:
        reason = "source_pipeline_failure"
    elif contradiction_rate > 0:
        reason = "contradicted_disclosures"
    elif stale_rate >= 0.5 and stale_rate > 0:
        reason = "stale_disclosure"
    elif field in _PRIVATE_FINANCIAL_FIELDS:
        reason = "private_financial_disclosure"
    elif field in _SPARSE_PUBLIC_FIELDS:
        reason = "sparse_public_disclosure"
    else:
        reason = "low_coverage"
    return reason, _REASON_ACTION[reason]


def assess_data_quality(
    snapshots: Iterable[Mapping[str, Any]],
    evidence_records: Iterable[Mapping[str, Any]] = (),
    disclosures: Optional[Iterable[Mapping[str, Any]]] = None,
    source_attempts: Iterable[Mapping[str, Any]] = (),
    *,
    tracked_fields: Optional[Iterable[str]] = None,
    stale_after_days: int = 365,
    hard_coverage_threshold: float = 0.60,
) -> dict[str, Any]:
    """Assess coverage, source health, contradictions, and evidence staleness.

    ``source_attempts`` is optional and may contain dictionaries from any
    scraper as long as they expose a source-ish key plus ``success``, HTTP
    status, or a textual status.  Without attempt telemetry, evidence counts
    remain useful and source status is reported as ``observed_only``.

    Hard-to-obtain fields are ranked by a transparent difficulty score:
    70% missingness, 15% contradiction rate, 10% stale-support rate, and 5%
    unsupported-present rate.  The report contains stable reason/action codes;
    it never writes or proposes an estimated canonical value.
    """
    snapshot_rows = [dict(row) for row in snapshots]
    evidence_rows = [dict(row) for row in evidence_records]
    disclosure_rows = (
        [dict(row) for row in disclosures]
        if disclosures is not None
        else extract_financial_disclosures(evidence_rows)
    )
    attempt_rows = [dict(row) for row in source_attempts]
    fields = _tracked_fields(snapshot_rows, tracked_fields)
    record_count = len(snapshot_rows)

    detected_conflicts = _detected_disclosure_conflicts(disclosure_rows)
    detected_by_field = Counter(row["field"] for row in detected_conflicts)
    declared_by_field: Counter[str] = Counter()
    declared_records: list[dict[str, Any]] = []
    for snapshot in snapshot_rows:
        declared = sorted({str(field) for field in (snapshot.get("contradicted_fields") or [])})
        if declared:
            declared_records.append(
                {
                    "entity_id": _entity_value(snapshot),
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "fields": declared,
                }
            )
        declared_by_field.update(declared)

    coverage_rows: list[dict[str, Any]] = []
    staleness_rows: list[dict[str, Any]] = []
    metrics_by_field: dict[str, dict[str, Any]] = {}
    for field in fields:
        present = sum(1 for snapshot in snapshot_rows if snapshot.get(field) is not None)
        missing = record_count - present
        supported = 0
        stale = 0
        fresh = 0
        unknown_source_date = 0
        ages: list[int] = []
        for snapshot in snapshot_rows:
            if snapshot.get(field) is None:
                continue
            matching = _matching_disclosures(
                snapshot, field, disclosure_rows, require_value_match=True
            )
            if not matching:
                unknown_source_date += 1
                continue
            latest = max(_parse_date(row.get("document_date")) for row in matching)
            cutoff = _cutoff_for(snapshot)
            if latest is None or cutoff is None:
                unknown_source_date += 1
                continue
            supported += 1
            age = max(0, (cutoff - latest).days)
            ages.append(age)
            if age > stale_after_days:
                stale += 1
            else:
                fresh += 1

        coverage_rate = present / record_count if record_count else 0.0
        support_rate = supported / present if present else None
        unsupported = max(0, present - supported)
        coverage_row = {
            "field": field,
            "present": present,
            "missing": missing,
            "coverage_rate": round(coverage_rate, 6),
            "evidence_supported_present": supported,
            "unsupported_present": unsupported,
            "support_rate": round(support_rate, 6) if support_rate is not None else None,
        }
        coverage_rows.append(coverage_row)
        stale_rate = stale / supported if supported else 0.0
        staleness_row = {
            "field": field,
            "supported_values": supported,
            "fresh": fresh,
            "stale": stale,
            "stale_rate": round(stale_rate, 6) if supported else None,
            "unknown_source_date": unknown_source_date,
            "age_days_median": round(median(ages), 3) if ages else None,
            "age_days_p95": _percentile([float(age) for age in ages], 0.95),
            "stale_after_days": stale_after_days,
        }
        staleness_rows.append(staleness_row)
        contradiction_count = declared_by_field[field] + detected_by_field[field]
        contradiction_rate = min(1.0, contradiction_count / record_count) if record_count else 0.0
        metrics_by_field[field] = {
            "present": present,
            "missing": missing,
            "coverage_rate": coverage_rate,
            "unsupported": unsupported,
            "unsupported_rate": unsupported / present if present else 0.0,
            "stale_rate": stale_rate,
            "contradiction_count": contradiction_count,
            "contradiction_rate": contradiction_rate,
            "missing_reasons": _missing_reason_counts(snapshot_rows, field),
        }

    hard_rows: list[dict[str, Any]] = []
    for field, metrics in metrics_by_field.items():
        if metrics["coverage_rate"] > hard_coverage_threshold:
            continue
        reason, action = _reason_and_action(
            field,
            missing_count=metrics["missing"],
            missing_reasons=metrics["missing_reasons"],
            contradiction_rate=metrics["contradiction_rate"],
            stale_rate=metrics["stale_rate"],
        )
        score = (
            0.70 * (1.0 - metrics["coverage_rate"])
            + 0.15 * metrics["contradiction_rate"]
            + 0.10 * metrics["stale_rate"]
            + 0.05 * metrics["unsupported_rate"]
        )
        hard_rows.append(
            {
                "field": field,
                "difficulty_score": round(score, 6),
                "coverage_rate": round(metrics["coverage_rate"], 6),
                "missing": metrics["missing"],
                "contradictions": metrics["contradiction_count"],
                "stale_rate": round(metrics["stale_rate"], 6),
                "unsupported_present": metrics["unsupported"],
                "reason_code": reason,
                "action_code": action,
                "observed_missing_reason_codes": [
                    {"code": code, "count": count}
                    for code, count in sorted(metrics["missing_reasons"].items())
                ],
                "source_targets": list(_SOURCE_TARGETS.get(field, [])),
            }
        )
    hard_rows.sort(key=lambda row: (-row["difficulty_score"], row["field"]))
    for rank, row in enumerate(hard_rows, start=1):
        row["rank"] = rank

    field_contradictions = [
        {
            "field": field,
            "declared_records": declared_by_field[field],
            "detected_same_date_conflicts": detected_by_field[field],
        }
        for field in sorted(set(declared_by_field) | set(detected_by_field))
    ]
    return {
        "record_count": record_count,
        "parameters": {
            "stale_after_days": stale_after_days,
            "hard_coverage_threshold": hard_coverage_threshold,
        },
        "coverage": coverage_rows,
        "source_health": _source_health(evidence_rows, disclosure_rows, attempt_rows),
        "contradictions": {
            "declared_records": sorted(
                declared_records,
                key=lambda row: (
                    str(row.get("entity_id") or ""),
                    str(row.get("snapshot_id") or ""),
                ),
            ),
            "detected_disclosure_conflicts": detected_conflicts,
            "fields": field_contradictions,
        },
        "staleness": staleness_rows,
        "hard_to_obtain": hard_rows,
    }


def _record_key(snapshot: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(_entity_value(snapshot) or ""),
        str(snapshot.get("snapshot_id") or ""),
        str(snapshot.get("observation_date") or ""),
        _stable_json(snapshot),
    )


def _specific_staleness(
    snapshot: Mapping[str, Any],
    field: str,
    disclosures: Sequence[Mapping[str, Any]],
    stale_after_days: int,
) -> Optional[int]:
    if snapshot.get(field) is None:
        return None
    matching = _matching_disclosures(snapshot, field, disclosures, require_value_match=True)
    cutoff = _cutoff_for(snapshot)
    if not matching or cutoff is None:
        return None
    latest = max(_parse_date(row.get("document_date")) for row in matching)
    if latest is None:
        return None
    age = max(0, (cutoff - latest).days)
    return age if age > stale_after_days else None


def build_enrichment_queue(
    snapshots: Iterable[Mapping[str, Any]],
    assessment: Mapping[str, Any],
    disclosures: Iterable[Mapping[str, Any]] = (),
    *,
    stale_after_days: Optional[int] = None,
) -> list[dict[str, Any]]:
    """Build deterministic field-level enrichment work items.

    Queue rows are created for null hard-to-obtain fields, declared
    contradictions, stale supported values, and present financial values that
    lack matching explicit evidence.  Rows contain actions and source targets,
    never proposed or estimated values.
    """
    snapshot_rows = sorted((dict(row) for row in snapshots), key=_record_key)
    disclosure_rows = [dict(row) for row in disclosures]
    hard_fields = {
        str(row["field"]): dict(row) for row in (assessment.get("hard_to_obtain") or [])
    }
    coverage = {str(row["field"]): dict(row) for row in (assessment.get("coverage") or [])}
    if stale_after_days is None:
        stale_after_days = int(
            (assessment.get("parameters") or {}).get("stale_after_days", 365)
        )

    queue: list[dict[str, Any]] = []
    for snapshot in snapshot_rows:
        contradicted = {str(field) for field in (snapshot.get("contradicted_fields") or [])}
        present_financial_schema = {field for field in FINANCIAL_FIELDS if field in snapshot}
        fields = sorted(set(hard_fields) | contradicted | present_financial_schema)
        for field in fields:
            value = snapshot.get(field)
            hard = hard_fields.get(field, {})
            reason: Optional[str] = None
            action: Optional[str] = None
            age_days: Optional[int] = None
            if field in contradicted:
                reason = "contradicted_disclosures"
                action = _REASON_ACTION[reason]
                priority_score = 100
            elif value is None and (field in hard_fields or field in FINANCIAL_FIELDS):
                if hard:
                    reason = str(hard.get("reason_code") or "low_coverage")
                    action = str(hard.get("action_code") or _REASON_ACTION["low_coverage"])
                    # Cohort-level contradiction/staleness may belong to a
                    # different company.  A plain null field should retain its
                    # own acquisition reason rather than inherit that global
                    # remediation action.
                    if reason in {"contradicted_disclosures", "stale_disclosure"}:
                        if field in _PRIVATE_FINANCIAL_FIELDS:
                            reason = "private_financial_disclosure"
                        elif field in _SPARSE_PUBLIC_FIELDS:
                            reason = "sparse_public_disclosure"
                        else:
                            reason = "low_coverage"
                        action = _REASON_ACTION[reason]
                elif field in _PRIVATE_FINANCIAL_FIELDS:
                    reason = "private_financial_disclosure"
                    action = _REASON_ACTION[reason]
                else:
                    reason = "sparse_public_disclosure"
                    action = _REASON_ACTION[reason]
                priority_score = min(95, 80 + max(0, 10 - int(hard.get("rank") or 10)))
            elif value is not None:
                age_days = _specific_staleness(
                    snapshot, field, disclosure_rows, stale_after_days
                )
                matching = _matching_disclosures(
                    snapshot, field, disclosure_rows, require_value_match=True
                )
                if age_days is not None:
                    reason = "stale_disclosure"
                    action = _REASON_ACTION[reason]
                    priority_score = min(85, 60 + age_days // max(stale_after_days, 1))
                elif field in FINANCIAL_FIELDS and not matching:
                    reason = "missing_supporting_evidence"
                    action = _REASON_ACTION[reason]
                    priority_score = 70
                else:
                    continue
            else:
                continue

            if priority_score >= 90:
                priority = "critical"
            elif priority_score >= 75:
                priority = "high"
            elif priority_score >= 50:
                priority = "medium"
            else:
                priority = "low"
            entity = _entity_value(snapshot)
            row = {
                "queue_id": _stable_id(
                    "enrich",
                    entity,
                    snapshot.get("snapshot_id"),
                    field,
                    reason,
                    snapshot.get("data_cutoff_date", snapshot.get("observation_date")),
                ),
                "entity_id": entity,
                "startup_id": snapshot.get("startup_id"),
                "company_id": snapshot.get("company_id"),
                "company_name": snapshot.get("company_name", snapshot.get("name")),
                "snapshot_id": snapshot.get("snapshot_id"),
                "data_cutoff_date": snapshot.get(
                    "data_cutoff_date", snapshot.get("observation_date")
                ),
                "field": field,
                "current_value": value,
                "priority": priority,
                "priority_score": int(priority_score),
                "reason_code": reason,
                "action_code": action,
                "source_targets": list(
                    hard.get("source_targets") or _SOURCE_TARGETS.get(field, [])
                ),
                "field_coverage_rate": (coverage.get(field) or {}).get("coverage_rate"),
                "evidence_age_days": age_days,
                "status": "pending",
            }
            queue.append(row)

    queue.sort(
        key=lambda row: (
            -row["priority_score"],
            str(row.get("entity_id") or ""),
            str(row.get("snapshot_id") or ""),
            row["field"],
            row["reason_code"],
        )
    )
    for rank, row in enumerate(queue, start=1):
        row["queue_rank"] = rank
    return queue


def postprocess_snapshots(
    snapshots: Iterable[Mapping[str, Any]],
    evidence_records: Iterable[Mapping[str, Any]],
    source_attempts: Iterable[Mapping[str, Any]] = (),
    *,
    tracked_fields: Optional[Iterable[str]] = None,
    stale_after_days: int = 365,
    hard_coverage_threshold: float = 0.60,
) -> dict[str, Any]:
    """Run the complete evidence-only postprocessing workflow.

    Returns sorted snapshots, extracted disclosures, an application audit,
    quality assessment, and enrichment queue.  Inputs are never mutated and no
    current wall-clock time is consulted, keeping fixture and rerun output
    deterministic.
    """
    snapshot_rows = sorted((dict(row) for row in snapshots), key=_record_key)
    evidence_rows = [dict(row) for row in evidence_records]
    attempt_rows = [dict(row) for row in source_attempts]
    disclosures = extract_financial_disclosures(evidence_rows)

    processed: list[dict[str, Any]] = []
    application_audit: list[dict[str, Any]] = []
    for snapshot in snapshot_rows:
        updated, applied = apply_latest_disclosures(snapshot, disclosures)
        updated = recompute_missing_fields(updated, tracked_fields)
        processed.append(updated)
        for row in applied:
            audit = dict(row)
            audit.update(
                {
                    "entity_id": _entity_value(snapshot),
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "data_cutoff_date": snapshot.get(
                        "data_cutoff_date", snapshot.get("observation_date")
                    ),
                }
            )
            application_audit.append(audit)

    application_audit.sort(
        key=lambda row: (
            str(row.get("entity_id") or ""),
            str(row.get("snapshot_id") or ""),
            str(row.get("field") or ""),
            str(row.get("document_date") or ""),
        )
    )
    assessment = assess_data_quality(
        processed,
        evidence_rows,
        disclosures,
        attempt_rows,
        tracked_fields=tracked_fields,
        stale_after_days=stale_after_days,
        hard_coverage_threshold=hard_coverage_threshold,
    )
    queue = build_enrichment_queue(
        processed,
        assessment,
        disclosures,
        stale_after_days=stale_after_days,
    )
    return {
        "snapshots": processed,
        "financial_disclosures": disclosures,
        "applied_disclosures": application_audit,
        "assessment": assessment,
        "enrichment_queue": queue,
    }
