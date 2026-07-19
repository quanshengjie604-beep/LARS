"""Evidence construction and deterministic claim-level de-duplication."""

from __future__ import annotations

import hashlib
from typing import Iterable

from .models import Evidence
from .util import stable_id

ALLOWED_SOURCE_TYPES = {
    "application",
    "pitch_deck",
    "founder_interview",
    "financial_statement",
    "bank_or_payment_record",
    "cap_table",
    "financing_document",
    "company_website",
    "regulatory_filing",
    "internal_investment_record",
    "internal_monitoring_record",
    "manual_research",
    "other",
}
ALLOWED_VERIFICATION = {
    "independently_verified",
    "document_verified",
    "founder_reported",
    "estimated",
    "unverified",
    "contradicted",
}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def make_evidence(
    *,
    startup_id: str,
    channel: str,
    source_type: str,
    document_name: str,
    excerpt: str,
    collected_at: str,
    source_uri: str | None = None,
    document_date: str | None = None,
    location: str | None = None,
    verification_status: str = "unverified",
    confidence: str = "low",
    raw_content: str | bytes | None = None,
) -> Evidence:
    """Build an immutable evidence record whose ID includes the claim date/content.

    Including the date prevents live and historical claims from overwriting one
    another, a subtle source of point-in-time leakage in the earlier prototype.
    """
    if source_type not in ALLOWED_SOURCE_TYPES:
        source_type = "other"
    if verification_status not in ALLOWED_VERIFICATION:
        verification_status = "unverified"
    if confidence not in ALLOWED_CONFIDENCE:
        confidence = "low"
    clean_excerpt = " ".join((excerpt or "").split())[:1000]
    content_hash = None
    if raw_content is not None:
        raw_bytes = raw_content if isinstance(raw_content, bytes) else raw_content.encode("utf-8")
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
    source_id = stable_id(
        "source",
        startup_id,
        channel,
        source_uri or document_name,
        document_date or "undated",
        clean_excerpt,
    )
    return Evidence(
        source_id=source_id,
        startup_id=startup_id,
        source_type=source_type,
        document_name=document_name,
        source_uri=source_uri,
        document_date=document_date,
        collected_at=collected_at,
        location=location,
        evidence_excerpt=clean_excerpt,
        verification_status=verification_status,
        confidence=confidence,
        channel=channel,
        content_hash=content_hash,
    )

def dedupe_evidence(records: Iterable[Evidence]) -> list[Evidence]:
    unique = {record.source_id: record for record in records}
    return sorted(
        unique.values(),
        key=lambda item: (
            item.startup_id,
            item.document_date or "9999-99-99",
            item.channel or "",
            item.source_id,
        ),
    )
