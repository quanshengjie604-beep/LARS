# 02_Evaluation — Pitch-deck judging

Convert a startup pitch-deck PDF to Markdown, then have ChatGPT judge it against
a fixed rubric that checks **whether the Problem is real** (via web search) and
**whether the Solution is novel** (via web search).

```
deck.pdf ──▶ pdf_to_markdown.py ──▶ Markdown ──▶ judge.py ──▶ ChatGPT verdict
                                                    ▲
                                       judging_instructions.md (unaltered rubric)
```

## Files

| File | Role |
| --- | --- |
| `judging_instructions.md` | The rubric sent to ChatGPT as the system prompt. **Read verbatim, never edited by code** — tune the judging here. |
| `pdf_to_markdown.py` | PDF → Markdown (one `## Slide N` per page). Works from a path or from raw upload bytes. |
| `judge.py` | Sends the Markdown + rubric to the OpenAI **Responses API** with the built-in `web_search` tool enabled. |
| `evaluate_deck.py` | End-to-end CLI: PDF in, judgment out. |

## Setup

```bash
pip install -r requirements.txt      # installs openai + pymupdf4llm
export OPENAI_API_KEY=sk-...          # PowerShell: $env:OPENAI_API_KEY = "sk-..."
```

Model defaults to `gpt-5`; override with `OPENAI_MODEL` or `--model`.

## Run

```bash
python Scripts/02_Evaluation/evaluate_deck.py path/to/deck.pdf
```

Writes the converted deck Markdown and the judgment to `Output/`. Use
`--no-write` to print the verdict to stdout, or `--out` / `--markdown-out` to
choose paths.

Convert only (no API call):

```bash
python Scripts/02_Evaluation/pdf_to_markdown.py deck.pdf -o deck.md
```

## From Python / the web layer

```python
from evaluate_deck import evaluate_pdf, evaluate_pdf_bytes

ev = evaluate_pdf("deck.pdf")            # from a path
ev = evaluate_pdf_bytes(upload_bytes)    # from an in-memory upload
print(ev.result.text)                    # the Markdown verdict
print(ev.result.citations)               # URLs the model cited
```

> Note: the package dir starts with a digit, so (like `03_Monte_Carlo`) the
> modules import each other by plain name and rely on the script's own directory
> being on `sys.path` — run the scripts directly, from the repo root.
