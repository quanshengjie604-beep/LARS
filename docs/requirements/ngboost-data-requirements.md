# Screening Data Handover Requirements for the NGBoost Decision Engine


---


## 1. Two separate datasets are required

The Screening team must deliver two logically separate types of data.

### 1.1 Historical training data

Historical training data teaches the model how startup conditions at an earlier date relate to later outcomes.

Each training observation must contain:

1. A historical feature snapshot containing only information known on the observation date. Any input field may be missing or `null` when the value cannot be found.
2. Outcome labels describing what happened after that observation date.
3. Evidence and provenance for both the features and the outcomes.
4. A censoring date for companies whose final outcome has not yet been observed.

The same startup may produce multiple observations at different dates, for example after Seed and after Series A. Each observation must have a unique `snapshot_id`.

### 1.2 Live inference data

Live inference data describes a startup currently being evaluated.

Each inference observation must contain:

1. The latest feature snapshot as of the evaluation date. Any input field may be missing or `null` when the value cannot be found.
2. Evidence and confidence for each important field.
3. No future outcome labels.

---

## 2. Required delivery files

Submit UTF-8 encoded files using the following structure:

```text
screening_handover/
├── training_features.jsonl
├── training_outcomes.jsonl
├── inference_requests.jsonl
├── evidence_registry.jsonl
└── data_dictionary.md
```

### File definitions

| File | Required | One line represents |
|---|---:|---|
| `training_features.jsonl` | Yes | One historical startup snapshot |
| `training_outcomes.jsonl` | Yes | Outcomes following one historical snapshot |
| `inference_requests.jsonl` | Yes for live use | One startup currently being evaluated |
| `evidence_registry.jsonl` | Yes | One source document or evidence item |
| `data_dictionary.md` | Yes | Definitions and collection rules for any custom fields |

JSONL means **one valid JSON object per line**. Do not wrap the file in a JSON array.

---

## 3. Identifier and date requirements

For input records (`training_features.jsonl` and `inference_requests.jsonl`), all fields in this section may be missing or `null` if they cannot be found. Supply them whenever available because they are needed for joining and point-in-time validation.

For output records (`training_outcomes.jsonl`), every field in this section must be present. Identifier values must be non-null so the output can be joined to the corresponding snapshot.

| Field | Type | Format | Description |
|---|---|---|---|
| `startup_id` | string | Stable internal ID | Must remain unchanged across all records |
| `opportunity_id` | string | Stable internal ID | Identifies the specific investment opportunity/application |
| `snapshot_id` | string | Unique ID | Identifies one company snapshot at one point in time |
| `observation_date` | string | `YYYY-MM-DD` | Date at which the feature snapshot was frozen |
| `data_cutoff_date` | string | `YYYY-MM-DD` | Latest date from which input evidence may be used |

Recommended snapshot ID format:

```text
{startup_id}_{observation_date}_{stage}
```

Example:

```text
startup_00421_2023-05-14_seed
```

---

## 4. Required input features

The following fields should be collected for every historical training snapshot and every live inference request, but **all input fields are nullable and optional**. If a value cannot be found, either omit the field or set it to `null`; never fabricate a value. When a field is omitted or `null`, include its full field path in `missing_fields` whenever that list is available.

### 4.1 Three-axis screening results

Do not average the three axes into one score.

| Field | Type | Allowed values | Required |
|---|---|---|---:|
| `founder_score_persistent` | float/null | 0–100 | No; use `null` or omit if unavailable |
| `founder_axis` | float/null | 0–100 | No; use `null` or omit if unavailable |
| `founder_axis_trend` | enum/null | `improving`, `stable`, `declining`, `unknown` | No; use `null` or omit if unavailable |
| `market_axis` | enum/null | `bull`, `neutral`, `bear`, `unknown` | No; use `null` or omit if unavailable |
| `market_axis_trend` | enum/null | `improving`, `stable`, `declining`, `unknown` | No; use `null` or omit if unavailable |
| `idea_vs_market` | float/null | 0–100 | No; use `null` or omit if unavailable |
| `idea_vs_market_trend` | enum/null | `improving`, `stable`, `declining`, `unknown` | No; use `null` or omit if unavailable |

The Screening team must retain the underlying evidence used to produce these scores. A score without evidence is not sufficient.

### 4.2 Traction and KPI features

| Field | Type | Unit | Required behavior |
|---|---|---|---|
| `arr_usd` | float/null | USD | Use annual recurring revenue or normalized annual run rate |
| `revenue_growth_rate_yoy` | float/null | Fraction/year | `0.65` means 65% growth; `-0.10` means 10% decline |
| `customer_count` | integer/null | Paying customers | Do not mix paying users with registered users |
| `churn_rate_annual` | float/null | Fraction/year | Specify logo churn or revenue churn in evidence |

### 4.3 Financial and funding features

| Field | Type | Unit/values |
|---|---|---|
| `burn_rate_usd_monthly` | float/null | Net USD/month |
| `runway_months` | float/null | Months |
| `current_stage` | enum/null | `pre_seed`, `seed`, `series_a`, `series_b`, `series_c_plus`, `unknown` |
| `last_round_size_usd` | float/null | USD |
| `last_round_date` | string/null | `YYYY-MM-DD` |
| `total_funding_to_date_usd` | float/null | Only funding known by `observation_date` |
| `cash_balance_usd` | float/null | USD, if disclosed |

### 4.4 Team and defensibility features

| Field | Type | Range/format |
|---|---|---|
| `founder_prior_exits` | integer/null | Non-negative |
| `founder_prior_startups` | integer/null | Non-negative |
| `single_founder_flag` | boolean/null | `true` or `false` |
| `team_size` | integer/null | Current headcount at snapshot date |
| `technical_founder_flag` | boolean/null | Based on documented technical background |
| `founder_industry_experience_years` | float/null | Years |
| `proprietary_score` | float/null | 0–1 |
| `patent_count` | integer/null | Patents known by observation date |
| `open_source_activity_score` | float/null | 0–1 |

### 4.5 Market features

| Field | Type | Unit/format |
|---|---|---|
| `tam_usd` | float/null | USD |
| `competitor_density` | float/null | 0–1 |
| `sector` | string/null | Normalized sector taxonomy |
| `geography` | string/null | Normalized country or region |
| `funding_climate_index` | float/null | 0–1 |

Market-size estimates must include their assumptions and source. Founder claims should not automatically be treated as independently verified values.

### 4.6 Cold-start founder features

These fields are particularly important when revenue, customers, and funding history are missing.

| Field | Type | Range |
|---|---|---|
| `public_footprint_score` | float/null | 0–1 |
| `network_centrality` | float/null | 0–1 |
| `soft_skill_estimate` | float/null | 0–1 |

Every modeled soft-skill value must include the interview evidence or rubric used to produce it. These fields should normally have lower confidence than verified financial fields.

---

## 5. Required historical outcome labels

The following information must be collected after the observation date. Different NGBoost models may use different subsets of the historical records.

**Output completeness rule:** every output object and every output field defined in Sections 5.1-5.7 must be present in each `training_outcomes.jsonl` record. A field must not be omitted. Observed facts, event indicators, durations, and computable labels must be non-null. A nullable output field may be `null` only when it is genuinely not applicable or the corresponding event was not observed; the associated indicator and `outcome_observation_end_date` must still be present and non-null. Unknown outputs must be resolved through follow-up collection or marked as censored/not observed according to the applicable definition.

### 5.1 Next-round outcome — Model M1

All five keys are required. When no next round was observed, dependent values may be `null`; the 24-month label may be `null` only when right-censored.

Required fields:

| Field | Type | Description |
|---|---|---|
| `next_round_observed` | boolean | Whether a later funding round was observed |
| `next_round_stage` | enum/null | Stage of the next round |
| `next_round_date` | string/null | `YYYY-MM-DD` |
| `next_round_size_usd` | float/null | Amount raised |
| `progressed_within_24_months` | boolean/null | Main binary training label |

### 5.2 Time to next event — Model M2

All four keys are required. Censored records require `none_observed`, a `null` event date, non-null duration, and a false event flag.

The next event is the earliest of the next funding round, IPO, acquisition, or another defined liquidity event.

| Field | Type | Description |
|---|---|---|
| `next_event_type` | enum/null | `funding_round`, `ipo`, `acquisition`, `secondary`, `none_observed` |
| `next_event_date` | string/null | Date of the next event |
| `time_to_next_event_days` | integer | Days from observation date |
| `next_event_observed` | boolean | `false` means right-censored |

### 5.3 Failure outcome — Model M3

All five keys are required. If failure was not observed, date and definition may be `null`; indicators and duration must be non-null.

| Field | Type | Description |
|---|---|---|
| `failure_observed` | boolean | Whether company failure was verified |
| `failure_date` | string/null | Best verified date of operational failure |
| `failure_definition` | enum/null | `ceased_operations`, `dissolved`, `bankruptcy`, `shutdown_confirmed`, `other` |
| `time_to_failure_days` | integer | Failure duration or censoring duration |
| `failure_event_observed` | boolean | Event indicator used by survival model |

A dead website alone is not sufficient proof of failure. The evidence must distinguish verified failure from an unavailable website.

### 5.4 Revenue-growth outcome — Model M4

All six keys are required and non-null for Model M4 records. Records lacking comparable observations must be explicitly excluded from that model subset.

At least two comparable revenue observations are required.

| Field | Type | Description |
|---|---|---|
| `revenue_start_date` | string | Start of measurement interval |
| `revenue_start_usd` | float | ARR or annualized revenue at start |
| `revenue_end_date` | string | End of measurement interval |
| `revenue_end_usd` | float | Same revenue definition at end |
| `growth_measurement_type` | enum | `arr`, `annualized_revenue`, `recognized_revenue` |
| `annual_growth_factor` | float | `revenue_end / revenue_start`, annualized if necessary |

Do not compare ARR at the start with recognized annual revenue at the end. The measurement definition must remain consistent.

### 5.5 Exit-type outcome — Model M5

All three keys are required. No observed exit requires `no_exit_observed`, a `null` date, and a false verification flag.

| Field | Type | Allowed values |
|---|---|---|
| `exit_type` | enum | `ipo`, `acquisition`, `secondary`, `no_exit_observed` |
| `exit_date` | string/null | `YYYY-MM-DD` |
| `exit_verified` | boolean | Whether the exit is independently verified |

### 5.6 Exit-valuation outcome — Model M6

All five keys are required. Values and type may be `null` only when undisclosed; disclosure and verification flags must be non-null.

| Field | Type | Description |
|---|---|---|
| `exit_valuation_usd` | float/null | Company valuation at exit |
| `exit_transaction_value_usd` | float/null | Total disclosed transaction value |
| `exit_value_type` | enum/null | `equity_value`, `enterprise_value`, `transaction_value`, `ipo_market_cap` |
| `exit_value_disclosed` | boolean | Whether a numeric value is available |
| `exit_value_verified` | boolean | Whether the value was independently verified |

Do not silently substitute transaction value for equity valuation. Preserve the value type explicitly.

### 5.7 Censoring information

Every historical outcome record must contain:

```json
{
  "outcome_observation_end_date": "2026-07-18",
  "company_still_observed": true
}
```

If no event has occurred, calculate duration to `outcome_observation_end_date` and set the corresponding event indicator to `false`.

---

## 6. Evidence and provenance requirements

Every available important input should reference at least one `source_id`; an unavailable input may have no evidence and may be missing or `null`. Every output must have supporting evidence or an explicit censoring/continued-observation record referenced by `source_id`.

Each evidence record must follow this format:

```json
{
  "source_id": "source_00981",
  "startup_id": "startup_00421",
  "source_type": "pitch_deck",
  "document_name": "Startup_0421_Seed_Deck.pdf",
  "source_uri": null,
  "document_date": "2023-05-10",
  "collected_at": "2023-05-14T09:30:00Z",
  "location": "slide 8",
  "evidence_excerpt": "ARR reached $500K as of April 2023.",
  "verification_status": "founder_reported",
  "confidence": "medium"
}
```

Allowed `source_type` values:

```text
application
pitch_deck
founder_interview
financial_statement
bank_or_payment_record
cap_table
financing_document
company_website
regulatory_filing
internal_investment_record
internal_monitoring_record
manual_research
other
```

Allowed `verification_status` values:

```text
independently_verified
document_verified
founder_reported
estimated
unverified
contradicted
```

Allowed confidence values:

```text
high
medium
low
```

If two sources disagree, keep both evidence records and add the field path to `contradicted_fields`. Do not silently select the more favorable value.

---

## 7. Historical training-feature example

One line in `training_features.jsonl`:

```json
{
  "startup_id": "startup_00421",
  "opportunity_id": "opportunity_00712",
  "snapshot_id": "startup_00421_2023-05-14_seed",
  "observation_date": "2023-05-14",
  "data_cutoff_date": "2023-05-14",
  "screening": {
    "founder_score_persistent": 78.0,
    "founder_axis": 82.0,
    "founder_axis_trend": "improving",
    "market_axis": "bull",
    "market_axis_trend": "stable",
    "idea_vs_market": 74.0,
    "idea_vs_market_trend": "improving"
  },
  "traction": {
    "arr_usd": 500000.0,
    "revenue_growth_rate_yoy": 0.65,
    "customer_count": 18,
    "churn_rate_annual": 0.08
  },
  "financials": {
    "burn_rate_usd_monthly": 80000.0,
    "runway_months": 14.0,
    "current_stage": "seed",
    "last_round_size_usd": 1500000.0,
    "last_round_date": "2023-04-20",
    "total_funding_to_date_usd": 1700000.0,
    "cash_balance_usd": null
  },
  "team": {
    "founder_prior_exits": 0,
    "founder_prior_startups": 1,
    "single_founder_flag": false,
    "team_size": 7,
    "technical_founder_flag": true,
    "founder_industry_experience_years": 6.0,
    "proprietary_score": 0.75,
    "patent_count": 2,
    "open_source_activity_score": 0.63
  },
  "market": {
    "tam_usd": 5000000000.0,
    "competitor_density": 0.55,
    "sector": "ai_infrastructure",
    "geography": "united_states",
    "funding_climate_index": 0.68
  },
  "cold_start": {
    "public_footprint_score": 0.71,
    "network_centrality": 0.43,
    "soft_skill_estimate": 0.68
  },
  "source_ids": ["source_00981", "source_00982", "source_00983"],
  "missing_fields": ["financials.cash_balance_usd"],
  "contradicted_fields": []
}
```

---

## 8. Historical training-outcome example

One line in `training_outcomes.jsonl`:

```json
{
  "startup_id": "startup_00421",
  "opportunity_id": "opportunity_00712",
  "snapshot_id": "startup_00421_2023-05-14_seed",
  "next_round": {
    "next_round_observed": true,
    "next_round_stage": "series_a",
    "next_round_date": "2024-08-01",
    "next_round_size_usd": 8000000.0,
    "progressed_within_24_months": true
  },
  "next_event": {
    "next_event_type": "funding_round",
    "next_event_date": "2024-08-01",
    "time_to_next_event_days": 445,
    "next_event_observed": true
  },
  "failure": {
    "failure_observed": false,
    "failure_date": null,
    "failure_definition": null,
    "time_to_failure_days": 1892,
    "failure_event_observed": false
  },
  "growth": {
    "revenue_start_date": "2023-05-01",
    "revenue_start_usd": 500000.0,
    "revenue_end_date": "2024-05-01",
    "revenue_end_usd": 900000.0,
    "growth_measurement_type": "arr",
    "annual_growth_factor": 1.8
  },
  "exit": {
    "exit_type": "no_exit_observed",
    "exit_date": null,
    "exit_verified": false,
    "exit_valuation_usd": null,
    "exit_transaction_value_usd": null,
    "exit_value_type": null,
    "exit_value_disclosed": false,
    "exit_value_verified": false
  },
  "outcome_observation_end_date": "2026-07-18",
  "company_still_observed": true,
  "source_ids": ["source_01210", "source_01211"],
  "missing_fields": ["exit.exit_valuation_usd"],
  "contradicted_fields": []
}
```

---

## 9. Live inference-request example

One line in `inference_requests.jsonl` uses the same feature schema as `training_features.jsonl`, but it must not include any outcome fields:

```json
{
  "startup_id": "startup_00888",
  "opportunity_id": "opportunity_00991",
  "snapshot_id": "startup_00888_2026-07-18_seed",
  "observation_date": "2026-07-18",
  "data_cutoff_date": "2026-07-18",
  "screening": {
    "founder_score_persistent": 71.0,
    "founder_axis": 76.0,
    "founder_axis_trend": "stable",
    "market_axis": "neutral",
    "market_axis_trend": "improving",
    "idea_vs_market": 69.0,
    "idea_vs_market_trend": "stable"
  },
  "traction": {
    "arr_usd": null,
    "revenue_growth_rate_yoy": null,
    "customer_count": 3,
    "churn_rate_annual": null
  },
  "financials": {
    "burn_rate_usd_monthly": 25000.0,
    "runway_months": 10.0,
    "current_stage": "seed",
    "last_round_size_usd": 500000.0,
    "last_round_date": "2026-02-10",
    "total_funding_to_date_usd": 650000.0,
    "cash_balance_usd": null
  },
  "team": {
    "founder_prior_exits": 0,
    "founder_prior_startups": 0,
    "single_founder_flag": true,
    "team_size": 4,
    "technical_founder_flag": true,
    "founder_industry_experience_years": 3.0,
    "proprietary_score": 0.62,
    "patent_count": 0,
    "open_source_activity_score": 0.81
  },
  "market": {
    "tam_usd": 2000000000.0,
    "competitor_density": 0.70,
    "sector": "developer_tools",
    "geography": "germany",
    "funding_climate_index": 0.58
  },
  "cold_start": {
    "public_footprint_score": 0.74,
    "network_centrality": 0.31,
    "soft_skill_estimate": 0.65
  },
  "source_ids": ["source_02001", "source_02002"],
  "missing_fields": [
    "traction.arr_usd",
    "traction.revenue_growth_rate_yoy",
    "traction.churn_rate_annual",
    "financials.cash_balance_usd"
  ],
  "contradicted_fields": []
}
```

---

## 10. Data-quality and leakage rules

### 10.1 No future information in model inputs

For a snapshot dated `2023-05-14`, every input feature must be supported by information available on or before `2023-05-14`.

The input must not include:

- Later financing rounds
- Later revenue or customer counts
- Eventual IPO or acquisition status
- Post-investment team growth
- Updated descriptions that reveal later success
- Scores recalculated using later outcomes

### 10.2 Preserve unsuccessful and incomplete cases

Do not collect only successful companies. The training set must include:

- Companies that raised the next round
- Companies that did not raise again
- Verified failures
- Companies still operating without an observed exit
- Companies with partially missing data

Otherwise, the model will systematically overestimate startup success.

### 10.3 Do not equate missing with zero

Examples:

```text
arr_usd = 0       → verified pre-revenue company
arr_usd = null    → ARR is unavailable
```

### 10.4 Consistent units

Use:

```text
Money       USD
Rates       Fractions, not percentages
Dates       YYYY-MM-DD
Durations   Days in outcome files
Scores      Use the defined 0–1 or 0–100 range
```

### 10.5 No silent estimation

If a field is estimated, its evidence must state:

- Estimation method
- Assumptions
- Person or system that produced the estimate
- Confidence level

---

## 11. Minimum viable collection priority

If time is limited, collect the following first.

### Priority 1 — required for every record

- Stable identifiers and observation date
- Founder, Market, and Idea-vs-Market axes
- Current stage and previous funding information
- Founder track record and team size
- Sector and geography
- Evidence references
- Missing-field list

### Priority 2 — required to train the first probabilistic models

- Complete funding-round dates and stages
- Next-round outcomes
- Outcome observation end date
- Verified company failure or continued-observation status
- Exit type and exit date

### Priority 3 — required for growth and exit-value models

- Comparable revenue observations at two or more dates
- Disclosed exit valuations or transaction values
- Value definitions and supporting documents

If Priority 3 cannot be collected reliably, do not invent these labels. The NGBoost team will use explicitly declared priors or omit those models from the MVP.

---

## 12. Final handover checklist

Before delivery, confirm that:

- [ ] Input fields that cannot be found are omitted or set to `null`; no input value is fabricated.
- [ ] Every output record has a non-null stable `startup_id`.
- [ ] Every defined output key is present; nullable output values are `null` only in explicitly allowed not-observed, not-applicable, or censored cases.
- [ ] Every observed or computable output label, indicator, and duration is non-null.
- [ ] Every historical observation has a unique `snapshot_id`.
- [ ] Feature snapshots contain no information after `data_cutoff_date`.
- [ ] Historical outcomes reference the correct `snapshot_id`.
- [ ] Active companies include a censoring date.
- [ ] Failed companies include a verified failure definition and date.
- [ ] Revenue growth uses consistent revenue definitions.
- [ ] Exit values specify whether they are equity, enterprise, transaction, or market-cap values.
- [ ] Missing input values are omitted or `null`, not zero or fabricated estimates.
- [ ] Important fields reference evidence IDs.
- [ ] Contradictions are retained and flagged.
- [ ] JSONL files contain one valid JSON object per line.
- [ ] Monetary values are normalized to USD.
- [ ] Rates are represented as fractions.

---

## 13. Handover boundary

The Screening team is responsible for:

- Collecting and structuring features
- Producing the three independent screening axes
- Tracking historical outcomes
- Preserving timestamps, missingness, evidence, and contradictions

The NGBoost team is responsible for:

- Validating and flattening the received JSONL
- Encoding categorical variables
- Training probabilistic models
- Producing distribution parameters and model confidence
- Passing the predicted distributions to the Monte Carlo simulation module
