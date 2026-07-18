"""Evidence registry: one record per source document/item (requirements §6).

Also centralises the `verification_status` assignment rule from plan §2.
Thread-safe so parallel workers can register evidence concurrently.
"""

from __future__ import annotations

import threading
from typing import Optional

from . import config
from .util import source_id

ALLOWED_SOURCE_TYPES = {
    "application", "pitch_deck", "founder_interview", "financial_statement",
    "bank_or_payment_record", "cap_table", "financing_document",
    "company_website", "regulatory_filing", "internal_investment_record",
    "internal_monitoring_record", "manual_research", "other",
}
ALLOWED_VERIFICATION = {
    "independently_verified", "document_verified", "founder_reported",
    "estimated", "unverified", "contradicted",
}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}

# plan §2: verification_status assignment rule, keyed by our internal channel.
CHANNEL_VERIFICATION = {
    "sec": "independently_verified",
    "uspto": "independently_verified",
    "regulatory": "independently_verified",
    "yc": "document_verified",          # curated accelerator directory
    "github": "document_verified",      # dated, machine-readable, verifiable
    "hackernews": "document_verified",
    "producthunt": "document_verified",
    "press": "unverified",
    "founder": "founder_reported",
    "computed": "estimated",            # any of our derived axes/scores
    "llm": "estimated",
}


class EvidenceRegistry:
    """Collects evidence records and hands back stable source_ids."""

    def __init__(self):
        self._records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(
        self,
        *,
        startup_id: str,
        channel: str,
        source_type: str,
        document_name: str,
        excerpt: str,
        source_uri: Optional[str] = None,
        document_date: Optional[str] = None,
        location: Optional[str] = None,
        confidence: str = "medium",
        verification_status: Optional[str] = None,
    ) -> str:
        """Register one evidence item; returns its source_id (deduped by content)."""
        if source_type not in ALLOWED_SOURCE_TYPES:
            source_type = "other"
        if verification_status is None:
            verification_status = CHANNEL_VERIFICATION.get(channel, "unverified")
        if verification_status not in ALLOWED_VERIFICATION:
            verification_status = "unverified"
        if confidence not in ALLOWED_CONFIDENCE:
            confidence = "low"

        sid = source_id(startup_id, channel, document_name, excerpt[:80])
        rec = {
            "source_id": sid,
            "startup_id": startup_id,
            "source_type": source_type,
            "document_name": document_name,
            "source_uri": source_uri,
            "document_date": document_date,
            "collected_at": config.now_iso(),
            "location": location,
            "evidence_excerpt": excerpt[:600],
            "verification_status": verification_status,
            "confidence": confidence,
            "_channel": channel,  # internal; stripped on write
        }
        with self._lock:
            self._records[sid] = rec
        return sid

    def records(self) -> list[dict]:
        with self._lock:
            out = []
            for r in self._records.values():
                r = dict(r)
                r.pop("_channel", None)
                out.append(r)
            return out
