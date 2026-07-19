"""End-to-end pitch-deck evaluation: PDF in, judgment out.

Ties the two halves of the evaluation layer together:

    PDF  --pdf_to_markdown-->  Markdown  --judge-->  ChatGPT verdict

The judging rubric is read verbatim from ``judging_instructions.md`` and is never
altered by this pipeline. Web search is performed by the model itself (see
``judge.py``) so the Problem-is-real and Solution-is-novel checks rest on live
evidence.

Command line:

    # needs OPENAI_API_KEY in the environment
    python Scripts/02_Evaluation/evaluate_deck.py path/to/deck.pdf

    # keep the intermediate Markdown and choose where the verdict lands
    python Scripts/02_Evaluation/evaluate_deck.py deck.pdf \
        --markdown-out Output/deck.md --out Output/deck_judgment.md

By default the converted Markdown and the judgment are written next to each other
in the repo's ``Output/`` directory.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from pdf_to_markdown import pdf_to_markdown, pdf_bytes_to_markdown
from judge import judge_markdown, JudgeResult


# repo root = three levels up from Scripts/02_Evaluation/evaluate_deck.py
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_OUTPUT_DIR = os.path.join(_REPO_ROOT, "Output")


@dataclass
class Evaluation:
    """Everything one deck evaluation produced."""
    markdown: str            # the deck converted to Markdown
    result: JudgeResult      # the model's judgment + citations
    markdown_path: str | None = None   # where the Markdown was written, if any
    judgment_path: str | None = None   # where the judgment was written, if any


def evaluate_pdf(
    pdf_path: str,
    *,
    model: str | None = None,
) -> Evaluation:
    """Convert a PDF and judge it. Returns the Markdown and the verdict."""
    markdown = pdf_to_markdown(pdf_path)
    result = judge_markdown(markdown, model=model)
    return Evaluation(markdown=markdown, result=result)


def evaluate_pdf_bytes(
    data: bytes,
    *,
    model: str | None = None,
) -> Evaluation:
    """Same as :func:`evaluate_pdf` but for in-memory upload bytes.

    This is the hook the web intake layer (``WebApp/intake.py``) can call once a
    deck is uploaded.
    """
    markdown = pdf_bytes_to_markdown(data)
    result = judge_markdown(markdown, model=model)
    return Evaluation(markdown=markdown, result=result)


def _render_judgment_file(evaluation: Evaluation, pdf_path: str) -> str:
    """Assemble the judgment Markdown file (verdict + citation appendix)."""
    result = evaluation.result
    p = "—" if result.problem_score is None else f"{result.problem_score}/10"
    n = "—" if result.novelty_score is None else f"{result.novelty_score}/10"
    parts = [
        f"# Pitch deck evaluation — {os.path.basename(pdf_path)}",
        f"_Model: {result.model} · rubric: {os.path.basename(result.instructions_path)}_",
        "",
        f"**Problem relevance: {p} · Solution novelty: {n}**",
        "",
        result.text.strip(),
    ]
    if result.citations:
        parts += ["", "## Sources cited", ""]
        parts += [f"- {url}" for url in result.citations]
    return "\n".join(parts).strip() + "\n"


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a pitch-deck PDF to Markdown and judge it with ChatGPT.",
    )
    parser.add_argument("pdf", help="path to the pitch-deck PDF")
    parser.add_argument("--model", help="override the OpenAI model (default: gpt-5)")
    parser.add_argument("--out", help="path for the judgment Markdown file")
    parser.add_argument("--markdown-out", help="path for the converted deck Markdown")
    parser.add_argument(
        "--no-write", action="store_true",
        help="print the judgment to stdout instead of writing files",
    )
    args = parser.parse_args()

    evaluation = evaluate_pdf(args.pdf, model=args.model)
    judgment_md = _render_judgment_file(evaluation, args.pdf)

    if args.no_write:
        print(judgment_md)
        return

    os.makedirs(_DEFAULT_OUTPUT_DIR, exist_ok=True)
    stem = os.path.splitext(os.path.basename(args.pdf))[0]
    md_path = args.markdown_out or os.path.join(_DEFAULT_OUTPUT_DIR, f"{stem}.md")
    out_path = args.out or os.path.join(_DEFAULT_OUTPUT_DIR, f"{stem}_judgment.md")

    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(evaluation.markdown)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(judgment_md)

    evaluation.markdown_path = md_path
    evaluation.judgment_path = out_path

    res = evaluation.result
    p = "n/a" if res.problem_score is None else f"{res.problem_score}/10"
    n = "n/a" if res.novelty_score is None else f"{res.novelty_score}/10"
    print(f"[evaluate_deck] deck Markdown -> {md_path}")
    print(f"[evaluate_deck] judgment      -> {out_path}")
    print(f"[evaluate_deck] scores: problem relevance {p}, solution novelty {n}")
    if res.citations:
        print(f"[evaluate_deck] {len(res.citations)} source(s) cited")


if __name__ == "__main__":
    _main()
