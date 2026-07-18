"""Product Hunt enrichment via the official GraphQL API (token-gated).

Requires PRODUCTHUNT_TOKEN (a developer access token). Without it, enrichment is
skipped gracefully and all signals stay null — never fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .. import config
from ..http_client import HttpError, post_json
from ..util import parse_date

API = "https://api.producthunt.com/v2/api/graphql"

_QUERY = """
query($q: String!) {
  posts(first: 5, order: VOTES, postedBefore: null) {
    edges { node { name tagline votesCount commentsCount createdAt url } }
  }
}
""".strip()


@dataclass
class PHSignals:
    launches: Optional[int] = None
    total_votes: Optional[int] = None
    top_votes: Optional[int] = None
    first_launch_date: Optional[str] = None
    evidence: list = field(default_factory=list)
    available: bool = False


def enrich(company, cutoff: Optional[date] = None) -> PHSignals:
    sig = PHSignals()
    if not config.PRODUCTHUNT_TOKEN:
        return sig
    headers = {"Authorization": f"Bearer {config.PRODUCTHUNT_TOKEN}"}
    try:
        data = post_json(API, {"query": _QUERY, "variables": {"q": company.name}}, headers=headers)
    except HttpError:
        return sig
    if not data:
        return sig
    edges = (((data.get("data") or {}).get("posts") or {}).get("edges")) or []
    name_l = company.name.lower()
    nodes = [e["node"] for e in edges if name_l in (e["node"].get("name") or "").lower()]
    if cutoff:
        nodes = [n for n in nodes if (parse_date(n.get("createdAt")) or cutoff) <= cutoff]
    if not nodes:
        return sig
    sig.available = True
    sig.launches = len(nodes)
    sig.total_votes = sum(int(n.get("votesCount") or 0) for n in nodes)
    top = max(nodes, key=lambda n: n.get("votesCount") or 0)
    sig.top_votes = top.get("votesCount")
    dates = sorted((n.get("createdAt", "")[:10] for n in nodes if n.get("createdAt")))
    if dates:
        sig.first_launch_date = dates[0]
    sig.evidence.append((
        f'Product Hunt: "{top.get("name")}" — {top.get("votesCount")} votes '
        f'({top.get("createdAt","")[:10]}).',
        top.get("url"),
        top.get("createdAt", "")[:10] or None,
    ))
    return sig
