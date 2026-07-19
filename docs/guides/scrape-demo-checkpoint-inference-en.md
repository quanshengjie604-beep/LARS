# Running Inference with the Six-Model Checkpoint

This guide explains how to use the M1–M6 checkpoints in `artifacts/scrape_demo_checkpoint/` to generate probabilistic predictions for one or more companies.

> **Scope:** This checkpoint is intended for demos, integration testing, and candidate ranking. It uses the latest scrape dataset as an anchor and synthetic augmentation to fill all six training targets. In particular, M4–M6 and parts of the other training labels are synthetic. Do not interpret the outputs as production-calibrated investment probabilities.

## 1. Model outputs

| Model | Task | Output |
|---|---|---|
| M1 | Progress within 24 months | Probabilities for `True` and `False` |
| M2 | Time to the next funding or liquidity event | Survival probability at configured horizons |
| M3 | Time to company failure | Survival probability at configured horizons |
| M4 | Annual revenue growth factor | Log-normal parameters and quantiles |
| M5 | Exit type | Probabilities for IPO, acquisition, secondary, and no observed exit |
| M6 | Exit valuation or transaction value | Log-normal parameters and USD quantiles |

For M2, the probability of an event occurring within two years is:

```text
1 - models.m2.parameters.survival_probability_by_day["730"]
```

For M3, the probability of failure within two years is:

```text
1 - models.m3.parameters.survival_probability_by_day["730"]
```

M6 is a **conditional exit-value estimate**. It does not include the probability that an exit will occur.

## 2. Requirements

The project requires Python 3.11 or newer. Install the package and dependencies from the repository root:

```powershell
python -m pip install -e .
```

If the package is not installed, expose the source directory before running the CLI:

```powershell
$env:PYTHONPATH = "src"
```

The checkpoint is serialized with `joblib`. Loading it requires the repository's `decision_engine` package and compatible versions of joblib, NumPy, pandas, scikit-learn, and NGBoost.

Expected checkpoint layout:

```text
artifacts/scrape_demo_checkpoint/
├── m1.joblib
├── m2.joblib
├── m3.joblib
├── m4.joblib
├── m5.joblib
├── m6.joblib
└── manifest.joblib
```

The same files are available in `artifacts/scrape_demo_checkpoint.zip`.

## 3. Input format

Inference input uses JSONL: one valid JSON object per line. Do not wrap records in a JSON array.

Each record should include:

- `startup_id`: stable company identifier;
- `opportunity_id`: identifier for the investment opportunity;
- `snapshot_id`: unique identifier for this point-in-time snapshot;
- `observation_date`: snapshot date in `YYYY-MM-DD` format;
- `data_cutoff_date`: latest date represented by the input evidence;
- six feature objects: `screening`, `traction`, `financials`, `team`, `market`, and `cold_start`;
- `source_ids`, `missing_fields`, and `contradicted_fields` for data-quality reporting.

`data_cutoff_date` must not be later than `observation_date`.

Inference records must not contain the outcome objects `next_round`, `next_event`, `failure`, `growth`, or `exit`. These are training labels, and including them in live input is treated as leakage.

### Complete example

```json
{"startup_id":"startup_demo_001","opportunity_id":"opportunity_demo_001","snapshot_id":"startup_demo_001_2026-07-19_seed","observation_date":"2026-07-19","data_cutoff_date":"2026-07-19","company_name":"Example Robotics","candidate_ids":[],"screening":{"founder_score_persistent":72.0,"founder_axis":74.0,"founder_axis_trend":"improving","market_axis":"bull","market_axis_trend":"stable","idea_vs_market":68.0,"idea_vs_market_trend":"improving"},"traction":{"arr_usd":1200000,"revenue_growth_rate_yoy":0.8,"customer_count":35,"churn_rate_annual":0.08},"financials":{"burn_rate_usd_monthly":180000,"runway_months":16,"current_stage":"seed","last_round_size_usd":3000000,"last_round_date":"2025-11-01","total_funding_to_date_usd":4200000,"cash_balance_usd":2900000},"team":{"founder_prior_exits":1,"founder_prior_startups":2,"single_founder_flag":false,"team_size":18,"technical_founder_flag":true,"founder_industry_experience_years":8,"proprietary_score":0.72,"patent_count":2,"open_source_activity_score":0.65},"market":{"tam_usd":8000000000,"competitor_density":0.35,"sector":"robotics","geography":"us","funding_climate_index":0.7},"cold_start":{"public_footprint_score":0.62,"network_centrality":0.48,"soft_skill_estimate":0.7},"source_ids":["source_demo_001","source_demo_002"],"missing_fields":[],"contradicted_fields":[]}
```

### Sparse input example

All model features are nullable and may be omitted. Missing values are aligned to the training schema and imputed by the saved preprocessing pipeline.

```json
{"startup_id":"startup_demo_002","opportunity_id":"opportunity_demo_002","snapshot_id":"startup_demo_002_2026-07-19_pre_seed","observation_date":"2026-07-19","data_cutoff_date":"2026-07-19","company_name":"Sparse Example","screening":{"founder_axis":61.0},"traction":{},"financials":{"current_stage":"pre_seed"},"team":{"team_size":4,"technical_founder_flag":true},"market":{"sector":"developer_tools","geography":"us"},"cold_start":{"public_footprint_score":0.25},"source_ids":["source_demo_003"],"missing_fields":["traction.arr_usd","traction.revenue_growth_rate_yoy","traction.customer_count","traction.churn_rate_annual","financials.runway_months","financials.last_round_size_usd","market.tam_usd"],"contradicted_fields":[]}
```

Technically, a record can contain very few features. However, sparse inputs produce predictions closer to the training prior and reduce company-level differentiation. The returned `model_confidence` describes input completeness and evidence coverage; it is not a probability-calibration metric.

## 4. Feature reference

### `screening`

| Field | Type or unit |
|---|---|
| `founder_score_persistent` | Float, recommended range 0–100 |
| `founder_axis` | Float, recommended range 0–100 |
| `founder_axis_trend` | `improving`, `stable`, `declining`, or `unknown` |
| `market_axis` | `bull`, `neutral`, `bear`, or `unknown` |
| `market_axis_trend` | Trend enum |
| `idea_vs_market` | Float, recommended range 0–100 |
| `idea_vs_market_trend` | Trend enum |

### `traction`

| Field | Type or unit |
|---|---|
| `arr_usd` | Annual recurring revenue in USD |
| `revenue_growth_rate_yoy` | Annual growth as a fraction; `0.8` means 80% |
| `customer_count` | Number of paying customers |
| `churn_rate_annual` | Annual churn as a fraction |

### `financials`

| Field | Type or unit |
|---|---|
| `burn_rate_usd_monthly` | Monthly net burn in USD |
| `runway_months` | Remaining runway in months |
| `current_stage` | `pre_seed`, `seed`, `series_a`, `series_b`, `series_c_plus`, or `unknown` |
| `last_round_size_usd` | Most recent round size in USD |
| `last_round_date` | `YYYY-MM-DD` |
| `total_funding_to_date_usd` | Total funding in USD |
| `cash_balance_usd` | Cash balance in USD |

### `team`

| Field | Type or unit |
|---|---|
| `founder_prior_exits` | Non-negative integer |
| `founder_prior_startups` | Non-negative integer |
| `single_founder_flag` | Boolean |
| `team_size` | Non-negative integer |
| `technical_founder_flag` | Boolean |
| `founder_industry_experience_years` | Years |
| `proprietary_score` | Recommended range 0–1 |
| `patent_count` | Non-negative integer |
| `open_source_activity_score` | Recommended range 0–1 |

### `market`

| Field | Type or unit |
|---|---|
| `tam_usd` | Total addressable market in USD |
| `competitor_density` | Recommended range 0–1 |
| `sector` | Normalized sector string |
| `geography` | Normalized geography string |
| `funding_climate_index` | Recommended range 0–1 |

### `cold_start`

| Field | Type or unit |
|---|---|
| `public_footprint_score` | Recommended range 0–1 |
| `network_centrality` | Recommended range 0–1 |
| `soft_skill_estimate` | Recommended range 0–1 |

## 5. Run inference with the CLI

The repository includes `config/scrape_demo.yaml`. By default, it reads:

```text
scrape_latest/ngboost_ready/inference_requests.jsonl
```

Run inference with the configured input:

```powershell
$env:PYTHONPATH = "src"
python -m decision_engine.cli --config config/scrape_demo.yaml infer `
  --output artifacts/my_predictions.jsonl
```

Use a custom input file:

```powershell
$env:PYTHONPATH = "src"
python -m decision_engine.cli --config config/scrape_demo.yaml infer `
  --input data/my_inference_requests.jsonl `
  --output artifacts/my_predictions.jsonl
```

Validate the files referenced by the configuration:

```powershell
$env:PYTHONPATH = "src"
python -m decision_engine.cli --config config/scrape_demo.yaml validate
```

## 6. Run inference from Python

The recommended interface is the project pipeline, which applies validation, feature flattening, checkpoint loading, feature alignment, and output assembly:

```python
from decision_engine.io import load_config
from decision_engine.pipeline import infer_all

config = load_config("config/scrape_demo.yaml")
config["data"]["inference_requests"] = "data/my_inference_requests.jsonl"

predictions = infer_all(config, "artifacts/my_predictions.jsonl")
print(f"Generated {len(predictions)} predictions")
```

Validate custom input before inference:

```python
from decision_engine.io import read_jsonl
from decision_engine.validation import validate_features

records = read_jsonl("data/my_inference_requests.jsonl")
report = validate_features(records, live=True)
print(report.model_dump())
```

### Load one checkpoint directly

Use the pipeline unless a lower-level integration is required. Each checkpoint stores the estimator, preprocessing pipeline, and exact training feature columns.

```python
import joblib

from decision_engine.features import flatten_features
from decision_engine.io import read_jsonl

bundle = joblib.load("artifacts/scrape_demo_checkpoint/m1.joblib")
records = read_jsonl("data/my_inference_requests.jsonl")
X = flatten_features(records).reindex(columns=bundle.feature_columns)

result = bundle.estimator.predict_distribution(
    X,
    quantiles=[0.1, 0.5, 0.9],
    horizons=[365, 730, 1825],
)
```

## 7. Output structure

Each input record produces one output record:

```json
{
  "startup_id": "startup_demo_001",
  "opportunity_id": "opportunity_demo_001",
  "snapshot_id": "startup_demo_001_2026-07-19_seed",
  "company_name": "Example Robotics",
  "prediction_generated_at": "2026-07-19T08:00:00+00:00",
  "models": {
    "m1": {"distribution": "categorical", "parameters": {"False": 0.39, "True": 0.61}},
    "m2": {"distribution": "survival_curve", "parameters": {"survival_probability_by_day": {"365": 0.81, "730": 0.66, "1825": 0.34}}},
    "m3": {"distribution": "survival_curve", "parameters": {"survival_probability_by_day": {"365": 0.92, "730": 0.86, "1825": 0.69}}},
    "m4": {"distribution": "lognormal", "parameters": {"log_mu": -0.2, "log_sigma": 0.1}, "quantiles": {"0.1": 0.72, "0.5": 0.82, "0.9": 0.94}},
    "m5": {"distribution": "categorical", "parameters": {"acquisition": 0.42, "ipo": 0.18, "no_exit_observed": 0.25, "secondary": 0.15}},
    "m6": {"distribution": "lognormal", "parameters": {"log_mu": 18.4, "log_sigma": 0.5}, "quantiles": {"0.1": 52000000, "0.5": 98000000, "0.9": 184000000}}
  },
  "model_confidence": {
    "level": "medium",
    "score": 0.72,
    "missing_field_count": 8,
    "contradicted_field_count": 0,
    "source_count": 2
  },
  "data_quality_warnings": []
}
```

## 8. Deployment notes

1. Never load an untrusted `joblib` file. Deserialization can execute arbitrary code.
2. Keep units consistent: monetary values are USD, rates are fractions, and dates use `YYYY-MM-DD`.
3. `snapshot_id` must be unique within an inference batch.
4. Do not use evidence collected after `observation_date`.
5. Unknown categorical values are ignored by the saved one-hot encoder. They do not crash inference, but may reduce accuracy.
6. Do not multiply M1, M2, and M3 as if they were independent probabilities.
7. M5 describes exit-type probabilities, while M6 estimates value conditional on exit. Combining them into expected value requires an explicit, calibrated business formula.
8. Before production use, retrain on sufficiently large real historical datasets with time-based validation and report log loss, Brier score, C-index, and calibration curves.

## 9. Current artifacts

```text
config/scrape_demo.yaml
artifacts/scrape_demo_checkpoint/
artifacts/scrape_demo_checkpoint.zip
artifacts/scrape_demo_predictions.jsonl
scrape_latest/ngboost_ready/inference_requests.jsonl
```