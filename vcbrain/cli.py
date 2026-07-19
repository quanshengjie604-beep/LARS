"""Command-line interface for collection, mini validation, and re-rendering."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, Sequence

from .config import Settings
from .mini import mini_scrape
from .pipeline import CollectionConfig, collect
from .postprocess import postprocess_snapshots
from .visualize import write_html


def _paths(values: Sequence[str]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            paths.extend(sorted(path.glob("*.zip")))
        else:
            paths.append(path)
    return tuple(paths)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env()
    if getattr(args, "as_of", None):
        settings.as_of = date.fromisoformat(args.as_of)
    if getattr(args, "cache_dir", None):
        settings.cache_dir = Path(args.cache_dir)
    if getattr(args, "output_dir", None):
        settings.output_dir = Path(args.output_dir)
    if getattr(args, "concurrency", None):
        settings.concurrency = args.concurrency
    return settings


async def _collect_command(args: argparse.Namespace) -> int:
    settings = _settings(args)
    config = CollectionConfig(
        settings=settings,
        directories=tuple(part.strip() for part in args.sources.split(",") if part.strip()),
        yc_export=Path(args.yc_export) if args.yc_export else None,
        use_yc_scraper=not args.no_yc_scraper,
        yc_scraper_path=Path(args.yc_scraper_path) if args.yc_scraper_path else None,
        yc_recent=args.yc_recent,
        yc_batches=tuple(args.yc_batches or ()),
        sec_form_d_zips=_paths(args.sec_form_d),
        limit=None if args.limit == 0 else args.limit,
        mode=args.mode,
        use_hackernews=not args.no_hackernews,
        use_producthunt=not args.no_producthunt,
        use_github=not args.no_github,
        pear_pace_seconds=args.pear_pace,
        output_dir=Path(args.output_dir),
    )
    result = await collect(config)
    print(json.dumps(result.summary, indent=2, sort_keys=True))
    return 0


async def _mini_command(args: argparse.Namespace) -> int:
    _, assessment = await mini_scrape(output_dir=Path(args.output_dir), live=args.live)
    print(json.dumps(assessment, indent=2, sort_keys=True))
    return 0 if assessment["ok"] else 1


def _visualize_command(args: argparse.Namespace) -> int:
    directory = Path(args.output_dir)
    destination = Path(args.destination) if args.destination else directory / "scrape_report.html"
    write_html(
        _read_jsonl(directory / "companies.jsonl"),
        _read_jsonl(directory / "funding_rounds.jsonl"),
        json.loads((directory / "quality_report.json").read_text(encoding="utf-8"))
        if (directory / "quality_report.json").exists()
        else {},
        json.loads((directory / "source_runs.json").read_text(encoding="utf-8"))
        if (directory / "source_runs.json").exists()
        else [],
        destination,
    )
    print(destination)
    return 0


def _postprocess_command(args: argparse.Namespace) -> int:
    directory = Path(args.output_dir)
    inference = _read_jsonl(directory / "inference_requests.jsonl")
    training = _read_jsonl(directory / "training_features.jsonl")
    evidence = _read_jsonl(directory / "evidence_registry.jsonl")
    source_runs = (
        json.loads((directory / "source_runs.json").read_text(encoding="utf-8"))
        if (directory / "source_runs.json").exists()
        else []
    )
    result = postprocess_snapshots([*training, *inference], evidence, source_runs)
    (directory / "quality_report.json").write_text(
        json.dumps(result["assessment"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (directory / "financial_disclosures.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in result["financial_disclosures"]
        ),
        encoding="utf-8",
    )
    (directory / "enrichment_queue.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in result["enrichment_queue"]),
        encoding="utf-8",
    )
    print(json.dumps({"records": len(result["snapshots"]), "queue": len(result["enrichment_queue"])}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vcbrain", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect_parser = sub.add_parser("collect", help="collect a startup cohort in parallel")
    collect_parser.add_argument("--sources", default="a16z,pear,yc,startx")
    collect_parser.add_argument(
        "--yc-export",
        help="authorised YC JSON/CSV export; omit to auto-scrape the YC Algolia directory",
    )
    collect_parser.add_argument(
        "--no-yc-scraper",
        action="store_true",
        help="disable the YC Algolia auto-scrape (skip YC unless --yc-export is given)",
    )
    collect_parser.add_argument(
        "--yc-scraper-path",
        help="path to algolia_extractor.py (defaults to the bundled yc-scraper/ or $VCBRAIN_YC_SCRAPER)",
    )
    collect_parser.add_argument(
        "--yc-recent", type=int, metavar="N", help="auto-scrape only the N most recent YC batches"
    )
    collect_parser.add_argument(
        "--yc-batches",
        nargs="+",
        metavar="BATCH",
        help='auto-scrape only these YC batches, e.g. "Winter 2024" "Summer 2024"',
    )
    collect_parser.add_argument("--sec-form-d", action="append", default=[], metavar="ZIP_OR_DIR")
    collect_parser.add_argument("--limit", type=int, default=25, help="0 means all discovered companies")
    collect_parser.add_argument("--mode", choices=("inference", "training", "both"), default="both")
    collect_parser.add_argument("--as-of", help="YYYY-MM-DD; defaults to today")
    collect_parser.add_argument("--output-dir", default="screening_handover")
    collect_parser.add_argument("--cache-dir", default=".vcbrain_cache")
    collect_parser.add_argument("--concurrency", type=int, default=32)
    collect_parser.add_argument("--pear-pace", type=float, default=10.0, help="seconds; keep 10 for live runs")
    collect_parser.add_argument("--no-hackernews", action="store_true")
    collect_parser.add_argument("--no-producthunt", action="store_true")
    collect_parser.add_argument("--no-github", action="store_true")

    mini_parser = sub.add_parser("mini", help="run the deterministic fixture scrape or a bounded live smoke")
    mini_parser.add_argument("--live", action="store_true", help="opt in to network sources")
    mini_parser.add_argument("--output-dir", default="mini_scrape_output")

    visualize_parser = sub.add_parser("visualize", help="render an existing collection")
    visualize_parser.add_argument("--output-dir", default="screening_handover")
    visualize_parser.add_argument("--destination")

    post_parser = sub.add_parser("postprocess", help="rerun missingness and disclosure assessment")
    post_parser.add_argument("--output-dir", default="screening_handover")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "collect":
        return asyncio.run(_collect_command(args))
    if args.command == "mini":
        return asyncio.run(_mini_command(args))
    if args.command == "visualize":
        return _visualize_command(args)
    return _postprocess_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
