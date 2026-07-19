"""VC Brain — Screening data acquisition & scoring pipeline.

Implements `plan.md` over a *list* of startups (parallelised), seeded from
accelerator directories (YC first-class) and enriched from GitHub, Hacker News,
and Product Hunt. Emits the `screening_handover/` JSONL contract defined in
`ngboost_data_requirements.md`.

Stdlib-only: no third-party packages are required to run the pipeline.
Optional environment variables upgrade coverage:
  GITHUB_TOKEN        -> higher GitHub rate limits + richer signals
  PRODUCTHUNT_TOKEN   -> enables Product Hunt enrichment
  ANTHROPIC_API_KEY   -> enables LLM-derived estimated fields (tam, rubrics)
"""

__version__ = "0.1.0"
