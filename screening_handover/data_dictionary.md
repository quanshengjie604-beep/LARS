# Screening handover data dictionary

Generated as of `2026-07-19` from configured sources: a16z, hackernews, pear, yc.

## Point-in-time contract

- A feature can reference evidence only when `evidence.document_date <= data_cutoff_date`.
- Current directory status, current engagement counters, and retrospective portfolio pages are not
  backdated into historical features.
- Accelerator cohort labels and Product Hunt launches are not funding rounds.
- Funding `date_basis` distinguishes announcements, SEC first sales/filings, and investor-first-
  investment dates. An a16z first-investment date is retained as such and is not represented as a
  complete company round.
- Null means not observed. Private financials are never estimated into canonical NGBoost fields.
- A current directory status of acquisition, IPO, or shutdown is recorded in `training_outcomes`
  as a terminal exit/failure dated to the observation horizon (the run date), flagged estimated and
  unverified. Such a status is never backdated into features nor presented as a dated, value-verified
  transaction; exit valuation and value-verification fields stay null.

## Primary files

| File | One row |
|---|---|
| `companies.jsonl` | Deduplicated startup entity |
| `funding_rounds.jsonl` | Dated funding/investment claim with evidence and date basis |
| `training_features.jsonl` | Historical, cutoff-safe startup snapshot |
| `training_outcomes.jsonl` | Strictly post-snapshot outcomes or explicit censoring |
| `inference_requests.jsonl` | Live startup snapshot as of the run date |
| `evidence_registry.jsonl` | Source document or derived formula evidence |
| `financial_disclosures.jsonl` | Explicit, mechanically parsed financial claims |
| `enrichment_queue.jsonl` | Missing/stale/contradicted fields requiring follow-up |
| `quality_report.json` | Coverage, staleness, source health, contradictions, hard fields |
| `source_runs.json` | Per-adapter success/skip/error telemetry |

## Funding fields

- `last_round_date`: latest cutoff-eligible dated financing claim.
- `last_round_size_usd`: disclosed USD amount for that claim; null when undisclosed, non-USD without
  FX evidence, or contradicted.
- `total_funding_to_date_usd`: sum only when every cutoff-eligible non-debt/non-grant event has a
  disclosed, non-contradicted USD amount. Otherwise it remains null rather than presenting a partial
  sum as a total.
- SEC Form D `totalAmountSold` is retained as cumulative issuer-reported amount sold; an offering
  target is evidence text only and is never treated as money raised.

## Scores

The three axes remain separate. Formula IDs in `feature_quality` and derived evidence implement
`plan.md` section 5. Missing components are dropped and weights renormalized. Coverage and confidence
are propagated so a score based on one weak public signal is visibly low-confidence.

## Source policy

- a16z uses its public portfolio data; Pear uses its public WordPress REST data with the declared
  crawl delay.
- YC is local authorised JSON/CSV export only. No YC web/Algolia scraper is used.
- StartX uses the authenticated Consider Boards API only.
- Hacker News uses Algolia discovery and canonical Firebase item records.
- Product Hunt uses its official token-gated GraphQL API and must be used within its API terms.
- SEC data uses locally supplied official quarterly Form D ZIP files, avoiding per-company crawling.

## Hard-to-obtain financials

ARR, burn, runway, cash, churn, and paying-customer count commonly remain null for new companies.
Postprocessing may fill a null only from an explicit, dated, definition-compatible disclosure at or
before the cutoff. Projections, TAM, approximate bounds, generic users, generic revenue, and non-USD
amounts without FX evidence never populate canonical fields. The enrichment queue recommends founder
documents, financial statements, regulatory filings, or primary press rather than inventing values.
