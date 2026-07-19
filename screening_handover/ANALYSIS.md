# Screened-Sample Analysis

Analysis of the delivered `screening_handover/` records produced by the `vcbrain`
pipeline (YC directory seed + GitHub / Hacker News enrichment, LLM disabled).

> **Sample count note.** The question refers to "31 screened samples." The files
> currently on disk hold **15** screened samples (inference + training snapshots
> for 15 top-tier YC companies ≤10 years old); the 31-company `--top-only` run
> was interrupted before it wrote. Every finding below is structural — driven by
> *which sources can supply a field*, not by N — so it holds identically at 15 or
> 31. Re-run `python -m vcbrain --top-only --limit 0` to materialise all 31.

## 1. The data is sparse — and the sparsity is structural, not accidental

Each record has 35 input feature fields. Across the 15 inference samples, **18 of
35 (51%) are null in 100% of records.** This is expected and, per the plan and
handover spec, *correct*: a value that no public source can supply is left `null`
and listed in `missing_fields` rather than fabricated (requirements §10.3, §10.5).

Coverage falls into three tiers:

| Tier | Fields | Coverage |
|---|---|---|
| **Always present** (deterministic / computed) | all 7 `screening.*` axes+trends, `market.sector/geography/competitor_density/funding_climate_index`, `financials.current_stage`, `cold_start.network_centrality`, `team.team_size` (inference only) | 100% |
| **Partial** (depend on an external hit) | `cold_start.public_footprint_score`, `team.open_source_activity_score`, `team.proprietary_score` | 40–87% present |
| **Never present** (no public source) | all `traction.*`, most `financials.*`, all founder-track-record `team.*`, `market.tam_usd`, `cold_start.soft_skill_estimate` | 0% |

## 2. Problematic fields (systematically null)

### 2a. Private KPIs — null by nature (100% null, both datasets)
`traction.arr_usd`, `traction.revenue_growth_rate_yoy`, `traction.customer_count`,
`traction.churn_rate_annual`, `financials.burn_rate_usd_monthly`,
`financials.runway_months`, `financials.total_funding_to_date_usd`,
`financials.last_round_size_usd`, `financials.cash_balance_usd`.

These are confidential operating metrics. No free source (YC / GitHub / HN)
exposes them, so they are honestly null. **Fix:** they only arrive via the
*inbound* channel (a founder's application/deck) or paid vendors
(Crunchbase/Dealroom for funding; Latka/Growjo for ARR). This is the expected
inbound-vs-outbound split from the challenge brief, not a pipeline defect.

### 2b. Founder track-record — null because the seed has no founder layer (100% null)
`team.founder_prior_exits`, `team.founder_prior_startups`,
`team.single_founder_flag`, `team.technical_founder_flag`,
`team.founder_industry_experience_years`.

**This is the most important gap.** The YC OSS directory carries *company*
records but **no founder names or bios**, so the entire founder track record is
empty — and with it, the persistent Founder Score degenerates to public-footprint
+ network signal only. For a "credit score for founders" this is the field group
most worth closing. **Fix:** add a people source (YC per-company founder scrape,
Crunchbase people, or a LinkedIn-derived vendor API — Proxycurl/PDL/Coresignal),
keyed off the founder name once resolved.

### 2c. Cold-start soft signals — null without the LLM (100% null here)
`cold_start.soft_skill_estimate` (and, in this run, `market.tam_usd`). Both are
LLM-derived and were skipped (`--no-llm`, no `ANTHROPIC_API_KEY`). **Fix:** set
`ANTHROPIC_API_KEY` and drop `--no-llm`; both then populate as
`verification_status: estimated`, low confidence.

### 2d. Partial-coverage enrichment — thin, and thinner in training
`open_source_activity_score` / `proprietary_score` are present for ~87% of
inference records but only ~40% of *training* records, and
`public_footprint_score` drops from 73%→47% inference→training. **Cause:** the
point-in-time filter correctly discards GitHub repos and HN mentions created
after the batch-date cutoff, so early-stage snapshots legitimately have less
signal. Note GitHub coverage is also throttled by the unauthenticated rate limit;
a `GITHUB_TOKEN` raises hit rates.

### 2e. A subtle one: `proprietary_score` never uses patents
`team.patent_count` is 100% null, so `proprietary_score` is computed from
open-source activity + (absent) LLM rubric alone — its patent term is always
dropped. Values are real but narrower than the formula implies. **Fix:** add a
USPTO PatentsView query (free).

## 3. Are company names included in the data?

**No — not in the feature or outcome records; yes — in the evidence registry.**

- `inference_requests.jsonl`, `training_features.jsonl`, `training_outcomes.jsonl`:
  companies are identified **only** by a stable hashed `startup_id` (e.g.
  `startup_f2c2ae5bce01`) plus `opportunity_id` / `snapshot_id`. There is **no
  `name` field** anywhere in these records (verified: no `name` key in any of the
  15 feature records). This matches the handover schema in
  `ngboost_data_requirements.md`, which never lists a company-name field — the
  NGBoost side joins on IDs, not names.
- `evidence_registry.jsonl`: names **are** present, in `document_name` and
  `evidence_excerpt` (e.g. `"YC directory: Zepto"`, `"hackernews:Zepto"`). This is
  the correct place for them — the registry is the human-auditable provenance
  trail (plan §2, stretch-goal "Agentic Traceability"), so a reviewer can resolve
  a `startup_id` back to a real company via its `source_ids`.

If the downstream consumer needs the name inline on each feature row, it is one
line to surface: the pipeline already carries it in `_meta.company` and currently
strips underscore-prefixed keys on write (`pipeline._write_jsonl`). Expose
`_meta` (or promote `company_name`) if a named join is wanted; keep it stripped
if ID-only records are preferred for the model handoff.

## 4. Bottom line

The sparsity is a faithful picture of what the **outbound** sourcing layer can
know about a company *before it applies*: strong on directory facts, structural
market context, and public-web footprint; empty on private financials and (right
now) founder biography. The three highest-leverage fixes, in order:

1. **Founder layer** — resolve founder identities and back-fill §2b (unblocks the
   real Founder Score).
2. **LLM on** — populate `tam_usd` + soft-skill/rubric estimates (§2c).
3. **GITHUB_TOKEN + PatentsView** — thicken defensibility/footprint (§2d, §2e).

None of these change the schema; they only reduce the null rate.
