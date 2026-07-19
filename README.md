# vcbrain — Screening data pipeline (list-scale)

Implements [`public-data-sourcing.md`](docs/plans/public-data-sourcing.md) over a **list of startups**, in parallel, and
emits the `screening_handover/` JSONL contract from
[`ngboost-data-requirements.md`](docs/requirements/ngboost-data-requirements.md).

Instead of collecting one company at a time, it **seeds the whole cohort from an
accelerator directory** (Y Combinator, first-class), **caps every company to
≤ 10 years old**, then fans out per-company enrichment (GitHub, Hacker News,
Product Hunt) across a thread pool and computes every plan §5 score.

## Why these sources

- **YC directory** (primary) — via the `yc-oss/api` static mirror (no auth). Rich
  per-company records: `status` (→ outcome labels), `batch` (→ 10-year age
  filter + point-in-time anchor), `team_size`, `industry`, `regions`.
- **GitHub REST** — open-source footprint & activity (§5.6).
- **Hacker News** (Algolia Search) — public footprint, date-filterable.
- **Product Hunt** (GraphQL, optional) — launch footprint.

## Run it

No third-party packages required (stdlib only). From the repo root:

```bash
# quick demo: 25 recent companies, GitHub off (avoids unauth rate limits)
python -m vcbrain --limit 25 --no-github --no-llm

# richer: enrich with GitHub (set a token to lift rate limits)
export GITHUB_TOKEN=ghp_...        # optional but recommended
python -m vcbrain --limit 100 --mode both

# target a slice
python -m vcbrain --batches "Summer 2024,Winter 2024" --sectors fintech --geographies germany
python -m vcbrain --top-only --mode inference
```

Output lands in `screening_handover/`:
`training_features.jsonl`, `training_outcomes.jsonl`, `inference_requests.jsonl`,
`evidence_registry.jsonl`, `data_dictionary.md`.

### Optional environment variables

| Var | Effect |
|---|---|
| `GITHUB_TOKEN` | Higher GitHub rate limits + org followers |
| `PRODUCTHUNT_TOKEN` | Enables Product Hunt enrichment |
| `ANTHROPIC_API_KEY` | Enables LLM-estimated fields (`tam_usd`, soft rubrics) |
| `VCBRAIN_TODAY` | Override the observation anchor (default `2026-07-18`) |
| `VCBRAIN_MAX_AGE_YEARS` | Override the 10-year age cap |

Everything degrades gracefully: no token → that source is skipped and its fields
stay `null` (never fabricated). Successful GETs are cached under `.vcbrain_cache/`
so reruns are free.

## Key CLI flags

`--mode {inference,training,both}` · `--limit N` (0 = all) · `--batches` ·
`--sectors` · `--geographies` · `--status` · `--top-only` · `--workers N` ·
`--no-github` · `--no-llm` · `--age-years N` · `--output-dir DIR`

## Architecture

```
sources/{yc,github,hackernews,producthunt,llm}.py   # one adapter per source
  yc.py       -> seed + 10-year age filter (primary directory)
context.py    -> corpus-level §5.6/§6 signals (competitor density, climate, centrality)
scoring.py    -> deterministic plan §5 formulas (null-drop + weight renorm)
evidence.py   -> evidence_registry + verification_status rules
assemble.py   -> leakage-aware feature snapshots + outcome labels
pipeline.py   -> parallel fan-out (ThreadPoolExecutor) + JSONL writers
cli.py        -> `python -m vcbrain`
```

## Point-in-time / leakage control

- **Inference** snapshots use live signals as of "today".
- **Training** snapshots anchor at the YC batch date and filter every signal to
  `≤ cutoff`; live-only figures (current team size, star counts) are nulled.
- **Outcomes** come from the post-cutoff YC `status` and preserve failures and
  censored still-alive companies (§10.2), not just winners.

See the generated `screening_handover/data_dictionary.md` for full field-level
rules and honest limitations.
