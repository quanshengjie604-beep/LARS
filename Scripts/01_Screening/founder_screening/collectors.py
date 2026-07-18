from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http import PoliteHttpClient
from .models import Candidate, CollectedCandidate, Evidence, stable_id, utc_now

LOGGER = logging.getLogger(__name__)
ATOM = {"a": "http://www.w3.org/2005/Atom"}


def _excerpt(text: str, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _evidence(candidate: Candidate, source_type: str, name: str, uri: str, excerpt: str,
              *, document_date=None, location=None, confidence="medium", metadata=None) -> Evidence:
    return Evidence(
        source_id=stable_id("source", candidate.candidate_id, uri, excerpt[:80]),
        candidate_id=candidate.candidate_id,
        source_type=source_type,
        document_name=name,
        source_uri=uri,
        document_date=document_date,
        collected_at=utc_now(),
        location=location,
        evidence_excerpt=_excerpt(excerpt),
        confidence=confidence,
        raw_metadata=metadata or {},
    )


class Collector(ABC):
    name: str

    def __init__(self, client: PoliteHttpClient, config: dict):
        self.client, self.config = client, config

    @abstractmethod
    def collect(self, limit: int) -> Iterable[CollectedCandidate]: ...


class ArxivCollector(Collector):
    name = "arxiv"
    endpoint = "https://export.arxiv.org/api/query"

    @staticmethod
    def parse_feed(xml: str) -> list[dict]:
        root = ET.fromstring(xml)
        records = []
        for entry in root.findall("a:entry", ATOM):
            records.append({
                "id": (entry.findtext("a:id", "", ATOM)).strip(),
                "title": _excerpt(entry.findtext("a:title", "", ATOM), 300),
                "summary": _excerpt(entry.findtext("a:summary", "", ATOM), 1200),
                "published": entry.findtext("a:published", None, ATOM),
                "authors": [a.findtext("a:name", "", ATOM).strip() for a in entry.findall("a:author", ATOM)],
                "categories": [c.attrib.get("term") for c in entry.findall("a:category", ATOM) if c.attrib.get("term")],
            })
        return records

    def collect(self, limit: int) -> Iterable[CollectedCandidate]:
        categories = self.config.get("categories", ["cs.AI", "cs.LG", "cs.RO", "cs.DC", "q-bio.QM"])
        months = int(self.config.get("months", 24))
        page_size = min(int(self.config.get("page_size", 100)), 100)
        earliest = (datetime.now(timezone.utc) - timedelta(days=months * 31)).strftime("%Y%m%d0000")
        latest = datetime.now(timezone.utc).strftime("%Y%m%d2359")
        query = f"({' OR '.join(f'cat:{c}' for c in categories)}) AND submittedDate:[{earliest} TO {latest}]"
        produced, start = 0, 0
        while produced < limit:
            response = self.client.get(self.endpoint, params={
                "search_query": query, "start": start, "max_results": page_size,
                "sortBy": "submittedDate", "sortOrder": "descending",
            }, minimum_delay=float(self.config.get("delay_seconds", 3.0)))
            papers = self.parse_feed(response.text)
            if not papers:
                return
            for paper in papers:
                for author in paper["authors"]:
                    if produced >= limit:
                        return
                    candidate = Candidate.from_name(author, paper["id"])
                    candidate.discovery_sources.append(self.name)
                    candidate.research["papers"].append({
                        "title": paper["title"], "uri": paper["id"], "published": paper["published"],
                    })
                    candidate.research["categories"] = paper["categories"]
                    candidate.research["coauthors"] = [x for x in paper["authors"] if x != author]
                    candidate.entrepreneurial_signals.append({
                        "type": "recent_research", "observed_at": paper["published"],
                        "description": f"Author of recent research in {', '.join(paper['categories'][:4])}",
                    })
                    ev = _evidence(candidate, "manual_research", paper["title"], paper["id"],
                                   f"{author} is listed as an author. Abstract: {paper['summary']}",
                                   document_date=(paper["published"] or "")[:10] or None,
                                   location="arXiv metadata", confidence="high",
                                   metadata={"categories": paper["categories"]})
                    candidate.source_ids.append(ev.source_id)
                    produced += 1
                    yield CollectedCandidate(candidate, [ev])
            start += len(papers)


class GitHubCollector(Collector):
    name = "github"
    api = "https://api.github.com"

    def __init__(self, client: PoliteHttpClient, config: dict):
        super().__init__(client, config)
        token = os.getenv(config.get("token_env", "GITHUB_TOKEN"))
        self.headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _get_json(self, path: str, params=None):
        return self.client.get(f"{self.api}{path}", params=params, headers=self.headers).json()

    def collect(self, limit: int) -> Iterable[CollectedCandidate]:
        if "Authorization" not in self.headers and self.config.get("require_token", True):
            LOGGER.warning("Skipping GitHub: set %s for authenticated public API access", self.config.get("token_env", "GITHUB_TOKEN"))
            return
        queries = self.config.get("repository_queries", [
            "topic:artificial-intelligence stars:>100 pushed:>2025-01-01",
            "topic:machine-learning stars:>100 pushed:>2025-01-01",
            "topic:robotics stars:>50 pushed:>2025-01-01",
            "topic:developer-tools stars:>100 pushed:>2025-01-01",
        ])
        seen_logins, produced = set(), 0
        for query in queries:
            if produced >= limit:
                return
            payload = self._get_json("/search/repositories", {"q": query, "sort": "updated", "per_page": 50})
            for repo in payload.get("items", []):
                if produced >= limit:
                    return
                contributors = self._get_json(f"/repos/{repo['full_name']}/contributors", {
                    "per_page": int(self.config.get("contributors_per_repo", 10)), "anon": "false",
                })
                for contributor in contributors if isinstance(contributors, list) else []:
                    login = contributor.get("login")
                    if not login or login in seen_logins:
                        continue
                    seen_logins.add(login)
                    user = self._get_json(f"/users/{login}") if self.config.get("enrich_users", True) else contributor
                    if user.get("type") != "User":
                        continue
                    full_name = user.get("name") or login
                    candidate = Candidate.from_name(full_name, f"github:{login}", user.get("company") or "")
                    candidate.discovery_sources.append(self.name)
                    candidate.profile_urls.append(user.get("html_url") or contributor.get("html_url"))
                    candidate.headline = user.get("bio")
                    candidate.affiliations = [user["company"]] if user.get("company") else []
                    candidate.location = user.get("location")
                    candidate.open_source.update({
                        "github_login": login,
                        "public_repos": user.get("public_repos"),
                        "followers": user.get("followers"),
                        "contributed_repositories": [{
                            "name": repo["full_name"], "url": repo["html_url"],
                            "stars": repo.get("stargazers_count"), "contributions": contributor.get("contributions"),
                        }],
                    })
                    candidate.entrepreneurial_signals.append({
                        "type": "open_source_momentum", "observed_at": repo.get("updated_at"),
                        "description": f"Contributor to {repo['full_name']} ({repo.get('stargazers_count', 0)} stars)",
                    })
                    excerpt = f"{full_name} (@{login}) contributed {contributor.get('contributions')} commits to {repo['full_name']}."
                    ev = _evidence(candidate, "manual_research", f"GitHub: {repo['full_name']}",
                                   user.get("html_url") or contributor.get("html_url"), excerpt,
                                   document_date=(repo.get("updated_at") or "")[:10] or None,
                                   location="GitHub public API", confidence="high",
                                   metadata={"repository": repo["full_name"], "stars": repo.get("stargazers_count")})
                    candidate.source_ids.append(ev.source_id)
                    produced += 1
                    yield CollectedCandidate(candidate, [ev])
                    if produced >= limit:
                        return


class PeoplePageCollector(Collector):
    name = "labs"
    NAME = re.compile(r"^[^\W\d_][\w'.-]+(?:\s+[^\W\d_][\w'.-]+){1,4}$", re.UNICODE)

    def collect(self, limit: int) -> Iterable[CollectedCandidate]:
        produced = 0
        for page in self.config.get("pages", []):
            if produced >= limit:
                return
            url, institution = page["url"], page["institution"]
            try:
                response = self.client.get(url, check_robots=True)
            except Exception as exc:
                LOGGER.warning("Skipping lab page %s: %s", url, exc)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            selector = page.get("person_selector", "a")
            seen = set()
            for element in soup.select(selector):
                name = _excerpt(element.get_text(" ", strip=True), 100)
                if not self.NAME.match(name) or name in seen:
                    continue
                seen.add(name)
                href = urljoin(response.url, element.get("href", ""))
                candidate = Candidate.from_name(name, href or response.url, institution)
                candidate.discovery_sources.append(self.name)
                candidate.affiliations.append(institution)
                candidate.current_role = page.get("default_role")
                if href and href != response.url:
                    candidate.profile_urls.append(href)
                candidate.entrepreneurial_signals.append({
                    "type": "research_lab_member", "observed_at": date.today().isoformat(),
                    "description": f"Publicly listed member of {institution}",
                })
                ev = _evidence(candidate, "company_website", f"{institution} people", response.url,
                               f"{name} is publicly listed on the {institution} people page.",
                               location="people directory", confidence="medium")
                candidate.source_ids.append(ev.source_id)
                produced += 1
                yield CollectedCandidate(candidate, [ev])
                if produced >= limit:
                    return


class HackathonCollector(PeoplePageCollector):
    name = "hackathons"

    def collect(self, limit: int) -> Iterable[CollectedCandidate]:
        produced = 0
        for page in self.config.get("pages", []):
            url = page["url"]
            try:
                response = self.client.get(url, check_robots=True)
            except Exception as exc:
                LOGGER.warning("Skipping hackathon page %s: %s", url, exc)
                continue
            soup = BeautifulSoup(response.text, "html.parser")
            project_selector = page.get("project_selector", "article, .project, .software-list-content")
            for project in soup.select(project_selector):
                title_el = project.select_one(page.get("title_selector", "h2, h3, .title"))
                title = _excerpt(title_el.get_text(" ", strip=True), 160) if title_el else "Hackathon project"
                project_link = project.select_one(page.get("project_link_selector", "a[href]"))
                project_url = urljoin(response.url, project_link.get("href", "")) if project_link else response.url
                detail = project
                if project_url != response.url:
                    try:
                        detail_response = self.client.get(project_url, check_robots=True)
                        detail = BeautifulSoup(detail_response.text, "html.parser")
                    except Exception as exc:
                        LOGGER.warning("Could not read hackathon project %s: %s", project_url, exc)
                member_selector = page.get("member_selector", ".user-profile-link, .team-member, [rel='author']")
                for member in detail.select(member_selector):
                    name = _excerpt(member.get_text(" ", strip=True), 100)
                    if not self.NAME.match(name):
                        continue
                    href = urljoin(response.url, member.get("href", ""))
                    candidate = Candidate.from_name(name, href or title, page.get("event_name", "hackathon"))
                    candidate.discovery_sources.append(self.name)
                    if href and href != response.url:
                        candidate.profile_urls.append(href)
                    candidate.hackathons["projects"].append({"name": title, "url": project_url})
                    if page.get("award"):
                        candidate.hackathons["awards"].append(page["award"])
                    candidate.entrepreneurial_signals.append({
                        "type": "hackathon_project", "observed_at": page.get("event_date"),
                        "description": f"Built {title} at {page.get('event_name', 'a hackathon')}",
                    })
                    ev = _evidence(candidate, "manual_research", title, project_url,
                                   f"{name} is listed as a team member for {title}.",
                                   document_date=page.get("event_date"), location="project team", confidence="medium")
                    candidate.source_ids.append(ev.source_id)
                    produced += 1
                    yield CollectedCandidate(candidate, [ev])
                    if produced >= limit:
                        return


from .company_collectors import YCombinatorCollector


COLLECTORS = {
    "yc": YCombinatorCollector,
    "arxiv": ArxivCollector,
    "github": GitHubCollector,
    "labs": PeoplePageCollector,
    "hackathons": HackathonCollector,
}
