"""Per-company enrichment through official/public APIs.

HN and Product Hunt provide launch/attention signals. They become funding
events only when their dated text contains an explicit financing statement.
Current votes, points, and stars are live-only signals and are never backdated.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from ..config import Settings
from ..evidence import make_evidence
from ..extract import extract_funding_round
from ..http import Fetcher
from ..models import Company, EnrichmentResult
from ..util import iso_date, normalized_name

HN_SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/{item_id}.json"
PRODUCT_HUNT_API = "https://api.producthunt.com/v2/api/graphql"
GITHUB_API = "https://api.github.com"


def _epoch_date(value: Any) -> str | None:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _name_in_text(name: str, text: str) -> bool:
    words = [part for part in re.split(r"[^a-z0-9]+", name.casefold()) if len(part) >= 3]
    if not words:
        return False
    if len(normalized_name(name)) < 5:
        return bool(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.I))
    return all(re.search(rf"\b{re.escape(word)}\b", text, re.I) for word in words[:2])


async def enrich_hackernews(
    client: Fetcher,
    company: Company,
    *,
    settings: Settings,
    collected_at: str,
    max_hits: int = 40,
) -> EnrichmentResult:
    result = EnrichmentResult(source="hackernews", startup_id=company.startup_id)
    cutoff_epoch = int(datetime.combine(settings.as_of, datetime.min.time(), tzinfo=timezone.utc).timestamp()) + 86399
    try:
        payload = await client.get_json(
            HN_SEARCH,
            params={
                "query": company.name,
                "tags": "story",
                "hitsPerPage": str(max_hits),
                "numericFilters": f"created_at_i<={cutoff_epoch}",
            },
        )
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    hits = payload.get("hits", []) if isinstance(payload, dict) else []
    candidates = [hit for hit in hits if _name_in_text(company.name, str(hit.get("title") or ""))]
    candidates.sort(key=lambda hit: (-(int(hit.get("points") or 0)), str(hit.get("objectID") or "")))
    # Canonical HN item bodies are effectively immutable and make stronger
    # evidence than search-index snippets. Bound this fan-out per company.
    selected = candidates[:5]

    async def canonical(hit: dict[str, Any]) -> dict[str, Any]:
        item_id = hit.get("objectID")
        if not item_id:
            return hit
        try:
            item = await client.get_json(HN_ITEM.format(item_id=item_id))
            return item if isinstance(item, dict) else hit
        except Exception:
            return hit

    items = await asyncio.gather(*(canonical(hit) for hit in selected))
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title or not _name_in_text(company.name, title):
            continue
        item_id = item.get("id") or item.get("objectID")
        hn_url = f"https://news.ycombinator.com/item?id={item_id}" if item_id else None
        linked_url = item.get("url")
        document_date = _epoch_date(item.get("time")) or iso_date(item.get("created_at"))
        evidence = make_evidence(
            startup_id=company.startup_id,
            channel="hackernews",
            source_type="other",
            document_name=f"Hacker News story: {title[:120]}",
            excerpt=title,
            source_uri=hn_url,
            document_date=document_date,
            collected_at=collected_at,
            location=f"item {item_id}" if item_id else None,
            verification_status="unverified",
            confidence="low",
        )
        result.evidence.append(evidence)
        funding = extract_funding_round(
            company_name=company.name,
            startup_id=company.startup_id,
            text=title,
            document_date=document_date,
            date_basis="announcement",
            source_name="Hacker News",
            source_url=str(linked_url or hn_url) if (linked_url or hn_url) else None,
            source_id=evidence.source_id,
            verification_status="unverified",
            confidence="low",
        )
        if funding:
            result.funding_rounds.append(funding)

    result.signals = {
        "hn_mention_count": len(candidates),
        "hn_points_current": sum(int(hit.get("points") or 0) for hit in candidates),
        "hn_comments_current": sum(int(hit.get("num_comments") or 0) for hit in candidates),
        "signal_observed_at": settings.as_of.isoformat(),
    }
    return result


_PH_QUERY = """
query CompanyPosts($url: String!) {
  posts(first: 10, order: NEWEST, url: $url) {
    edges {
      node {
        id slug name tagline description website url createdAt featuredAt
        votesCount commentsCount
      }
    }
  }
}
""".strip()


async def enrich_producthunt(
    client: Fetcher,
    company: Company,
    *,
    settings: Settings,
    collected_at: str,
) -> EnrichmentResult:
    result = EnrichmentResult(source="producthunt", startup_id=company.startup_id)
    if not settings.producthunt_token:
        result.errors.append("auth_required: PRODUCTHUNT_TOKEN is not set")
        return result
    if not company.website:
        result.errors.append("entity_unresolved: no canonical company website")
        return result
    headers = {
        "Authorization": f"Bearer {settings.producthunt_token}",
        "Content-Type": "application/json",
    }
    try:
        payload = await client.post_json(
            PRODUCT_HUNT_API,
            {"query": _PH_QUERY, "variables": {"url": company.website}},
            headers=headers,
        )
    except Exception as exc:
        result.errors.append(str(exc))
        return result
    if isinstance(payload, dict) and payload.get("errors"):
        result.errors.extend(str(error.get("message") or error) for error in payload["errors"])
        return result
    edges = (((payload or {}).get("data") or {}).get("posts") or {}).get("edges") or []
    nodes = [edge.get("node") or {} for edge in edges if isinstance(edge, dict)]
    # URL filtering is done server-side; retain an extra domain check when the
    # response contains a concrete website to guard against API drift.
    expected_host = company.domain
    filtered: list[dict[str, Any]] = []
    for node in nodes:
        website = node.get("website")
        host = (urlsplit(website).hostname or "").removeprefix("www.") if website else None
        if expected_host and host and host.casefold() != expected_host.casefold():
            continue
        filtered.append(node)
    filtered.sort(key=lambda node: (str(node.get("createdAt") or ""), str(node.get("id") or "")))

    for node in filtered:
        name = str(node.get("name") or company.name)
        tagline = str(node.get("tagline") or "")
        description = str(node.get("description") or "")
        document_date = iso_date(node.get("createdAt"))
        product_url = node.get("url")
        excerpt = " — ".join(part for part in (name, tagline, description) if part)[:1000]
        evidence = make_evidence(
            startup_id=company.startup_id,
            channel="producthunt",
            source_type="company_website",
            document_name=f"Product Hunt launch: {name[:120]}",
            excerpt=excerpt,
            source_uri=str(product_url) if product_url else None,
            document_date=document_date,
            collected_at=collected_at,
            location=f"post {node.get('id')}" if node.get("id") else None,
            verification_status="founder_reported",
            confidence="medium",
        )
        result.evidence.append(evidence)
        funding = extract_funding_round(
            company_name=company.name,
            startup_id=company.startup_id,
            text=excerpt,
            document_date=document_date,
            date_basis="announcement",
            source_name="Product Hunt",
            source_url=str(product_url) if product_url else None,
            source_id=evidence.source_id,
            verification_status="founder_reported",
            confidence="low",
        )
        if funding:
            result.funding_rounds.append(funding)

    result.signals = {
        "producthunt_launch_count": len(filtered),
        "producthunt_votes_current": sum(int(node.get("votesCount") or 0) for node in filtered),
        "producthunt_comments_current": sum(int(node.get("commentsCount") or 0) for node in filtered),
        "signal_observed_at": settings.as_of.isoformat(),
    }
    return result


def _github_login(company: Company) -> str | None:
    value = company.external_ids.get("github") or company.external_ids.get("github_org")
    if not value:
        return None
    if "/" in value:
        parts = [part for part in urlsplit(value).path.split("/") if part]
        return parts[0] if parts else None
    return value.strip().lstrip("@") or None


async def enrich_github(
    client: Fetcher,
    company: Company,
    *,
    settings: Settings,
    collected_at: str,
) -> EnrichmentResult:
    """Collect GitHub only for an explicit directory-provided organization ID."""
    result = EnrichmentResult(source="github", startup_id=company.startup_id)
    login = _github_login(company)
    if not login:
        result.errors.append("entity_unresolved: no explicit GitHub organization")
        return result
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    try:
        repos = await client.get_json(
            f"{GITHUB_API}/orgs/{login}/repos",
            params={"per_page": "100", "sort": "updated", "direction": "desc"},
            headers=headers,
        )
    except Exception as exc:
        result.errors.append(str(exc))
        return result
    if not isinstance(repos, list):
        result.errors.append("parse_failed: GitHub repository response was not a list")
        return result
    public = [repo for repo in repos if isinstance(repo, dict) and not repo.get("fork")]
    stars = sum(int(repo.get("stargazers_count") or 0) for repo in public)
    forks = sum(int(repo.get("forks_count") or 0) for repo in public)
    if public:
        top = max(public, key=lambda repo: (int(repo.get("stargazers_count") or 0), str(repo.get("full_name") or "")))
        evidence = make_evidence(
            startup_id=company.startup_id,
            channel="github",
            source_type="company_website",
            document_name=f"GitHub organization: {login}",
            excerpt=f"Explicit organization {login}; {len(public)} public non-fork repositories observed.",
            source_uri=f"https://github.com/{login}",
            document_date=settings.as_of.isoformat(),
            collected_at=collected_at,
            verification_status="document_verified",
            confidence="high",
        )
        result.evidence.append(evidence)
        result.signals = {
            "github_repo_count_current": len(public),
            "github_stars_current": stars,
            "github_forks_current": forks,
            "github_top_repo": top.get("full_name"),
            "signal_observed_at": settings.as_of.isoformat(),
        }
    return result

