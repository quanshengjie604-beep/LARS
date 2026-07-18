from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import yaml

from .pipeline import run


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Collect public evidence for potential founders")
    result.add_argument("--config", type=Path, default=Path(__file__).parents[1] / "config.yaml")
    result.add_argument("--output-dir", type=Path, default=Path("screening_handover/founder_sourcing"))
    result.add_argument("--max-candidates", type=int, default=2000)
    result.add_argument("--sources", help="Comma-separated subset: arxiv,github,labs,hackathons")
    result.add_argument("--dry-run", action="store_true", help="Validate and print the crawl plan without network requests")
    result.add_argument("--verbose", action="store_true")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    with args.config.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    selected = [item.strip() for item in args.sources.split(",") if item.strip()] if args.sources else None
    if args.dry_run:
        enabled = selected or [name for name, value in config.get("sources", {}).items() if value.get("enabled")]
        print(json.dumps({
            "max_candidates": args.max_candidates,
            "sources": enabled,
            "output_dir": str(args.output_dir),
            "github_token_configured": bool(__import__("os").getenv("GITHUB_TOKEN")),
            "network_requests": False,
        }, indent=2))
        return 0
    report = run(config, args.output_dir, args.max_candidates, selected)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["errors"] else 1
