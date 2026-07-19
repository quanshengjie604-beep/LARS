"""Dependency-free HTML visualization for startup funding scrape results.

The public API is intentionally small and free of pipeline or configuration
coupling:

``render_html``
    Turns in-memory records into one self-contained HTML document.

``write_html``
    Writes that document to a caller-selected path and returns the path.

All rendering is deterministic for the same inputs.  There are no timestamps,
random identifiers, remote assets, or network requests in the generated page.
"""

from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

__all__ = ["render_html", "write_html"]


_NOT_FOUND = "not found"
_DEFAULT_TITLE = "Startup funding mini-scrape"


def _records(value: Any) -> list[dict[str, Any]]:
    """Return JSON-like mapping records while tolerating ``None``.

    A single mapping is accepted as a convenience, although the documented API
    uses sequences of mappings.  Invalid sequence members are ignored instead
    of making the visualization fail after a successful scrape.
    """

    if value is None:
        return []
    if isinstance(value, Mapping):
        return [dict(value)]
    if isinstance(value, (str, bytes)):
        return []
    try:
        return [dict(item) for item in value if isinstance(item, Mapping)]
    except TypeError:
        return []


def _stable_json(value: Any) -> str:
    """Stable fallback sort key for otherwise identical records."""

    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if _present(value):
            return value
    return None


def _plain(value: Any) -> str:
    """Convert a value to unescaped display text, with an explicit null label."""

    if not _present(value):
        return _NOT_FOUND
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Mapping):
        return _stable_json(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts = [_plain(item) for item in value if _present(item)]
        return ", ".join(parts) if parts else _NOT_FOUND
    return str(value)


def _esc(value: Any) -> str:
    return html.escape(_plain(value), quote=True)


def _humanize(value: Any) -> str:
    text = _plain(value)
    if text == _NOT_FOUND:
        return text
    return text.replace("_", " ")


def _safe_url(value: Any) -> str | None:
    """Return only absolute HTTP(S) URLs suitable for an ``href`` attribute."""

    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(ord(char) < 32 for char in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        # Requiring a host rejects javascript:, data:, relative, and malformed
        # values while retaining ordinary http(s) evidence links.
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
    except ValueError:
        return None
    return candidate


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_money(value: Any) -> str:
    number = _number(value)
    if number is None:
        return _plain(value)
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    for suffix, divisor in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if magnitude >= divisor:
            scaled = magnitude / divisor
            digits = 0 if scaled >= 100 else 1
            return f"{sign}${scaled:.{digits}f}{suffix}"
    return f"{sign}${magnitude:,.0f}"


def _company_name(record: Mapping[str, Any]) -> Any:
    nested = record.get("company")
    if isinstance(nested, Mapping):
        nested_name = _first(nested, "name", "company_name", "startup_name")
        if _present(nested_name):
            return nested_name
    return _first(record, "company_name", "name", "startup_name", "startup")


def _company_id(record: Mapping[str, Any]) -> Any:
    nested = record.get("company")
    if isinstance(nested, Mapping):
        nested_id = _first(nested, "startup_id", "company_id", "id")
        if _present(nested_id):
            return nested_id
    return _first(record, "startup_id", "company_id", "id")


def _company_key(record: Mapping[str, Any]) -> str:
    identifier = _company_id(record)
    if _present(identifier):
        return "id:" + str(identifier).strip().casefold()
    domain = _first(record, "domain", "website_domain")
    if _present(domain):
        return "domain:" + str(domain).strip().casefold()
    name = _company_name(record)
    if _present(name):
        return "name:" + str(name).strip().casefold()
    return "record:" + _stable_json(record)


def _dedupe_companies(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate independently of input/concurrency completion order."""

    ordered = sorted(
        records,
        key=lambda row: (
            _company_key(row),
            _plain(_company_name(row)).casefold(),
            _stable_json(row),
        ),
    )
    unique: dict[str, dict[str, Any]] = {}
    for record in ordered:
        unique.setdefault(_company_key(record), record)
    return list(unique.values())


def _company_lookup(companies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for company in companies:
        identifier = _company_id(company)
        if _present(identifier):
            lookup[str(identifier).strip().casefold()] = company
    return lookup


def _source_parts(value: Any) -> list[tuple[str, str | None]]:
    """Normalize string/dict/list source representations into label/URL pairs."""

    if not _present(value):
        return []
    if isinstance(value, Mapping):
        label = _first(
            value,
            "source_name",
            "source",
            "name",
            "provider",
            "adapter",
            "channel",
            "accelerator",
            "source_type",
            "document_name",
        )
        uri = _first(value, "source_uri", "url", "evidence_url", "link")
        safe_uri = _safe_url(uri)
        if not _present(label) and safe_uri:
            label = urlsplit(safe_uri).netloc
        return [(_plain(label), safe_uri)] if _present(label) else []
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        parts: list[tuple[str, str | None]] = []
        for item in value:
            parts.extend(_source_parts(item))
        return parts
    safe_uri = _safe_url(value)
    if safe_uri:
        return [(urlsplit(safe_uri).netloc, safe_uri)]
    return [(_plain(value), None)]


def _record_sources(record: Mapping[str, Any]) -> list[tuple[str, str | None]]:
    parts: list[tuple[str, str | None]] = []
    for key in (
        "sources",
        "source_names",
        "source",
        "source_name",
        "provider",
        "adapter",
        "channel",
    ):
        if key in record:
            parts.extend(_source_parts(record.get(key)))

    # A record-level URI is paired with the first source when the source itself
    # did not carry a URI.
    uri = _safe_url(_first(record, "source_uri", "evidence_url", "url", "link"))
    if uri and parts and all(part_uri is None for _, part_uri in parts):
        first_label, _ = parts[0]
        parts[0] = (first_label, uri)

    unique: dict[str, tuple[str, str | None]] = {}
    for label, source_uri in sorted(parts, key=lambda item: (item[0].casefold(), item[1] or "")):
        if label != _NOT_FOUND:
            key = label.casefold()
            prior = unique.get(key)
            # Prefer the linked form without depending on input order.
            if prior is None or (prior[1] is None and source_uri is not None):
                unique[key] = (label, source_uri)
    return list(unique.values())


def _source_names_from_record(record: Mapping[str, Any]) -> list[str]:
    names = [label for label, _ in _record_sources(record)]
    for key in ("accelerator", "directory_source"):
        value = record.get(key)
        if _present(value):
            names.extend(label for label, _ in _source_parts(value))
    return names


def _round_company(
    record: Mapping[str, Any], lookup: Mapping[str, Mapping[str, Any]]
) -> tuple[str, Mapping[str, Any] | None]:
    identifier = _company_id(record)
    company = lookup.get(str(identifier).strip().casefold()) if _present(identifier) else None
    direct_name = _company_name(record)
    name = direct_name if _present(direct_name) else (_company_name(company) if company else None)
    if not _present(name) and _present(identifier):
        name = identifier
    return _plain(name), company


_DATE_FIELDS = (
    ("round_date", "reported"),
    ("funding_date", "reported"),
    ("event_date", "event"),
    ("closed_on", "close"),
    ("announced_on", "announcement"),
    ("announced_at", "announcement"),
    ("filed_on", "filing"),
    ("date", None),
    ("last_round_date", "reported"),
)


def _round_date(record: Mapping[str, Any]) -> tuple[Any, Any]:
    explicit_basis = _first(record, "date_basis", "round_date_basis", "funding_date_basis")
    for field, inferred_basis in _DATE_FIELDS:
        value = record.get(field)
        if _present(value):
            return value, explicit_basis if _present(explicit_basis) else inferred_basis
    return None, explicit_basis


def _date_year(value: Any) -> int | None:
    if not _present(value):
        return None
    match = re.match(r"^\s*(\d{4})", str(value))
    if not match:
        return None
    year = int(match.group(1))
    return year if 1800 <= year <= 2200 else None


def _round_amount(record: Mapping[str, Any]) -> Any:
    return _first(
        record,
        "amount_usd",
        "round_size_usd",
        "funding_amount_usd",
        "funding_usd",
        "last_round_size_usd",
        "amount",
    )


def _round_stage(record: Mapping[str, Any]) -> Any:
    return _first(record, "stage", "round_stage", "funding_stage", "current_stage", "round_type")


def _round_verification(record: Mapping[str, Any]) -> Any:
    return _first(record, "verification_status", "verification", "evidence_status", "confidence")


def _round_sort_key(
    record: Mapping[str, Any], lookup: Mapping[str, Mapping[str, Any]]
) -> tuple[str, str, str, float, str]:
    name, _ = _round_company(record, lookup)
    round_date, _ = _round_date(record)
    amount = _number(_round_amount(record))
    return (
        name.casefold(),
        _plain(round_date),
        _plain(_round_stage(record)).casefold(),
        amount if amount is not None else math.inf,
        _stable_json(record),
    )


def _coverage_number(record: Mapping[str, Any]) -> float | None:
    raw = _first(
        record,
        "coverage",
        "coverage_rate",
        "coverage_pct",
        "present_rate",
        "availability_rate",
    )
    if isinstance(raw, str) and raw.strip().endswith("%"):
        raw = raw.strip()[:-1]
        number = _number(raw)
        return max(0.0, min(1.0, number / 100.0)) if number is not None else None
    number = _number(raw)
    if number is not None:
        if 1.0 < number <= 100.0:
            number /= 100.0
        return max(0.0, min(1.0, number))
    present = _number(_first(record, "present", "present_count", "available"))
    total = _number(_first(record, "total", "record_count", "sample_count"))
    if present is not None and total is not None and total > 0:
        return max(0.0, min(1.0, present / total))
    return None


def _expand_quality(value: Any) -> list[dict[str, Any]]:
    """Accept common sequence and report-dictionary quality shapes."""

    if value is None:
        return []
    if not isinstance(value, Mapping):
        return _records(value)

    expanded: list[dict[str, Any]] = []
    for key in ("fields", "field_quality", "field_coverage", "coverage", "hard_to_obtain"):
        nested = value.get(key)
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            for item in nested:
                if isinstance(item, Mapping):
                    row = dict(item)
                elif _present(item):
                    row = {"field": item}
                else:
                    continue
                if key == "hard_to_obtain":
                    row.setdefault("hard_to_obtain", True)
                expanded.append(row)

    for key in ("field_coverage", "coverage_by_field"):
        nested = value.get(key)
        if isinstance(nested, Mapping):
            for field, details in nested.items():
                if isinstance(details, Mapping):
                    row = dict(details)
                    row.setdefault("field", field)
                else:
                    row = {"field": field, "coverage": details}
                expanded.append(row)

    if expanded:
        return expanded
    if any(key in value for key in ("field", "field_path", "path", "coverage", "coverage_rate")):
        return [dict(value)]
    return []


def _fallback_quality(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rounds:
        return []

    checks = (
        ("funding.company", lambda row: _company_name(row) or _company_id(row)),
        ("funding.date", lambda row: _round_date(row)[0]),
        ("funding.stage", _round_stage),
        ("funding.amount_usd", _round_amount),
        ("funding.source", lambda row: _record_sources(row)),
        ("funding.verification_status", _round_verification),
    )
    total = len(rounds)
    return [
        {
            "field": field,
            "present": sum(1 for row in rounds if _present(getter(row))),
            "total": total,
            "reason": "computed from the displayed funding rounds",
        }
        for field, getter in checks
    ]


def _quality_rows(value: Any, rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_rows = _expand_quality(value) or _fallback_quality(rounds)
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        field = _first(row, "field", "field_path", "path", "name", "parameter")
        if not _present(field):
            continue
        coverage = _coverage_number(row)
        reason = _first(
            row,
            "reason",
            "reason_code",
            "missing_reason",
            "why",
            "description",
            "action",
        )
        hard_value = _first(row, "hard_to_obtain", "hard", "is_hard")
        status = _first(row, "status", "classification")
        hard = bool(hard_value) or (isinstance(status, str) and "hard" in status.casefold())
        normalized.append(
            {
                "field": _plain(field),
                "coverage": coverage,
                "reason": _plain(reason) if _present(reason) else None,
                "hard": hard,
                "stable": _stable_json(row),
            }
        )

    # Resolve duplicate field entries reproducibly, preferring a row that has a
    # measured coverage value and then the lowest (most conservative) coverage.
    normalized.sort(
        key=lambda row: (
            row["field"].casefold(),
            row["coverage"] is None,
            row["coverage"] if row["coverage"] is not None else math.inf,
            row["stable"],
        )
    )
    unique: dict[str, dict[str, Any]] = {}
    for row in normalized:
        key = row["field"].casefold()
        if key not in unique:
            unique[key] = row
        else:
            unique[key]["hard"] = unique[key]["hard"] or row["hard"]

    return sorted(
        unique.values(),
        key=lambda row: (
            row["coverage"] is None,
            row["coverage"] if row["coverage"] is not None else math.inf,
            row["field"].casefold(),
        ),
    )


def _source_error_count(source_runs: list[dict[str, Any]]) -> int:
    count = 0
    failure_statuses = {"error", "failed", "failure", "unavailable", "timed_out", "timeout"}
    for run in source_runs:
        explicit = _number(_first(run, "error_count", "errors_count", "failures"))
        if explicit is not None:
            count += max(0, int(explicit))
            continue
        errors = run.get("errors")
        if isinstance(errors, Sequence) and not isinstance(errors, (str, bytes)):
            count += len(errors)
            continue
        if _present(run.get("error")):
            count += 1
            continue
        status = _first(run, "status", "state", "result")
        ok = run.get("ok")
        if ok is False or (isinstance(status, str) and status.casefold() in failure_statuses):
            count += 1
    return count


def _all_source_names(
    companies: list[dict[str, Any]],
    rounds: list[dict[str, Any]],
    source_runs: list[dict[str, Any]],
) -> list[str]:
    names: list[str] = []
    for record in [*companies, *rounds]:
        names.extend(_source_names_from_record(record))
    for run in source_runs:
        names.extend(_source_names_from_record(run))
        run_name = _first(run, "source", "source_name", "provider", "adapter", "channel", "name")
        if _present(run_name):
            names.extend(label for label, _ in _source_parts(run_name))
    unique: dict[str, str] = {}
    for name in sorted(names, key=lambda item: (item.casefold(), item)):
        if name != _NOT_FOUND:
            unique.setdefault(name.casefold(), name)
    return list(unique.values())


def _source_cell(parts: list[tuple[str, str | None]]) -> str:
    if not parts:
        return '<span class="missing">not found</span>'
    rendered: list[str] = []
    for label, uri in parts:
        safe_label = html.escape(label, quote=True)
        if uri:
            rendered.append(
                f'<a href="{html.escape(uri, quote=True)}" target="_blank" '
                f'rel="noopener noreferrer">{safe_label}</a>'
            )
        else:
            rendered.append(safe_label)
    return ", ".join(rendered)


def _summary_cards(company_count: int, round_count: int, source_count: int, error_count: int) -> str:
    cards = (
        ("Companies", company_count),
        ("Funding rounds", round_count),
        ("Sources", source_count),
        ("Source errors", error_count),
    )
    return "".join(
        f'<div class="metric"><span class="metric-value">{value:,}</span>'
        f'<span class="metric-label">{html.escape(label)}</span></div>'
        for label, value in cards
    )


def _timeline(rounds: list[dict[str, Any]]) -> str:
    by_year: dict[int, dict[str, float]] = {}
    undated = 0
    for record in rounds:
        round_date, _ = _round_date(record)
        year = _date_year(round_date)
        if year is None:
            undated += 1
            continue
        bucket = by_year.setdefault(year, {"count": 0.0, "amount": 0.0, "amount_count": 0.0})
        bucket["count"] += 1
        amount = _number(_round_amount(record))
        if amount is not None:
            bucket["amount"] += amount
            bucket["amount_count"] += 1

    if not by_year:
        return '<p class="empty">No dated funding rounds found.</p>'

    max_count = max(bucket["count"] for bucket in by_year.values())
    rows: list[str] = []
    for year in sorted(by_year):
        bucket = by_year[year]
        count = int(bucket["count"])
        width = 100.0 * count / max_count if max_count else 0.0
        amount_text = (
            _format_money(bucket["amount"]) + " disclosed"
            if bucket["amount_count"]
            else "amount not found"
        )
        aria = f"{year}: {count} funding round{'s' if count != 1 else ''}; {amount_text}"
        rows.append(
            '<div class="timeline-row" role="img" '
            f'aria-label="{html.escape(aria, quote=True)}">'
            f'<span class="timeline-year">{year}</span>'
            '<span class="timeline-track">'
            f'<span class="timeline-bar" style="width:{width:.2f}%"></span></span>'
            f'<span class="timeline-value">{count} · {html.escape(amount_text)}</span></div>'
        )
    if undated:
        rows.append(f'<p class="note">{undated} additional round{"s" if undated != 1 else ""} had no usable date.</p>')
    return "".join(rows)


def _coverage_bars(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<p class="empty">No field coverage results found.</p>'

    rendered: list[str] = []
    for row in rows:
        coverage = row["coverage"]
        percentage = coverage * 100.0 if coverage is not None else 0.0
        percentage_text = (
            (f"{percentage:.0f}%" if abs(percentage - round(percentage)) < 0.05 else f"{percentage:.1f}%")
            if coverage is not None
            else _NOT_FOUND
        )
        reason = row.get("reason")
        badge = '<span class="hard-badge">hard to obtain</span>' if row.get("hard") else ""
        reason_html = f'<span class="coverage-reason">{html.escape(reason)}</span>' if reason else ""
        aria = (
            f' role="progressbar" aria-valuemin="0" aria-valuemax="100" '
            f'aria-valuenow="{percentage:.1f}"'
            if coverage is not None
            else ' role="img" aria-label="coverage not found"'
        )
        rendered.append(
            '<div class="coverage-item">'
            '<div class="coverage-copy">'
            f'<span class="coverage-name">{html.escape(row["field"])}</span>{badge}'
            f'<span class="coverage-pct">{percentage_text}</span>{reason_html}</div>'
            f'<div class="coverage-track"{aria}>'
            f'<span class="coverage-bar" style="width:{percentage:.2f}%"></span></div></div>'
        )
    return "".join(rendered)


def _rounds_table(
    rounds: list[dict[str, Any]], lookup: Mapping[str, Mapping[str, Any]]
) -> tuple[str, list[str]]:
    ordered = sorted(rounds, key=lambda row: _round_sort_key(row, lookup))
    table_sources: list[str] = []
    body: list[str] = []

    for record in ordered:
        company_name, company = _round_company(record, lookup)
        parts = _record_sources(record)
        if not parts and company:
            # Directory/accelerator is useful context when the event has no
            # provider label, but it never becomes a link unless explicitly so.
            accelerator = _first(company, "accelerator", "directory_source")
            parts = _source_parts(accelerator)
        source_names = [label for label, _ in parts]
        table_sources.extend(source_names)
        date_value, date_basis = _round_date(record)
        stage = _humanize(_round_stage(record))
        amount = _format_money(_round_amount(record))
        verification = _humanize(_round_verification(record))
        source_data = json.dumps([name.casefold() for name in source_names], ensure_ascii=False)
        date_class = "missing" if not _present(date_value) else ""
        body.append(
            '<tr class="round-row" '
            f'data-company="{html.escape(company_name, quote=True)}" '
            f'data-sources="{html.escape(source_data, quote=True)}">'
            f'<th scope="row">{html.escape(company_name)}</th>'
            f'<td>{_source_cell(parts)}</td>'
            f'<td>{html.escape(stage)}</td>'
            f'<td><time class="{date_class}">{_esc(date_value)}</time></td>'
            f'<td>{_esc(_humanize(date_basis))}</td>'
            f'<td class="numeric">{html.escape(amount)}</td>'
            f'<td>{html.escape(verification)}</td></tr>'
        )

    if not body:
        body.append('<tr class="empty-row"><td colspan="7">No funding rounds found.</td></tr>')

    unique_sources: dict[str, str] = {}
    for name in sorted(table_sources, key=lambda item: (item.casefold(), item)):
        unique_sources.setdefault(name.casefold(), name)
    return "".join(body), list(unique_sources.values())


_CSS = r"""
:root {
  color-scheme: light dark;
  --bg: #f7f8fb;
  --panel: #ffffff;
  --panel-soft: #f1f4f8;
  --ink: #172033;
  --muted: #627086;
  --line: #dce2ea;
  --brand: #1769e0;
  --brand-soft: #dceaff;
  --warn: #9b4d00;
  --warn-soft: #fff0db;
  --missing: #747d8c;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #11151d;
    --panel: #191f29;
    --panel-soft: #222a36;
    --ink: #f5f7fb;
    --muted: #aeb8c8;
    --line: #343e4d;
    --brand: #72a9ff;
    --brand-soft: #203a62;
    --warn: #ffc176;
    --warn-soft: #4a3218;
    --missing: #aab3c0;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); line-height: 1.45; }
main { width: min(1180px, calc(100% - 32px)); margin: 0 auto; padding: 32px 0 48px; }
h1 { margin: 0; font-size: clamp(1.55rem, 4vw, 2.25rem); letter-spacing: -0.035em; }
h2 { margin: 0 0 16px; font-size: 1rem; letter-spacing: 0.01em; }
.lede { margin: 7px 0 24px; color: var(--muted); max-width: 70ch; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.metric, .panel { background: var(--panel); border: 1px solid var(--line); border-radius: 12px; box-shadow: 0 1px 1px rgba(20, 30, 48, .03); }
.metric { padding: 15px 17px; }
.metric-value { display: block; font-size: 1.45rem; font-weight: 750; font-variant-numeric: tabular-nums; }
.metric-label { display: block; color: var(--muted); font-size: .78rem; margin-top: 2px; }
.grid { display: grid; grid-template-columns: minmax(0, .92fr) minmax(0, 1.08fr); gap: 16px; margin-bottom: 16px; align-items: start; }
.panel { padding: 18px; min-width: 0; }
.timeline-row { display: grid; grid-template-columns: 4.2rem minmax(80px, 1fr) minmax(8.5rem, auto); align-items: center; gap: 10px; margin: 10px 0; }
.timeline-year, .timeline-value { font-size: .76rem; font-variant-numeric: tabular-nums; }
.timeline-value { color: var(--muted); text-align: right; }
.timeline-track, .coverage-track { display: block; background: var(--panel-soft); border-radius: 999px; overflow: hidden; }
.timeline-track { height: 10px; }
.timeline-bar, .coverage-bar { display: block; height: 100%; background: var(--brand); border-radius: inherit; min-width: 0; }
.coverage-list { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px 20px; }
.coverage-copy { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 4px 8px; margin-bottom: 5px; }
.coverage-name { font-size: .76rem; overflow-wrap: anywhere; }
.coverage-pct { color: var(--muted); font-size: .73rem; font-variant-numeric: tabular-nums; }
.coverage-reason { grid-column: 1 / -1; color: var(--muted); font-size: .68rem; }
.coverage-track { height: 7px; }
.hard-badge { justify-self: start; color: var(--warn); background: var(--warn-soft); border-radius: 999px; padding: 1px 6px; font-size: .61rem; font-weight: 700; text-transform: uppercase; letter-spacing: .02em; }
.filters { display: flex; flex-wrap: wrap; gap: 12px; align-items: end; margin: 0 0 14px; }
.control { display: grid; gap: 4px; }
.control label { color: var(--muted); font-size: .72rem; font-weight: 650; }
input, select { min-height: 36px; padding: 7px 10px; color: var(--ink); background: var(--panel-soft); border: 1px solid var(--line); border-radius: 7px; font: inherit; font-size: .82rem; }
input { width: min(290px, 72vw); }
.shown-count { color: var(--muted); margin-left: auto; padding-bottom: 7px; font-size: .75rem; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 9px; }
table { width: 100%; border-collapse: collapse; min-width: 810px; font-size: .78rem; }
caption.sr-only, .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
th, td { padding: 10px 11px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
thead th { background: var(--panel-soft); color: var(--muted); font-size: .68rem; letter-spacing: .025em; text-transform: uppercase; white-space: nowrap; }
tbody th { font-weight: 700; }
tbody tr:last-child > * { border-bottom: 0; }
tbody tr:hover { background: color-mix(in srgb, var(--brand-soft) 22%, transparent); }
.numeric { text-align: right; font-variant-numeric: tabular-nums; }
a { color: var(--brand); text-underline-offset: 2px; }
.missing { color: var(--missing); }
.empty, .note { color: var(--muted); font-size: .78rem; }
.empty { margin: 0; padding: 8px 0; }
.note { margin: 12px 0 0; }
.empty-row td { text-align: center; color: var(--muted); padding: 24px; }
[hidden] { display: none !important; }
@media (max-width: 820px) {
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid { grid-template-columns: 1fr; }
  .shown-count { width: 100%; margin-left: 0; }
}
@media (max-width: 520px) {
  main { width: min(100% - 20px, 1180px); padding-top: 20px; }
  .coverage-list { grid-template-columns: 1fr; }
  .timeline-row { grid-template-columns: 3.5rem minmax(60px, 1fr); }
  .timeline-value { grid-column: 2; text-align: left; }
}
"""


_JS = r"""
(() => {
  const companyInput = document.getElementById("company-filter");
  const sourceSelect = document.getElementById("source-filter");
  const shown = document.getElementById("shown-count");
  const rows = Array.from(document.querySelectorAll("tr.round-row"));

  function applyFilters() {
    const companyQuery = (companyInput?.value || "").trim().toLocaleLowerCase();
    const selectedSource = (sourceSelect?.value || "").toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      const company = (row.dataset.company || "").toLocaleLowerCase();
      let sources = [];
      try { sources = JSON.parse(row.dataset.sources || "[]"); } catch (_) { sources = []; }
      const companyMatch = !companyQuery || company.includes(companyQuery);
      const sourceMatch = !selectedSource || sources.includes(selectedSource);
      row.hidden = !(companyMatch && sourceMatch);
      if (!row.hidden) visible += 1;
    }
    if (shown) shown.textContent = `${visible} round${visible === 1 ? "" : "s"} shown`;
  }

  companyInput?.addEventListener("input", applyFilters);
  sourceSelect?.addEventListener("change", applyFilters);
  applyFilters();
})();
"""


def render_html(
    companies: Sequence[Mapping[str, Any]] = (),
    funding_rounds: Sequence[Mapping[str, Any]] = (),
    quality_report: Sequence[Mapping[str, Any]] | Mapping[str, Any] = (),
    source_runs: Sequence[Mapping[str, Any]] = (),
    *,
    title: str = _DEFAULT_TITLE,
) -> str:
    """Render a deterministic, self-contained funding-scrape dashboard.

    Args:
        companies: Discovered/canonical company records.
        funding_rounds: Normalized funding-event records.
        quality_report: Per-field quality records, or a report mapping containing
            ``fields``, ``field_coverage``, or ``hard_to_obtain`` entries.
        source_runs: One status record per attempted source adapter.
        title: Page and document title.

    Returns:
        A complete UTF-8 HTML document as a string.  The output contains only
        inline CSS and JavaScript and performs no network requests.
    """

    company_rows = _dedupe_companies(_records(companies))
    round_rows = _records(funding_rounds)
    source_run_rows = _records(source_runs)
    lookup = _company_lookup(company_rows)
    quality_rows = _quality_rows(quality_report, round_rows)
    source_names = _all_source_names(company_rows, round_rows, source_run_rows)
    error_count = _source_error_count(source_run_rows)
    table_body, table_sources = _rounds_table(round_rows, lookup)

    source_options = "".join(
        f'<option value="{html.escape(name.casefold(), quote=True)}">{html.escape(name)}</option>'
        for name in table_sources
    )
    safe_title = html.escape(str(title) if _present(title) else _DEFAULT_TITLE, quote=True)
    metrics = _summary_cards(len(company_rows), len(round_rows), len(source_names), error_count)
    timeline = _timeline(round_rows)
    coverage = _coverage_bars(quality_rows)

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        '<main>\n<header>\n'
        f"<h1>{safe_title}</h1>\n"
        '<p class="lede">A compact, evidence-aware view of discovered companies, funding events, and collection coverage.</p>\n'
        f'<div class="metrics" aria-label="Scrape summary">{metrics}</div>\n'
        "</header>\n"
        '<div class="grid">\n'
        '<section class="panel" aria-labelledby="timeline-heading">\n'
        '<h2 id="timeline-heading">Funding by year</h2>\n'
        f"{timeline}\n</section>\n"
        '<section class="panel" aria-labelledby="coverage-heading">\n'
        '<h2 id="coverage-heading">Field coverage</h2>\n'
        f'<div class="coverage-list">{coverage}</div>\n</section>\n</div>\n'
        '<section class="panel" aria-labelledby="rounds-heading">\n'
        '<h2 id="rounds-heading">Funding rounds</h2>\n'
        '<div class="filters" role="search" aria-label="Filter funding rounds">\n'
        '<div class="control"><label for="company-filter">Company</label>'
        '<input id="company-filter" type="search" placeholder="Filter by company" autocomplete="off" aria-controls="rounds-table"></div>\n'
        '<div class="control"><label for="source-filter">Source</label>'
        f'<select id="source-filter" aria-controls="rounds-table"><option value="">All sources</option>{source_options}</select></div>\n'
        f'<div class="shown-count" id="shown-count" aria-live="polite">{len(round_rows)} round{"" if len(round_rows) == 1 else "s"} shown</div>\n'
        "</div>\n"
        '<div class="table-wrap"><table id="rounds-table">\n'
        '<caption class="sr-only">Funding rounds with company, evidence source, stage, date, amount, and verification status</caption>\n'
        '<thead><tr><th scope="col">Company</th><th scope="col">Source</th><th scope="col">Stage</th>'
        '<th scope="col">Funding date</th><th scope="col">Date basis</th><th scope="col">Amount</th>'
        '<th scope="col">Verification</th></tr></thead>\n'
        f"<tbody>{table_body}</tbody>\n</table></div>\n</section>\n"
        f"</main>\n<script>{_JS}</script>\n</body>\n</html>\n"
    )


def write_html(
    companies: Sequence[Mapping[str, Any]] = (),
    funding_rounds: Sequence[Mapping[str, Any]] = (),
    quality_report: Sequence[Mapping[str, Any]] | Mapping[str, Any] = (),
    source_runs: Sequence[Mapping[str, Any]] = (),
    path: str | Path | None = None,
    *,
    title: str = _DEFAULT_TITLE,
) -> Path:
    """Write :func:`render_html` to ``path`` and return that ``Path``.

    Parent directories are created when needed.  ``path`` is intentionally the
    only filesystem concern in this module.
    """

    if path is None:
        raise TypeError("write_html() missing required argument: 'path'")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        render_html(
            companies,
            funding_rounds,
            quality_report,
            source_runs,
            title=title,
        ),
        encoding="utf-8",
    )
    return destination
