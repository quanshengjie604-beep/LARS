# Founder Screening Crawler

One-shot CLI for collecting up to 2,000 potential-founder records from public research, open-source, research-lab, and hackathon evidence. It creates sourcing data only; it does not create `inference_requests.jsonl` and does not calculate Founder Score, screening axes, soft skills, network centrality, or any other model-derived score.

## Outputs

The output directory contains:

- `sourcing_candidates.jsonl`: one deduplicated person per line.
- `evidence_registry.jsonl`: one evidence item per line, joined by `candidate_id` and `source_id`.
- `crawl_runs.jsonl`: run metadata, source counts, and non-fatal source errors.

All founder feature keys named in `Data_requirements.md` are present. Values that cannot be established as public facts remain `null` and their full paths are included in `missing_fields`. The crawler never converts missing information to zero and never calculates speculative scores.

## Setup

```powershell
python -m pip install -r Scripts/01_Screening/requirements.txt
$env:GITHUB_TOKEN = "your-public-read-token"
```

Use a fine-grained token with public metadata read access only. Do not commit it or place it in the YAML file. Authenticated GitHub requests have substantially higher limits than anonymous requests, which is necessary for a 2,000-person run.

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
python Scripts/01_Screening/crawl_founders.py --sources arxiv,github --max-candidates 2000
```

## Collection policy

- Uses the official arXiv Atom API and GitHub REST API.
- Requests only public GitHub metadata; GitHub enrichment is skipped without a token by default.
- Checks `robots.txt` before reading laboratory or hackathon pages.
- Skips a web source when robots policy is unavailable or disallows access.
- Does not log in, bypass anti-bot controls, scrape LinkedIn, or collect private contact details.
- Uses exact strong identifiers for cross-source merging. Exact names are merged only within the same source; uncertain cross-source identity matches remain separate.
- Records source failures in the crawl-run report rather than fabricating data.

HTML sources change over time. Their selectors are configuration, not hard-coded assumptions; update `person_selector`, `project_selector`, and `member_selector` when a permitted public page changes structure.

