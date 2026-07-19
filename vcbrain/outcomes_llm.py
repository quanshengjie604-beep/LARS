"""Web-search LLM enrichment of company funding rounds and exit/failure outcomes.

Rather than reading a company's exit/failure signal only from its point-in-time
directory statuses (:func:`vcbrain.assemble.classify_status_outcome`), the pipeline
queries a web-search-enabled LLM for *every* company, asking for its funding rounds
and whether it is still operating or dissolved. The verdict is parsed back into the
outcome record's ``failure_observed`` / ``failure_date`` / ``failure_definition``
fields (see :func:`apply_outcome_enrichment` in ``assemble``) and its funding rounds
replace the scraped funding-round output entirely.

The default provider is Google Gemini (``GEMINI_API_KEY``); an ``--use-openai`` flag
switches to the OpenAI Responses API (``OPENAI_API_KEY``). Both are web-search
enabled and share the same parsing/verdict logic.

Design constraints honoured here:

* No new dependency — both HTTP APIs are called directly through ``httpx``,
  which the project already vendors.
* Queries are issued concurrently through a thread pool so the whole cohort is
  enriched in roughly the time of the slowest single request, not their sum.
* Nothing runs unless the provider's key is set, and the network-free fixture
  pipeline never reaches this code (the caller gates on a live run).
* A live company is left on the deterministic censored baseline; only a confirmed
  dissolution/bankruptcy flips the failure fields.
"""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, List, Literal, Mapping, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .util import iso_date, parse_date, scaled_number

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
GEMINI_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

# Provider identifiers.
PROVIDER_GEMINI = "gemini"
PROVIDER_OPENAI = "openai"
DEFAULT_PROVIDER = PROVIDER_GEMINI

# Per-provider default models and the API-key environment variable.
# gemini-3.1-flash-lite balances capability and request throughput for batch
# enrichment. Override with $GEMINI_MODEL (e.g. gemini-3.5-flash for more capable
# but lower-RPM, or gemini-2.5-flash-lite for highest throughput).
DEFAULT_MODELS = {
    PROVIDER_GEMINI: "gemini-3.1-flash-lite",
    PROVIDER_OPENAI: "gpt-4o-mini",
}
MODEL_ENV_VARS = {
    PROVIDER_GEMINI: "GEMINI_MODEL",
    PROVIDER_OPENAI: "OPENAI_MODEL",
}
API_KEY_ENV_VARS = {
    PROVIDER_GEMINI: "GEMINI_API_KEY",
    PROVIDER_OPENAI: "OPENAI_API_KEY",
}

# Retained for backwards compatibility with imports/tests referencing the old name.
DEFAULT_MODEL = DEFAULT_MODELS[PROVIDER_OPENAI]


def _load_dotenv(path: str | os.PathLike[str] = ".env") -> None:
    """Populate os.environ from a local ``.env`` for keys not already set.

    A minimal, dependency-free parser (``KEY=VALUE`` / ``export KEY=VALUE``,
    ``#`` comments, optional surrounding quotes). The real process environment
    always wins, so an exported variable is never overridden by the file.
    """
    file = Path(path)
    if not file.is_file():
        return
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ[key] = value


_UNSET = object()


def resolve_api_key(
    explicit: str | None | object = _UNSET, *, provider: str = DEFAULT_PROVIDER
) -> str | None:
    """Provider API key from an explicit value, the environment, or a local ``.env``.

    ``provider`` selects the key variable (``GEMINI_API_KEY`` by default, or
    ``OPENAI_API_KEY`` for the OpenAI provider). An explicitly-passed value
    (including an empty string) is authoritative and disables the environment/``.env``
    fallback — passing ``""`` means "no key", which keeps offline tests from silently
    picking up a real ``.env``.
    """
    if explicit is not _UNSET:
        return explicit or None
    env_var = API_KEY_ENV_VARS.get(provider, API_KEY_ENV_VARS[DEFAULT_PROVIDER])
    if os.environ.get(env_var):
        return os.environ[env_var]
    _load_dotenv()
    return os.environ.get(env_var)

# The exact, mandated research question. ``{company}`` is substituted per company.
BASE_PROMPT = (
    "Please give me a table of dates and founding amount for each funding round of "
    "{company}, and whether it is still operating or is dissolved."
)

# Appended after the mandated question so the free-text answer is accompanied by a
# strictly-parseable block. The web search still drives the answer; this only fixes
# the shape of the part we machine-read.
_JSON_INSTRUCTION = (
    "\n\nAfter your table, output a fenced ```json code block and nothing after it, "
    "matching exactly this schema:\n"
    "{\n"
    '  "status": "operating" | "dissolved" | "acquired" | "unknown",\n'
    '  "status_reason": string,   // short justification, cite what the web says\n'
    '  "ceased_date": string|null, // ISO YYYY-MM-DD if dissolved/closed, else null\n'
    '  "failure_kind": "ceased_operations" | "dissolved" | "bankruptcy" '
    '| "shutdown_confirmed" | "other" | null,\n'
    '  "funding_rounds": [\n'
    '    {"date": string|null, "stage": string|null, "amount_usd": number|null, '
    '"amount_text": string|null}\n'
    "  ]\n"
    "}\n"
    "Use null when a value is not known; do not guess. If the company is still "
    "operating, set ceased_date and failure_kind to null."
)

# LLM failure_kind -> the enum required by ngboost_data_requirements.md §5.3.
_FAILURE_DEFINITIONS = {
    "ceased_operations",
    "dissolved",
    "bankruptcy",
    "shutdown_confirmed",
    "other",
}
_DISSOLVED_STATUSES = {"dissolved", "closed", "defunct", "dead", "shutdown", "shut down", "ceased", "bankrupt"}


class LLMFundingRound(BaseModel):
    """One funding round as reported by the LLM (all fields optional/nullable)."""

    model_config = ConfigDict(extra="ignore")

    date: Optional[str] = None
    stage: Optional[str] = None
    amount_usd: Optional[float] = None
    amount_text: Optional[str] = None


class LLMVerdict(BaseModel):
    """Pydantic schema the LLM's JSON block is validated against.

    Both providers are instructed (OpenAI via a forced ``json_schema`` structured
    output, Gemini via the prompt) to emit exactly this shape; the parsed JSON is
    validated here before it is turned into an :class:`OutcomeEnrichment`.
    """

    model_config = ConfigDict(extra="ignore")

    status: Literal["operating", "dissolved", "acquired", "unknown"] = "unknown"
    status_reason: str = ""
    ceased_date: Optional[str] = None
    failure_kind: Optional[
        Literal["ceased_operations", "dissolved", "bankruptcy", "shutdown_confirmed", "other"]
    ] = None
    funding_rounds: List[LLMFundingRound] = Field(default_factory=list)


def _openai_json_schema() -> dict[str, Any]:
    """A strict JSON Schema for OpenAI Responses structured output.

    Derived from :class:`LLMVerdict` but tightened to the ``strict`` structured-output
    rules (every property required and ``additionalProperties: false``); nullability is
    expressed with union types so optional values are still allowed.
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "status_reason", "ceased_date", "failure_kind", "funding_rounds"],
        "properties": {
            "status": {"type": "string", "enum": ["operating", "dissolved", "acquired", "unknown"]},
            "status_reason": {"type": "string"},
            "ceased_date": {"type": ["string", "null"]},
            "failure_kind": {
                "type": ["string", "null"],
                "enum": ["ceased_operations", "dissolved", "bankruptcy", "shutdown_confirmed", "other", None],
            },
            "funding_rounds": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["date", "stage", "amount_usd", "amount_text"],
                    "properties": {
                        "date": {"type": ["string", "null"]},
                        "stage": {"type": ["string", "null"]},
                        "amount_usd": {"type": ["number", "null"]},
                        "amount_text": {"type": ["string", "null"]},
                    },
                },
            },
        },
    }


@dataclass(slots=True)
class OutcomeEnrichment:
    """Parsed web-search verdict for one company.

    ``failure_observed`` is only ``True`` for a confirmed dissolution/bankruptcy.
    ``operating`` / ``acquired`` / ``unknown`` all leave it ``False`` so the
    deterministic censored baseline (and any directory-status exit) is preserved.
    """

    startup_id: str
    status: str  # operating | dissolved | acquired | unknown
    failure_observed: bool
    failure_date: str | None
    failure_definition: str | None
    status_reason: str
    funding_rounds: list[dict[str, Any]] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    query: str = ""
    raw_excerpt: str = ""
    error: str | None = None


def _amount_to_usd(value: Any, text: str | None) -> int | float | None:
    """Coerce a numeric amount, falling back to parsing ``$1.2M`` style text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if float(value).is_integer() else float(value)
    if isinstance(value, str):
        parsed = _amount_from_text(value)
        if parsed is not None:
            return parsed
    return _amount_from_text(text) if text else None


def _amount_from_text(text: str | None) -> int | float | None:
    if not text:
        return None
    match = re.search(
        r"\$?\s*([\d][\d,]*(?:\.\d+)?)\s*(billion|bn|b|million|mm|m|thousand|k)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    suffix = (match.group(2) or "").lower()
    # scaled_number understands "k"/"m"/"b"/word forms; normalise "bn"->"b".
    suffix = {"bn": "b", "mm": "m"}.get(suffix, suffix)
    return scaled_number(match.group(1), suffix)


def _extract_json_block(text: str) -> dict[str, Any] | None:
    """Pull the last JSON object out of the model's answer, fenced or bare."""
    if not text:
        return None
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = list(fenced)
    if not candidates:
        # Fall back to the last balanced-looking {...} span.
        start = text.rfind("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start : end + 1])
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _collect_output_text(payload: Mapping[str, Any]) -> str:
    """Flatten OpenAI Responses-API output into plain text across SDK versions."""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, Mapping):
            continue
        for part in item.get("content") or []:
            if isinstance(part, Mapping):
                piece = part.get("text")
                if isinstance(piece, str):
                    chunks.append(piece)
    return "\n".join(chunks)


def _collect_gemini_text(payload: Mapping[str, Any]) -> str:
    """Flatten a Gemini generateContent response into plain text."""
    chunks: list[str] = []
    for candidate in payload.get("candidates") or []:
        if not isinstance(candidate, Mapping):
            continue
        content = candidate.get("content")
        if not isinstance(content, Mapping):
            continue
        for part in content.get("parts") or []:
            if isinstance(part, Mapping):
                piece = part.get("text")
                if isinstance(piece, str):
                    chunks.append(piece)
    return "\n".join(chunks)


def _normalize_status(raw: Any) -> str:
    """Fold free-text/synonym statuses onto the four schema-allowed values.

    Keeps the model robust to phrasing ("closed", "defunct", "shut down", ...) before
    the strict Pydantic ``Literal`` validation rejects anything unexpected.
    """
    status = str(raw or "unknown").strip().casefold()
    if status in _DISSOLVED_STATUSES:
        return "dissolved"
    if status in {"operating", "dissolved", "acquired", "unknown"}:
        return status
    if "acqui" in status or "merged" in status:
        return "acquired"
    if "operat" in status or "active" in status or "alive" in status:
        return "operating"
    return "unknown"


def _validate_verdict(data: Mapping[str, Any]) -> LLMVerdict:
    """Coerce a raw JSON dict to the canonical enums, then validate with Pydantic.

    Raises :class:`pydantic.ValidationError` if the payload cannot be made to fit the
    :class:`LLMVerdict` schema — the caller turns that into a neutral "unknown" verdict.
    """
    payload = dict(data)
    payload["status"] = _normalize_status(payload.get("status"))
    kind = str(payload.get("failure_kind") or "").strip().casefold()
    payload["failure_kind"] = kind if kind in _FAILURE_DEFINITIONS else None
    return LLMVerdict.model_validate(payload)


def _parse_verdict(
    startup_id: str, model: str, query: str, answer: str, *, as_of: date
) -> OutcomeEnrichment:
    data = _extract_json_block(answer)
    if not isinstance(data, Mapping):
        # No parseable JSON at all -> neutral verdict, deterministic baseline preserved.
        verdict = LLMVerdict()
        validation_error: str | None = "no JSON block found in answer"
    else:
        try:
            verdict = _validate_verdict(data)
            validation_error = None
        except ValidationError as exc:
            verdict = LLMVerdict()
            validation_error = f"schema validation failed: {exc.errors()[:3]}"

    rounds: list[dict[str, Any]] = []
    for row in verdict.funding_rounds:
        amount = _amount_to_usd(row.amount_usd, row.amount_text)
        stage = row.stage.strip() if row.stage else None
        amount_text = row.amount_text.strip() if row.amount_text else None
        rounds.append(
            {
                "date": iso_date(row.date),
                "stage": stage or None,
                "amount_usd": amount,
                "amount_text": amount_text or None,
            }
        )

    dissolved = verdict.status == "dissolved"
    failure_definition: str | None = None
    failure_date: str | None = None
    if dissolved:
        failure_definition = verdict.failure_kind if verdict.failure_kind in _FAILURE_DEFINITIONS else "other"
        # Prefer a verified ceased date; otherwise fall back to the observation
        # horizon, matching how directory-status failures are dated in assemble.
        ceased = parse_date(verdict.ceased_date)
        if ceased is not None and ceased <= as_of:
            failure_date = ceased.isoformat()
        else:
            failure_date = as_of.isoformat()

    return OutcomeEnrichment(
        startup_id=startup_id,
        status=verdict.status,
        failure_observed=dissolved,
        failure_date=failure_date,
        failure_definition=failure_definition,
        status_reason=verdict.status_reason.strip(),
        funding_rounds=rounds,
        model=model,
        query=query,
        raw_excerpt=answer.strip()[:1500],
        error=validation_error,
    )


def _error_verdict(startup_id: str, model: str, query: str, error: str) -> OutcomeEnrichment:
    return OutcomeEnrichment(
        startup_id=startup_id,
        status="unknown",
        failure_observed=False,
        failure_date=None,
        failure_definition=None,
        status_reason="",
        model=model,
        query=query,
        error=error,
    )


# Default request budget (per-minute). The account's measured Gemini ceiling is
# 30 RPM / 200k TPM peak; we sit just under 30 RPM so the shared limiter never
# invites a 429 storm (a single web-search response is well under the token cap,
# so RPM is the binding constraint). Retry/backoff below absorbs any overshoot.
# Tune down for the OpenAI provider or a busier project via ``requests_per_minute``.
DEFAULT_REQUESTS_PER_MINUTE = 28.0
# Transient HTTP statuses worth retrying: 429 (rate limited) and 503 (overloaded).
_RETRYABLE_STATUS = frozenset({429, 503})
_MAX_RETRIES = 5
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


class _RateLimiter:
    """Thread-safe gate that spaces request *starts* to at most ``rpm`` per minute.

    All worker threads share one instance; each :meth:`acquire` atomically reserves
    the next ``60 / rpm``-second slot and sleeps until it arrives. This keeps the
    aggregate send rate under a provider's free-tier RPM even while the thread pool
    fans many queries out concurrently. ``rpm <= 0`` disables throttling entirely.
    """

    def __init__(self, rpm: float) -> None:
        self._min_interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            scheduled = max(time.monotonic(), self._next_allowed)
            self._next_allowed = scheduled + self._min_interval
        wait = scheduled - time.monotonic()
        if wait > 0:
            time.sleep(wait)


def _retry_after_seconds(response: httpx.Response | None, attempt: int) -> float:
    """Seconds to wait before the next attempt, honouring ``Retry-After`` if sent."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass  # non-numeric (HTTP-date) — fall back to exponential backoff
    # Exponential backoff (1s, 2s, 4s, ...) capped, plus jitter to de-sync workers.
    base = min(_BASE_BACKOFF_SECONDS * (2 ** attempt), _MAX_BACKOFF_SECONDS)
    return base + random.uniform(0.0, base * 0.25)


def _post_llm(
    client: httpx.Client,
    *,
    url: str,
    headers: Mapping[str, str],
    params: Mapping[str, str] | None,
    body: Mapping[str, Any],
    company_name: str,
    startup_id: str,
    model: str,
    query: str,
    limiter: _RateLimiter | None = None,
) -> tuple[Mapping[str, Any] | None, OutcomeEnrichment | None]:
    """POST a request, returning ``(payload, None)`` or ``(None, error_verdict)``.

    Rate-limit (429) and overloaded (503) responses, plus transient transport
    errors, are retried with exponential backoff (honouring ``Retry-After``) up to
    :data:`_MAX_RETRIES` times before giving up with an error verdict.
    """
    for attempt in range(_MAX_RETRIES + 1):
        if limiter is not None:
            limiter.acquire()
        try:
            response = client.post(url, headers=dict(headers), params=dict(params or {}), json=body)
            response.raise_for_status()
            return response.json(), None
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                delay = _retry_after_seconds(exc.response, attempt)
                print(
                    f"[outcomes_llm] {company_name}: HTTP {status}, backing off "
                    f"{delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES})",
                    flush=True,
                )
                time.sleep(delay)
                continue
            detail = exc.response.text[:300] if exc.response is not None else ""
            error = f"http {status if status else '?'}: {detail}"
            print(f"[outcomes_llm] Response for {company_name}: ERROR {error}", flush=True)
            return None, _error_verdict(startup_id, model, query, error)
        except httpx.TransportError as exc:
            # Timeouts / connection resets are transient — retry a few times.
            if attempt < _MAX_RETRIES:
                delay = _retry_after_seconds(None, attempt)
                print(
                    f"[outcomes_llm] {company_name}: {type(exc).__name__}, backing off "
                    f"{delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES})",
                    flush=True,
                )
                time.sleep(delay)
                continue
            print(f"[outcomes_llm] Response for {company_name}: ERROR {exc}", flush=True)
            return None, _error_verdict(startup_id, model, query, str(exc))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            print(f"[outcomes_llm] Response for {company_name}: ERROR {exc}", flush=True)
            return None, _error_verdict(startup_id, model, query, str(exc))
    # Unreachable: the loop always returns, but keeps type checkers satisfied.
    return None, _error_verdict(startup_id, model, query, "retries exhausted")


def _query_openai(
    client: httpx.Client,
    *,
    api_key: str,
    model: str,
    company_name: str,
    startup_id: str,
    as_of: date,
    limiter: _RateLimiter | None = None,
) -> OutcomeEnrichment:
    query = BASE_PROMPT.format(company=company_name)
    body = {
        "model": model,
        "tools": [{"type": "web_search"}],
        "input": query + _JSON_INSTRUCTION,
        # Force a schema-conformant JSON object. OpenAI's Responses API supports
        # json_schema structured output alongside the web_search tool.
        "text": {
            "format": {
                "type": "json_schema",
                "name": "company_outcome",
                "strict": True,
                "schema": _openai_json_schema(),
            }
        },
    }
    # Concurrent queries run on separate threads; emit each message as one string so
    # the "querying"/"response" lines for a company stay together in the output.
    print(f"[outcomes_llm] Querying OpenAI ({model}) for: {company_name}", flush=True)
    payload, error = _post_llm(
        client,
        url=OPENAI_RESPONSES_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        params=None,
        body=body,
        company_name=company_name,
        startup_id=startup_id,
        model=model,
        query=query,
        limiter=limiter,
    )
    if payload is None:
        return error  # type: ignore[return-value]
    answer = _collect_output_text(payload)
    print(f"[outcomes_llm] Response for {company_name}:\n{answer}\n", flush=True)
    return _parse_verdict(startup_id, model, query, answer, as_of=as_of)


def _query_gemini(
    client: httpx.Client,
    *,
    api_key: str,
    model: str,
    company_name: str,
    startup_id: str,
    as_of: date,
    limiter: _RateLimiter | None = None,
) -> OutcomeEnrichment:
    query = BASE_PROMPT.format(company=company_name)
    body = {
        "contents": [{"role": "user", "parts": [{"text": query + _JSON_INSTRUCTION}]}],
        # google_search grounds the answer in a live web search, mirroring the
        # OpenAI web_search tool. NOTE: the generateContent API rejects a forced
        # responseSchema/responseMimeType together with google_search, so the JSON
        # shape is forced through the prompt (_JSON_INSTRUCTION) and then validated
        # with Pydantic in _parse_verdict rather than by the API.
        "tools": [{"google_search": {}}],
    }
    print(f"[outcomes_llm] Querying Gemini ({model}) for: {company_name}", flush=True)
    payload, error = _post_llm(
        client,
        url=GEMINI_GENERATE_URL.format(model=model),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        params=None,
        body=body,
        company_name=company_name,
        startup_id=startup_id,
        model=model,
        query=query,
        limiter=limiter,
    )
    if payload is None:
        return error  # type: ignore[return-value]
    answer = _collect_gemini_text(payload)
    print(f"[outcomes_llm] Response for {company_name}:\n{answer}\n", flush=True)
    return _parse_verdict(startup_id, model, query, answer, as_of=as_of)


_PROVIDER_QUERIES = {
    PROVIDER_GEMINI: _query_gemini,
    PROVIDER_OPENAI: _query_openai,
}


def enrich_outcomes(
    companies: Iterable[tuple[str, str]],
    *,
    as_of: date,
    provider: str = DEFAULT_PROVIDER,
    api_key: str | None | object = _UNSET,
    model: str | None = None,
    max_workers: int = 8,
    requests_per_minute: float = DEFAULT_REQUESTS_PER_MINUTE,
    timeout_seconds: float = 90.0,
) -> dict[str, OutcomeEnrichment]:
    """Query the web-search LLM for each ``(startup_id, company_name)`` in parallel.

    ``provider`` selects the backend: ``"gemini"`` (default) or ``"openai"``.
    ``api_key`` defaults to being resolved from the environment / a local ``.env``
    for that provider; pass an explicit value (including ``""`` to force "no key")
    to override that. Returns a mapping keyed by ``startup_id``. Companies whose
    query errored are included with ``error`` set and neutral (non-failure) fields,
    so the caller can safely keep the deterministic baseline for them.

    ``max_workers`` caps how many queries are in flight at once; ``requests_per_minute``
    caps how fast new queries *start*, shared across every worker so the aggregate
    send rate stays under the provider's rate limit (pass ``0`` to disable). Rate-limit
    (429) responses are additionally retried with exponential backoff inside each query.
    """
    provider = (provider or DEFAULT_PROVIDER).strip().casefold()
    query_fn = _PROVIDER_QUERIES.get(provider)
    if query_fn is None:
        raise ValueError(f"unknown provider: {provider!r} (expected 'gemini' or 'openai')")
    key = resolve_api_key(api_key, provider=provider)
    if not key:
        return {}
    resolved_model = (
        model
        or os.environ.get(MODEL_ENV_VARS[provider])
        or DEFAULT_MODELS[provider]
    )
    targets = [(sid, name) for sid, name in companies if sid and name]
    if not targets:
        return {}

    results: dict[str, OutcomeEnrichment] = {}
    workers = max(1, min(max_workers, len(targets)))
    # One limiter shared across every worker throttles the aggregate start rate to
    # ``requests_per_minute`` regardless of how many threads are fanned out.
    limiter = _RateLimiter(requests_per_minute)
    # A web-search call is I/O-bound and long; a thread pool with a shared,
    # connection-pooled httpx.Client fans them out without an event loop.
    with httpx.Client(timeout=httpx.Timeout(timeout_seconds)) as client:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    query_fn,
                    client,
                    api_key=key,
                    model=resolved_model,
                    company_name=name,
                    startup_id=sid,
                    as_of=as_of,
                    limiter=limiter,
                ): sid
                for sid, name in targets
            }
            for future in as_completed(futures):
                sid = futures[future]
                try:
                    results[sid] = future.result()
                except Exception as exc:  # pragma: no cover - defensive
                    results[sid] = OutcomeEnrichment(
                        startup_id=sid,
                        status="unknown",
                        failure_observed=False,
                        failure_date=None,
                        failure_definition=None,
                        status_reason="",
                        model=resolved_model,
                        error=str(exc),
                    )
    return results
