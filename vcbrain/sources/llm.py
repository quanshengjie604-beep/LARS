"""Optional LLM-derived estimated fields via the Anthropic Messages API.

Token-gated (ANTHROPIC_API_KEY). Uses raw HTTP (no SDK dependency). Every value
produced here is `verification_status: estimated` with the rubric recorded as
evidence (plan §10.5 — no silent estimation). Returns None on any failure so the
deterministic pipeline degrades cleanly to null.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from .. import config
from ..http_client import HttpError, request

API = "https://api.anthropic.com/v1/messages"


@dataclass
class LLMEstimates:
    tam_usd: Optional[float] = None
    tam_rationale: Optional[str] = None
    defensibility_rubric: Optional[float] = None       # 0-1
    soft_skill_estimate: Optional[float] = None        # 0-1
    founder_market_fit: Optional[float] = None         # 0-1
    pedigree: Optional[float] = None                   # 0-1
    evidence: list = field(default_factory=list)
    available: bool = False


_PROMPT = """You are a venture-analyst estimation tool. Given a startup's public one-liner and \
description, output ONLY strict JSON with these keys (no prose):
{{
  "tam_usd": <number|null>,               // total addressable market in USD, order-of-magnitude estimate
  "tam_rationale": <string>,              // one sentence stating the assumption behind tam_usd
  "defensibility_rubric": <0..1|null>,    // how proprietary/defensible the tech sounds
  "soft_skill_estimate": <0..1|null>,     // communication/clarity signal from the writing (low confidence)
  "founder_market_fit": <0..1|null>,      // fit between the described product and a domain-expert team
  "pedigree": <0..1|null>                 // signal of elite background if stated, else null
}}
Estimate conservatively. Use null when there is genuinely no basis. Do not invent specific facts.

Company: {name}
One-liner: {one_liner}
Sector: {sector}
Description: {description}"""


def enrich(company) -> LLMEstimates:
    est = LLMEstimates()
    if not config.ANTHROPIC_API_KEY:
        return est
    prompt = _PROMPT.format(
        name=company.name,
        one_liner=company.one_liner or "",
        sector=company.sector or "",
        description=(company.description or "")[:1500],
    )
    body = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": 400,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        status, text = request(API, method="POST", json_body=body, headers=headers, use_cache=True)
    except HttpError:
        return est
    if not (200 <= status < 300):
        return est
    try:
        payload = json.loads(text)
        content = payload["content"][0]["text"]
        parsed = json.loads(content[content.index("{"): content.rindex("}") + 1])
    except (json.JSONDecodeError, KeyError, IndexError, ValueError):
        return est

    def _num(x, lo=0.0, hi=1.0):
        try:
            return max(lo, min(hi, float(x)))
        except (TypeError, ValueError):
            return None

    est.available = True
    tam = parsed.get("tam_usd")
    est.tam_usd = float(tam) if isinstance(tam, (int, float)) and tam > 0 else None
    est.tam_rationale = parsed.get("tam_rationale")
    est.defensibility_rubric = _num(parsed.get("defensibility_rubric"))
    est.soft_skill_estimate = _num(parsed.get("soft_skill_estimate"))
    est.founder_market_fit = _num(parsed.get("founder_market_fit"))
    est.pedigree = _num(parsed.get("pedigree"))
    if est.tam_usd:
        est.evidence.append((
            f"LLM TAM estimate ${est.tam_usd:,.0f}: {est.tam_rationale} "
            f"(model={config.ANTHROPIC_MODEL}, estimated).",
            None, None,
        ))
    return est
