# VC Brain — Probabilistic Decision Engine

**Pipeline: Screening → NGBoost-style evaluation → Monte Carlo simulation → Output**

This document specifies the parameters and handover contracts for the decision/reasoning
half of the VC Brain (challenge *02-Maschmeyer-Group-The-VC-Brain*). The sourcing layer is
owned by a separate workstream and is out of scope here; this pipeline begins at the point
where a screened opportunity is handed over.

The core design principle: **uncertainty is preserved end-to-end.** The evaluation layer emits
*probability distributions*, not point estimates; the Monte Carlo layer samples those
distributions to produce a *distribution of returns*; the output layer reports intervals and
probabilities, never a single "success score."

---

## 0. Pipeline overview

```
  Screening layer                NGBoost-style evaluation           Monte Carlo simulation          Output
  (3-axis, per opportunity)      (conditional density estimation)   (forward uncertainty prop.)     (decision-ready)
 ┌────────────────────────┐     ┌──────────────────────────────┐  ┌───────────────────────────┐  ┌──────────────────┐
 │ Founder axis + trend   │     │ 6 models, each: features → θ  │  │ N iterations, each = one  │  │ MOIC distribution│
 │ Market axis + trend    │ ──▶ │ (parameters of a named       │─▶│ plausible company path    │─▶│ P(>10x), P(loss) │
 │ Idea-vs-Market + trend │  X  │  distribution per target)    │θ │ raise → grow → dilute →    │  │ expected MOIC    │
 │ + evidence + Trust     │     │ + SHAP attribution per param  │  │ exit or die → return      │  │ exit-time median │
 └────────────────────────┘     └──────────────────────────────┘  └───────────────────────────┘  └──────────────────┘
        FEATURE VECTOR X                DISTRIBUTION PARAMS θ            + DETERMINISTIC TERMS D          AGGREGATES
```

Three handover contracts are defined below:

- **H1 — `FeatureVector`**: Screening → Evaluation (Section 2)
- **H2 — `DistributionParams`**: Evaluation → Simulation (Section 3)
- **H3 — `SimulationResult`**: Simulation → Output (Section 5)

Plus one side-input, **`DeterministicTerms`** (Section 4), supplied by the Thesis Engine.

---

## 1. Notation and conventions

| Symbol | Meaning |
|--------|---------|
| `X` | Feature vector describing one opportunity (the model inputs) |
| `θ` | The set of distribution parameters the evaluation layer emits |
| `D` | Deterministic terms (check size, ownership, dilution) from the Thesis Engine |
| `N` | Number of Monte Carlo iterations (default 100,000) |
| Beta(α, β) | Distribution over a probability in [0, 1] |
| LogNormal(μ, σ) | Distribution over a positive quantity; μ, σ are in log-space |
| Weibull(k, λ) | Time-to-event distribution; k = shape, λ = scale |
| Pareto(x_m, α) | Heavy-tailed distribution; x_m = minimum, α = tail index |
| Categorical(**p**) | Discrete distribution over named outcomes with probability vector **p** |

**Missing data:** every field carries an implicit `is_present` flag. A missing feature is
never imputed to a fabricated value — it is passed to the model as `null` (models handle
missingness natively) and the resulting distribution is expected to *widen*. Missing
deterministic terms fall back to Thesis-Engine defaults and are flagged in the output.

---

## 2. Handover H1 — Screening → Evaluation (`FeatureVector`)

The screening layer scores each opportunity along three **independent** axes (never averaged)
and passes the underlying evidence forward as features. This object is the input `X` to every
model in Section 3.

### 2.1 Axis scores (from 3-axis screening)

| Parameter | Type | Range | Description |
|-----------|------|-------|-------------|
| `founder_score_persistent` | float | 0–100 | The **Founder Score** from Memory — persists across applications, never resets. Follows the person across startups. One input into the Founder axis, not a substitute for it. |
| `founder_axis` | float | 0–100 | Per-opportunity Founder axis: traits, track record, team pedigree. |
| `founder_axis_trend` | enum | improving / stable / declining | Direction of the Founder axis over time. |
| `market_axis` | enum | bull / neutral / bear | Market rating: sizing, competitors, SWOT. |
| `market_axis_trend` | enum | improving / stable / declining | Direction of the Market axis. |
| `idea_vs_market` | float | 0–100 | Does the idea survive scrutiny as-is, or is the team strong enough to pivot? |
| `idea_vs_market_trend` | enum | improving / stable / declining | Direction of the Idea-vs-Market axis. |

> The three axes are kept **separate** on purpose (challenge FAQ #5). They enter the models as
> distinct conditioning features and are never collapsed into one number.

### 2.2 Traction & KPI features *(required memo section)*

| Parameter | Type | Unit | Description |
|-----------|------|------|-------------|
| `arr_usd` | float | USD | Annual recurring revenue (or revenue run-rate). **⚠ Hard to obtain** (private) but core input to M4 growth and M6 valuation. Expect `null` pre-seed → distribution widens by design. |
| `revenue_growth_rate_yoy` | float | fraction/yr | Observed YoY growth (e.g. 0.65 = 65%). **⚠ Hard to obtain** (needs ≥2 revenue points) but is the direct driver of M4 — keep. |
| `customer_count` | int | count | Number of paying customers. |
| `churn_rate_annual` | float | fraction/yr | Annual logo or revenue churn. **⚠ Hard to obtain** (private) but a named M3 failure driver — keep despite scarcity; proxy from cohort/retention when disclosed. |

### 2.3 Financial & runway features

| Parameter | Type | Unit | Description |
|-----------|------|------|-------------|
| `burn_rate_usd_monthly` | float | USD/month | Net monthly cash burn. **⚠ Hard to obtain** (private); M3 failure driver. Estimate from `team_size` × avg comp + `last_round_size_usd` if undisclosed, and lower `confidence_flag`. |
| `runway_months` | float | months | Cash ÷ burn. **Primary driver of failure timing. ⚠ Hard to obtain** but the single highest-value input to M3 — estimate as `last_round_size_usd` ÷ `burn_rate_usd_monthly` when cash is undisclosed rather than dropping it. |
| `current_stage` | enum | pre_seed / seed / series_a / series_b / series_c_plus | Funding stage at time of evaluation. |
| `last_round_size_usd` | float | USD | Size of the most recent round, if any. |

### 2.4 Team & defensibility features

| Parameter | Type | Description |
|-----------|------|-------------|
| `founder_prior_exits` | int | Number of prior founder exits (proxy for track record). |
| `single_founder_flag` | bool | Red-flag indicator; conditions failure hazard. |
| `team_size` | int | Headcount. |
| `proprietary_score` | float 0–1 | Proprietary vs. commoditizable technology / data moat. |
| `patent_count` | int | Patents known at evaluation date (defensibility signal). |
| `open_source_activity_score` | float 0–1 | Open-source traction signal. |

### 2.5 Market & macro features

| Parameter | Type | Unit | Description |
|-----------|------|------|-------------|
| `tam_usd` | float | USD | Total addressable market — **caps the exit-valuation ceiling** in M6. Estimated (state assumptions in evidence). Only `tam_usd` is consumed downstream, so `sam_usd`/`som_usd` are dropped. |
| `competitor_density` | float | 0–1 | How crowded the space is. Partly redundant with `market_axis`; keep only if independently sourced (e.g. category count), else `null`. |
| `sector` | enum | — | Sector tag (also used by the Thesis Engine filter). |
| `geography` | enum | — | HQ region. |
| `funding_climate_index` | float | 0–1 | Macro capital-availability signal at evaluation time (conditions M1/M2 raise probability). |

### 2.6 Cold-start footprint features

For pre-track-record founders (no ARR, no funding, no GitHub), these public-footprint signals
carry the load. When Section 2.2–2.4 are mostly `null`, the models fall back to these plus the
Founder Score.

| Parameter | Type | Description |
|-----------|------|-------------|
| `public_footprint_score` | float 0–1 | Aggregate signal from public presence (writing, talks, community). |
| `network_centrality` | float 0–1 | Position in the sourcing graph (accelerators, institutions, peers). **⚠ Hard to obtain** (requires building the sourcing graph) but high-signal for cold-start — keep at low confidence until the graph exists. |
| `soft_skill_estimate` | float 0–1 | Modeled resilience / founder-market fit. **⚠ Hard to obtain / inherently noisy** — mandated cold-start signal (Area of Research 1); keep with wide uncertainty, never as a point value. |

### 2.7 What was cut, and what to fight for

The test for keeping a feature is simple: **does a model in Section 3 or the Monte Carlo loop in
Section 5 actually consume it?** Features that fail that test *and* are hard to source were removed.

**Cut** — not consumed downstream and low-yield to collect:

| Removed | Was in | Why it's safe to drop |
|---------|--------|-----------------------|
| `cac_usd` | 2.2 | No model conditions on it; unit-economics detail no downstream target uses. |
| `sales_cycle_days` | 2.2 | Same — never referenced by M1–M6 or the MC loop. |
| `dau` | 2.2 | Usage-intensity nice-to-have; not a model input; rarely public. |
| `sam_usd`, `som_usd` | 2.5 | Only `tam_usd` is consumed (M6 valuation cap). SAM/SOM add collection cost, no signal here. |

**Hard to obtain but do NOT cut** — these are private/estimated yet each is a *named driver* of a
model, so dropping them degrades the signal that matters most. Collect or estimate them and lower
`confidence_flag` rather than omitting:

| Keep | Feeds | Fallback when unavailable |
|------|-------|---------------------------|
| `runway_months`, `burn_rate_usd_monthly` | M3 failure clock (the loop's `t_fail`) | Estimate `runway ≈ last_round_size_usd ÷ burn`; `burn ≈ team_size × avg comp`. |
| `arr_usd`, `revenue_growth_rate_yoy` | M4 growth, M6 valuation | `null` → wider growth distribution (honest, not fabricated). |
| `churn_rate_annual` | M3 hazard | Proxy from retention/cohort data if disclosed; else `null`. |
| `network_centrality`, `soft_skill_estimate` | Cold-start founder signal | Low confidence by design; carry wide uncertainty. |

Everything else in 2.1–2.6 is both consumed and cheaply sourced — keep as-is.

**Naming.** Field names follow the collection contract in `ngboost_data_requirements.md`
(`_usd` / `_yoy` / `_annual` / `_monthly` suffixes) so the producer and consumer schemas join 1:1.

**Superset.** That doc also collects a few fields not listed here — `last_round_date`,
`total_funding_to_date_usd`, `cash_balance_usd`, `founder_prior_startups`, `technical_founder_flag`,
`founder_industry_experience_years`. They feed point-in-time joins, the fallback estimates above, and
the upstream axis scoring, but are not passed to the models as direct features.

---

## 3. Handover H2 — Evaluation → Simulation (`DistributionParams`)

The evaluation layer is a set of **six conditional-density models** (NGBoost-style: each
outputs the *parameters* of a named distribution, trained by minimizing negative
log-likelihood on historical startup outcomes). Each model maps `X → θ_i`.

The choice of distribution family per target is deliberate — probabilities use Beta, positive
quantities use LogNormal, time-to-event uses Weibull, and the fat-tailed exit outcome uses a
Pareto tail.

### 3.1 The six models and their emitted parameters

| # | Target variable | Family | Emitted params | Meaning |
|---|-----------------|--------|----------------|---------|
| M1 | P(progress to next round) | Beta(α, β) | `series_alpha`, `series_beta` | Uncertainty over the probability of raising the next round. Mean = α/(α+β); spread encodes confidence. One instance per stage transition (Seed→A, A→B, B→C+). |
| M2 | Time to next round / exit | Weibull(k, λ) | `t_next_k`, `t_next_lambda` | Time-to-event; trained with **censoring** (companies still alive contribute "survived at least t"). |
| M3 | Failure timing | Weibull(k, λ) | `fail_k`, `fail_lambda` | Hazard of running out of cash, driven mainly by `runway_months`, `burn_rate_usd_monthly`, `churn_rate_annual`. |
| M4 | Revenue / ARR growth per period | LogNormal(μ, σ) | `growth_mu`, `growth_sigma` | Multiplicative growth factor per simulated period. |
| M5 | Exit type | Categorical(**p**) | `p_ipo`, `p_acq`, `p_secondary`, `p_none` | Probability vector over exit modes; sums to 1. |
| M6 | Exit valuation \| exit | LogNormal body + Pareto tail | `exit_mu`, `exit_sigma`, `tail_xm`, `tail_alpha` | Heavy-tailed exit value. Pareto tail (`tail_alpha` ≈ 1.5–2.0) captures the power-law outliers that drive VC returns. Capped by `tam_usd`. |

### 3.2 Parameter detail

**Beta parameters (M1).** `α` and `β` are pseudo-counts. High α+β = confident (tight
distribution); low α+β = uncertain (wide). Cold-start founders should produce low α+β → the
model is honest that it doesn't know. Never collapse to a point probability.

**Weibull parameters (M2, M3).** `k` (shape) < 1 means failure risk is front-loaded (early
death); `k` > 1 means risk rises with age. `λ` (scale) sets the characteristic timescale in
years. Censoring during training is mandatory — otherwise survivorship bias inflates the
numbers.

**LogNormal parameters (M4, M6 body).** `μ`, `σ` live in log-space. The median is `exp(μ)`;
`σ` controls right-skew. Growth (M4) uses this directly; exit valuation (M6) uses it for the
"normal" outcomes and hands the tail to Pareto.

**Pareto tail (M6).** `tail_xm` is the valuation threshold at which power-law behavior takes
over; `tail_alpha` is the tail index. **Critical:** `tail_alpha` just under 2 means the mean is
dominated by rare outliers and the sample mean *understates* the true mean — so this tail must
be modeled explicitly, not resampled from historical exits. A pure LogNormal would under-tail
and systematically undervalue the one 100× outcome.

**Categorical (M5).** The exit-type vector routes each simulated path: `none` implies the
company neither exits nor fails within the horizon (return realized at horizon or written to 0).

### 3.3 Attribution payload (for Trust Score / traceability)

Alongside `θ`, each model emits a **SHAP attribution vector** — which input features moved each
parameter, and by how much. This is what lets a sampled outcome trace back to the evidence that
drove it (challenge stretch goal: Agentic Traceability; core requirement: per-claim Trust
Score).

| Field | Type | Description |
|-------|------|-------------|
| `shap[target][feature]` | float | Contribution of each feature to each emitted parameter. |
| `confidence_flag` | enum | high / medium / low — derived from how much of `θ` rests on present vs. missing features. |

### 3.4 `DistributionParams` contract (illustrative JSON)

```json
{
  "opportunity_id": "startup_0421",
  "horizon_years": 10,
  "round_progression": [
    { "from": "seed", "to": "A",  "series_alpha": 6.0, "series_beta": 4.0 },
    { "from": "A",    "to": "B",  "series_alpha": 3.0, "series_beta": 5.0 },
    { "from": "B",    "to": "C+", "series_alpha": 2.0, "series_beta": 6.0 }
  ],
  "time_to_event":   { "t_next_k": 1.3, "t_next_lambda": 2.1 },
  "failure_timing":  { "fail_k": 0.8, "fail_lambda": 3.5 },
  "growth_per_year": { "growth_mu": 0.45, "growth_sigma": 0.6 },
  "exit_type":       { "p_ipo": 0.05, "p_acq": 0.35, "p_secondary": 0.10, "p_none": 0.50 },
  "exit_valuation":  { "exit_mu": 18.2, "exit_sigma": 1.1, "tail_xm": 5.0e8, "tail_alpha": 1.8 },
  "attribution":     { "confidence_flag": "medium", "shap": { "...": "..." } }
}
```

---

## 4. Side-input — `DeterministicTerms` (from the Thesis Engine)

These are **not** predicted; they are set by the investor's thesis and the round structure. They
convert a sampled outcome path into an MOIC. Per the challenge, cap-table data is often
undisclosed — when missing, fall back to Thesis defaults and **flag the assumption in the
output rather than fabricating it.**

| Parameter | Type | Unit | Source | If missing |
|-----------|------|------|--------|------------|
| `check_size` | float | USD | Thesis Engine | Thesis default (always present). |
| `target_ownership` | float | fraction | Thesis Engine | Thesis default. |
| `entry_valuation` | float | USD | Round structure | Implied from check ÷ target ownership. |
| `dilution_per_round` | float | fraction | Cap table | Assume standard schedule (e.g. 0.20/round) + **flag**. |
| `liquidation_pref` | float | multiple | Cap table | Ignore in base case + **flag as not disclosed**. |
| `horizon_years` | int | years | Thesis Engine | Default 10. |

---

## 5. Monte Carlo simulation

The simulation is **forward uncertainty propagation**: for each of `N` iterations it draws one
sample from every distribution in `θ`, walks the company through its life path, and computes a
single MOIC. The `N` resulting MOICs form the output distribution.

### 5.1 Per-iteration algorithm

```
for i in 1..N:
    ownership = target_ownership
    stage     = current_stage
    valuation = entry_valuation
    t         = 0

    # 1. Failure clock
    t_fail = sample Weibull(fail_k, fail_lambda)

    # 2. Walk funding rounds
    while stage < exit_stage and t < horizon_years:
        raised = sample Bernoulli(mean of Beta(series_alpha, series_beta) for this stage)
        if not raised: break                      # stalls; no further dilution or growth
        dt = sample Weibull(t_next_k, t_next_lambda)
        t += dt
        if t > t_fail: mark_failed; break          # ran out of cash first
        ownership *= (1 - dilution_per_round)      # diluted by the new round
        g = sample LogNormal(growth_mu, growth_sigma)
        valuation *= (1 + g)                       # grows with revenue
        stage = next(stage)

    # 3. Resolve outcome
    if failed or t >= horizon_years and no_exit:
        moic_i = 0                                 # total loss / no liquidity
    else:
        exit_type = sample Categorical(p_ipo, p_acq, p_secondary, p_none)
        if exit_type == none:
            moic_i = 0
        else:
            exit_val = sample LogNormal(exit_mu, exit_sigma) with Pareto(tail_xm, tail_alpha) tail
            exit_val = min(exit_val, tam_usd)      # capped by market size
            proceeds = ownership * exit_val
            moic_i   = proceeds / check_size

    record moic_i, t_exit_i, exit_type_i
```

### 5.2 Simulation control parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `n_iterations` | int | 100,000 | Number of sampled paths. |
| `random_seed` | int | fixed | For reproducibility of a given memo. |
| `horizon_years` | int | 10 | Truncation horizon; unexited companies realize 0 (or carrying value). |
| `period_length` | float | 1.0 | Growth compounding period in years. |
| `correlation_mode` | enum | independent / copula | Whether variables are sampled independently or with a dependency structure (e.g. growth ↔ exit valuation). Independent is the MVP default. |

---

## 6. Handover H3 — Simulation → Output (`SimulationResult`)

The `N` recorded MOICs and exit times are aggregated into decision-ready metrics. These are the
numbers a VC actually reasons in — far more informative than a single success probability.

| Output metric | Type | Description |
|---------------|------|-------------|
| `expected_moic` | float | Mean of the MOIC distribution. |
| `median_moic` | float | 50th percentile — robust central estimate given the fat tail. |
| `p_total_loss` | float | Share of iterations with MOIC = 0. |
| `p_gt_1x` | float | Probability of returning capital (MOIC > 1). |
| `p_gt_10x` | float | Probability of a ≥10× outcome (the return-driver). |
| `moic_p5`, `moic_p95` | float | 5th / 95th percentile — the return interval. |
| `median_exit_years` | float | Median time to liquidity across non-failed paths. |
| `exit_type_mix` | dict | Realized share of IPO / acquisition / secondary / none. |
| `confidence_flag` | enum | Propagated from evaluation — high / medium / low, driven by feature completeness. |
| `assumptions_flagged` | list | Every Thesis-default fallback used (e.g. "cap table not disclosed — assumed 20%/round dilution"). |
| `evidence_trace` | dict | Top feature attributions per output, for the memo's per-claim Trust Score. |

### 6.1 `SimulationResult` contract (illustrative JSON)

```json
{
  "opportunity_id": "startup_0421",
  "expected_moic": 3.6,
  "median_moic": 0.0,
  "p_total_loss": 0.42,
  "p_gt_1x": 0.34,
  "p_gt_10x": 0.18,
  "moic_p5": 0.0,
  "moic_p95": 14.0,
  "median_exit_years": 7.2,
  "exit_type_mix": { "ipo": 0.03, "acq": 0.28, "secondary": 0.07, "none": 0.62 },
  "confidence_flag": "medium",
  "assumptions_flagged": [
    "Cap table not disclosed — assumed 20%/round dilution",
    "Entry valuation implied from thesis check size and target ownership"
  ],
  "evidence_trace": { "p_gt_10x": { "founder_score_persistent": 0.21, "revenue_growth_rate_yoy": 0.17 } }
}
```

---

## 7. Cross-cutting requirements (mapped to the challenge)

| Requirement | How this pipeline satisfies it |
|-------------|-------------------------------|
| **Transparent about uncertainty** (Intelligence layer) | Every stage carries a distribution, not a point; output reports intervals and a `confidence_flag`. |
| **Cold-start / pre-track-record** (30% eval note) | Missing features → wider distributions by construction; footprint features (2.6) + Founder Score carry the load; wide intervals are reported honestly, not hidden. |
| **Per-claim Trust Score** (25%) | SHAP attribution (3.3) + `evidence_trace` (6) link each output back to the features that drove it. |
| **Don't fabricate missing data** (Appendix / FAQ #9) | `DeterministicTerms` fall back to Thesis defaults and are surfaced in `assumptions_flagged`, never silently filled. |
| **Three axes not averaged** (FAQ #5) | Axis scores enter as separate conditioning features; the MC adds a return view *on top of* them, never replacing them. |
| **Fat-tailed VC returns** (methodological correctness) | Exit valuation (M6) uses an explicit Pareto tail, not a thin LogNormal, so outlier-driven returns aren't undervalued. |

---

## 8. Suggested implementation stack

| Layer | Library |
|-------|---------|
| Point models + native missing-value handling | LightGBM / XGBoost |
| Distributional regression (M1, M4, M6) | NGBoost |
| Survival / time-to-event (M2, M3) | lifelines (Weibull AFT) |
| Interval calibration (verification) | MAPIE / conformal prediction |
| Monte Carlo core | NumPy |
| Feature attribution | SHAP |

**Calibration note:** hold out a test set and verify that predicted intervals are calibrated
(an 80% interval should contain the truth ~80% of the time). A single reliability plot proving
the uncertainty is *real* is worth more to the judges than any point estimate.
