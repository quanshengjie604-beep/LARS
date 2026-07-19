"""Judge a pitch-deck's Markdown with ChatGPT via the OpenAI API.

The judging *rubric* lives in ``judging_instructions.md`` and is read verbatim
from disk on every call -- this module never edits or paraphrases it, so the
instructions stay the single source of truth you can tune without touching code.
The deck Markdown is passed as the model input; the instructions become the
developer/system prompt.

Because the rubric asks the model to "search the web" to check whether the
Problem is real and whether the Solution is novel, we call the OpenAI
**Responses API** with the built-in ``web_search`` tool enabled, so the model
can actually browse rather than guess.

    from judge import judge_markdown

    result = judge_markdown(deck_markdown)   # -> JudgeResult(text=..., ...)
    print(result.text)

Requires the ``OPENAI_API_KEY`` environment variable. The model can be overridden
with ``OPENAI_MODEL`` (default: ``gpt-5``).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from openai import OpenAI


# Directory of this file, so the instructions are found regardless of cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
INSTRUCTIONS_PATH = os.path.join(_HERE, "judging_instructions.md")

# repo root = three levels up; the git-ignored key file lives in secrets/.
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
API_KEY_PATH = os.path.join(_REPO_ROOT, "secrets", "openai_api_key.txt")

# Model is configurable but defaults to a current, web-search-capable model.
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")


def load_api_key() -> str | None:
    """Return the OpenAI API key, or ``None`` if none is configured.

    Order of precedence: the ``OPENAI_API_KEY`` environment variable first, then
    ``secrets/openai_api_key.txt`` (git-ignored). Returning ``None`` lets the
    OpenAI client fall back to its own default resolution and raise its own clear
    error if nothing is set.
    """
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        return env_key.strip()
    if os.path.isfile(API_KEY_PATH):
        with open(API_KEY_PATH, "r", encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    return None


@dataclass
class JudgeResult:
    """The outcome of one judging call."""
    text: str                                  # the model's Markdown verdict
    model: str                                 # model actually used
    instructions_path: str                     # rubric file that was applied
    citations: list[str] = field(default_factory=list)  # URLs the model cited
    problem_score: int | None = None           # 1-10 problem relevance, if parsed
    novelty_score: int | None = None           # 1-10 solution novelty, if parsed


def load_instructions(path: str = INSTRUCTIONS_PATH) -> str:
    """Read the judging instructions verbatim. Never modified in code."""
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def judge_markdown(
    deck_markdown: str,
    *,
    model: str | None = None,
    instructions_path: str = INSTRUCTIONS_PATH,
    client: OpenAI | None = None,
) -> JudgeResult:
    """Send the deck Markdown to ChatGPT and return its judgment.

    Parameters
    ----------
    deck_markdown:
        The pitch deck already converted to Markdown (see ``pdf_to_markdown``).
    model:
        Override the model. Defaults to ``OPENAI_MODEL`` env / ``gpt-5``.
    instructions_path:
        Path to the (unaltered) judging rubric. Read fresh on every call.
    client:
        An existing ``OpenAI`` client (handy for tests). Created if omitted.
    """
    if not deck_markdown.strip():
        raise ValueError("deck_markdown is empty; nothing to judge")

    model = model or DEFAULT_MODEL
    instructions = load_instructions(instructions_path)
    # Key from env or secrets/openai_api_key.txt; None lets OpenAI() resolve/raise.
    client = client or OpenAI(api_key=load_api_key())

    user_input = (
        "Here is the pitch deck, converted from PDF to Markdown. Judge it "
        "according to your instructions.\n\n"
        "```markdown\n"
        f"{deck_markdown}\n"
        "```"
    )

    response = client.responses.create(
        model=model,
        instructions=instructions,   # the rubric, passed through untouched
        input=user_input,
        tools=[{"type": "web_search"}],
    )

    text = response.output_text
    problem_score, novelty_score = _extract_scores(text)
    return JudgeResult(
        text=text,
        model=model,
        instructions_path=instructions_path,
        citations=_extract_citations(response),
        problem_score=problem_score,
        novelty_score=novelty_score,
    )


def _extract_scores(text: str) -> tuple[int | None, int | None]:
    """Read the two ``X/10`` scores back out of the model's Markdown.

    Matches the fixed lines the rubric asks for (bold markers optional, spacing
    lenient). Returns ``(problem_score, novelty_score)``; either may be ``None``
    if the line was missing or out of the 1-10 range.
    """
    def _find(label: str) -> int | None:
        m = re.search(
            rf"{label}\s*score:?\**\s*(\d{{1,2}})\s*/\s*10",
            text,
            flags=re.IGNORECASE,
        )
        if not m:
            return None
        value = int(m.group(1))
        return value if 1 <= value <= 10 else None

    return _find("problem relevance"), _find("solution novelty")


def _extract_citations(response: object) -> list[str]:
    """Pull cited URLs out of the Responses payload (best-effort, deduped)."""
    urls: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            for ann in getattr(content, "annotations", []) or []:
                url = getattr(ann, "url", None)
                if url and url not in urls:
                    urls.append(url)
    return urls
