from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from founder_screening.public_web_enrichment import run_public_web_enrichment


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Enrich founders from LinkedIn-anchored public sources")
    result.add_argument("--input-dir", type=Path, default=Path("screening_handover/founder_enriched"))
    result.add_argument("--output-dir", type=Path, default=Path("screening_handover/founder_public_web_pilot"))
    result.add_argument(
        "--qs-file", type=Path,
        default=Path("screening_handover/reference/qs_world_university_rankings_2027.xlsx"),
    )
    result.add_argument("--max-domains", type=int, default=100, help="Use 0 for every available company domain")
    result.add_argument("--workers", type=int, default=12)
    result.add_argument("--timeout", type=float, default=12)
    result.add_argument("--max-extra-pages", type=int, default=2)
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--verbose", action="store_true")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    required = [
        args.input_dir / "sourcing_candidates.jsonl",
        args.input_dir / "evidence_registry.jsonl",
        args.qs_file,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required input files not found: {missing}")
    if args.dry_run:
        print(json.dumps({
            "input_dir": str(args.input_dir), "output_dir": str(args.output_dir),
            "max_domains": args.max_domains or "all", "workers": args.workers,
            "linkedin_pages_requested": False, "writes": False,
        }, ensure_ascii=False, indent=2))
        return 0
    report = run_public_web_enrichment(
        args.input_dir,
        args.output_dir,
        args.qs_file,
        max_domains=args.max_domains or None,
        workers=args.workers,
        timeout=args.timeout,
        max_extra_pages=args.max_extra_pages,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
