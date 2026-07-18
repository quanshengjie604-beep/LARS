from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from .models import Candidate, CollectedCandidate

LOGGER = logging.getLogger(__name__)

DIRECTORY_URL = "https://www.ycombinator.com/companies"
INDEX_NAME = "YCCompany_production"
FOUNDER_ROLE = re.compile(r"\b(?:co[ -]?)?founder\b", re.IGNORECASE)
SEASON_ORDER = {"Winter": 1, "Spring": 2, "Summer": 3, "Fall": 4}


def _clean(text: str | None, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


class YCombinatorCollector:
    """Collect founders whose public YC company page explicitly names their role."""

    name = "yc"

    def __init__(self, client, config: dict):
        self.client, self.config = client, config

    @staticmethod
    def _parse_algolia_options(page_html: str) -> dict[str, str]:
        match = re.search(r"window\.AlgoliaOpts\s*=\s*(\{.*?\});", page_html)
        if not match:
            raise ValueError("YC directory did not expose its public search configuration")
        options = json.loads(html.unescape(match.group(1)))
        if not options.get("app") or not options.get("key"):
            raise ValueError("YC public search configuration is incomplete")
        return options

    @staticmethod
    def _batch_key(batch: str) -> tuple[int, int]:
        match = re.fullmatch(r"(Winter|Spring|Summer|Fall)\s+(\d{4})", batch or "")
        if not match:
            return (0, 0)
        return (int(match.group(2)), SEASON_ORDER[match.group(1)])

    def _query(self, endpoint: str, headers: dict[str, str], **parameters) -> dict:
        encoded = urlencode(parameters)
        response = self.client.post(
            endpoint,
            json={"params": encoded},
            headers=headers,
            minimum_delay=float(self.config.get("search_delay_seconds", 0.25)),
        )
        return response.json()

    def _company_hits(self) -> list[dict]:
        directory = self.client.get(DIRECTORY_URL, check_robots=True)
        options = self._parse_algolia_options(directory.text)
        endpoint = f"https://{options['app']}-dsn.algolia.net/1/indexes/{INDEX_NAME}/query"
        headers = {
            "X-Algolia-Application-Id": options["app"],
            "X-Algolia-API-Key": options["key"],
            "Content-Type": "application/json",
        }
        facets = self._query(
            endpoint,
            headers,
            hitsPerPage=0,
            facets=json.dumps(["batch"]),
            maxValuesPerFacet=1000,
        ).get("facets", {}).get("batch", {})
        batches = sorted(facets, key=self._batch_key, reverse=True)
        hits: dict[str, dict] = {}
        for batch in batches:
            result = self._query(
                endpoint,
                headers,
                hitsPerPage=1000,
                page=0,
                facetFilters=json.dumps([f"batch:{batch}"]),
            )
            for hit in result.get("hits", []):
                slug = hit.get("slug")
                if slug:
                    hits[slug] = hit
        cutoff = datetime.now(timezone.utc) - timedelta(days=int(self.config.get("recent_months", 24)) * 31)

        def priority(hit: dict) -> tuple[int, int]:
            launched = hit.get("launched_at") or 0
            recent = int(bool(launched and datetime.fromtimestamp(launched, timezone.utc) >= cutoff))
            return recent, launched

        in_scope = [hit for hit in hits.values() if self._in_scope(hit)]
        in_scope.sort(key=priority, reverse=True)
        if not self.config.get("backfill_older", True):
            in_scope = [hit for hit in in_scope if priority(hit)[0]]
        return in_scope

    def _in_scope(self, hit: dict) -> bool:
        terms = [str(value) for value in hit.get("tags", [])]
        terms.extend([
            hit.get("industry") or "",
            hit.get("subindustry") or "",
            hit.get("one_liner") or "",
            hit.get("long_description") or "",
        ])
        haystack = " ".join(terms).casefold()
        keywords = self.config.get("domain_keywords", [])
        return any(str(keyword).casefold() in haystack for keyword in keywords)

    @staticmethod
    def parse_founders(page_html: str) -> list[dict[str, object]]:
        soup = BeautifulSoup(page_html, "html.parser")
        founders: list[dict[str, object]] = []
        seen: set[tuple[str, str]] = set()
        headings = soup.find_all(string=lambda value: bool(
            value and "founders" in value.strip().casefold()
        ))
        for heading_text in headings:
            heading = heading_text.parent
            if not heading or "founders" not in heading.get_text(" ", strip=True).casefold():
                continue
            section = heading.parent
            for _ in range(4):
                if not section or section.select(".ycdc-card-new"):
                    break
                section = section.parent
            if not section:
                continue
            for card in section.select(".ycdc-card-new"):
                desktop = card.select_one("div.hidden.gap-4") or card
                name_element = desktop.select_one(".text-xl.font-bold")
                if not name_element:
                    continue
                name = _clean(name_element.get_text(" ", strip=True), 160)
                role_element = desktop.select_one(".text-gray-600")
                role = _clean(role_element.get_text(" ", strip=True), 160) if role_element else None
                if not name or not role or not FOUNDER_ROLE.search(role):
                    continue
                key = (name.casefold(), role.casefold())
                if key in seen:
                    continue
                seen.add(key)
                bio_element = desktop.select_one(".prose")
                links = []
                for anchor in desktop.select("a[href]"):
                    href = anchor.get("href")
                    if href and href.startswith(("http://", "https://")) and href not in links:
                        links.append(href)
                founders.append({
                    "full_name": name,
                    "founder_role": role,
                    "bio": _clean(bio_element.get_text(" ", strip=True), 1000) if bio_element else None,
                    "profile_urls": links,
                })
        return founders

    def collect(self, limit: int) -> Iterable[CollectedCandidate]:
        # Imported lazily to avoid a module cycle with the collector registry.
        from .collectors import _evidence

        produced = 0
        hits = self._company_hits()
        LOGGER.info("Found %d in-scope YC companies; verifying founder cards", len(hits))
        for hit in hits:
            if produced >= limit:
                return
            slug = hit["slug"]
            evidence_url = f"{DIRECTORY_URL}/{slug}"
            try:
                response = self.client.get(
                    evidence_url,
                    check_robots=True,
                    minimum_delay=float(self.config.get("page_delay_seconds", 0.4)),
                )
            except Exception as exc:
                LOGGER.warning("Skipping YC company page %s: %s", evidence_url, exc)
                continue
            company_name = _clean(hit.get("name"), 200)
            company_url = hit.get("website") or evidence_url
            for founder in self.parse_founders(response.text):
                if produced >= limit:
                    return
                role = str(founder["founder_role"])
                candidate = Candidate.from_name(str(founder["full_name"]), evidence_url, company_name)
                candidate.discovery_sources.append(self.name)
                candidate.affiliations.append(company_name)
                candidate.company_name = company_name
                candidate.company_url = company_url
                candidate.founder_role = role
                candidate.founder_relationship_evidence_url = evidence_url
                candidate.current_role = role
                candidate.headline = founder.get("bio") or None
                candidate.location = hit.get("all_locations") or None
                candidate.geography = (hit.get("regions") or [None])[0]
                candidate.profile_urls.extend(founder.get("profile_urls") or [])
                company = {
                    "company_name": company_name,
                    "company_url": company_url,
                    "company_profile_url": evidence_url,
                    "founder_role": role,
                    "relationship_evidence_url": evidence_url,
                    "status": hit.get("status"),
                    "batch": hit.get("batch"),
                    "industry": hit.get("industry"),
                    "subindustry": hit.get("subindustry"),
                    "launched_at": datetime.fromtimestamp(
                        hit["launched_at"], timezone.utc
                    ).date().isoformat() if hit.get("launched_at") else None,
                }
                candidate.founded_companies.append(company)
                candidate.entrepreneurial_signals.append({
                    "type": "verified_company_founder",
                    "observed_at": None,
                    "description": f"Publicly listed as {role} of {company_name}",
                    "company_url": company_url,
                    "evidence_url": evidence_url,
                })
                excerpt = f"{founder['full_name']} is publicly listed as {role} of {company_name}."
                evidence = _evidence(
                    candidate,
                    "manual_research",
                    f"{company_name} company profile",
                    evidence_url,
                    excerpt,
                    location="founder card",
                    confidence="high",
                    metadata=company,
                )
                candidate.source_ids.append(evidence.source_id)
                produced += 1
                if produced % int(self.config.get("progress_interval", 100)) == 0:
                    LOGGER.info("Verified %d founder-company records", produced)
                yield CollectedCandidate(candidate, [evidence])
