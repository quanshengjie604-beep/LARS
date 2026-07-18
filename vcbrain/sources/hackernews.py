"""Hacker News enrichment via the Algolia HN Search API (no auth).

Public-footprint signals: mentions, aggregate points/comments. Fetch once, then
derive point-in-time views by filtering `created_at <= cutoff` client-side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..http_client import HttpError, get_json
from ..util import parse_date

API = "http://hn.algolia.com/api/v1/search"


@dataclass
class HNSignals:
    mentions: Optional[int] = None
    total_points: Optional[int] = None
    total_comments: Optional[int] = None
    top_points: Optional[int] = None
    first_mention_date: Optional[str] = None
    evidence: list = field(default_factory=list)
    available: bool = False


def fetch(company) -> list[dict]:
    """Fetch candidate HN stories mentioning the company (one call, cached)."""
    try:
        data = get_json(API, params={"query": f'"{company.name}"', "tags": "story", "hitsPerPage": "50"})
    except HttpError:
        return []
    hits = (data or {}).get("hits") or []
    name_l = company.name.lower()
    relevant = [h for h in hits if name_l in (h.get("title") or "").lower()]
    return relevant


def signals(hits: list[dict], cutoff: Optional[date] = None) -> HNSignals:
    sig = HNSignals()
    rel = hits
    if cutoff:
        rel = [h for h in hits if (parse_date(h.get("created_at")) or cutoff) <= cutoff]
    if not rel:
        return sig
    sig.available = True
    sig.mentions = len(rel)
    sig.total_points = sum(int(h.get("points") or 0) for h in rel)
    sig.total_comments = sum(int(h.get("num_comments") or 0) for h in rel)
    top = max(rel, key=lambda h: h.get("points") or 0)
    sig.top_points = top.get("points")
    dates = sorted(h.get("created_at", "")[:10] for h in rel if h.get("created_at"))
    if dates:
        sig.first_mention_date = dates[0]
    oid = top.get("objectID")
    sig.evidence.append((
        f'HN: "{top.get("title")}" — {top.get("points")} points, {top.get("num_comments")} comments '
        f'({top.get("created_at","")[:10]}). {sig.mentions} total mentions.',
        f"https://news.ycombinator.com/item?id={oid}" if oid else None,
        top.get("created_at", "")[:10] or None,
    ))
    return sig


def enrich(company, cutoff: Optional[date] = None) -> HNSignals:
    return signals(fetch(company), cutoff)
