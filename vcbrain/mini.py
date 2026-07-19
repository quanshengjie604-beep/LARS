"""Small deterministic scrape for adapter and pipeline health assessment."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .config import Settings
from .pipeline import CollectionConfig, CollectionResult, collect
from .util import parse_date


class FixtureFetcher:
    """Recorded-response fetcher used by the default, network-free mini scrape."""

    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir
        self._hn_search = self._json("hn_search.json")
        self._hn_items = self._json("hn_items.json")
        self._producthunt = self._json("producthunt_posts.json")

    def _json(self, name: str) -> Any:
        return json.loads((self.fixture_dir / name).read_text(encoding="utf-8"))

    async def get_text(
        self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> str:
        if url == "https://a16z.com/portfolio/":
            return (self.fixture_dir / "a16z_portfolio.html").read_text(encoding="utf-8")
        if url == "https://a16z.com/investment-list/":
            return (self.fixture_dir / "a16z_investment_list.html").read_text(encoding="utf-8")
        raise RuntimeError(f"fixture route not found: GET text {url}")

    async def get_json(
        self, url: str, *, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None
    ) -> Any:
        if url == "https://boards.considerapi.com/v0/companies":
            return self._json("startx_companies.json")
        if url.endswith("/first_investment"):
            return self._json("pear_first_investment.json")
        if url.endswith("/current_stage"):
            return self._json("pear_current_stage.json")
        if url.endswith("/pear_vc_company_sector"):
            return self._json("pear_sector.json")
        if url.endswith("/pear_vc_company"):
            page = int((params or {}).get("page", 1))
            return self._json("pear_companies.json") if page == 1 else []
        if url == "https://hn.algolia.com/api/v1/search_by_date":
            query = str((params or {}).get("query") or "").casefold()
            words = [word for word in query.replace(",", " ").split() if len(word) >= 3]
            hits = [
                hit
                for hit in self._hn_search["hits"]
                if all(word in str(hit.get("title") or "").casefold() for word in words[:2])
            ]
            return {"hits": hits}
        if "/v0/item/" in url:
            item_id = url.rsplit("/", 1)[-1].split(".", 1)[0]
            return self._hn_items.get(item_id)
        if url.startswith("https://api.github.com/"):
            return []
        raise RuntimeError(f"fixture route not found: GET json {url}")

    async def post_json(
        self, url: str, payload: Mapping[str, Any], *, headers: Mapping[str, str] | None = None
    ) -> Any:
        if url != "https://api.producthunt.com/v2/api/graphql":
            raise RuntimeError(f"fixture route not found: POST {url}")
        website = str((payload.get("variables") or {}).get("url") or "")
        nodes = self._producthunt.get(website, [])
        return {"data": {"posts": {"edges": [{"node": node} for node in nodes]}}}


def assess_mini_result(result: CollectionResult) -> dict[str, Any]:
    evidence_ids = {row["source_id"] for row in result.evidence}
    referenced = {
        source_id
        for collection in (
            result.funding_rounds,
            result.inference_requests,
            result.training_features,
            result.training_outcomes,
        )
        for row in collection
        for source_id in row.get("source_ids", [])
    }
    cutoff_violations = []
    evidence_by_id = {row["source_id"]: row for row in result.evidence}
    for snapshot in [*result.inference_requests, *result.training_features]:
        cutoff = parse_date(snapshot.get("data_cutoff_date"))
        for source_id in snapshot.get("source_ids", []):
            document_date = parse_date((evidence_by_id.get(source_id) or {}).get("document_date"))
            if cutoff and document_date and document_date > cutoff:
                cutoff_violations.append(
                    {"snapshot_id": snapshot.get("snapshot_id"), "source_id": source_id}
                )
    checks = {
        "companies_found": len(result.companies) > 0,
        "multiple_directories_worked": sum(
            1 for row in result.source_runs if row.get("source") in {"yc", "a16z", "startx", "pear"} and row.get("status") == "ok"
        ) >= 2,
        "funding_round_found": len(result.funding_rounds) > 0,
        "all_evidence_references_resolve": referenced <= evidence_ids,
        "no_feature_cutoff_violations": not cutoff_violations,
        "private_financial_gaps_ranked": any(
            row.get("field") in {"burn_rate_usd_monthly", "cash_balance_usd", "runway_months"}
            for row in result.quality_report.get("hard_to_obtain", [])
        ),
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "counts": result.summary,
        "cutoff_violations": cutoff_violations,
    }


async def mini_scrape(
    *,
    output_dir: Path = Path("mini_scrape_output"),
    live: bool = False,
    fixture_dir: Path | None = None,
) -> tuple[CollectionResult, dict[str, Any]]:
    """Run a bounded scrape; recorded fixtures are the deterministic default."""
    if live:
        settings = Settings.from_env()
        settings.output_dir = output_dir
        config = CollectionConfig(
            settings=settings,
            directories=("a16z", "pear", "yc", "startx"),
            limit=5,
            mode="both",
            pear_pace_seconds=10.0,
            output_dir=output_dir,
        )
        result = await collect(config)
    else:
        fixture_dir = fixture_dir or Path(__file__).resolve().parents[1] / "tests" / "fixtures"
        settings = Settings(
            as_of=date(2026, 7, 18),
            cache_dir=output_dir / ".cache",
            output_dir=output_dir,
            concurrency=8,
            per_host_concurrency=4,
            producthunt_token="fixture-token",
            startx_api_key="fixture-key",
            max_company_age_years=None,
        )
        config = CollectionConfig(
            settings=settings,
            yc_export=fixture_dir / "yc_authorized.json",
            limit=None,
            mode="both",
            pear_pace_seconds=0,
            output_dir=output_dir,
            collected_at="2026-07-18T12:00:00Z",
        )
        result = await collect(config, fetcher=FixtureFetcher(fixture_dir))
    assessment = assess_mini_result(result)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "mini_assessment.json").write_text(
        json.dumps(assessment, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result, assessment

