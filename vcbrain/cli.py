"""Command-line entrypoint: `python -m vcbrain [options]`."""

from __future__ import annotations

import argparse
import sys

from . import config
from .pipeline import RunConfig, run


def _csv(s: str):
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vcbrain",
        description="Screening data acquisition & scoring over a LIST of startups "
                    "(YC directory seed + GitHub/HN/ProductHunt enrichment). "
                    "Emits the screening_handover/ JSONL contract. "
                    "All companies are capped to <=10 years old.")
    p.add_argument("--mode", choices=["inference", "training", "both"], default="both",
                   help="Which handover records to emit (default: both).")
    p.add_argument("--limit", type=int, default=25,
                   help="Max companies to process (0 = all). Keep small without a GITHUB_TOKEN.")
    p.add_argument("--batches", type=_csv, default=None,
                   help='Comma-separated YC batches, e.g. "Summer 2024,Winter 2024".')
    p.add_argument("--sectors", type=_csv, default=None, help="Comma-separated normalised sectors.")
    p.add_argument("--geographies", type=_csv, default=None, help="Comma-separated normalised geographies.")
    p.add_argument("--status", type=_csv, default=None,
                   help="Filter by YC status, e.g. 'Active,Acquired,Public,Inactive'.")
    p.add_argument("--top-only", action="store_true", help="Only YC 'top' companies.")
    p.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    p.add_argument("--no-github", action="store_true", help="Skip GitHub (avoids rate limits without a token).")
    p.add_argument("--no-llm", action="store_true", help="Skip LLM-derived estimated fields.")
    p.add_argument("--output-dir", default=config.OUTPUT_DIR)
    p.add_argument("--age-years", type=int, default=config.MAX_AGE_YEARS,
                   help="Max company age in years (default 10).")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.age_years != config.MAX_AGE_YEARS:
        config.MAX_AGE_YEARS = args.age_years
        config.MIN_FOUNDING_YEAR = config.TODAY.year - args.age_years
    rc = RunConfig(
        mode=args.mode,
        limit=(args.limit or None),
        batches=args.batches,
        sectors=args.sectors,
        geographies=args.geographies,
        include_status=args.status,
        top_only=args.top_only,
        workers=args.workers,
        use_github=not args.no_github,
        use_llm=not args.no_llm,
        output_dir=args.output_dir,
    )
    run(rc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
