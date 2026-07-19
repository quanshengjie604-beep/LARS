"""Pure normalization, identifier, date, and scoring helpers."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

SHARED_HOSTS = {
    "facebook.com",
    "github.com",
    "instagram.com",
    "linkedin.com",
    "linktr.ee",
    "medium.com",
    "notion.site",
    "twitter.com",
    "x.com",
}


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def sat(value: float, ceiling: float) -> float:
    return clamp(value / ceiling) if ceiling > 0 else 0.0


def nlog(value: float, low_power: float, high_power: float) -> float:
    if high_power <= low_power:
        raise ValueError("high_power must exceed low_power")
    return clamp((math.log10(max(value, 1.0)) - low_power) / (high_power - low_power))


def weighted_present(
    components: Iterable[tuple[float | None, float]],
) -> tuple[float | None, float]:
    """Return a null-dropping weighted mean and input-weight coverage."""
    present_weight = 0.0
    total_weight = 0.0
    weighted_sum = 0.0
    for value, weight in components:
        total_weight += weight
        if value is None:
            continue
        present_weight += weight
        weighted_sum += value * weight
    if not present_weight:
        return None, 0.0
    return weighted_sum / present_weight, present_weight / total_weight if total_weight else 0.0


def mean_present(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def slugify(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-") or "unknown"


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"\b(?:incorporated|inc|llc|ltd|limited|corp|corporation|company|co)\b", " ", value)
    return re.sub(r"[^a-z0-9]+", "", value)


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip()
    if not re.match(r"^https?://", candidate, re.I):
        candidate = "https://" + candidate
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname.casefold().removeprefix("www.")
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = re.sub(r"/{2,}", "/", parts.path or "").rstrip("/")
    return urlunsplit((parts.scheme.lower(), host + port, path, parts.query, ""))


def domain_of(value: str | None, *, allow_shared: bool = False) -> str | None:
    normalized = normalize_url(value)
    if not normalized:
        return None
    host = (urlsplit(normalized).hostname or "").casefold().removeprefix("www.")
    if not allow_shared and host in SHARED_HOSTS:
        return None
    return host or None


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    payload = "\x1f".join(str(part).strip() for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}_{digest}"


def parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{4}", text):
        return None  # do not invent month/day precision
    if re.fullmatch(r"\d{4}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def iso_date(value: Any) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_batch(value: str | None) -> tuple[int | None, str | None]:
    """Return cohort year and precision without fabricating a funding/founding day."""
    if not value:
        return None, None
    match = re.search(r"\b(20\d{2})\b", value)
    return (int(match.group(1)), "year") if match else (None, None)


_MULTIPLIER = {
    "": Decimal("1"),
    "k": Decimal("1000"),
    "thousand": Decimal("1000"),
    "m": Decimal("1000000"),
    "mm": Decimal("1000000"),
    "million": Decimal("1000000"),
    "b": Decimal("1000000000"),
    "bn": Decimal("1000000000"),
    "billion": Decimal("1000000000"),
}


def scaled_number(number: str, suffix: str = "") -> int | float | None:
    try:
        raw = Decimal(number.replace(",", "")) * _MULTIPLIER[suffix.casefold().strip()]
    except (InvalidOperation, KeyError):
        return None
    return int(raw) if raw == raw.to_integral_value() else float(raw)


def unique_sorted(values: Iterable[str | None]) -> list[str]:
    return sorted({value.strip() for value in values if value and value.strip()}, key=str.casefold)


def json_ready(value: Any) -> Any:
    """Recursively convert dataclasses/date/tuples into deterministic JSON values."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(value):
        return json_ready(asdict(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_ready(item) for item in value]
    return value


def deep_get(record: Mapping[str, Any], path: str, default: Any = None) -> Any:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return default
        current = current.get(part, default)
    return current


def date_sort_key(value: str | None) -> tuple[int, str]:
    return (0, value) if value else (1, "")
