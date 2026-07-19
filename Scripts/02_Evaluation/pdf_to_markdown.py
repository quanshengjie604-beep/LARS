"""Convert an uploaded pitch-deck PDF into LLM-ready Markdown.

The judging layer only ever sees text, so the first step of evaluation is to
turn a (possibly image-and-layout-heavy) PDF into a clean Markdown string with
one clear slide separator per page. We lean on ``pymupdf4llm`` for the heavy
lifting -- it walks each page and emits Markdown headings, lists and tables --
and add a thin wrapper that gives predictable per-slide separators and a couple
of convenience entry points (bytes in, string out; or path in, file out).

    from pdf_to_markdown import pdf_to_markdown, pdf_bytes_to_markdown

    md = pdf_to_markdown("deck.pdf")             # from a path
    md = pdf_bytes_to_markdown(uploaded_bytes)   # from raw upload bytes

Run directly to convert a file from the command line:

    python Scripts/02_Evaluation/pdf_to_markdown.py deck.pdf -o deck.md
"""

from __future__ import annotations

import argparse
import os
import re

import pymupdf  # bundled with pymupdf4llm
import pymupdf4llm


# Written between slides so the LLM (and a human reader) can tell pages apart.
SLIDE_SEPARATOR = "\n\n---\n\n"

# A raw line counts as "dropped" if fewer than this fraction of its words already
# appear in pymupdf4llm's Markdown for the same page (see _reconcile).
_COVERAGE_THRESHOLD = 0.6


def pdf_bytes_to_markdown(data: bytes) -> str:
    """Convert raw PDF bytes to Markdown. Use this for in-memory uploads.

    Opens the bytes as a PyMuPDF document (no temp file needed) and defers to
    :func:`_doc_to_markdown`.
    """
    if not data:
        raise ValueError("empty PDF payload")
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return _doc_to_markdown(doc)
    finally:
        doc.close()


def pdf_to_markdown(pdf_path: str) -> str:
    """Convert a PDF file on disk to Markdown and return the string."""
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(pdf_path)
    doc = pymupdf.open(pdf_path)
    try:
        return _doc_to_markdown(doc)
    finally:
        doc.close()


def _doc_to_markdown(doc: "pymupdf.Document") -> str:
    """Render every page to Markdown, joined by :data:`SLIDE_SEPARATOR`.

    ``page_chunks=True`` gives us one dict per page so we can control the
    between-slide separator ourselves instead of relying on pymupdf4llm's
    default form feeds. Each page's Markdown is reconciled against the raw text
    layer (:func:`_reconcile`) so slide-style pages don't silently lose lines --
    that would quietly bias the judgment.
    """
    chunks = pymupdf4llm.to_markdown(doc, page_chunks=True)

    slides: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        md_text = (chunk.get("text") or "").strip()
        raw_text = doc[i - 1].get_text().strip()
        text = _reconcile(md_text, raw_text)
        # Keep a stable slide marker even when a page is image-only / empty,
        # so downstream code and the LLM can still count and reference slides.
        header = f"## Slide {i}"
        slides.append(f"{header}\n\n{text}" if text else f"{header}\n\n_(no extractable text on this slide)_")

    return SLIDE_SEPARATOR.join(slides).strip() + "\n"


def _tokens(text: str) -> list[str]:
    """Lower-cased word tokens, for comparing text coverage."""
    return re.findall(r"\w+", text.lower())


def _reconcile(md_text: str, raw_text: str) -> str:
    """Guard against pymupdf4llm dropping content on a page.

    pymupdf4llm's layout heuristics occasionally discard text lines on
    slide-style pages. We compare its Markdown against the raw text layer and
    append any raw line whose words are mostly missing from the Markdown, so no
    slide content silently disappears before it reaches the judge.
    """
    if not md_text:
        return raw_text
    if not raw_text:
        return md_text

    have = set(_tokens(md_text))
    recovered: list[str] = []
    for line in raw_text.splitlines():
        line = line.strip()
        line_tokens = _tokens(line)
        if not line_tokens:
            continue
        present = sum(1 for t in line_tokens if t in have) / len(line_tokens)
        if present < _COVERAGE_THRESHOLD:
            recovered.append(line)

    if not recovered:
        return md_text
    return md_text + "\n\n" + "\n".join(recovered)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Convert a PDF to Markdown.")
    parser.add_argument("pdf", help="path to the PDF to convert")
    parser.add_argument(
        "-o", "--out",
        help="write Markdown here (default: alongside the PDF, .md extension)",
    )
    args = parser.parse_args()

    markdown = pdf_to_markdown(args.pdf)
    out_path = args.out or (os.path.splitext(args.pdf)[0] + ".md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    print(f"[pdf_to_markdown] wrote {len(markdown):,} chars to {out_path}")


if __name__ == "__main__":
    _main()
