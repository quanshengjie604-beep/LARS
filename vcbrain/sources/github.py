"""GitHub enrichment — open-source footprint & activity signals.

Best-effort: on rate-limit (403) or any failure we return null-filled signals so
the pipeline keeps going. With GITHUB_TOKEN limits are far higher.

Fetch and compute are split: we fetch the repo list once (live), then derive both
the live view and a point-in-time view by filtering `created_at <= cutoff`
client-side. Live star counts are only reported when cutoff is None/today, since
historical star counts are not reconstructable from this API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from .. import config
from ..http_client import HttpError, get_json
from ..util import domain_of, parse_date

API = "https://api.github.com"


@dataclass
class GithubRaw:
    items: list = field(default_factory=list)
    org_login: Optional[str] = None
    followers: Optional[int] = None


@dataclass
class GithubSignals:
    org_login: Optional[str] = None
    stars_total: Optional[int] = None
    forks_total: Optional[int] = None
    repo_count: Optional[int] = None
    top_repo: Optional[str] = None
    top_repo_stars: Optional[int] = None
    followers: Optional[int] = None
    recent_push_days: Optional[int] = None
    earliest_repo_date: Optional[str] = None
    evidence: list = field(default_factory=list)  # (excerpt, uri, date)
    available: bool = False


def _headers() -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


def _guess_org(company) -> Optional[str]:
    dom = domain_of(company.website) or ""
    base = dom.split(".")[0] if dom else ""
    if base and base not in ("github", "www", "app"):
        return base
    return None


def _search_repos(query: str, limit: int = 10) -> Optional[list[dict]]:
    try:
        data = get_json(
            f"{API}/search/repositories",
            params={"q": query, "sort": "stars", "order": "desc", "per_page": limit},
            headers=_headers(),
        )
    except HttpError:
        return None
    return (data or {}).get("items") or []


def fetch(company) -> GithubRaw:
    """Fetch the raw repo list + org info for a company (one live call set)."""
    raw = GithubRaw()
    guess = _guess_org(company)
    if guess:
        try:
            org = get_json(f"{API}/orgs/{guess}", headers=_headers())
        except HttpError:
            org = None
        if org and org.get("login"):
            raw.org_login = org["login"]
            raw.followers = org.get("followers")
            raw.items = _search_repos(f"org:{org['login']}") or []
    if not raw.items:
        raw.items = _search_repos(f'"{company.name}" in:name,description', limit=6) or []
    return raw


def signals(raw: GithubRaw, cutoff: Optional[date] = None) -> GithubSignals:
    """Derive signals from a fetched GithubRaw, filtered to <= cutoff."""
    sig = GithubSignals(org_login=raw.org_login, followers=raw.followers)
    kept = []
    for it in raw.items:
        created = parse_date(it.get("created_at"))
        if cutoff and created and created > cutoff:
            continue
        kept.append(it)
    if not kept:
        return sig

    sig.available = True
    sig.repo_count = len(kept)
    live = cutoff is None or cutoff >= config.TODAY
    if live:
        sig.stars_total = sum(int(it.get("stargazers_count") or 0) for it in kept)
        sig.forks_total = sum(int(it.get("forks_count") or 0) for it in kept)
    else:
        # historical: followers count is also a live figure -> null for training
        sig.followers = None

    pushes = [parse_date(it.get("pushed_at")) for it in kept]
    pushes = [p for p in pushes if p]
    if pushes:
        ref = cutoff or config.TODAY
        sig.recent_push_days = max(0, (ref - max(pushes)).days)
    creates = [parse_date(it.get("created_at")) for it in kept]
    creates = [c for c in creates if c]
    if creates:
        sig.earliest_repo_date = min(creates).isoformat()

    top = max(kept, key=lambda it: it.get("stargazers_count") or 0)
    sig.top_repo = top.get("full_name")
    if live:
        sig.top_repo_stars = top.get("stargazers_count")
    stars_txt = f"{top.get('stargazers_count')} stars" if live else "activity present pre-cutoff"
    sig.evidence.append((
        f"GitHub {top.get('full_name')}: {stars_txt}, created {top.get('created_at','')[:10]}, "
        f"last push {top.get('pushed_at','')[:10]}. {sig.repo_count} repos.",
        top.get("html_url"),
        (top.get("created_at") or "")[:10] or None,
    ))
    return sig


def enrich(company, cutoff: Optional[date] = None) -> GithubSignals:
    return signals(fetch(company), cutoff)
