from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable

from export_chroma import validate_records


DEFAULT_INPUT = Path("screening_handover/chroma")


def read_records(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    errors = validate_records(records)
    if errors:
        raise ValueError(f"invalid Chroma records in {path}: {errors[:20]}")
    return records


def batches(records: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(records), size):
        yield records[start:start + size]


def upsert_file(collection, path: Path, batch_size: int) -> int:
    records = read_records(path)
    uploaded = 0
    for batch in batches(records, batch_size):
        collection.upsert(
            ids=[item["id"] for item in batch],
            documents=[item["document"] for item in batch],
            metadatas=[item["metadata"] for item in batch],
        )
        uploaded += len(batch)
        print(f"{collection.name}: {uploaded}/{len(records)}")
    return uploaded


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Upsert LARS founder records into Chroma")
    result.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    result.add_argument("--mode", choices=("cloud", "local"), default="cloud")
    result.add_argument("--local-path", type=Path, default=Path(".chroma"))
    result.add_argument("--tenant", default=os.getenv("CHROMA_TENANT"))
    result.add_argument("--database", default=os.getenv("CHROMA_DATABASE"))
    result.add_argument("--api-key", default=os.getenv("CHROMA_API_KEY"))
    result.add_argument("--founders-collection", default="lars_founders")
    result.add_argument("--evidence-collection", default="lars_founder_evidence")
    result.add_argument("--batch-size", type=int, default=100)
    result.add_argument("--dry-run", action="store_true")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.batch_size < 1:
        raise ValueError("batch-size must be positive")
    founder_path = args.input_dir / "founders.chroma.jsonl"
    evidence_path = args.input_dir / "founder_evidence.chroma.jsonl"
    founders = read_records(founder_path)
    evidence = read_records(evidence_path)
    if args.dry_run:
        print(json.dumps({
            "mode": args.mode,
            "founders_collection": args.founders_collection,
            "founder_records": len(founders),
            "evidence_collection": args.evidence_collection,
            "evidence_records": len(evidence),
            "batch_size": args.batch_size,
            "network_requests": False,
        }, indent=2))
        return 0
    try:
        import chromadb
    except ImportError as exc:
        raise RuntimeError(
            "chromadb is not installed; run: python -m pip install -r "
            "Scripts/01_Screening/requirements-chroma.txt"
        ) from exc
    if args.mode == "cloud":
        missing = [name for name, value in {
            "CHROMA_API_KEY": args.api_key,
            "CHROMA_TENANT": args.tenant,
            "CHROMA_DATABASE": args.database,
        }.items() if not value]
        if missing:
            raise ValueError(f"missing cloud configuration: {', '.join(missing)}")
        client = chromadb.CloudClient(
            api_key=args.api_key,
            tenant=args.tenant,
            database=args.database,
        )
    else:
        client = chromadb.PersistentClient(path=str(args.local_path))
    founders_collection = client.get_or_create_collection(name=args.founders_collection)
    evidence_collection = client.get_or_create_collection(name=args.evidence_collection)
    result = {
        args.founders_collection: upsert_file(founders_collection, founder_path, args.batch_size),
        args.evidence_collection: upsert_file(evidence_collection, evidence_path, args.batch_size),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
