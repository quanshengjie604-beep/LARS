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


def _progression_keys(record: Mapping[str, Any]) -> list[str]:
    """Keys used to link a company to its funding rounds (id first, name fallback)."""

    keys: list[str] = []
    identifier = _company_id(record)
    if _present(identifier):
        keys.append("id:" + str(identifier).strip().casefold())
    name = _company_name(record)
    if _present(name):
        keys.append("name:" + str(name).strip().casefold())
    return keys


def _progression_step(record: Mapping[str, Any]) -> dict[str, Any]:
    round_date, _ = _round_date(record)
    year = _date_year(round_date)
    amount = _number(_round_amount(record))
    stage = _round_stage(record) or record.get("raw_stage")
    if amount is not None:
        label = _format_money(amount)
    elif _present(stage):
        label = _humanize(stage)
    elif year is not None:
        label = str(year)
    else:
        label = "round"
    return {
        "year": year,
        "amount": amount,
        "label": label,
        "sort_date": _plain(round_date),
        "stable": _stable_json(record),
    }


def _funding_progressions(rounds: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group rounds by company key into time-ordered progression steps.

    A round is filed under every key it exposes (id and/or name) so a company
    can be matched by whichever identifier it carries.  Rounds are deduplicated
    per group by ``round_id`` (or a stable fingerprint) to tolerate a round that
    appears under multiple keys or is repeated across inputs.
    """

    groups: dict[str, dict[str, dict[str, Any]]] = {}
    for record in rounds:
        dedupe = _plain(_first(record, "round_id"))
        if dedupe == _NOT_FOUND:
            dedupe = _stable_json(record)
        for key in _progression_keys(record):
            groups.setdefault(key, {})[dedupe] = record

    result: dict[str, list[dict[str, Any]]] = {}
    for key, members in groups.items():
        steps = [_progression_step(record) for record in members.values()]
        steps.sort(
            key=lambda step: (
                step["sort_date"],
                step["amount"] if step["amount"] is not None else math.inf,
                step["stable"],
            )
        )
        result[key] = steps
    return result


def _company_steps(
    record: Mapping[str, Any], progressions: Mapping[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    for key in _progression_keys(record):
        steps = progressions.get(key)
        if steps:
            return steps
    return []


def _progression_direction(prev: dict[str, Any], current: dict[str, Any]) -> str:
    prior, latest = prev["amount"], current["amount"]
    if prior is not None and latest is not None:
        if latest > prior:
            return "up"
        if latest < prior:
            return "down"
    return "flat"


_ARROW_GLYPH = {"up": "↑", "down": "↓", "flat": "→"}


def _progression_plain(steps: list[dict[str, Any]]) -> str | None:
    """Plain-text progression (arrows included) for search and null handling."""

    if not steps:
        return None
    parts = [(f"{step['year']} " if step["year"] is not None else "") + step["label"] for step in steps]
    return " → ".join(parts)


def _progression_html(steps: list[dict[str, Any]]) -> str:
    if not steps:
        return '<span class="missing">not found</span>'
    parts: list[str] = []
    prev: dict[str, Any] | None = None
    for index, step in enumerate(steps):
        if index and prev is not None:
            direction = _progression_direction(prev, step)
            glyph = _ARROW_GLYPH[direction]
            parts.append(
                f'<span class="prog-arrow {direction}" aria-hidden="true">{glyph}</span>'
            )
        year_html = (
            f'<span class="prog-year">{step["year"]}</span>' if step["year"] is not None else ""
        )
        parts.append(
            '<span class="prog-step">'
            f'{year_html}<span class="prog-amt">{html.escape(step["label"])}</span></span>'
        )
        prev = step
    return '<span class="prog">' + "".join(parts) + "</span>"


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


def _link_html(uri: str, label: str) -> str:
    return (
        f'<a href="{html.escape(uri, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{html.escape(label)}</a>'
    )


def _grid_columns(
    records: list[dict[str, Any]], priority: Sequence[str] = ()
) -> list[str]:
    """Union of every key across ``records``, deterministically ordered.

    Priority keys that actually occur are placed first (in the given order); all
    remaining keys follow alphabetically.  This is what makes the grid show
    *all possible parameters* rather than a hand-picked subset.
    """

    keys: set[str] = set()
    for record in records:
        keys.update(str(key) for key in record.keys())
    ordered = [key for key in priority if key in keys]
    ordered.extend(sorted(key for key in keys if key not in ordered))
    return ordered


def _grid_cell(value: Any) -> str:
    """Render a single spreadsheet cell, linking any absolute HTTP(S) URLs."""

    if not _present(value):
        return '<span class="missing">not found</span>'

    single = _safe_url(value)
    if single:
        return _link_html(single, urlsplit(single).netloc or single)

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        present = [item for item in value if _present(item)]
        if present and all(_safe_url(item) for item in present):
            links = [
                _link_html(_safe_url(item) or "", urlsplit(_safe_url(item) or "").netloc)
                for item in present
            ]
            return ", ".join(links)

    return html.escape(_plain(value))


def _grid_table(
    records: list[dict[str, Any]],
    sort_key,
    *,
    priority: Sequence[str] = (),
    empty_label: str = "No records found.",
    renderers: Mapping[str, Any] | None = None,
) -> tuple[str, str, int]:
    """Build a full-parameter spreadsheet grid.

    Returns ``(header_html, body_html, column_count)``.  Every discovered field
    becomes a column; the first column is rendered as a row header so it can be
    frozen while the reader scrolls horizontally through the remaining columns.
    """

    renderers = renderers or {}
    columns = _grid_columns(records, priority)
    if not columns:
        placeholder = '<tr class="empty-row"><td>' + html.escape(empty_label) + "</td></tr>"
        return "<tr><th scope=\"col\">&nbsp;</th></tr>", placeholder, 1

    header = "".join(
        f'<th scope="col">{html.escape(column.replace("_", " "))}</th>' for column in columns
    )

    body: list[str] = []
    for record in sorted(records, key=sort_key):
        cells: list[str] = []
        search_parts: list[str] = []
        for index, column in enumerate(columns):
            value = record.get(column)
            plain = _plain(value)
            if plain != _NOT_FOUND:
                search_parts.append(plain.casefold())
            renderer = renderers.get(column)
            rendered = renderer(value, record) if renderer else _grid_cell(value)
            if index == 0:
                cells.append(f'<th scope="row">{rendered}</th>')
            else:
                cells.append(f"<td>{rendered}</td>")
        blob = html.escape(" ".join(search_parts), quote=True)
        body.append(f'<tr class="grid-row" data-search="{blob}">' + "".join(cells) + "</tr>")

    if not body:
        body.append(
            f'<tr class="empty-row"><td colspan="{len(columns)}">'
            f"{html.escape(empty_label)}</td></tr>"
        )
    return f"<tr>{header}</tr>", "".join(body), len(columns)


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


def _company_grid_sort(record: Mapping[str, Any]):
    return (_plain(_company_name(record)).casefold(), _stable_json(record))


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
  .prog-arrow.up { color: #4ccb7f; }
  .prog-arrow.down { color: #ff7a63; }
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
.grid-wrap { overflow: auto; max-height: 78vh; border: 1px solid var(--line); border-radius: 9px; }
.grid-table { min-width: max-content; }
.grid-table td, .grid-table th { max-width: 360px; overflow-wrap: anywhere; }
.grid-table thead th { position: sticky; top: 0; z-index: 2; }
.grid-table th[scope="row"] { position: sticky; left: 0; z-index: 1; background: var(--panel); box-shadow: 1px 0 0 var(--line); }
.grid-table thead th:first-child { z-index: 3; background: var(--panel-soft); }
.grid-table tbody tr:hover th[scope="row"] { background: color-mix(in srgb, var(--brand-soft) 22%, var(--panel)); }
a { color: var(--brand); text-underline-offset: 2px; }
.prog { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 3px 6px; font-variant-numeric: tabular-nums; }
.prog-step { display: inline-flex; align-items: baseline; gap: 4px; white-space: nowrap; }
.prog-year { color: var(--muted); font-size: .68rem; }
.prog-amt { font-weight: 650; }
.prog-arrow { font-weight: 800; font-size: .95rem; line-height: 1; }
.prog-arrow.up { color: #157a41; }
.prog-arrow.down { color: #b3341f; }
.prog-arrow.flat { color: var(--muted); }
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
  // Generic free-text filter shared by every spreadsheet grid on the page.
  // A search box declares which grid it drives, which count element to update,
  // and the singular/plural noun for that count.
  const inputs = Array.from(document.querySelectorAll("input.grid-search"));
  for (const input of inputs) {
    const table = document.getElementById(input.dataset.target || "");
    const count = document.getElementById(input.dataset.count || "");
    const singular = input.dataset.singular || "row";
    const plural = input.dataset.plural || singular + "s";
    const rows = table ? Array.from(table.querySelectorAll("tr.grid-row")) : [];

    const apply = () => {
      const query = (input.value || "").trim().toLocaleLowerCase();
      let visible = 0;
      for (const row of rows) {
        const blob = row.dataset.search || "";
        row.hidden = query ? !blob.includes(query) : false;
        if (!row.hidden) visible += 1;
      }
      if (count) count.textContent = `${visible} ${visible === 1 ? singular : plural} shown`;
    };

    input.addEventListener("input", apply);
    apply();
  }
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

    safe_title = html.escape(str(title) if _present(title) else _DEFAULT_TITLE, quote=True)
    metrics = _summary_cards(len(company_rows), len(round_rows), len(source_names), error_count)
    timeline = _timeline(round_rows)
    coverage = _coverage_bars(quality_rows)

    # Per-company time series: link each company to its funding rounds and
    # surface the round-over-round progression as a dedicated column.
    progressions = _funding_progressions(round_rows)
    for company in company_rows:
        company["funding_progression"] = _progression_plain(_company_steps(company, progressions))

    def _progression_renderer(_value: Any, record: Mapping[str, Any]) -> str:
        return _progression_html(_company_steps(record, progressions))

    company_head, company_body, company_cols = _grid_table(
        company_rows,
        _company_grid_sort,
        priority=("name", "funding_progression", "startup_id", "domain", "website"),
        empty_label="No companies found.",
        renderers={"funding_progression": _progression_renderer},
    )
    rounds_head, rounds_body, rounds_cols = _grid_table(
        round_rows,
        lambda row: _round_sort_key(row, lookup),
        priority=("company_name", "startup_id", "round_id", "date", "stage", "amount_usd"),
        empty_label="No funding rounds found.",
    )
    company_count = len(company_rows)
    round_count = len(round_rows)

    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        '<main>\n<header>\n'
        f"<h1>{safe_title}</h1>\n"
        '<p class="lede">A complete, evidence-aware view of every discovered company and funding event. '
        "Each table is a spreadsheet-style grid showing every collected parameter; scroll horizontally to see all columns.</p>\n"
        f'<div class="metrics" aria-label="Scrape summary">{metrics}</div>\n'
        "</header>\n"
        '<div class="grid">\n'
        '<section class="panel" aria-labelledby="timeline-heading">\n'
        '<h2 id="timeline-heading">Funding by year</h2>\n'
        f"{timeline}\n</section>\n"
        '<section class="panel" aria-labelledby="coverage-heading">\n'
        '<h2 id="coverage-heading">Field coverage</h2>\n'
        f'<div class="coverage-list">{coverage}</div>\n</section>\n</div>\n'
        '<section class="panel" aria-labelledby="companies-heading">\n'
        f'<h2 id="companies-heading">Companies · all parameters ({company_cols} columns)</h2>\n'
        '<div class="filters" role="search" aria-label="Filter companies">\n'
        '<div class="control"><label for="company-list-filter">Search</label>'
        '<input id="company-list-filter" class="grid-search" type="search" placeholder="Filter across every field" '
        'autocomplete="off" aria-controls="companies-table" '
        'data-target="companies-table" data-count="company-list-shown" data-singular="company" data-plural="companies"></div>\n'
        f'<div class="shown-count" id="company-list-shown" aria-live="polite">{company_count} compan{"y" if company_count == 1 else "ies"} shown</div>\n'
        "</div>\n"
        '<div class="grid-wrap"><table id="companies-table" class="grid-table">\n'
        '<caption class="sr-only">All scraped companies with every collected parameter as a column</caption>\n'
        f"<thead>{company_head}</thead>\n"
        f"<tbody>{company_body}</tbody>\n</table></div>\n</section>\n"
        '<section class="panel" aria-labelledby="rounds-heading">\n'
        f'<h2 id="rounds-heading">Funding rounds · all parameters ({rounds_cols} columns)</h2>\n'
        '<div class="filters" role="search" aria-label="Filter funding rounds">\n'
        '<div class="control"><label for="rounds-filter">Search</label>'
        '<input id="rounds-filter" class="grid-search" type="search" placeholder="Filter across every field" '
        'autocomplete="off" aria-controls="rounds-table" '
        'data-target="rounds-table" data-count="rounds-shown" data-singular="round" data-plural="rounds"></div>\n'
        f'<div class="shown-count" id="rounds-shown" aria-live="polite">{round_count} round{"" if round_count == 1 else "s"} shown</div>\n'
        "</div>\n"
        '<div class="grid-wrap"><table id="rounds-table" class="grid-table">\n'
        '<caption class="sr-only">Funding rounds with every collected parameter as a column</caption>\n'
        f"<thead>{rounds_head}</thead>\n"
        f"<tbody>{rounds_body}</tbody>\n</table></div>\n</section>\n"
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
