"""
enrich.py
=========

Given only a person's name (plus any optional hints the user already typed),
use the OpenAI Responses API with the built-in ``web_search`` tool to research
the founder / company on the public web and return a **schema-clean NGBoost
inference request** ready to feed the decision engine.

Design notes
------------
* The heavy lifting of *shape* correctness lives in ``schema.py``. The model is
  asked to return "a JSON object with the six feature groups", and whatever it
  returns is run through ``normalize_inference_request`` so the result is always
  valid regardless of how well the model behaved.
* We deliberately parse free-form JSON out of the model's text (tolerating code
  fences) instead of forcing structured-output, because structured-output and
  web-search tool calls don't always compose across model/tool versions. The
  normaliser makes this safe.
* The API key is read from ``OPENAI_API_KEY`` if set, otherwise from
  ``secrets/openai_api_key.txt`` (which is git-ignored).

Nothing here is FastAPI-specific; ``server.py`` just calls
``enrich_profile_from_name`` and serialises the result.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

from schema import llm_field_guide, normalize_inference_request

_REPO_ROOT = Path(__file__).resolve().parent.parent
_KEY_FILE = _REPO_ROOT / "secrets" / "openai_api_key.txt"

# Model + web-search tool are configurable via env so this survives model
# renames without a code change. The tool type is tried in order; different
# model generations expose web search under slightly different names.
_MODEL = os.environ.get("LARS_ENRICH_MODEL", "gpt-4o")
_TOOL_TYPES = tuple(
    t for t in os.environ.get("LARS_ENRICH_TOOL", "web_search,web_search_preview").split(",") if t
)


class EnrichmentError(RuntimeError):
    """Raised when the enrichment cannot be produced (config or API failure)."""


# --------------------------------------------------------------------------
# API key + client
# --------------------------------------------------------------------------
def _load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    if _KEY_FILE.exists():
        key = _KEY_FILE.read_text(encoding="utf-8").strip()
        if key:
            return key
    raise EnrichmentError(
        "No OpenAI API key found. Set OPENAI_API_KEY or add it to "
        f"{_KEY_FILE.relative_to(_REPO_ROOT)}."
    )


def _client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise EnrichmentError(
            "The 'openai' package is not installed. Run: pip install openai"
        ) from exc
    return OpenAI(api_key=_load_api_key())


# --------------------------------------------------------------------------
# Prompt construction
# --------------------------------------------------------------------------
def _build_instructions() -> str:
    guide = llm_field_guide()
    return (
        "You are a diligent venture-capital research analyst. Given a person's "
        "name (a startup founder), search the public web to find factual, "
        "verifiable information about them and their company, then fill out a "
        "structured screening snapshot.\n\n"
        "STRICT RULES:\n"
        "- Only use information you can actually find. If a value cannot be "
        "found, set it to null. NEVER guess or invent numbers.\n"
        "- Do not equate missing with zero. arr_usd=0 means a verified "
        "pre-revenue company; arr_usd=null means unknown.\n"
        "- Money in USD. Rates as fractions (0.65 = 65%). Dates as YYYY-MM-DD. "
        "Scores within their stated 0-1 or 0-100 range.\n"
        "- The three screening axes (founder_axis, market_axis, "
        "idea_vs_market) are YOUR analytic judgement based on evidence; only "
        "provide them if you have enough signal, else null.\n\n"
        "Return ONLY a single JSON object (no prose, no markdown fences) with "
        "exactly these top-level keys, each an object of the listed fields:\n"
        f"{json.dumps(guide['groups'], indent=2)}\n\n"
        "Allowed enum values:\n"
        f"{json.dumps(guide['enums'], indent=2)}\n\n"
        "Also include one extra top-level key \"sources\": an array of "
        "{\"url\": string, \"title\": string} for every web page you actually "
        "used as evidence. If you found nothing usable, use an empty array."
    )


def _build_query(name: str, hints: dict[str, str] | None) -> str:
    lines = [f"Founder name: {name}"]
    for label, value in (hints or {}).items():
        if value and value.strip():
            lines.append(f"{label}: {value.strip()}")
    lines.append(
        "\nResearch this founder and their current company on the public web "
        "and return the structured JSON snapshot as instructed."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------
def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first complete JSON object out of the model's text output.

    Tolerates surrounding prose and ```` ```json ```` fences by scanning from
    the first ``{`` and counting braces (while respecting string literals) to
    find its matching close.
    """
    if not text:
        raise EnrichmentError("The model returned an empty response.")

    start = text.find("{")
    if start == -1:
        raise EnrichmentError("Could not find a JSON object in the model response.")

    depth = 0
    in_string = False
    escaped = False
    end = -1
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break

    candidate = text[start : end + 1] if end != -1 else text[start:]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise EnrichmentError(f"Model response was not valid JSON: {exc}") from exc


def _extract_citations(response: Any) -> list[dict[str, str]]:
    """Best-effort pull of url citations from a Responses API result."""
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            for ann in getattr(content, "annotations", None) or []:
                url = getattr(ann, "url", None)
                if url and url not in seen:
                    seen.add(url)
                    citations.append({"url": url, "title": getattr(ann, "title", "") or ""})
    return citations


def _call_openai(instructions: str, query: str) -> tuple[str, Any]:
    """Call the Responses API, trying each configured web-search tool name."""
    client = _client()
    last_error: Exception | None = None
    for tool_type in _TOOL_TYPES:
        try:
            response = client.responses.create(
                model=_MODEL,
                instructions=instructions,
                input=query,
                tools=[{"type": tool_type}],
                tool_choice="auto",
            )
            return response.output_text, response
        except Exception as exc:  # noqa: BLE001 - try the next tool spelling
            last_error = exc
            msg = str(exc).lower()
            # Only fall through to the next tool name for tool-shape errors.
            if "tool" not in msg and "web_search" not in msg:
                break
    raise EnrichmentError(f"OpenAI request failed: {last_error}")


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def enrich_profile_from_name(
    name: str,
    hints: dict[str, str] | None = None,
    *,
    observation_date: str | None = None,
) -> dict[str, Any]:
    """Research ``name`` on the web and return an NGBoost inference request.

    Parameters
    ----------
    name:
        The founder's full name (required, non-empty).
    hints:
        Optional extra context already known (e.g. company, linkedin) to focus
        the search. Keys are used verbatim as labels in the query.
    observation_date:
        Override the snapshot date (defaults to today).

    Returns
    -------
    dict
        ``{"inference_request": {...}, "citations": [...], "model": str,
        "raw_text": str}`` — ``inference_request`` is schema-clean and ready to
        write to ``inference_requests.jsonl`` / feed NGBoost.
    """
    name = (name or "").strip()
    if not name:
        raise EnrichmentError("A name is required to run the web search.")

    obs_date = observation_date or date.today().isoformat()
    instructions = _build_instructions()
    query = _build_query(name, hints)

    raw_text, response = _call_openai(instructions, query)
    raw_json = _extract_json(raw_text)

    # Pull the model-declared sources out before normalising (they are not part
    # of the feature schema). Merge them with any inline url_citation
    # annotations the API attached, de-duplicating by URL.
    model_sources = raw_json.pop("sources", None) or []
    inference_request = normalize_inference_request(
        raw_json, name=name, observation_date=obs_date
    )

    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for src in list(model_sources) + _extract_citations(response):
        if not isinstance(src, dict):
            continue
        url = str(src.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            citations.append({"url": url, "title": str(src.get("title") or "").strip()})

    # Record where the evidence came from, as lightweight source ids, so the
    # request is self-describing about its provenance.
    if citations and not inference_request["source_ids"]:
        inference_request["source_ids"] = [
            f"web_{i+1:02d}" for i in range(len(citations))
        ]

    return {
        "inference_request": inference_request,
        "citations": citations,
        "model": _MODEL,
        "raw_text": raw_text,
    }


if __name__ == "__main__":
    import sys

    who = " ".join(sys.argv[1:]) or "Patrick Collison"
    out = enrich_profile_from_name(who)
    print(json.dumps(out["inference_request"], indent=2))
    print(f"\n{len(out['citations'])} citations, model={out['model']}")
    for c in out["citations"]:
        print(f"  - {c['url']}")
