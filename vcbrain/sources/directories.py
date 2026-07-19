"""Compliant, deterministic accelerator and investor directory adapters.

The adapters in this module deliberately distinguish directory membership from a
funding event.  Batch/cohort labels, current-stage taxonomies, portfolio-page
publication dates, and an investor's first-investment date are useful evidence,
but none of them is emitted as a :class:`~vcbrain.models.FundingRound`.

Y Combinator is read only from an explicitly supplied, authorised JSON or CSV
export.  StartX is read through an authenticated Consider Boards API.  The two
public website adapters use only their documented/public surfaces and degrade to
structured ``SourceRun`` results instead of aborting a wider collection run.
"""

from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..config import Settings
from ..evidence import make_evidence
from ..http import Fetcher
from ..models import Company, DirectoryResult, SourceRun
from ..util import iso_date, normalize_url, normalized_name, slugify, unique_sorted, utc_now_iso

YC_SOURCE = "yc"
A16Z_SOURCE = "a16z"
STARTX_SOURCE = "startx"
PEAR_SOURCE = "pear"

A16Z_PORTFOLIO_URL = "https://a16z.com/portfolio/"
A16Z_INVESTMENT_LIST_URL = "https://a16z.com/investment-list/"
STARTX_CONSIDER_API = "https://boards.considerapi.com/v0/companies"
PEAR_WP_API = "https://pear.vc/wp-json/wp/v2"

_PEAR_TAXONOMIES = ("first_investment", "current_stage", "pear_vc_company_sector")
_KEY_RE = re.compile(r"[^a-z0-9]+")
_TAG_RE = re.compile(r"<[^>]+>")
_YEAR_RE = re.compile(r"\b(18|19|20|21)\d{2}\b")


def _clean_text(value: Any) -> str | None:
    """Return compact human-readable text without treating zero as missing."""
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("rendered", "name", "title", "label", "value"):
            if key in value:
                return _clean_text(value[key])
        return None
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    text = " ".join(text.replace("\u00a0", " ").split()).strip()
    return text or None


def _key(value: Any) -> str:
    return _KEY_RE.sub("_", str(value).casefold()).strip("_")


def _get(record: Mapping[str, Any], *names: str) -> Any:
    """Case/punctuation-insensitive field lookup, including dotted paths."""
    for name in names:
        current: Any = record
        found = True
        for part in name.split("."):
            if not isinstance(current, Mapping):
                found = False
                break
            if part in current:
                current = current[part]
                continue
            wanted = _key(part)
            actual = next((key for key in current if _key(key) == wanted), None)
            if actual is None:
                found = False
                break
            current = current[actual]
        if found and current not in (None, ""):
            return current
    return None


def _strings(value: Any, *, split_commas: bool = True) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, Mapping):
        text = _clean_text(value)
        return [text] if text else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return unique_sorted(item for value_item in value for item in _strings(value_item, split_commas=split_commas))
    text = _clean_text(value)
    if not text:
        return []
    if text[:1] in "[{" and text[-1:] in "]}":
        try:
            return _strings(json.loads(text), split_commas=split_commas)
        except json.JSONDecodeError:
            pass
    separator = r"[,;|]" if split_commas else r"[;|]"
    return unique_sorted(_clean_text(part) for part in re.split(separator, text))


def _year(value: Any) -> int | None:
    if isinstance(value, int) and 1800 <= value <= 2199:
        return value
    text = _clean_text(value)
    match = _YEAR_RE.search(text or "")
    return int(match.group(0)) if match else None


def _integer(value: Any) -> int | None:
    """Return only an explicit non-negative integer; never midpoint a range."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    text = _clean_text(value)
    if text and re.fullmatch(r"\d[\d,]*", text):
        return int(text.replace(",", ""))
    return None


def _http_url(value: Any, *, base: str | None = None) -> str | None:
    text = _clean_text(value)
    if not text:
        return None
    if base:
        text = urljoin(base, text)
    parsed = urlsplit(text)
    if parsed.scheme and parsed.scheme.casefold() not in {"http", "https"}:
        return None
    return normalize_url(text)


def _first_http_url(values: Iterable[Any], *, base: str | None = None) -> str | None:
    for value in values:
        url = _http_url(value, base=base)
        if url:
            return url
    return None


def _directory_urls(values: Iterable[Any], *, base: str | None = None) -> list[str]:
    return unique_sorted(_http_url(value, base=base) for value in values)


def _sorted_companies(companies: Iterable[Company]) -> list[Company]:
    """Deduplicate repeated DOM/API rows without merging unrelated companies."""
    unique: dict[str, Company] = {}
    for company in companies:
        source_identity = next(
            (
                f"{key}:{company.external_ids[key]}"
                for key in ("yc", "a16z", "a16z_list", "startx_consider", "pear_wp")
                if company.external_ids.get(key)
            ),
            None,
        )
        identity = source_identity or company.identity_key
        prior = unique.get(identity)
        if prior is None:
            unique[identity] = company
            continue
        richness = lambda item: (
            bool(item.domain),
            len(item.description or ""),
            len(item.sectors) + len(item.founders) + len(item.statuses),
            len(item.external_ids),
        )
        if richness(company) > richness(prior):
            unique[identity] = company
    return sorted(unique.values(), key=lambda item: (item.name.casefold(), item.startup_id))


def _in_scope(companies: Iterable[Company], settings: Settings | None) -> list[Company]:
    """Apply the configured age bound only where a real founding year exists."""
    if settings is None:
        return list(companies)
    oldest = None
    if settings.max_company_age_years is not None:
        oldest = settings.as_of.year - settings.max_company_age_years
    return [
        company
        for company in companies
        if (company.founded_year is None or company.founded_year <= settings.as_of.year)
        and (oldest is None or company.founded_year is None or company.founded_year >= oldest)
    ]


def _result(
    source: str,
    *,
    collected_at: str,
    companies: Iterable[Company] = (),
    evidence: Iterable[Any] = (),
    requests: int = 0,
    errors: Iterable[str] = (),
    status: str | None = None,
    skipped_reason: str | None = None,
) -> DirectoryResult:
    company_rows = _sorted_companies(companies)
    error_rows = sorted({str(error) for error in errors if str(error)}, key=str.casefold)
    final_status = status or ("partial" if company_rows and error_rows else "error" if error_rows else "ok")
    run = SourceRun(
        source=source,
        status=final_status,
        started_at=collected_at,
        finished_at=collected_at,
        requests=requests,
        cache_hits=0,
        records=len(company_rows),
        errors=error_rows,
        skipped_reason=skipped_reason,
    )
    return DirectoryResult(
        source=source,
        companies=company_rows,
        evidence=sorted(evidence, key=lambda item: (item.startup_id, item.source_id)),
        run=run,
    )


def _stamp(collected_at: str | None) -> str:
    return collected_at or utc_now_iso()


def _snapshot_date(collected_at: str) -> str | None:
    return iso_date(collected_at)


def _attach_evidence(company: Company, evidence: Any) -> None:
    company.source_ids = unique_sorted([*company.source_ids, evidence.source_id])


# ---------------------------------------------------------------------------
# Y Combinator: explicitly supplied, authorised export only


def parse_yc_json(text: str) -> list[dict[str, Any]]:
    """Parse an authorised YC JSON export into raw rows."""
    payload = json.loads(text)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        rows = next(
            (
                value
                for key in ("companies", "records", "results", "data", "items")
                if isinstance((value := _get(payload, key)), list)
            ),
            [payload] if _get(payload, "name", "company_name") else [],
        )
    else:
        raise ValueError("YC JSON export must contain an object or a list of objects")
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def parse_yc_csv(text: str) -> list[dict[str, Any]]:
    """Parse an authorised YC CSV export into raw rows."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if reader.fieldnames is None:
        return []
    return [dict(row) for row in reader]


def parse_yc_export(text: str, *, format_name: str) -> list[dict[str, Any]]:
    normalized = format_name.casefold().removeprefix(".")
    if normalized == "json":
        return parse_yc_json(text)
    if normalized == "csv":
        return parse_yc_csv(text)
    raise ValueError("YC export must be JSON or CSV")


def _yc_company(record: Mapping[str, Any]) -> Company | None:
    name = _clean_text(_get(record, "name", "company_name", "company"))
    if not name:
        return None
    slug = _clean_text(_get(record, "slug", "company_slug"))
    website = _first_http_url(
        (
            _get(record, "website", "website_url", "company_website", "external_url"),
            _get(record, "domain"),
        )
    )
    directory_url = _first_http_url(
        (_get(record, "directory_url", "yc_url", "profile_url", "url"),),
        base="https://www.ycombinator.com",
    )
    if directory_url is None and slug:
        directory_url = _http_url(f"/companies/{slug}", base="https://www.ycombinator.com")
    batch_values = _strings(_get(record, "batches", "batch", "cohort"), split_commas=False)
    sectors = unique_sorted(
        [
            *_strings(_get(record, "sectors", "sector")),
            *_strings(_get(record, "industry")),
            *_strings(_get(record, "subindustry", "sub_industry")),
        ]
    )
    statuses = unique_sorted(
        [
            *(f"YC status: {value}" for value in _strings(_get(record, "status"))),
            *(f"YC stage: {value}" for value in _strings(_get(record, "stage"))),
        ]
    )
    external_id = _clean_text(_get(record, "id", "object_id", "objectID", "company_id", "slug"))
    description = _clean_text(
        _get(record, "description", "long_description", "one_liner", "one_liner_description")
    )
    return Company(
        name=name,
        website=website,
        description=description,
        accelerators=["Y Combinator"],
        batches=batch_values,
        sectors=sectors,
        geography=_clean_text(_get(record, "all_locations", "location", "headquarters")),
        founded_year=_year(_get(record, "founded_year", "year_founded", "founded")),
        team_size=_integer(_get(record, "team_size", "teamSize", "staff_count", "staffCount")),
        founders=_strings(_get(record, "founders", "founder_names", "team")),
        statuses=statuses,
        directory_urls=[directory_url] if directory_url else [],
        external_ids={"yc": external_id} if external_id else {},
    )


def parse_yc_records(records: Iterable[Mapping[str, Any]]) -> list[Company]:
    """Convert YC export rows to companies without inferring dates from cohorts."""
    return _sorted_companies(company for record in records if (company := _yc_company(record)) is not None)


async def load_yc_export(
    path: str | Path | None,
    *,
    settings: Settings | None = None,
    collected_at: str | None = None,
) -> DirectoryResult:
    """Read an explicitly authorised local YC JSON/CSV export.

    Missing input is a structured skip.  This function never reaches YC's web
    directory or any unofficial mirror.
    """
    stamp = _stamp(collected_at)
    if path is None:
        return _result(
            YC_SOURCE,
            collected_at=stamp,
            status="skipped",
            skipped_reason="authorised_yc_export_not_provided",
        )
    export_path = Path(path)
    if not export_path.is_file():
        return _result(
            YC_SOURCE,
            collected_at=stamp,
            status="skipped",
            skipped_reason="authorised_yc_export_not_found",
        )
    try:
        text = await asyncio.to_thread(export_path.read_text, encoding="utf-8-sig")
        records = await asyncio.to_thread(parse_yc_export, text, format_name=export_path.suffix)
        companies = _in_scope(parse_yc_records(records), settings)
        evidence = []
        for company in companies:
            details = [f"The authorised YC export lists {company.name}"]
            if company.batches:
                details.append(f"with cohort {', '.join(company.batches)}")
            details.append("; the cohort is not treated as a funding date.")
            item = make_evidence(
                startup_id=company.startup_id,
                channel="yc_authorized_export",
                source_type="manual_research",
                document_name="Authorised Y Combinator company export",
                source_uri=str(export_path),
                document_date=_snapshot_date(stamp),
                collected_at=stamp,
                location=company.directory_urls[0] if company.directory_urls else None,
                excerpt=" ".join(details),
                verification_status="document_verified",
                confidence="medium",
            )
            _attach_evidence(company, item)
            evidence.append(item)
        return _result(YC_SOURCE, collected_at=stamp, companies=companies, evidence=evidence)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, csv.Error) as exc:
        return _result(YC_SOURCE, collected_at=stamp, errors=[f"YC export: {exc}"])


# ---------------------------------------------------------------------------
# a16z public portfolio and names-only fallback


def _decode_json_attribute(value: str) -> Mapping[str, Any] | None:
    candidate: Any = value
    for _ in range(3):
        if isinstance(candidate, Mapping):
            return candidate
        if not isinstance(candidate, str):
            return None
        try:
            candidate = json.loads(html.unescape(candidate).strip())
        except json.JSONDecodeError:
            candidate = html.unescape(candidate)
            if candidate == value:
                return None
            value = candidate
    return candidate if isinstance(candidate, Mapping) else None


def parse_a16z_portfolio_html(text: str) -> list[dict[str, Any]]:
    """Extract and deduplicate ``data-company`` JSON from the a16z portfolio."""
    soup = BeautifulSoup(text, "html.parser")
    unique: dict[str, dict[str, Any]] = {}
    for node in soup.select("[data-company]"):
        raw = node.attrs.get("data-company")
        if not isinstance(raw, str):
            continue
        record = _decode_json_attribute(raw)
        if record is None:
            continue
        row = dict(record)
        identity = _clean_text(_get(row, "id", "company_id", "permalink", "name"))
        if not identity:
            continue
        identity_key = normalized_name(identity)
        prior = unique.get(identity_key)
        if prior is None or sum(value not in (None, "", [], {}) for value in row.values()) > sum(
            value not in (None, "", [], {}) for value in prior.values()
        ):
            unique[identity_key] = row
    return sorted(unique.values(), key=lambda row: (_clean_text(_get(row, "name")) or "").casefold())


_A16Z_NON_COMPANY_TEXT = {
    "about",
    "companies",
    "contact",
    "investment list",
    "jobs",
    "news & content",
    "portfolio",
    "privacy",
    "terms",
}


def parse_a16z_investment_list_html(text: str) -> list[str]:
    """Parse company names from a16z's lower-fidelity investment list."""
    soup = BeautifulSoup(text, "html.parser")
    for node in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        node.decompose()
    heading = next(
        (
            node
            for node in soup.find_all(re.compile(r"^h[1-6]$"))
            if (_clean_text(node.get_text(" ", strip=True)) or "").casefold() == "investment list"
        ),
        None,
    )
    scope = heading.find_parent("main") if heading is not None else soup.find("main")
    scope = scope or soup
    candidates = heading.find_all_next("li") if heading is not None else scope.find_all("li")
    names: list[str] = []
    for node in candidates:
        if node.find_parent("main") is not scope and scope.name == "main":
            continue
        name = _clean_text(node.get_text(" ", strip=True))
        if not name or len(name) > 120 or name.casefold() in _A16Z_NON_COMPANY_TEXT:
            continue
        names.append(name)
    if not names:
        # Some revisions render the list as cards/links instead of list items.
        for node in scope.select("[data-company-name], .company-name, a[href*='/portfolio/']"):
            name = _clean_text(node.attrs.get("data-company-name") or node.get_text(" ", strip=True))
            if name and len(name) <= 120 and name.casefold() not in _A16Z_NON_COMPANY_TEXT:
                names.append(name)
    return unique_sorted(names)


def _a16z_company(
    record: Mapping[str, Any], *, default_directory_url: str = A16Z_PORTFOLIO_URL
) -> Company | None:
    name = _clean_text(_get(record, "name", "company_name", "title"))
    if not name:
        return None
    website = _first_http_url(
        (
            _get(record, "external_url", "company_url", "website", "website_url", "url"),
        )
    )
    directory_url = _first_http_url(
        (_get(record, "permalink", "portfolio_url", "profile_url"),), base="https://a16z.com"
    )
    founders = _strings(_get(record, "founders_list", "founders", "founder_names"))
    sectors = unique_sorted(
        [
            *_strings(_get(record, "focus_areas", "focus_area")),
            *_strings(_get(record, "sectors", "sector", "verticals")),
        ]
    )
    statuses = unique_sorted(
        [
            *(f"a16z status: {value}" for value in _strings(_get(record, "status"))),
            *(f"a16z listed investment stage: {value}" for value in _strings(_get(record, "stages"))),
        ]
    )
    external_id = _clean_text(_get(record, "id", "company_id", "slug", "permalink"))
    initial_investment_date = _clean_text(
        _get(record, "initial_a16z_date_funded", "initial_a16z_investment_date", "initial_investment_date")
    )
    initial_stage = _clean_text(
        _get(record, "initial_a16z_stage", "initial_investment_stage", "initial_stage")
    )
    listed_stages = _strings(_get(record, "stages"))
    if initial_stage is None and initial_investment_date and len(listed_stages) == 1:
        # A single listed investment stage is unambiguous; multiple stages are
        # retained as labels above and are not guessed into chronological order.
        initial_stage = listed_stages[0]
    announcement_url = _first_http_url(
        (
            _get(record, "announcement_url", "initial_announcement_url"),
            _get(record, "announcement.url", "announcement.href"),
            _get(record, "announcement"),
        ),
        base="https://a16z.com",
    )
    external_ids = {"a16z": external_id} if external_id else {}
    # These are investor-specific source fields, not canonical round attributes.
    # Keeping them namespaced lets the pipeline emit a distinct initial-investment
    # event without pretending the directory describes the company's whole round.
    if initial_investment_date:
        external_ids["a16z_initial_investment_date"] = initial_investment_date
    if initial_stage:
        external_ids["a16z_initial_stage"] = initial_stage
    if announcement_url:
        external_ids["a16z_announcement_url"] = announcement_url
    return Company(
        name=name,
        website=website,
        description=_clean_text(_get(record, "description", "excerpt", "one_liner")),
        accelerators=["Andreessen Horowitz (a16z)"],
        sectors=sectors,
        founded_year=_year(_get(record, "year_founded", "founded_year")),
        founders=founders,
        statuses=statuses,
        directory_urls=[directory_url] if directory_url else [default_directory_url],
        external_ids=external_ids,
    )


def parse_a16z_records(
    records: Iterable[Mapping[str, Any]], *, default_directory_url: str = A16Z_PORTFOLIO_URL
) -> list[Company]:
    return _sorted_companies(
        company
        for record in records
        if (company := _a16z_company(record, default_directory_url=default_directory_url)) is not None
    )


def _a16z_names_to_companies(
    names: Iterable[str], *, directory_url: str = A16Z_INVESTMENT_LIST_URL
) -> list[Company]:
    return _sorted_companies(
        Company(
            name=name,
            accelerators=["Andreessen Horowitz (a16z)"],
            statuses=["Listed in a16z investment list"],
            directory_urls=[directory_url],
            external_ids={"a16z_list": slugify(name)},
        )
        for name in names
        if _clean_text(name)
    )


async def fetch_a16z_directory(
    fetcher: Fetcher,
    *,
    settings: Settings | None = None,
    collected_at: str | None = None,
    portfolio_url: str = A16Z_PORTFOLIO_URL,
    fallback_url: str = A16Z_INVESTMENT_LIST_URL,
) -> DirectoryResult:
    """Collect a16z portfolio companies, falling back to names-only data."""
    stamp = _stamp(collected_at)
    requests = 0
    errors: list[str] = []
    records: list[dict[str, Any]] = []
    try:
        requests += 1
        portfolio_html = await fetcher.get_text(portfolio_url)
        records = await asyncio.to_thread(parse_a16z_portfolio_html, portfolio_html)
    except Exception as exc:  # source isolation is intentional at adapter boundaries
        errors.append(f"a16z portfolio: {exc}")

    names_only = False
    if not records:
        try:
            requests += 1
            fallback_html = await fetcher.get_text(fallback_url)
            names = await asyncio.to_thread(parse_a16z_investment_list_html, fallback_html)
            companies = _a16z_names_to_companies(names, directory_url=fallback_url)
            names_only = True
        except Exception as exc:  # see source-isolation note above
            errors.append(f"a16z investment list: {exc}")
            companies = []
    else:
        companies = parse_a16z_records(records, default_directory_url=portfolio_url)
    companies = _in_scope(companies, settings)

    by_id: dict[str, Mapping[str, Any]] = {}
    by_name: dict[str, Mapping[str, Any]] = {}
    for record in records:
        record_id = _clean_text(_get(record, "id", "company_id", "slug", "permalink"))
        if record_id:
            by_id[record_id] = record
        record_name = _clean_text(_get(record, "name", "company_name", "title"))
        if record_name:
            by_name[normalized_name(record_name)] = record

    evidence = []
    for company in companies:
        record_id = company.external_ids.get("a16z")
        record = by_id.get(record_id or "") or by_name.get(normalized_name(company.name), {})
        initial_date = _clean_text(
            _get(record, "initial_a16z_date_funded", "initial_investment_date", "initial_a16z_investment_date")
        )
        excerpt = f"The a16z {'investment list' if names_only else 'portfolio directory'} lists {company.name}."
        if initial_date:
            excerpt += (
                f" It labels {initial_date} as the initial a16z investment date; this adapter does not "
                "interpret that investor-specific date as a complete funding round."
            )
        item = make_evidence(
            startup_id=company.startup_id,
            channel="a16z_investment_list" if names_only else "a16z_portfolio",
            source_type="manual_research",
            document_name="a16z investment list" if names_only else "a16z portfolio directory",
            source_uri=(
                company.directory_urls[0]
                if company.directory_urls
                else (fallback_url if names_only else portfolio_url)
            ),
            document_date=_snapshot_date(stamp),
            collected_at=stamp,
            location=company.directory_urls[0] if company.directory_urls else None,
            excerpt=excerpt,
            verification_status="document_verified",
            confidence="low" if names_only else "medium",
        )
        _attach_evidence(company, item)
        evidence.append(item)
    return _result(
        A16Z_SOURCE,
        collected_at=stamp,
        companies=companies,
        evidence=evidence,
        requests=requests,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# StartX authenticated Consider Boards API


def _page_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("data", "companies", "results", "items", "records"):
        value = _get(payload, key)
        if isinstance(value, list):
            return [dict(row) for row in value if isinstance(row, Mapping)]
        if isinstance(value, Mapping):
            nested = _page_records(value)
            if nested:
                return nested
    return []


def _next_link(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    value = _get(payload, "links.next", "pagination.next", "next")
    if isinstance(value, Mapping):
        value = _get(value, "href", "url")
    return _clean_text(value)


def parse_startx_page(payload: Any) -> tuple[list[dict[str, Any]], str | None]:
    """Return one Consider API page and its exact ``links.next`` cursor URL."""
    return _page_records(payload), _next_link(payload)


def _startx_company(record: Mapping[str, Any]) -> Company | None:
    attributes = _get(record, "attributes")
    flattened: dict[str, Any] = dict(record)
    if isinstance(attributes, Mapping):
        flattened.update(attributes)
    name = _clean_text(_get(flattened, "name", "company_name", "title"))
    if not name:
        return None
    links = _get(record, "links")
    website = _first_http_url(
        (
            _get(flattened, "website", "website_url", "company_url", "url", "domain"),
            _get(links, "website") if isinstance(links, Mapping) else None,
        )
    )
    directory_url = _first_http_url(
        (
            _get(flattened, "board_url", "profile_url", "consider_url"),
            _get(links, "html", "self") if isinstance(links, Mapping) else None,
        )
    )
    markets = _get(flattened, "markets", "sectors", "industries", "industry")
    funding_stage = _strings(_get(flattened, "funding", "funding_stage", "stage"))
    status = _strings(_get(flattened, "status"))
    external_id = _clean_text(_get(record, "id") or _get(flattened, "slug", "company_id"))
    return Company(
        name=name,
        website=website,
        description=_clean_text(_get(flattened, "description", "one_liner", "summary")),
        accelerators=["StartX"],
        batches=_strings(_get(flattened, "batch", "cohort"), split_commas=False),
        sectors=_strings(markets),
        geography=_clean_text(_get(flattened, "location", "headquarters", "city")),
        founded_year=_year(_get(flattened, "founded_year", "year_founded", "founded")),
        team_size=_integer(
            _get(flattened, "team_size", "teamSize", "staff_count", "staffCount", "employee_count")
        ),
        founders=_strings(_get(flattened, "founders", "founder_names")),
        statuses=unique_sorted(
            [
                *(f"StartX status: {value}" for value in status),
                *(f"StartX current funding stage: {value}" for value in funding_stage),
            ]
        ),
        directory_urls=[directory_url] if directory_url else [],
        external_ids={"startx_consider": external_id} if external_id else {},
    )


def parse_startx_records(records: Iterable[Mapping[str, Any]]) -> list[Company]:
    return _sorted_companies(company for record in records if (company := _startx_company(record)) is not None)


async def fetch_startx_directory(
    fetcher: Fetcher,
    *,
    api_key: str | None,
    settings: Settings | None = None,
    collected_at: str | None = None,
    companies_url: str = STARTX_CONSIDER_API,
    max_pages: int = 1_000,
) -> DirectoryResult:
    """Collect StartX's authorised Consider board, following ``links.next``."""
    stamp = _stamp(collected_at)
    if not api_key:
        return _result(
            STARTX_SOURCE,
            collected_at=stamp,
            status="skipped",
            skipped_reason="startx_consider_api_key_not_provided",
        )
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    current = companies_url
    visited: set[str] = set()
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    requests = 0
    while current and current not in visited and requests < max_pages:
        visited.add(current)
        try:
            requests += 1
            payload = await fetcher.get_json(current, headers=headers)
            page, next_link = parse_startx_page(payload)
            records.extend(page)
            current = urljoin(current, next_link) if next_link else ""
        except Exception as exc:  # source isolation is intentional at adapter boundaries
            errors.append(f"StartX Consider API: {exc}")
            break
    if current and current in visited:
        errors.append("StartX Consider API returned a pagination loop")
    elif current and requests >= max_pages:
        errors.append(f"StartX Consider API exceeded max_pages={max_pages}")

    companies = _in_scope(parse_startx_records(records), settings)
    evidence = []
    for company in companies:
        excerpt = f"The authenticated StartX Consider board lists {company.name}."
        funding_labels = [value for value in company.statuses if value.startswith("StartX current funding stage:")]
        if funding_labels:
            excerpt += " Its current stage label is directory metadata, not evidence of a dated funding round."
        item = make_evidence(
            startup_id=company.startup_id,
            channel="startx_consider_api",
            source_type="manual_research",
            document_name="StartX authenticated Consider board",
            source_uri=company.directory_urls[0] if company.directory_urls else companies_url,
            document_date=_snapshot_date(stamp),
            collected_at=stamp,
            location=company.directory_urls[0] if company.directory_urls else None,
            excerpt=excerpt,
            verification_status="document_verified",
            confidence="medium",
        )
        _attach_evidence(company, item)
        evidence.append(item)
    return _result(
        STARTX_SOURCE,
        collected_at=stamp,
        companies=companies,
        evidence=evidence,
        requests=requests,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Pear VC WordPress REST API, with robots-aware pacing


def parse_pear_taxonomy(payload: Any) -> dict[str, str]:
    """Parse WordPress taxonomy terms into ``id -> display name``."""
    terms: dict[str, str] = {}
    rows = payload if isinstance(payload, list) else _page_records(payload)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        identifier = _clean_text(_get(row, "id", "term_id", "slug"))
        name = _clean_text(_get(row, "name", "title.rendered", "slug"))
        if identifier and name:
            terms[identifier] = name
    return dict(sorted(terms.items(), key=lambda item: item[0]))


def _term_names(value: Any, lookup: Mapping[str, str]) -> list[str]:
    names: list[str] = []
    raw_values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else [value]
    for raw in raw_values:
        if isinstance(raw, Mapping):
            name = _clean_text(_get(raw, "name", "title.rendered", "slug"))
        else:
            key = _clean_text(raw)
            name = lookup.get(key or "")
            if name is None and key and not key.isdigit():
                name = key
        if name:
            names.append(name)
    return unique_sorted(names)


def _pear_company(record: Mapping[str, Any], taxonomies: Mapping[str, Mapping[str, str]]) -> Company | None:
    name = _clean_text(_get(record, "title.rendered", "name", "title"))
    if not name:
        return None
    website = _first_http_url(
        (
            _get(record, "meta.website_url", "meta.company_website", "meta.company_url", "meta.website"),
            _get(record, "acf.website_url", "acf.company_website", "acf.company_url", "acf.website"),
            _get(record, "website_url", "company_website", "external_url"),
        )
    )
    # WordPress `link` is the Pear profile and never substitutes for the company's website.
    directory_url = _first_http_url((_get(record, "link", "guid.rendered"),), base="https://pear.vc")
    first_investment = _term_names(
        _get(record, "first_investment", "meta.first_investment"), taxonomies.get("first_investment", {})
    )
    current_stage = _term_names(
        _get(record, "current_stage", "meta.current_stage"), taxonomies.get("current_stage", {})
    )
    sectors = _term_names(
        _get(record, "pear_vc_company_sector", "sector", "sectors"),
        taxonomies.get("pear_vc_company_sector", {}),
    )
    external_id = _clean_text(_get(record, "id", "slug"))
    description = _clean_text(
        _get(
            record,
            "meta.short_description",
            "acf.short_description",
            "excerpt.rendered",
            "content.rendered",
        )
    )
    return Company(
        name=name,
        website=website,
        description=description,
        accelerators=["Pear VC"],
        sectors=sectors,
        geography=_clean_text(
            _get(record, "meta.headquarters", "meta.location", "acf.headquarters", "acf.location")
        ),
        founded_year=_year(_get(record, "meta.founded_year", "acf.founded_year", "year_founded")),
        founders=_strings(_get(record, "meta.founders", "acf.founders", "founders")),
        statuses=unique_sorted(
            [
                *(f"Pear first-investment stage: {value}" for value in first_investment),
                *(f"Pear current stage: {value}" for value in current_stage),
            ]
        ),
        directory_urls=[directory_url] if directory_url else [],
        external_ids={"pear_wp": external_id} if external_id else {},
    )


def parse_pear_records(
    records: Iterable[Mapping[str, Any]],
    taxonomies: Mapping[str, Mapping[str, str]],
) -> list[Company]:
    """Convert Pear posts; taxonomy stages remain labels, never round events."""
    return _sorted_companies(
        company for record in records if (company := _pear_company(record, taxonomies)) is not None
    )


class _AsyncPacer:
    """Serialize request starts with an explicit delay between them."""

    def __init__(self, delay: float, sleep: Callable[[float], Awaitable[None]]):
        if delay < 0:
            raise ValueError("pace_seconds must be non-negative")
        self.delay = delay
        self.sleep = sleep
        self._started = False

    async def wait(self) -> None:
        if self._started and self.delay:
            await self.sleep(self.delay)
        self._started = True


async def fetch_pear_directory(
    fetcher: Fetcher,
    *,
    settings: Settings | None = None,
    collected_at: str | None = None,
    api_root: str = PEAR_WP_API,
    pace_seconds: float = 10.0,
    per_page: int = 100,
    max_pages: int = 100,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DirectoryResult:
    """Collect Pear's WP REST portfolio while honoring its 10-second crawl delay."""
    stamp = _stamp(collected_at)
    pacer = _AsyncPacer(pace_seconds, sleep)
    requests = 0
    errors: list[str] = []
    taxonomies: dict[str, dict[str, str]] = {}

    for taxonomy in _PEAR_TAXONOMIES:
        try:
            await pacer.wait()
            requests += 1
            payload = await fetcher.get_json(
                f"{api_root.rstrip('/')}/{taxonomy}", params={"per_page": per_page, "page": 1}
            )
            taxonomies[taxonomy] = parse_pear_taxonomy(payload)
        except Exception as exc:  # source isolation is intentional at adapter boundaries
            errors.append(f"Pear taxonomy {taxonomy}: {exc}")
            taxonomies[taxonomy] = {}

    records: list[dict[str, Any]] = []
    endpoint = f"{api_root.rstrip('/')}/pear_vc_company"
    for page_number in range(1, max_pages + 1):
        try:
            await pacer.wait()
            requests += 1
            payload = await fetcher.get_json(endpoint, params={"per_page": per_page, "page": page_number})
        except Exception as exc:  # a page beyond the WP maximum is a normal pagination terminator
            if page_number == 1:
                errors.append(f"Pear company directory: {exc}")
            break
        if not isinstance(payload, list):
            if page_number == 1:
                errors.append("Pear company directory returned a non-list payload")
            break
        page = [dict(row) for row in payload if isinstance(row, Mapping)]
        records.extend(page)
        if len(payload) < per_page:
            break
    else:
        errors.append(f"Pear company directory reached max_pages={max_pages}")

    companies = _in_scope(parse_pear_records(records, taxonomies), settings)
    record_by_id = {
        identifier: record
        for record in records
        if (identifier := _clean_text(_get(record, "id", "slug"))) is not None
    }
    evidence = []
    for company in companies:
        record = record_by_id.get(company.external_ids.get("pear_wp", ""), {})
        document_date = iso_date(_get(record, "modified_gmt", "modified", "date_gmt", "date"))
        excerpt = f"The Pear VC portfolio directory lists {company.name}."
        if any(value.startswith("Pear first-investment stage:") for value in company.statuses):
            excerpt += " Pear's first-investment taxonomy is a stage label, not a funding-round date."
        item = make_evidence(
            startup_id=company.startup_id,
            channel="pear_wp_rest",
            source_type="manual_research",
            document_name="Pear VC portfolio directory",
            source_uri=company.directory_urls[0] if company.directory_urls else endpoint,
            document_date=document_date or _snapshot_date(stamp),
            collected_at=stamp,
            location=company.directory_urls[0] if company.directory_urls else None,
            excerpt=excerpt,
            verification_status="document_verified",
            confidence="medium",
        )
        _attach_evidence(company, item)
        evidence.append(item)
    return _result(
        PEAR_SOURCE,
        collected_at=stamp,
        companies=companies,
        evidence=evidence,
        requests=requests,
        errors=errors,
    )


# Consistent collection-oriented aliases for callers that compose many sources.
collect_yc_directory = load_yc_export
collect_a16z_directory = fetch_a16z_directory
collect_startx_directory = fetch_startx_directory
collect_pear_directory = fetch_pear_directory


__all__ = [
    "A16Z_INVESTMENT_LIST_URL",
    "A16Z_PORTFOLIO_URL",
    "PEAR_WP_API",
    "STARTX_CONSIDER_API",
    "collect_a16z_directory",
    "collect_pear_directory",
    "collect_startx_directory",
    "collect_yc_directory",
    "fetch_a16z_directory",
    "fetch_pear_directory",
    "fetch_startx_directory",
    "load_yc_export",
    "parse_a16z_investment_list_html",
    "parse_a16z_portfolio_html",
    "parse_a16z_records",
    "parse_pear_records",
    "parse_pear_taxonomy",
    "parse_startx_page",
    "parse_startx_records",
    "parse_yc_csv",
    "parse_yc_export",
    "parse_yc_json",
    "parse_yc_records",
]
