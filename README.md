# vcbrain — parallel startup and funding collection

This repository implements [`plan.md`](plan.md) over a **list of startups**. It discovers a
cohort from accelerator/investor directories, resolves duplicate companies, enriches every company
concurrently, extracts dated funding claims, builds cutoff-safe NGBoost snapshots, and runs an
evidence-only missing-financials assessment.

The implementation is deliberately conservative: cohort membership and product launches are not
funding rounds, current directory data is not backdated, and unavailable private financials remain
`null`.

## Quick validation

The default mini scrape is network-free and deterministic. It exercises all four directory adapter
shapes, Hacker News, Product Hunt, funding extraction, duplicate resolution, postprocessing, and the
standalone report.

```bash
python -m vcbrain mini --output-dir mini_scrape_output
open mini_scrape_output/scrape_report.html
```

It writes `mini_assessment.json`; a nonzero exit code means a functional or leakage check failed.
The optional live smoke is intentionally explicit:

```bash
python -m vcbrain mini --live --output-dir live_mini_output
```

## Full collection

```bash
python -m vcbrain collect \
  --sources a16z,pear,yc,startx \
  --limit 0 \
  --mode training \
  --output-dir screening_handover
```

Use `--limit 0` for every discovered company. `--concurrency` controls the bounded source/company
worker pool. Pear remains paced at 10 seconds per request by default; its waits are asynchronous, so
other source work continues.

With `--sources yc` and no `--yc-export`, the pipeline auto-runs the bundled Algolia scraper to fetch
the public YC directory (see the YC entry under [Source boundary](#source-boundary) for the bounding
and opt-out flags).

Optional credentials are read from the environment:

| Variable | Use |
|---|---|
| `PRODUCTHUNT_TOKEN` | Official Product Hunt GraphQL API |
| `STARTX_API_KEY` or `CONSIDER_API_KEY` | Authenticated StartX Consider board |
| `GITHUB_TOKEN` | Explicit directory-linked GitHub organizations |
| `VCBRAIN_AS_OF` | Snapshot/censoring date (`YYYY-MM-DD`) |
| `VCBRAIN_CONCURRENCY` | Global asynchronous request/work bound |

### Source boundary

- **a16z:** one public portfolio request; parses the page's `data-company` JSON and falls back to
  the names-only investment list.
- **Pear VC:** public WordPress REST portfolio plus taxonomy endpoints, with the declared crawl
  delay.
- **YC:** an authorised local JSON/CSV export when supplied via `--yc-export`; otherwise the pipeline
  auto-runs the bundled Algolia extractor (`yc-scraper/algolia_extractor.py`, overridable with
  `--yc-scraper-path` or `$VCBRAIN_YC_SCRAPER`) to pull the public company directory. Cohort/batch
  labels are retained as evidence, never as funding dates. Scraped batches are cached under the cache
  directory so repeated runs do not re-query YC. Disable the auto-scrape with `--no-yc-scraper`, and
  bound it with `--yc-recent N` or `--yc-batches "Winter 2024" "Summer 2024"`.
- **StartX:** authenticated Consider Boards API only. Without a key it is reported as a structured
  skip rather than silently scraped.
- **Hacker News:** Algolia discovery plus canonical Firebase item records.
- **Product Hunt:** official token-gated GraphQL API. A launch is a launch; only explicit dated
  financing language creates a low-confidence funding claim.
- **SEC Form D:** local official quarterly ZIPs, joined once across the entire cohort. Amount sold is
  kept distinct from the offering target.

## Outputs

The output directory contains the required handover plus audit artifacts:

```text
companies.jsonl
funding_rounds.jsonl
training_features.jsonl
training_outcomes.jsonl
inference_requests.jsonl
evidence_registry.jsonl
financial_disclosures.jsonl
enrichment_queue.jsonl
quality_report.json
source_runs.json
collection_summary.json
data_dictionary.md
scrape_report.html
```

`scrape_report.html` is produced through the pure API:

```python
from vcbrain.visualize import render_html, write_html

html = render_html(companies, funding_rounds, quality_report, source_runs)
write_html(companies, funding_rounds, quality_report, source_runs, "report.html")
```

It shows source health, funding by year, coverage, and a filterable funding table. Null amounts are
shown as “not found”, never `$0`.

## Point-in-time and financial postprocessing

Every historical feature reference must satisfy:

```text
evidence.document_date <= snapshot.data_cutoff_date
```

Retrospective portfolio dates may support later outcome collection, but they cannot leak current
directory contents into an earlier feature snapshot. Funding events remain plural; stage and total
funding are derived only from cutoff-eligible claims.

After snapshot assembly, `vcbrain.postprocess`:

1. extracts exact, dated ARR/revenue/customer/growth/runway/burn/cash disclosures;
2. fills a null only when the definition, currency, entity, and cutoff are compatible;
3. refuses projections, bounds, generic users/revenue, non-USD values without FX evidence, and
   same-date conflicts;
4. reports coverage, staleness, contradictions, and source failures; and
5. emits a ranked enrichment queue for fields likely requiring founder documents or vendor access.

It never infers cash from a funding round, burn from headcount, runway from guessed burn, or ARR from
traffic.

## Tests

```bash
python -m unittest discover -s tests -v
```

The suite covers all directory parsers, funding/valuation/total-raised distinctions, non-USD amounts,
round contradictions, SEC amount-sold semantics, point-in-time disclosure filtering, deterministic
parallel output, duplicate company resolution, unresolved evidence references, private-field nulls,
and safe report escaping.
