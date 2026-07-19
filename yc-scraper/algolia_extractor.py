"""Algolia-based Y Combinator company extractor.

A drop-in replacement for the Selenium/Firefox `yc_links_extractor.py`. The YC
directory (https://www.ycombinator.com/companies) is powered by an Algolia
search index, so we can pull the full dataset directly from the API instead of
driving a headless browser. No Firefox, geckodriver, or Selenium required.

Outputs:
  - scrapy-project/ycombinator/start_urls.txt : JSON list of company page URLs
                                                (consumed by the Scrapy spider)
  - companies.json                            : full company records from Algolia

Algolia caps pagination at 1000 hits per query, so we query one batch at a time
(the largest batch is ~400 companies) to retrieve every company.
"""

import argparse
import json
import logging
import re
import ssl
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def _make_ssl_context() -> ssl.SSLContext:
    """Build an SSL context that works even when Python lacks the system CA store.

    Python.org builds on macOS don't trust the system keychain, and some
    networks inject a self-signed proxy cert. Prefer certifi's CA bundle; fall
    back to an unverified context so the scraper still runs (public data only).
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        logger_ctx = logging.getLogger(__name__)
        logger_ctx.warning("certifi unavailable; disabling TLS verification")
        return ssl._create_unverified_context()  # noqa: SLF001

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

COMPANIES_PAGE = "https://www.ycombinator.com/companies"
INDEX = "YCCompany_production"
HITS_PER_PAGE = 1000  # == Algolia paginationLimitedTo; one page covers any batch

START_URLS_FILE = Path("scrapy-project/ycombinator/start_urls.txt")
COMPANIES_FILE = Path("companies.json")

# Fallback credentials (public search-only key embedded in the YC page). These
# rotate occasionally, so we try to read fresh ones from the live page first.
FALLBACK_APP = "45BWZJ1SGC"
FALLBACK_KEY = (
    "NzllNTY5MzJiZGM2OTY2ZTQwMDEzOTNhYWZiZGRjODlhYzVkNjBmOGRjNzJiMWM4ZTU0ZDlh"
    "YTZjOTJiMjlhMWFuYWx5dGljc1RhZ3M9eWNkYyZyZXN0cmljdEluZGljZXM9WUNDb21wYW55"
    "X3Byb2R1Y3Rpb24lMkNZQ0NvbXBhbnlfQnlfTGF1bmNoX0RhdGVfcHJvZHVjdGlvbiZ0YWdG"
    "aWx0ZXJzPSU1QiUyMnljZGNfcHVibGljJTIyJTVE"
)

_SSL_CTX = _make_ssl_context()


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        return resp.read()


def get_credentials() -> Dict[str, str]:
    """Scrape the live Algolia app id + search key from the YC companies page."""
    try:
        html = _http_get(COMPANIES_PAGE).decode("utf-8", "replace")
        match = re.search(r'AlgoliaOpts\s*=\s*(\{.*?\})', html, re.S)
        if match:
            opts = json.loads(match.group(1))
            logger.info("Loaded fresh Algolia credentials from live page")
            return {"app": opts["app"], "key": opts["key"]}
    except Exception as exc:  # noqa: BLE001 - fall back on any failure
        logger.warning("Could not read live credentials (%s); using fallback", exc)
    return {"app": FALLBACK_APP, "key": FALLBACK_KEY}


def algolia_query(creds: Dict[str, str], body: Dict[str, Any]) -> Dict[str, Any]:
    url = f"https://{creds['app'].lower()}-dsn.algolia.net/1/indexes/{INDEX}/query"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "X-Algolia-Application-Id": creds["app"],
            "X-Algolia-API-Key": creds["key"],
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        return json.loads(resp.read())


def get_batches(creds: Dict[str, str]) -> List[str]:
    resp = algolia_query(
        creds,
        {"query": "", "hitsPerPage": 0, "facets": ["batch"], "maxValuesPerFacet": 1000},
    )
    batches = list(resp.get("facets", {}).get("batch", {}).keys())
    logger.info("Found %d batches", len(batches))
    return batches


def fetch_batch(creds: Dict[str, str], batch: str) -> List[Dict[str, Any]]:
    """Fetch every company in a single batch (paginating just in case)."""
    hits: List[Dict[str, Any]] = []
    page = 0
    while True:
        resp = algolia_query(
            creds,
            {
                "query": "",
                "facetFilters": [[f"batch:{batch}"]],
                "hitsPerPage": HITS_PER_PAGE,
                "page": page,
            },
        )
        hits.extend(resp.get("hits", []))
        if page >= resp.get("nbPages", 1) - 1:
            break
        page += 1
    return hits


def normalize(hit: Dict[str, Any]) -> Dict[str, Any]:
    """Map an Algolia hit to a clean, stable company record."""
    slug = hit.get("slug")
    return {
        "company_id": hit.get("id"),
        "company_name": hit.get("name"),
        "slug": slug,
        "url": f"https://www.ycombinator.com/companies/{slug}" if slug else None,
        "short_description": hit.get("one_liner"),
        "long_description": hit.get("long_description"),
        "batch": hit.get("batch"),
        "status": hit.get("status"),
        "stage": hit.get("stage"),
        "tags": hit.get("tags", []),
        "industry": hit.get("industry"),
        "industries": hit.get("industries", []),
        "subindustry": hit.get("subindustry"),
        "location": hit.get("all_locations"),
        "regions": hit.get("regions", []),
        "team_size": hit.get("team_size"),
        "year_founded": hit.get("year_founded"),
        "is_hiring": hit.get("isHiring"),
        "nonprofit": hit.get("nonprofit"),
        "top_company": hit.get("top_company"),
        "website": hit.get("website"),
        "logo_url": hit.get("small_logo_thumb_url"),
    }


def extract(recent: Optional[int] = None, batches_filter: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    creds = get_credentials()
    batches = get_batches(creds)

    if batches_filter:
        wanted = set(batches_filter)
        batches = [b for b in batches if b in wanted]
    if recent:
        # Batches roughly ordered newest-first by Algolia facet ordering; sort by
        # trailing year then season to be safe.
        season_rank = {"Winter": 0, "Spring": 1, "Summer": 2, "Fall": 3}

        def key(b: str):
            parts = b.split()
            year = int(parts[-1]) if parts[-1].isdigit() else 0
            return (year, season_rank.get(parts[0], 0))

        batches = sorted(batches, key=key, reverse=True)[:recent]

    all_records: List[Dict[str, Any]] = []
    seen = set()
    for batch in batches:
        hits = fetch_batch(creds, batch)
        for hit in hits:
            oid = hit.get("objectID") or hit.get("id")
            if oid in seen:
                continue
            seen.add(oid)
            all_records.append(normalize(hit))
        logger.info("Batch %-14s -> %4d companies (running total %d)", batch, len(hits), len(all_records))

    logger.info("Collected %d unique companies", len(all_records))
    return all_records


def write_outputs(records: List[Dict[str, Any]]) -> None:
    urls = [r["url"] for r in records if r.get("url")]
    START_URLS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(START_URLS_FILE, "w") as fh:
        json.dump(urls, fh, indent=2)
    logger.info("Wrote %d URLs -> %s", len(urls), START_URLS_FILE)

    with open(COMPANIES_FILE, "w") as fh:
        json.dump(records, fh, indent=2)
    logger.info("Wrote %d company records -> %s", len(records), COMPANIES_FILE)


def main() -> None:
    parser = argparse.ArgumentParser(description="Algolia-based YC company extractor")
    parser.add_argument("--recent", type=int, metavar="N", help="Only the N most recent batches")
    parser.add_argument("--batches", nargs="+", metavar="BATCH", help='e.g. "Winter 2024" "Summer 2024"')
    args = parser.parse_args()

    records = extract(recent=args.recent, batches_filter=args.batches)
    write_outputs(records)
    logger.info("Done.")


if __name__ == "__main__":
    main()
