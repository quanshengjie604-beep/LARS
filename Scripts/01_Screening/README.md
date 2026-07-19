# Founder Screening Crawler

One-shot CLI for collecting up to 2,000 people who are explicitly identified as a company Founder or Co-founder on a public company or accelerator page. It creates sourcing data only; it does not create `inference_requests.jsonl` and does not calculate Founder Score, screening axes, soft skills, network centrality, or any other model-derived score.

## Outputs

The output directory contains:

- `sourcing_candidates.jsonl`: one deduplicated person per line. Every record contains `company_name`, `company_url`, `founder_role`, `founder_relationship_evidence_url`, and a `founded_companies` list.
- `evidence_registry.jsonl`: one evidence item per line, joined by `candidate_id` and `source_id`.
- `crawl_runs.jsonl`: run metadata, source counts, and non-fatal source errors.

All founder feature keys named in `Data_requirements.md` are present. Values that cannot be established as public facts remain `null` and their full paths are included in `missing_fields`. The crawler never converts missing information to zero and never calculates speculative scores.

## Setup

```powershell
python -m pip install -r Scripts/01_Screening/requirements.txt
# GITHUB_TOKEN is optional and is not needed for the verified-founder run.
```

GitHub, arXiv, research-lab, and hackathon collectors remain available for future evidence enrichment, but they are disabled as primary sources because those pages alone do not prove that a person founded a company.

The configured user agent identifies this repository. arXiv requests attribution for use of its public interoperability API.

## Run

Validate the plan without network access:

```powershell
python Scripts/01_Screening/crawl_founders.py --dry-run
```

Run all configured sources with the global cap:

```powershell
python Scripts/01_Screening/crawl_founders.py `
  --max-candidates 2000 `
  --output-dir screening_handover/founder_sourcing
```

Run selected sources:

```powershell
python Scripts/01_Screening/crawl_founders.py --sources yc --max-candidates 2000
```

## Collection policy

- Uses public YC company directory and company-detail pages as the primary strong-evidence source.
- Rejects every person without an explicit Founder/Co-founder role, company name, company link, and relationship-evidence link.
- Prioritizes companies launched in the last 24 months in AI/ML, systems, robotics, developer tools, healthcare, and bio/AI; older companies in the same domains are allowed only to backfill the 2,000-person target.
- Retains inactive, acquired, and public companies as requested.
- Uses the official arXiv Atom API and GitHub REST API only when those optional collectors are explicitly enabled.
- Checks `robots.txt` before reading laboratory or hackathon pages.
- Skips a web source when robots policy is unavailable or disallows access.
- Does not log in, bypass anti-bot controls, scrape LinkedIn, or collect private contact details.
- Uses exact strong identifiers for cross-source merging. Exact names are merged only within the same source; uncertain cross-source identity matches remain separate.
- Records source failures in the crawl-run report rather than fabricating data.

HTML sources change over time. Their selectors are configuration, not hard-coded assumptions; update `person_selector`, `project_selector`, and `member_selector` when a permitted public page changes structure.


## Evidence-backed enrichment

The enrichment step keeps the original crawl unchanged and writes a second, auditable dataset. It extracts only claims explicitly present in the collected public founder biography, existing GitHub/paper evidence, and verified company status.

1. Download the official QS World University Rankings 2027 workbook from the [QS report page](https://www.qs.com/insights/qs-world-university-rankings-2027-results-table-excel) and save it as `screening_handover/reference/qs_world_university_rankings_2027.xlsx`.
2. Validate the local plan:

```powershell
python Scripts/01_Screening/enrich_founders.py --dry-run
```

3. Generate the enriched dataset:

```powershell
python Scripts/01_Screening/enrich_founders.py
```

The default output is `screening_handover/founder_enriched` and contains the original candidate/evidence records plus:

- `education.degrees`: explicit bachelor, master, and PhD claims; institution name; completion status when stated; official QS 2027 published rank; biography and QS source URLs.
- `education.has_bachelor`, `has_master`, `has_phd`: `true` only when that level is explicitly documented; otherwise `null`, never guessed `false`.
- `career_history.verified_prior_exit_count`: verified minimum across acquired, public, closed, or YC-inactive founded companies. No observed evidence remains `null`, not zero.
- `skills.items` and `documented_skill_count`: unique explicit public-bio terms, GitHub repository languages, and matched paper fields. Titles and company sectors do not generate inferred skills.
- `enrichment_report.json`: record counts, source version, and coverage.

QS publishes a single numeric rank through approximately the top 700 and official rank intervals below that point. `qs_world_rank` preserves the official cell exactly (integer or interval string); it never fabricates a single rank from an interval.

To regenerate Chroma-ready records from the enriched source:

```powershell
python Scripts/01_Screening/export_chroma.py `
  --input-dir screening_handover/founder_enriched `
  --output-dir screening_handover/chroma_enriched
```