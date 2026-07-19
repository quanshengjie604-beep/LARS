from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


TABLE_ORDER = (
    "founders",
    "founder_companies",
    "founder_company_roles",
    "founder_education",
    "founder_skills",
    "founder_exits",
    "founder_reported_exit_claims",
    "founder_evidence",
)
CONFLICT_COLUMNS = {
    "founders": ("candidate_id",),
    "founder_companies": ("company_id",),
    "founder_company_roles": ("candidate_id", "company_id"),
    "founder_education": ("education_id",),
    "founder_skills": ("founder_skill_id",),
    "founder_exits": ("exit_id",),
    "founder_reported_exit_claims": ("claim_id",),
    "founder_evidence": ("source_id",),
}
JSON_COLUMNS = {
    "founders": {
        "aliases", "affiliations", "profile_urls", "discovery_sources",
        "entrepreneurial_signals", "research", "hackathons", "open_source",
        "founder_features", "missing_fields", "contradicted_fields", "raw_record",
    },
    "founder_companies": {"raw_record"},
    "founder_company_roles": {"raw_record"},
    "founder_education": {"raw_record"},
    "founder_skills": {"raw_record"},
    "founder_exits": {"raw_record"},
    "founder_reported_exit_claims": {"raw_record"},
    "founder_evidence": {"raw_metadata", "raw_record"},
}
DATE_COLUMNS = {
    "founder_companies": {"launched_at"},
    "founder_evidence": {"document_date"},
}
TIMESTAMP_COLUMNS = {
    "founders": {"first_seen_at", "last_seen_at"},
    "founder_evidence": {"collected_at"},
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_bundle(input_dir: Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Supabase manifest not found: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "lars-supabase-jsonl-v1":
        raise ValueError(f"unsupported bundle format: {manifest.get('format')!r}")
    entries = {item["table"]: item for item in manifest.get("tables") or []}
    if set(entries) != set(TABLE_ORDER):
        raise ValueError("manifest table list does not match the expected Supabase schema")
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in TABLE_ORDER:
        entry = entries[table]
        path = input_dir / entry["file"]
        if not path.is_file():
            raise FileNotFoundError(f"bundle file not found: {path}")
        if entry.get("sha256") and _sha256(path) != entry["sha256"]:
            raise ValueError(f"checksum mismatch for {path}")
        rows = read_jsonl(path)
        if len(rows) != entry["records"]:
            raise ValueError(
                f"record count mismatch for {table}: manifest={entry['records']} actual={len(rows)}"
            )
        tables[table] = rows
    candidate_ids = {row["candidate_id"] for row in tables["founders"]}
    for table in TABLE_ORDER[2:]:
        invalid = {row["candidate_id"] for row in tables[table]} - candidate_ids
        if invalid:
            raise ValueError(f"{table} contains unknown candidate IDs: {sorted(invalid)[:5]}")
    return manifest, tables


def _read_env_file(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'") or None
    return None


def validate_database_url(database_url: str) -> None:
    placeholders = ("PROJECT_REF", "REGION", "URL_ENCODED_PASSWORD", "YOUR-PASSWORD", "[", "]")
    if any(token.casefold() in database_url.casefold() for token in placeholders):
        raise RuntimeError(
            "SUPABASE_DB_URL still contains template placeholders; copy the complete Session pooler URI from Supabase Connect"
        )
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgresql", "postgres"} or not parsed.hostname:
        raise RuntimeError("SUPABASE_DB_URL must be a complete PostgreSQL connection URI")
    if parsed.port != 5432:
        raise RuntimeError("use the Supabase Session pooler URI on port 5432")


def _date_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value)[:10])


def _timestamp_value(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _upsert_table(cursor, sql, Jsonb, table: str, rows: list[dict[str, Any]], batch_size: int) -> int:
    if not rows:
        return 0
    columns = list(rows[0])
    if any(list(row) != columns for row in rows):
        raise ValueError(f"inconsistent columns in {table} bundle")
    conflicts = CONFLICT_COLUMNS[table]
    updates = [column for column in columns if column not in conflicts]
    query = sql.SQL("insert into public.{} ({}) values ({}) on conflict ({}) do update set {}").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        sql.SQL(", ").join(map(sql.Identifier, conflicts)),
        sql.SQL(", ").join(
            sql.SQL("{} = excluded.{}").format(sql.Identifier(column), sql.Identifier(column))
            for column in updates
        ),
    )
    json_columns = JSON_COLUMNS.get(table, set())
    date_columns = DATE_COLUMNS.get(table, set())
    timestamp_columns = TIMESTAMP_COLUMNS.get(table, set())
    for start in range(0, len(rows), batch_size):
        parameters = []
        for row in rows[start:start + batch_size]:
            values = []
            for column in columns:
                value = row[column]
                if column in json_columns:
                    value = Jsonb(value)
                elif column in date_columns:
                    value = _date_value(value)
                elif column in timestamp_columns:
                    value = _timestamp_value(value)
                values.append(value)
            parameters.append(values)
        cursor.executemany(query, parameters)
    return len(rows)


def upload_bundle(database_url: str, tables: dict[str, list[dict[str, Any]]], batch_size: int) -> dict[str, Any]:
    try:
        import psycopg
        from psycopg import sql
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "psycopg is required for upload; install Scripts/01_Screening/requirements-supabase.txt"
        ) from exc

    uploaded: dict[str, int] = {}
    database_totals: dict[str, int] = {}
    with psycopg.connect(database_url, connect_timeout=20) as connection:
        with connection.cursor() as cursor:
            for table in TABLE_ORDER:
                cursor.execute("select to_regclass(%s)", (f"public.{table}",))
                if cursor.fetchone()[0] is None:
                    raise RuntimeError(
                        f"public.{table} is missing; apply supabase/migrations/202607180001_founder_screening.sql first"
                    )
            for table in TABLE_ORDER:
                uploaded[table] = _upsert_table(
                    cursor, sql, Jsonb, table, tables[table], max(1, batch_size)
                )
            for table in TABLE_ORDER:
                cursor.execute(sql.SQL("select count(*) from public.{}").format(sql.Identifier(table)))
                database_totals[table] = cursor.fetchone()[0]
        connection.commit()
    return {
        "uploaded_records": uploaded,
        "database_table_totals": database_totals,
        "transaction_committed": True,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Idempotently upsert a prepared LARS founder bundle into Supabase Postgres"
    )
    result.add_argument(
        "--input-dir", type=Path,
        default=Path("screening_handover/supabase_public_web"),
    )
    result.add_argument("--database-url", help="Supabase Postgres session/direct connection string")
    result.add_argument("--env-file", type=Path, default=Path(".env.supabase"))
    result.add_argument("--batch-size", type=int, default=250)
    result.add_argument(
        "--dry-run", action="store_true",
        help="Validate checksums, foreign keys, and counts without connecting to Supabase",
    )
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    manifest, tables = validate_bundle(args.input_dir)
    plan = {
        "mode": "dry-run" if args.dry_run else "upload",
        "bundle_format": manifest["format"],
        "tables": {table: len(tables[table]) for table in TABLE_ORDER},
        "batch_size": args.batch_size,
        "network_requests": not args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    database_url = (
        args.database_url
        or os.environ.get("SUPABASE_DB_URL")
        or _read_env_file(args.env_file, "SUPABASE_DB_URL")
    )
    if not database_url:
        raise RuntimeError(
            "SUPABASE_DB_URL is not configured; copy .env.supabase.example to .env.supabase"
        )
    validate_database_url(database_url)
    result = upload_bundle(database_url, tables, args.batch_size)
    print(json.dumps({**plan, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
