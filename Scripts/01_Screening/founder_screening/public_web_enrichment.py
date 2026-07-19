from __future__ import annotations

import json
import logging
import re
import time
import urllib.robotparser
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .enrichment import (
    InstitutionMatcher,
    QS_EDITION,
    QS_SOURCE_URL,
    extract_education,
    extract_exits,
    extract_skills,
    load_qs_rankings_xlsx,
)
from .models import stable_id


LOGGER = logging.getLogger(__name__)
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
LINKEDIN_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)
TEAM_LINK_RE = re.compile(
    r"(?:about|team|people|leadership|founder|company|who[-_ ]we[-_ ]are)", re.IGNORECASE
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def _linkedin_anchor(candidate: dict[str, Any]) -> tuple[str, str] | None:
    for url in candidate.get("profile_urls") or []:
        match = LINKEDIN_RE.search(url)
        if match:
            slug = match.group(1).strip().rstrip("/")
            return slug, f"https://www.linkedin.com/in/{slug}"
    return None


def _root_url(url: str) -> str | None:
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"


def _same_host(left: str, right: str) -> bool:
    def host(value: str) -> str:
        return urlparse(value).netloc.casefold().removeprefix("www.")

    return bool(host(left)) and host(left) == host(right)


def _visible_text(node) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def founder_bio_snippets(page_html: str, full_names: Iterable[str]) -> dict[str, str]:
    """Return a bounded local DOM excerpt only when a founder's full name is present."""
    soup = BeautifulSoup(page_html, "html.parser")
    for element in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        element.decompose()
    results: dict[str, str] = {}
    for full_name in full_names:
        name_key = _normalize(full_name)
        if len(name_key) < 4:
            continue
        best: str | None = None
        for text_node in soup.find_all(string=True):
            if name_key not in _normalize(str(text_node)):
                continue
            node = text_node.parent
            candidates: list[str] = []
            for _ in range(5):
                if node is None:
                    break
                text = _visible_text(node)
                if 20 <= len(text) <= 2200 and name_key in _normalize(text):
                    candidates.append(text)
                node = node.parent
            if candidates:
                local = min(candidates, key=len)
                if best is None or len(local) < len(best):
                    best = local
        if best:
            results[full_name] = best[:2200]
    return results


def discover_team_links(page_html: str, base_url: str, limit: int = 2) -> list[str]:
    soup = BeautifulSoup(page_html, "html.parser")
    scored: list[tuple[int, str]] = []
    seen: set[str] = set()
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        url = urljoin(base_url, href).split("#", 1)[0]
        if not _same_host(base_url, url) or url in seen:
            continue
        label = f"{anchor.get_text(' ', strip=True)} {urlparse(url).path}"
        if not TEAM_LINK_RE.search(label):
            continue
        seen.add(url)
        lowered = label.casefold()
        score = 3 if "team" in lowered or "people" in lowered else 2 if "leadership" in lowered or "founder" in lowered else 1
        scored.append((score, url))
    scored.sort(key=lambda item: (-item[0], len(item[1])))
    return [url for _, url in scored[:limit]]


def _fetch_company_domain(
    root: str,
    candidates: list[dict[str, Any]],
    user_agent: str,
    timeout: float,
    max_extra_pages: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {"root": root, "pages": [], "snippets": {}, "status": "unknown", "error": None}
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"})
    try:
        robots_url = urljoin(root, "/robots.txt")
        robots_response = session.get(robots_url, timeout=timeout)
        if robots_response.status_code != 200:
            result["status"] = "robots_unavailable"
            return result
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(robots_response.text.splitlines())
        if not parser.can_fetch(user_agent, root):
            result["status"] = "robots_disallowed"
            return result
        response = session.get(root, timeout=timeout)
        response.raise_for_status()
        if "html" not in response.headers.get("Content-Type", "").casefold():
            result["status"] = "non_html"
            return result
        pages = [(response.url, response.text[:2_000_000])]
        for url in discover_team_links(response.text, response.url, max_extra_pages):
            if not parser.can_fetch(user_agent, url):
                continue
            time.sleep(0.35)
            try:
                detail = session.get(url, timeout=timeout)
                detail.raise_for_status()
                if "html" in detail.headers.get("Content-Type", "").casefold():
                    pages.append((detail.url, detail.text[:2_000_000]))
            except requests.RequestException:
                continue
        names = [item["full_name"] for item in candidates]
        for url, html in pages:
            result["pages"].append(url)
            for name, snippet in founder_bio_snippets(html, names).items():
                current = result["snippets"].get(name)
                if current is None or len(snippet) > len(current["snippet"]):
                    result["snippets"][name] = {"url": url, "snippet": snippet}
        result["status"] = "success"
    except requests.RequestException as exc:
        result["status"] = "request_error"
        result["error"] = str(exc)[:500]
    except Exception as exc:
        result["status"] = "parse_error"
        result["error"] = str(exc)[:500]
    return result


def query_wikidata_by_linkedin(
    candidates: list[dict[str, Any]], user_agent: str, batch_size: int = 150, timeout: float = 60
) -> dict[str, list[dict[str, str]]]:
    anchors = {}
    for candidate in candidates:
        anchor = _linkedin_anchor(candidate)
        if anchor:
            anchors[anchor[0]] = candidate["candidate_id"]
    slugs = sorted(anchors)
    by_candidate: dict[str, list[dict[str, str]]] = defaultdict(list)
    for start in range(0, len(slugs), batch_size):
        batch = slugs[start:start + batch_size]
        values = " ".join(json.dumps(value, ensure_ascii=False) for value in batch)
        query = f"""SELECT ?linkedin ?person ?personLabel ?school ?schoolLabel ?degree ?degreeLabel ?orcid ?website ?field ?fieldLabel WHERE {{
          VALUES ?linkedin {{ {values} }}
          ?person wdt:P6634 ?linkedin.
          OPTIONAL {{ ?person wdt:P69 ?school. }}
          OPTIONAL {{ ?person wdt:P512 ?degree. }}
          OPTIONAL {{ ?person wdt:P496 ?orcid. }}
          OPTIONAL {{ ?person wdt:P856 ?website. }}
          OPTIONAL {{ ?person wdt:P101 ?field. }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
        }}"""
        try:
            response = requests.get(
                WIKIDATA_ENDPOINT,
                params={"query": query, "format": "json"},
                headers={"User-Agent": user_agent, "Accept": "application/sparql-results+json"},
                timeout=timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.warning("Wikidata batch %d failed: %s", start // batch_size + 1, exc)
            continue
        for binding in response.json().get("results", {}).get("bindings", []):
            values_dict = {key: value["value"] for key, value in binding.items()}
            candidate_id = anchors.get(values_dict.get("linkedin", ""))
            if candidate_id:
                by_candidate[candidate_id].append(values_dict)
    return by_candidate


def _rank_key(value: int | str | None) -> int:
    match = re.match(r"\d+", str(value or ""))
    return int(match.group()) if match else 10**9


def _refresh_education_summary(education: dict[str, Any]):
    degrees = education.get("degrees") or []
    levels = {item.get("level") for item in degrees}
    order = {"bachelor": 1, "master": 2, "phd": 3}
    education["has_bachelor"] = True if "bachelor" in levels else None
    education["has_master"] = True if "master" in levels else None
    education["has_phd"] = True if "phd" in levels else None
    known_levels = [level for level in levels if level in order]
    education["highest_documented_level"] = max(known_levels, key=order.get) if known_levels else None
    ranked = [item for item in degrees if item.get("qs_world_rank") is not None]
    best = min(ranked, key=lambda item: _rank_key(item.get("qs_world_rank"))) if ranked else None
    education["best_qs_world_rank"] = best.get("qs_world_rank") if best else None
    education["highest_ranked_institution"] = best.get("institution") if best else None
    education["qs_edition"] = QS_EDITION if best else None


def _merge_company_snippet(
    candidate: dict[str, Any], snippet: str, source_url: str, matcher: InstitutionMatcher
) -> tuple[dict[str, int], dict[str, Any] | None]:
    counts = Counter()
    new_education = extract_education(snippet, source_url, matcher)
    education = candidate.setdefault("education", {"degrees": []})
    existing_degrees = {
        (item.get("level"), item.get("institution"), item.get("status"), item.get("evidence_excerpt"))
        for item in education.get("degrees") or []
    }
    for degree in new_education["degrees"]:
        key = (degree.get("level"), degree.get("institution"), degree.get("status"), degree.get("evidence_excerpt"))
        if key not in existing_degrees:
            education.setdefault("degrees", []).append(degree)
            existing_degrees.add(key)
            counts["new_degree_records"] += 1
    _refresh_education_summary(education)

    source_candidate = deepcopy(candidate)
    source_candidate["headline"] = snippet
    source_candidate["founder_relationship_evidence_url"] = source_url
    source_candidate["open_source"] = {"owned_repositories": []}
    source_candidate["research"] = {"categories": []}
    new_skills = extract_skills(source_candidate)
    skills = candidate.setdefault("skills", {"items": []})
    existing_skills = {item["name"].casefold() for item in skills.get("items") or []}
    for item in new_skills["items"]:
        if item["name"].casefold() not in existing_skills:
            skills.setdefault("items", []).append(item)
            existing_skills.add(item["name"].casefold())
            counts["new_skill_records"] += 1
    skills["documented_skill_count"] = len(existing_skills)
    skills["count_interpretation"] = "unique explicitly documented terms; no skill is inferred from title or company sector"

    exit_candidate = deepcopy(source_candidate)
    exit_candidate["founded_companies"] = []
    extracted_exits = extract_exits(exit_candidate)
    new_exits = extracted_exits["exits"]
    career = candidate.setdefault("career_history", {"exits": []})
    existing_exits = {
        (_normalize(item.get("company_name")), item.get("outcome")) for item in career.get("exits") or []
    }
    for item in new_exits:
        key = (_normalize(item.get("company_name")), item.get("outcome"))
        if key not in existing_exits:
            career.setdefault("exits", []).append(item)
            existing_exits.add(key)
            counts["new_exit_records"] += 1
    career["verified_prior_exit_count"] = len(existing_exits) if existing_exits else None
    if existing_exits:
        candidate.setdefault("founder_features", {})["founder_prior_exits"] = len(existing_exits)
        candidate["missing_fields"] = [
            path for path in candidate.get("missing_fields", []) if path != "founder_features.founder_prior_exits"
        ]

    reported_claims = career.setdefault("reported_exit_claims", [])
    existing_claims = {
        (item.get("count"), item.get("source_url"), item.get("evidence_excerpt"))
        for item in reported_claims
    }
    for claim in extracted_exits.get("reported_exit_claims") or []:
        key = (claim.get("count"), claim.get("source_url"), claim.get("evidence_excerpt"))
        if key not in existing_claims:
            reported_claims.append(claim)
            existing_claims.add(key)
            counts["new_reported_exit_counts"] += 1
    reported_counts = [item.get("count") for item in reported_claims if item.get("count") is not None]
    career["reported_prior_exit_count"] = max(reported_counts) if reported_counts else None
    career["reported_count_interpretation"] = (
        "explicit public self-report; kept separate from verified company outcomes"
    )

    if not counts:
        return dict(counts), None
    evidence = {
        "source_id": stable_id("source", candidate["candidate_id"], source_url, snippet[:100]),
        "candidate_id": candidate["candidate_id"],
        "source_type": "company_website",
        "document_name": f"{candidate.get('company_name') or 'Company'} public founder biography",
        "source_uri": source_url,
        "document_date": None,
        "collected_at": _utc_now(),
        "location": "founder biography near exact full-name match",
        "evidence_excerpt": snippet[:1000],
        "verification_status": "founder_reported",
        "confidence": "medium",
        "raw_metadata": {"linkedin_anchor": (_linkedin_anchor(candidate) or (None, None))[1], **dict(counts)},
    }
    candidate.setdefault("source_ids", []).append(evidence["source_id"])
    return dict(counts), evidence


def _merge_wikidata(
    candidate: dict[str, Any], rows: list[dict[str, str]], matcher: InstitutionMatcher
) -> tuple[dict[str, int], dict[str, Any] | None]:
    if not rows:
        return {}, None
    counts = Counter()
    entity_url = rows[0]["person"]
    education = candidate.setdefault("education", {"degrees": []})
    degree_labels = sorted({row["degreeLabel"] for row in rows if row.get("degreeLabel")})
    school_labels = sorted({row["schoolLabel"] for row in rows if row.get("schoolLabel")})
    for degree_label in degree_labels:
        level = next((name for name, pattern in {
            "phd": r"\b(?:phd|doctor|dphil)", "master": r"\b(?:master|mba|msc|ms\b|ma\b)",
            "bachelor": r"\b(?:bachelor|bsc|bs\b|ba\b)"
        }.items() if re.search(pattern, degree_label, re.IGNORECASE)), "other")
        institutions = school_labels if len(school_labels) == 1 else [None]
        for institution in institutions:
            match = matcher.find(institution or "")
            rank = match[0]["qs_world_rank"] if len(match) == 1 else None
            canonical = match[0]["institution"] if len(match) == 1 else institution
            record = {
                "level": level,
                "degree_name": degree_label,
                "institution": canonical,
                "status": "documented_in_wikidata",
                "completed": None,
                "qs_world_rank": rank,
                "qs_edition": QS_EDITION if rank is not None else None,
                "education_source_url": entity_url,
                "qs_source_url": QS_SOURCE_URL if rank is not None else None,
                "evidence_excerpt": f"Wikidata academic degree: {degree_label}",
            }
            key = (record["level"], record["degree_name"], record["institution"])
            existing = {(x.get("level"), x.get("degree_name"), x.get("institution")) for x in education.get("degrees") or []}
            if key not in existing:
                education.setdefault("degrees", []).append(record)
                counts["new_degree_records"] += 1
    documented = education.setdefault("documented_institutions", [])
    for school in school_labels:
        matches = matcher.find(school)
        item = {
            "institution": matches[0]["institution"] if len(matches) == 1 else school,
            "qs_world_rank": matches[0]["qs_world_rank"] if len(matches) == 1 else None,
            "qs_edition": QS_EDITION if len(matches) == 1 else None,
            "source_url": entity_url,
        }
        if item not in documented:
            documented.append(item)
            counts["new_institution_records"] += 1
    _refresh_education_summary(education)

    skills = candidate.setdefault("skills", {"items": []})
    existing_skills = {item["name"].casefold() for item in skills.get("items") or []}
    for field in sorted({row["fieldLabel"] for row in rows if row.get("fieldLabel")}):
        if field.casefold() not in existing_skills:
            skills.setdefault("items", []).append({
                "name": field, "evidence_type": "wikidata_field_of_work",
                "evidence_url": entity_url, "evidence_excerpt": f"Wikidata field of work: {field}",
            })
            existing_skills.add(field.casefold())
            counts["new_skill_records"] += 1
    skills["documented_skill_count"] = len(existing_skills)

    for row in rows:
        for url in [row.get("website"), f"https://orcid.org/{row['orcid']}" if row.get("orcid") else None]:
            if url and url not in candidate.setdefault("profile_urls", []):
                candidate["profile_urls"].append(url)
                counts["new_profile_urls"] += 1
    if not counts:
        return {}, None
    evidence = {
        "source_id": stable_id("source", candidate["candidate_id"], entity_url, "wikidata-linkedin-anchor"),
        "candidate_id": candidate["candidate_id"],
        "source_type": "wikidata",
        "document_name": "Wikidata person record matched by LinkedIn personal profile ID",
        "source_uri": entity_url,
        "document_date": None,
        "collected_at": _utc_now(),
        "location": "P6634 LinkedIn personal profile ID",
        "evidence_excerpt": f"Exact LinkedIn ID match for {candidate['full_name']}.",
        "verification_status": "document_verified",
        "confidence": "high",
        "raw_metadata": {"linkedin_anchor": (_linkedin_anchor(candidate) or (None, None))[1], **dict(counts)},
    }
    candidate.setdefault("source_ids", []).append(evidence["source_id"])
    return dict(counts), evidence


def _coverage(records: Iterable[dict[str, Any]]) -> dict[str, int]:
    rows = list(records)
    return {
        "founders_with_education": sum(bool(row.get("education", {}).get("degrees")) for row in rows),
        "founders_with_bachelor": sum(row.get("education", {}).get("has_bachelor") is True for row in rows),
        "founders_with_master": sum(row.get("education", {}).get("has_master") is True for row in rows),
        "founders_with_phd": sum(row.get("education", {}).get("has_phd") is True for row in rows),
        "founders_with_qs_rank": sum(row.get("education", {}).get("best_qs_world_rank") is not None for row in rows),
        "founders_with_documented_skills": sum((row.get("skills", {}).get("documented_skill_count") or 0) > 0 for row in rows),
        "founders_with_verified_exit": sum(row.get("career_history", {}).get("verified_prior_exit_count") is not None for row in rows),
        "founders_with_reported_exit_count": sum(row.get("career_history", {}).get("reported_prior_exit_count") is not None for row in rows),
    }


def _core_additions(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, int]:
    baseline = {row["candidate_id"]: row for row in before}
    counts = Counter()
    changed: set[str] = set()
    for row in after:
        old = baseline[row["candidate_id"]]
        for level in ("bachelor", "master", "phd"):
            if old.get("education", {}).get(f"has_{level}") is not True and row.get("education", {}).get(f"has_{level}") is True:
                counts["new_documented_degree_levels"] += 1
                changed.add(row["candidate_id"])
        old_institutions = {
            item.get("institution") for key in ("degrees", "documented_institutions")
            for item in old.get("education", {}).get(key, []) if item.get("institution")
        }
        new_institutions = {
            item.get("institution") for key in ("degrees", "documented_institutions")
            for item in row.get("education", {}).get(key, []) if item.get("institution")
        }
        if new_institutions - old_institutions:
            counts["new_institution_names"] += len(new_institutions - old_institutions)
            changed.add(row["candidate_id"])
        if old.get("education", {}).get("best_qs_world_rank") is None and row.get("education", {}).get("best_qs_world_rank") is not None:
            counts["new_founders_with_qs_rank"] += 1
            changed.add(row["candidate_id"])
        old_skills = {item["name"].casefold() for item in old.get("skills", {}).get("items", [])}
        new_skills = {item["name"].casefold() for item in row.get("skills", {}).get("items", [])}
        if new_skills - old_skills:
            counts["new_unique_skill_terms"] += len(new_skills - old_skills)
            changed.add(row["candidate_id"])
        old_exits = {(item.get("company_name"), item.get("outcome")) for item in old.get("career_history", {}).get("exits", [])}
        new_exits = {(item.get("company_name"), item.get("outcome")) for item in row.get("career_history", {}).get("exits", [])}
        if new_exits - old_exits:
            counts["new_verified_exit_records"] += len(new_exits - old_exits)
            changed.add(row["candidate_id"])
        if (
            old.get("career_history", {}).get("reported_prior_exit_count") is None
            and row.get("career_history", {}).get("reported_prior_exit_count") is not None
        ):
            counts["new_founders_with_reported_exit_count"] += 1
            changed.add(row["candidate_id"])
    counts["founders_with_core_field_improvements"] = len(changed)
    return dict(counts)

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            count += 1
    return count


def run_public_web_enrichment(
    input_dir: Path,
    output_dir: Path,
    qs_path: Path,
    *,
    max_domains: int | None = 100,
    workers: int = 12,
    timeout: float = 12,
    max_extra_pages: int = 2,
    user_agent: str = "LARS-Founder-Screening/0.3 (https://github.com/quanshengjie604-beep/LARS; research prototype)",
) -> dict[str, Any]:
    candidates = read_jsonl(input_dir / "sourcing_candidates.jsonl")
    baseline_candidates = deepcopy(candidates)
    evidence = {item["source_id"]: item for item in read_jsonl(input_dir / "evidence_registry.jsonl")}
    matcher = InstitutionMatcher(load_qs_rankings_xlsx(qs_path))
    by_id = {item["candidate_id"]: item for item in candidates}
    wikidata = query_wikidata_by_linkedin(candidates, user_agent)
    totals = Counter()
    founders_changed: set[str] = set()
    for candidate_id, rows in wikidata.items():
        counts, record = _merge_wikidata(by_id[candidate_id], rows, matcher)
        totals.update(counts)
        if record:
            evidence[record["source_id"]] = record
            founders_changed.add(candidate_id)
    totals["wikidata_founders_matched"] = len(wikidata)

    domain_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        root = _root_url(candidate.get("company_url") or "")
        if root:
            domain_groups[root].append(candidate)
    roots = sorted(domain_groups, key=lambda root: (-len(domain_groups[root]), root))
    if max_domains is not None:
        roots = roots[:max_domains]
    status_counts = Counter()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                _fetch_company_domain, root, domain_groups[root], user_agent, timeout, max_extra_pages
            ): root
            for root in roots
        }
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            status_counts[result["status"]] += 1
            totals["company_pages_fetched"] += len(result["pages"])
            for full_name, match in result["snippets"].items():
                candidate = next((item for item in domain_groups[result["root"]] if item["full_name"] == full_name), None)
                if not candidate:
                    continue
                totals["founder_name_matches"] += 1
                counts, record = _merge_company_snippet(candidate, match["snippet"], match["url"], matcher)
                totals.update(counts)
                if record:
                    evidence[record["source_id"]] = record
                    founders_changed.add(candidate["candidate_id"])
            if index % 25 == 0:
                LOGGER.info("Processed %d/%d company domains", index, len(roots))
    write_jsonl(output_dir / "sourcing_candidates.jsonl", candidates)
    write_jsonl(output_dir / "evidence_registry.jsonl", evidence.values())
    report = {
        "generated_at": _utc_now(),
        "input_candidates": len(candidates),
        "output_candidates": len(candidates),
        "linkedin_anchors": sum(1 for item in candidates if _linkedin_anchor(item)),
        "unique_company_domains_available": len(domain_groups),
        "company_domains_attempted": len(roots),
        "company_domain_status": dict(status_counts),
        "founders_with_new_facts": len(founders_changed),
        "coverage_before": _coverage(baseline_candidates),
        "coverage_after": _coverage(candidates),
        "verified_core_additions": _core_additions(baseline_candidates, candidates),
        "new_evidence_records": len(evidence) - len(read_jsonl(input_dir / "evidence_registry.jsonl")),
        "additions": dict(totals),
        "openalex_status": "skipped: OPENALEX_API_KEY is not configured",
        "linkedin_policy": "LinkedIn pages were not requested; public vanity IDs were used only as identity anchors.",
    }
    (output_dir / "public_web_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report
