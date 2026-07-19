from __future__ import annotations

import argparse
import json
from pathlib import Path

from founder_screening.enrichment import QS_DOWNLOAD_URL, enrich_dataset


DEFAULT_INPUT = Path("screening_handover/founder_sourcing")
DEFAULT_OUTPUT = Path("screening_handover/founder_enriched")
DEFAULT_QS = Path("screening_handover/reference/qs_world_university_rankings_2027.xlsx")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Enrich verified founders with evidence-backed education, QS rank, exits, and skills"
    )
    result.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    result.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    result.add_argument("--qs-file", type=Path, default=DEFAULT_QS)
    result.add_argument("--dry-run", action="store_true", help="Validate paths and print the plan without writing output")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    required = [
        args.input_dir / "sourcing_candidates.jsonl",
        args.input_dir / "evidence_registry.jsonl",
        args.qs_file,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required input files not found: {missing}")
    if args.dry_run:
        with (args.input_dir / "sourcing_candidates.jsonl").open(encoding="utf-8") as handle:
            candidate_count = sum(1 for line in handle if line.strip())
        print(json.dumps({
            "input_dir": str(args.input_dir),
            "output_dir": str(args.output_dir),
            "qs_file": str(args.qs_file),
            "qs_download_url": QS_DOWNLOAD_URL,
            "candidate_records": candidate_count,
            "network_requests": False,
            "writes": False,
        }, ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(
        enrich_dataset(args.input_dir, args.output_dir, args.qs_file),
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
