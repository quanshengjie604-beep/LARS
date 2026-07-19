from __future__ import annotations

import argparse
import json
from pathlib import Path

from founder_screening.supabase_export import export_bundle


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Convert enriched founder screening data to a normalized Supabase upload bundle"
    )
    result.add_argument(
        "--input-dir", type=Path,
        default=Path("screening_handover/founder_public_web"),
    )
    result.add_argument(
        "--output-dir", type=Path,
        default=Path("screening_handover/supabase_public_web"),
    )
    result.add_argument(
        "--dry-run", action="store_true",
        help="Validate and count records without writing files or making network requests",
    )
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    required = [
        args.input_dir / "sourcing_candidates.jsonl",
        args.input_dir / "evidence_registry.jsonl",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"required input files not found: {missing}")
    manifest = export_bundle(args.input_dir, args.output_dir, write=not args.dry_run)
    if args.dry_run:
        manifest["writes"] = False
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
