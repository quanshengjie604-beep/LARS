"""Enrich the company list with per-company details (founders, country, etc.).

The Algolia index (see `algolia_extractor.py`) gives us the full company list
plus most metadata, but not founder profiles, country, year_founded, or the
Crunchbase URL. Those live in the JSON embedded in each company page's
`data-page` attribute -- the same data the Scrapy spider parses.

This script fetches every company page concurrently (stdlib only, no Scrapy /
Selenium) and writes records in the exact schema the web explorer and README
expect, to `data/companies.json` (and `output.jl` as JSON Lines).
"""

import argparse
import html
import json
import logging
import re
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

START_URLS_FILE = Path("scrapy-project/ycombinator/start_urls.txt")
WEB_OUTPUT = Path("data/companies.json")
JL_OUTPUT = Path("output.jl")

_DATA_PAGE_RE = re.compile(r'data-page="(.*?)"\s*>', re.S)


def _make_ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        logger.warning("certifi unavailable; disabling TLS verification")
        return ssl._create_unverified_context()  # noqa: SLF001


_SSL_CTX = _make_ssl_context()


def fetch(url: str, retries: int = 3) -> Optional[str]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            if attempt == retries - 1:
                logger.warning("Failed %s (%s)", url, exc)
                return None
    return None


def parse_company(url: str, page_html: str) -> Optional[Dict[str, Any]]:
    match = _DATA_PAGE_RE.search(page_html)
    if not match:
        return None
    try:
        data = json.loads(html.unescape(match.group(1)))
        c = data["props"]["company"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None

    founders = c.get("founders", []) or []
    return {
        "company_id": c.get("id"),
        "company_name": c.get("name"),
        "short_description": c.get("one_liner"),
        "long_description": c.get("long_description"),
        "batch": c.get("batch_name"),
        "status": c.get("ycdc_status"),
        "tags": c.get("tags", []),
        "location": c.get("location"),
        "country": c.get("country"),
        "year_founded": c.get("year_founded"),
        "num_founders": len(founders),
        "founders_names": [f.get("full_name") for f in founders if f.get("full_name")],
        "founder_details": [
            {
                "name": f.get("full_name"),
                "title": f.get("title"),
                "bio": f.get("founder_bio"),
                "linkedin_url": f.get("linkedin_url"),
                "twitter_url": f.get("twitter_url"),
            }
            for f in founders
        ],
        "team_size": c.get("team_size"),
        "website": c.get("website"),
        "cb_url": c.get("cb_url"),
        "linkedin_url": c.get("linkedin_url"),
    }


def scrape_one(url: str) -> Optional[Dict[str, Any]]:
    page_html = fetch(url)
    if page_html is None:
        return None
    return parse_company(url, page_html)


def load_urls(limit: Optional[int]) -> List[str]:
    if not START_URLS_FILE.exists():
        raise FileNotFoundError(
            f"{START_URLS_FILE} not found. Run algolia_extractor.py first."
        )
    with open(START_URLS_FILE) as fh:
        urls = json.load(fh)
    return urls[:limit] if limit else urls


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich YC companies with founder details")
    parser.add_argument("--limit", type=int, help="Only scrape the first N companies (for testing)")
    parser.add_argument("--workers", type=int, default=12, help="Concurrent fetchers (default 12)")
    args = parser.parse_args()

    urls = load_urls(args.limit)
    total = len(urls)
    logger.info("Enriching %d companies with %d workers", total, args.workers)

    records: List[Dict[str, Any]] = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(scrape_one, u): u for u in urls}
        for fut in as_completed(futures):
            rec = fut.result()
            done += 1
            if rec:
                records.append(rec)
            if done % 200 == 0 or done == total:
                logger.info("  %d/%d fetched (%d parsed)", done, total, len(records))

    records.sort(key=lambda r: r.get("company_id") or 0)

    WEB_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(WEB_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    logger.info("Wrote %d records -> %s", len(records), WEB_OUTPUT)

    with open(JL_OUTPUT, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info("Wrote %d records -> %s", len(records), JL_OUTPUT)

    with_founders = sum(1 for r in records if r["founder_details"])
    logger.info("Done. %d/%d have founder details.", with_founders, len(records))


if __name__ == "__main__":
    main()
