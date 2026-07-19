"""Conservative, deterministic extraction of funding claims from short text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from .models import Evidence, FundingRound
from .util import normalized_name, scaled_number

_FUNDING_ACTION = re.compile(
    r"\b(?:raise[sd]?|raising|secure[sd]?|close[sd]?|closing|land(?:s|ed)?|"
    r"snag(?:s|ged)?|bag(?:s|ged)?|receive[sd]?|announce[sd]?|funded|funding)\b",
    re.I,
)
_ROUND_CONTEXT = re.compile(
    r"\b(?:pre[- ]?seed|seed|series\s+[a-j]|venture\s+round|financing|funding\s+round|round)\b",
    re.I,
)
_AMOUNT_AFTER_ACTION = re.compile(
    r"\b(?:raise[sd]?|raising|secure[sd]?|close[sd]?|closing|land(?:s|ed)?|"
    r"snag(?:s|ged)?|bag(?:s|ged)?|receive[sd]?)\b"
    r"(?:\s+(?:an?|its|a\s+new|approximately|about|over|nearly|up\s+to))*\s*"
    r"(?P<currency>US\$|USD\s*|\$|€|£)\s*(?P<number>\d[\d,.]*)\s*"
    r"(?P<suffix>billion|million|thousand|bn|mm|[bmk])?\b",
    re.I,
)
_GENERIC_AMOUNT = re.compile(
    r"(?P<currency>US\$|USD\s*|\$|€|£)\s*(?P<number>\d[\d,.]*)\s*"
    r"(?P<suffix>billion|million|thousand|bn|mm|[bmk])?\b",
    re.I,
)
_TOTAL_ONLY = re.compile(r"\b(?:total|bringing\s+(?:its\s+)?total|raised\s+to\s+date)\b", re.I)
_VALUATION = re.compile(r"\b(?:valuation|valued\s+at|post[- ]money|pre[- ]money)\b", re.I)
_STAGE = re.compile(r"\b(pre[- ]?seed|seed|series\s+[a-j]|angel|venture\s+round)\b", re.I)

_CURRENCY = {"$": "USD", "us$": "USD", "usd": "USD", "€": "EUR", "£": "GBP"}


def normalize_stage(raw: str | None) -> str | None:
    if not raw:
        return None
    stage = re.sub(r"\s+", "_", raw.strip().casefold().replace("-", "_"))
    if stage == "pre_seed":
        return "pre_seed"
    if stage == "seed" or stage == "angel":
        return "seed"
    if stage.startswith("series_"):
        letter = stage[-1]
        return {"a": "series_a", "b": "series_b"}.get(letter, "series_c_plus")
    return "unknown"


def _company_mentioned(company_name: str, text: str) -> bool:
    normalized = normalized_name(company_name)
    if len(normalized) < 4:
        return bool(re.search(rf"(?<!\w){re.escape(company_name)}(?!\w)", text, re.I))
    words = [word for word in re.split(r"[^a-z0-9]+", company_name.casefold()) if len(word) >= 3]
    return bool(words) and all(re.search(rf"\b{re.escape(word)}\b", text, re.I) for word in words[:2])


def _round_amount(text: str) -> tuple[int | float | None, int | float | None, str | None]:
    match = _AMOUNT_AFTER_ACTION.search(text)
    if match is None:
        # A stage-qualified headline such as "Foo's $5M seed round" is useful,
        # but never use a generic amount that is explicitly a valuation/total.
        generic = _GENERIC_AMOUNT.search(text)
        if generic and _ROUND_CONTEXT.search(text):
            prefix = text[max(0, generic.start() - 45) : generic.start()]
            suffix = text[generic.end() : generic.end() + 45]
            if not _TOTAL_ONLY.search(prefix + suffix) and not _VALUATION.search(prefix + suffix):
                match = generic
    if match is None:
        return None, None, None
    currency_token = re.sub(r"\s+", "", match.group("currency")).casefold()
    currency = _CURRENCY.get(currency_token)
    amount = scaled_number(match.group("number"), match.group("suffix") or "")
    amount_usd = amount if currency == "USD" else None
    return amount_usd, amount, currency


def extract_funding_round(
    *,
    company_name: str,
    startup_id: str,
    text: str,
    document_date: str | None,
    date_basis: str,
    source_name: str,
    source_url: str | None,
    source_id: str,
    verification_status: str = "unverified",
    confidence: str = "low",
) -> FundingRound | None:
    """Extract one explicit financing event; return ``None`` for mere launches.

    The document date is an announcement/filing date, not silently re-labelled
    as a closing date. Undated claims are retained as evidence but cannot become
    a canonical round because the handover is event/time-series based.
    """
    compact = " ".join((text or "").split())
    if not compact or not document_date or not _company_mentioned(company_name, compact):
        return None
    if not _FUNDING_ACTION.search(compact) or not (_ROUND_CONTEXT.search(compact) or _AMOUNT_AFTER_ACTION.search(compact)):
        return None
    stage_match = _STAGE.search(compact)
    raw_stage = stage_match.group(1) if stage_match else None
    amount_usd, original_amount, currency = _round_amount(compact)
    instrument = None
    if re.search(r"\b(?:debt|credit|loan)\b", compact, re.I):
        instrument = "debt"
    elif re.search(r"\bgrant\b", compact, re.I):
        instrument = "grant"
    elif re.search(r"\b(?:equity|venture|seed|series)\b", compact, re.I):
        instrument = "equity"
    return FundingRound(
        startup_id=startup_id,
        company_name=company_name,
        date=document_date,
        date_basis=date_basis,
        stage=normalize_stage(raw_stage),
        raw_stage=raw_stage,
        amount_usd=amount_usd,
        amount_original=original_amount,
        currency=currency,
        instrument=instrument,
        announced_date=document_date if date_basis == "announcement" else None,
        filed_date=document_date if date_basis == "filing" else None,
        source_ids=[source_id],
        source_names=[source_name],
        source_urls=[source_url] if source_url else [],
        headline=compact[:500],
        verification_status=verification_status,
        confidence=confidence,
    )


def dedupe_funding_rounds(rounds: Iterable[FundingRound]) -> list[FundingRound]:
    """Merge source corroboration while retaining contradictions explicitly."""
    groups: dict[tuple[str, str, str], list[FundingRound]] = {}
    for round_ in rounds:
        key = (round_.startup_id, round_.date, round_.stage or "unknown")
        groups.setdefault(key, []).append(round_)

    result: list[FundingRound] = []
    verification_rank = {"independently_verified": 3, "document_verified": 2, "unverified": 1, "estimated": 0}
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    for key, items in sorted(groups.items()):
        amounts = {item.amount_usd for item in items if item.amount_usd is not None}
        currencies = {item.currency for item in items if item.currency}
        # Multiple differently described same-day events may be extensions. Only
        # merge when their stage matches (the group key) and mark amount conflict.
        base = max(
            items,
            key=lambda item: (
                verification_rank.get(item.verification_status, 0),
                confidence_rank.get(item.confidence, 0),
                item.amount_usd is not None,
                item.round_id,
            ),
        )
        contradicted = len(amounts) > 1
        amount_usd = next(iter(amounts)) if len(amounts) == 1 else None
        original_values = {item.amount_original for item in items if item.amount_original is not None}
        original_amount = next(iter(original_values)) if len(original_values) == 1 else None
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        result.append(
            FundingRound(
                startup_id=base.startup_id,
                company_name=base.company_name,
                date=base.date,
                date_basis=base.date_basis,
                stage=base.stage,
                raw_stage=base.raw_stage,
                amount_usd=amount_usd,
                amount_original=original_amount,
                currency=currency,
                instrument=base.instrument,
                announced_date=base.announced_date,
                close_date=base.close_date,
                filed_date=base.filed_date,
                source_ids=sorted({value for item in items for value in item.source_ids}),
                source_names=sorted({value for item in items for value in item.source_names}, key=str.casefold),
                source_urls=sorted({value for item in items for value in item.source_urls}),
                headline=base.headline,
                verification_status="contradicted" if contradicted else base.verification_status,
                confidence="low" if contradicted else base.confidence,
                contradicted=contradicted,
            )
        )
    return sorted(result, key=lambda item: (item.startup_id, item.date, item.stage or "", item.round_id))

