# Data Acquisition & Scoring Plan for the NGBoost Decision Engine

Scope: source the fields in [`ngboost-data-requirements.md`](../requirements/ngboost-data-requirements.md) from the public web, and
define the scoring formulas that [`vc-brain-mc-pipeline.md`](../architecture/vc-brain-mc-pipeline.md) leaves unspecified (the three
screening axes, their trends, and the derived 0–1 signals).

---

## 0. TL;DR strategy

1. **The real constraint is point-in-time, not access.** Scraping returns *today's* state.
   Training data (`training_features.jsonl`) requires **frozen historical snapshots with no
   future leakage** (§10.1). So training and inference use *different* source strategies:
   inference = scrape-now; training = reconstruct-from-timestamped-history + archives.
2. **LinkedIn and Crunchbase forbid scraping in their ToS** (LinkedIn especially — it
   litigates). Do **not** build brittle HTML scrapers against them for a deliverable. Use
   official/vendor APIs, permissible open sources, and archives; fill gaps with **synthetic
   seeded data**, which the challenge brief explicitly endorses ("bring or synthesise your
   own… ingestion quality beats dataset size", FAQ #3).
3. **Never fabricate a value** (§10.3, §10.5). Missing → `null` + entry in `missing_fields`.
   Derived scores are allowed, but every derived score must carry its formula + inputs as
   evidence with `verification_status: estimated`.

---

## 1. Source → field mapping

Legend for how we get each source:
`API` = official/vendor API · `OPEN` = freely queryable open data · `ARCHIVE` = point-in-time
reconstruction · `LLM` = model-derived from collected text (estimated) · `SYNTH` = synthetic
seeding for training volume.

| Field group (requirements §) | Primary sources | Method | Point-in-time notes |
|---|---|---|---|
| **Identifiers / dates** (§3) | Assigned internally | — | We mint `startup_id`, `snapshot_id`; `observation_date` = the snapshot's cutoff |
| **Traction / KPI** (§4.2): arr, growth, customers, churn | Pitch decks, founder interviews/application, company blog, press | LLM extract from decks/text | Almost never public historically → mostly `null` for training unless in decks; **do not impute** |
| **Funding / stage** (§4.3): stage, last_round_size/date, total_funding | Crunchbase `API`, SEC EDGAR Form D `OPEN`, Dealroom/PitchBook `API`, press releases | `API`+`OPEN` | **Cleanly timestamped** → best point-in-time source. Filter rounds to `≤ observation_date` |
| burn_rate, runway, cash_balance | Founder application/deck only | LLM extract | Rarely disclosed → expect `null` |
| **Team / defensibility** (§4.4): prior_exits, prior_startups, single_founder, team_size, technical, industry_years | LinkedIn (via `API` vendor: Proxycurl/People Data Labs/Coresignal), Crunchbase people, GitHub | `API` | LinkedIn = current CV; reconstruct history from dated role entries. team_size = headcount **as of** obs date (LinkedIn employee-count history / Coresignal) |
| patent_count, proprietary | Google Patents / USPTO PatentsView `OPEN`, Lens.org `API` | `OPEN` | Patents have filing dates → filter `≤ observation_date` |
| open_source_activity | GitHub `API` (REST/GraphQL) | `API` | Commit/star history is dated → point-in-time exact |
| **Market** (§4.5): tam | Analyst reports, decks, sector databases | LLM + `manual_research` | Estimate; record assumptions (§4.5 mandates this) |
| competitor_density, sector, geography | Crunchbase categories `API`, deck | `API` | sector/geo normalized to our taxonomy |
| funding_climate_index | Crunchbase/PitchBook aggregate deal volume, NVCA/CB Insights reports | `API`+`OPEN` | Computed **per quarter** → naturally point-in-time |
| **Cold-start footprint** (§4.6): public_footprint, network_centrality, soft_skill | Twitter/X `API`, GitHub, arXiv/Semantic Scholar `API`, HN Algolia `OPEN`, ProductHunt, personal sites | `API`+`LLM` | Footprint at obs date via archive/snapshot counts; **low confidence by design** |
| **Outcome labels** (§5) | Crunchbase `API`, SEC EDGAR (S-1/8-K), press, OpenCorporates `API`, Wayback | `API`+`OPEN`+`ARCHIVE` | Collected **after** obs date — this is where future info is *required* |

### Concretely usable, hackathon-friendly sources
- **Free / open:** SEC EDGAR (Form D funding, S-1 IPOs, 8-K acquisitions), USPTO PatentsView,
  GitHub API, Hacker News (Algolia API), arXiv + Semantic Scholar API, OpenCorporates (company
  status → failure signal), Wayback Machine CDX API (archival snapshots), Wikidata/Wikipedia
  (exits), ProductHunt API.
- **Vendor APIs (free tiers / trials, ToS-clean):** Crunchbase Basic API, Dealroom, People Data
  Labs, Coresignal, Proxycurl (LinkedIn-derived data *without* you scraping LinkedIn).
- **Do NOT:** headless-browser scrape LinkedIn or Crunchbase HTML. It violates ToS, breaks
  constantly, and is not defensible in a demo.

---

## 2. Collection architecture

```
  Resolver → Fetchers (per source) → Extractor (LLM) → Normalizer → Point-in-time filter
     │              │                      │               │               │
  entity ID     raw docs            structured fields    units/enums   drop > cutoff
     └──────────────┴──────────── evidence_registry.jsonl (every doc) ───────────────┘
                                          │
                          Feature assembler → training_features / inference_requests
                          Outcome tracker  → training_outcomes  (post-cutoff only)
```

1. **Entity resolution.** Seed with company name + founder name (the min application input,
   FAQ #4). Resolve to canonical IDs across sources (Crunchbase permalink, LinkedIn URN via
   vendor API, GitHub login, domain). Dedup by domain + normalized name. Store crosswalk.
2. **Fetchers.** One adapter per source, each returning `(payload, document_date, source_uri)`.
   Rate-limit and cache raw responses to disk (they *are* the evidence).
3. **Extractor.** LLM (Claude) with a strict JSON schema per field group; every extracted value
   must return `{value, source_id, excerpt, confidence}`. No excerpt → drop the value.
4. **Normalizer.** Money→USD, rates→fractions, dates→`YYYY-MM-DD`, sector/geo→taxonomy,
   scores→defined 0–1/0–100 range (§10.4).
5. **Point-in-time filter (training only).** Discard any evidence with `document_date >
   data_cutoff_date`; recompute all derived scores using only surviving evidence.
6. **Evidence-first write.** Every fetched document → one `evidence_registry.jsonl` line with
   `source_type`, `document_date`, `collected_at`, `verification_status`, `confidence` per §6.
   Contradictions between sources → keep both + add to `contradicted_fields` (§6, never pick
   the favorable one).

### `verification_status` assignment rule
- SEC/USPTO/regulatory filing → `independently_verified`
- Crunchbase/vendor API cross-confirmed by ≥2 sources → `document_verified`
- Single API or press → `document_verified` (single) else `unverified`
- Founder deck/interview only → `founder_reported`
- Any of our computed axes/scores → `estimated`
- Conflicting sources unresolved → `contradicted`

---

## 3. Point-in-time reconstruction (the leakage problem)

For inference this is trivial (`observation_date = today`, scrape current state). For **training**,
each snapshot must contain *only* what was knowable on its date (§10.1). Techniques:

- **Timestamped events (best).** Funding rounds, patents, GitHub commits, papers, press all
  carry dates → simply filter to `≤ data_cutoff_date`. `total_funding_to_date` = sum of rounds
  before the cutoff only. `team_size` = headcount at that date (employee-count history).
- **Archives.** Wayback CDX for old company-site copies (ARR/customer claims as-of), old team
  pages (team_size), dead-site detection for failure.
- **Multiple snapshots per company (§1.1).** Generate one snapshot per known stage transition
  (e.g. post-Seed, post-A). Each gets a unique `snapshot_id` = `{startup_id}_{date}_{stage}`.
- **What we can't reconstruct → `null`.** Historical ARR/burn are usually irreconcilable
  from public data. Leave `null`, list in `missing_fields`. Better a wide distribution than a
  fabricated one (the pipeline widens distributions on missing input by design).
- **Balance the label set (§10.2).** Deliberately include failures (OpenCorporates dissolved
  status, dead sites + confirmation), stalled companies (no follow-on round), and censored
  still-alive companies — not just winners. Otherwise the model overestimates success.

---

## 4. Outcome-label collection (§5, post-cutoff)

| Label | Source | Rule |
|---|---|---|
| next_round_* (M1) | Crunchbase/Dealroom rounds, SEC Form D | First round strictly after obs date; `progressed_within_24_months` from date delta; `null` if right-censored |
| next_event / time_to_event (M2) | earliest of round/IPO/acq/secondary | Censored → `none_observed`, `next_event_observed=false`, duration to `outcome_observation_end_date` |
| failure (M3) | OpenCorporates status, insolvency registers, confirmed shutdown press; **dead site alone ≠ failure** (§5.3) | Require a corroborating signal beyond a 404 |
| growth (M4) | ≥2 comparable ARR/revenue points | Only for records with two consistent-definition observations; else exclude from M4 subset |
| exit_type / date (M5) | SEC S-1 (IPO), 8-K / press / Wikidata (acq) | `no_exit_observed` default; `exit_verified` if from filing |
| exit_valuation (M6) | S-1 market cap, disclosed deal value, press | Preserve `exit_value_type` exactly; undisclosed → `null` but keep `exit_value_disclosed=false` |
| censoring (§5.7) | — | Every record: `outcome_observation_end_date` (= today, 2026-07-18), `company_still_observed` |

---

## 5. Deterministic formulas for the unspecified elements

The requirements demand the three axes + several 0–1 signals but **do not define how to
compute them**. Below is a deterministic, evidence-traceable specification. All use these
helpers:

```
clamp(x, lo, hi)          = max(lo, min(hi, x))
nlog(x, lo, hi)           = clamp((log10(max(x,1)) - lo) / (hi - lo), 0, 1)   # log-scale money/counts
sat(x, k)                 = clamp(x / k, 0, 1)                                 # saturating count
```
Any input that is `null` is **dropped from its weighted average and the remaining weights are
renormalized** — a missing input widens uncertainty, it does not score as zero. Record the
formula id + inputs used as `estimated` evidence, and lower `confidence` in proportion to how
many inputs were `null` (drives `confidence_flag`).

### 5.1 Founder Score — persistent, 0–100 (Memory, follows the person)
```
components (each in [0,1]):
  s_exits    = sat(founder_prior_exits, 2)                 # 2+ exits saturates
  s_startups = sat(founder_prior_startups, 3)              # prior attempts, even failed
  s_exp      = sat(founder_industry_experience_years, 10)
  s_tech     = technical_founder_flag ? 1 : 0
  s_pedigree = rubric 0–1 from top school / FAANG-tier employer / notable prior co (LLM-scored)
  s_footprint= public_footprint_score
  s_network  = network_centrality

FS = 100 * Σ wᵢsᵢ / Σ wᵢ (present only)
weights: exits .25, startups .10, exp .15, tech .10, pedigree .15, footprint .15, network .10
```
This is the input to — not a substitute for — the Founder **axis** (FAQ #6).

### 5.2 Founder axis — per-opportunity, 0–100
```
fmf        = founder-market fit 0–1 = overlap(industry_experience, sector) (LLM rubric)
team_adeq  = sat(team_size, 8)                    # ~8 = healthy seed team
soft       = soft_skill_estimate
single_pen = single_founder_flag ? 8 : 0          # red-flag penalty (points)

founder_axis = clamp(
    0.45*FS + 0.25*100*fmf + 0.15*100*team_adeq + 0.15*100*soft - single_pen, 0, 100)
```

### 5.3 Market axis — enum {bull, neutral, bear} via continuous m
```
tam_norm    = nlog(tam_usd, 8, 12)                # $100M→0 … $1T→1
climate     = funding_climate_index
crowd       = 1 - competitor_density

# sam/som dropped from the handover schema → growth_room term removed, weights renormalized
m = 100*(0.45*tam_norm + 0.30*crowd + 0.25*climate)

market_axis = bull    if m ≥ 66
              bear    if m ≤ 40
              neutral otherwise
```

### 5.4 Idea-vs-Market — 0–100 ("survives as-is, or team strong enough to pivot")
```
traction_sig = 0.5*sat(revenue_growth_rate_yoy, 1.0) + 0.5*(1 - clamp(churn_rate_annual/0.4,0,1))
pivot_cap    = founder_axis/100                    # strong team can pivot

# sam/som dropped from the handover schema → fit term removed, weights renormalized
idea_vs_market = 100*(0.35*proprietary_score + 0.20*(1-competitor_density)
               + 0.25*traction_sig + 0.20*pivot_cap)
```

### 5.5 Trends — enum {improving, stable, declining, unknown}
Compare current axis value `v_t` to the previous snapshot `v_{t-1}` for the same startup:
```
if no prior snapshot:            unknown
Δ = (v_t - v_{t-1}) / range      # range = 100 for float axes; map enums to {bear/decl=0, neutral/stable=50, bull/impr=100}
improving  if Δ >  0.05
declining  if Δ < -0.05
stable     otherwise
```

### 5.6 Derived 0–1 signals
```
open_source_activity_score = mean( nlog(stars,1,4), nlog(forks,0,3), sat(contributors,20),
                                    sat(commits_last_90d, 200) )      # GitHub API
proprietary_score          = 0.4*sat(patent_count,5) + 0.3*open_source_activity_score
                             + 0.3*rubric_defensibility(0–1)          # LLM over tech description
public_footprint_score     = mean( nlog(twitter_followers,2,6), nlog(github_followers,1,4),
                                    sat(paper_count,10), sat(hn_ph_mentions,20) )
competitor_density         = sat(n_competitors_in_category, 50)       # Crunchbase category count
funding_climate_index      = clamp(sector_deals_in_quarter / max(trailing_8q_quarterly_deals), 0, 1)
soft_skill_estimate        = (llm_rubric_1_to_5 - 1) / 4              # low confidence by mandate
network_centrality         = normalized eigenvector centrality in the sourcing graph
                             (nodes: founders/accelerators/co-investors/co-authors; see §6)
```

All of the above are `estimated`. `soft_skill_estimate`, `network_centrality`, and
`public_footprint_score` should default to `confidence: low` (§4.6).

---

## 6. Sourcing graph (for network_centrality + stretch goal 3)

Build a graph: nodes = founders, companies, accelerators, universities, investors, paper
co-authors, GitHub collaborators; edges = "attended / backed by / worked-with / co-authored".
Compute eigenvector (or degree) centrality per founder, min-max normalize to 0–1. Doubles as
the challenge's "Sourcing & Network Intelligence" stretch goal.

---

## 7. Execution phases (mapped to requirements §11 priorities)

**Phase 1 — MVP inference path (scrape-now, one live company end-to-end).**
Entity resolve → GitHub + Crunchbase API + Twitter + patents + SEC → extract → normalize →
compute all §5 scores → emit one `inference_requests.jsonl` + full `evidence_registry.jsonl`.
Covers requirements Priority 1.

**Phase 2 — Historical training set (point-in-time).**
Pull a cohort of companies with **known outcomes** (some raised, some failed, some stalled —
§10.2). For each, reconstruct snapshots at prior stages using timestamped events + Wayback;
collect outcome labels (§5). Emit `training_features` + `training_outcomes`. Priority 2.

**Phase 3 — Growth/exit enrichment + synthetic augmentation.**
Add M4/M6 records where two revenue points and disclosed valuations exist (Priority 3). Where
real coverage is thin, generate **synthetic** startups with seeded contradictions to exercise
the pipeline and the Trust Score — clearly tagged `source_type: other`, never mixed into
verified evidence.

---

## 8. Key risks & mitigations

| Risk | Mitigation |
|---|---|
| LinkedIn/Crunchbase ToS + anti-scraping | Use vendor APIs (Proxycurl/PDL/Coresignal/Crunchbase API), not HTML scrapers |
| Future-info leakage into training features | Hard `document_date ≤ cutoff` filter; recompute scores post-filter; audit sampled snapshots |
| Survivorship bias | Explicitly seed failures + stalls + censored cases; report class balance |
| Fabricated / imputed values | `null` + `missing_fields`; renormalize weights; every score carries formula-as-evidence |
| Private KPIs (ARR/burn/churn) mostly unavailable | Accept high null rates; lean on cold-start footprint features; let distributions widen |
| Estimated scores over-trusted | `verification_status: estimated`, low `confidence`, propagate to `confidence_flag` |

---

## 9. Event-based time-series collection

We do **not** snapshot on a fixed cadence. We build each company's timeline as a sequence of
**events**; each event is an *outcome*, and the company **state at that moment** (financials +
footprint known on or before the event date) is the *input*. News is naturally timestamped, so
it is a first-class point-in-time source here, not just enrichment.

### 9.1 Event taxonomy → source
| Event (outcome) | Source |
|---|---|
| Funding round | SEC Form D, Crunchbase/Dealroom API, press |
| Product launch / release | ProductHunt, GitHub releases, press |
| IPO / acquisition / secondary | SEC S-1 / 8-K, press, Wikidata |
| Failure / shutdown / layoffs | OpenCorporates status, layoffs.fyi, confirmed shutdown press (dead site alone ≠ failure) |
| Revenue/valuation disclosure | News + financial-data vendors (below) |

Merge events from all sources per company, dedup, sort by date → one snapshot per event.

### 9.2 Financial-value-at-time sources — **Tier 1 (has coverage)**
Defined-site crawl; predictable and cacheable at scale.
- **News:** Reuters, Bloomberg, TechCrunch, The Information, Sifted (EU), FT, WSJ, Forbes,
  Business Insider, Fortune Term Sheet, Axios Pro Rata.
- **Filings (verified, free):** SEC EDGAR — Form D, S-1, 8-K, 10-K/10-Q.
- **Private-company financial time series:** **Latka** (SaaS ARR over time), **Growjo**
  (revenue + headcount estimates), Sacra, Tracxn, CB Insights, PitchBook, Dealroom.
- **News-as-search-API:** **AlphaSense**, **Tegus/BamSEC** — search engines over business
  content + transcripts, API-accessible.

### 9.3 Long-tail proxies — **Tier 2 (no press)**
Direct financials stay `null` (never impute). Collect timestamped proxies as the state vector:
headcount-over-time (Coresignal/PDL), web traffic (SimilarWeb/Semrush), app installs (Sensor
Tower/data.ai), hiring velocity, GitHub activity, ProductHunt, G2/Capterra review counts,
**Wayback** snapshots of the company's own pricing/customers page.

### 9.4 Search-engine route — use search **APIs**, never scrape SERPs
Scraping Google/Bing HTML violates ToS and gets blocked. Prefer APIs that **return page
content inline** to skip a second fetch:
| API | Fit |
|---|---|
| Exa | Neural search + cleaned content; best for intent queries ("company X revenue 2023") |
| Tavily | Built for LLM agents; returns extraction-ready content |
| Serper.dev / SerpAPI | Cheap Google-SERP-as-API when you need the ranked link list |
| Brave / Bing Web Search | General fallback, decent free tiers |
| Google Programmable Search (CSE) | ToS-clean Google results, low quota |

### 9.5 Parallelization (I/O-bound → near-linear until rate limits)
1. **Async, not threads:** `asyncio` + `httpx`/`aiohttp`, hundreds in-flight; per-API
   `Semaphore` caps to each provider's rate limit.
2. **Two-stage pipeline, no barrier:** search→URLs, then fetch+extract; company B searches
   while A fetches. Prefer Exa/Tavily to collapse both stages.
3. **Dedup + content-addressed cache:** one article covers many companies → fetch each doc
   once per run; cache search results so reruns are free.
4. **Distribute at scale:** Redis-backed queue (RQ/Celery) or bounded `asyncio.gather`;
   exponential backoff + jitter on 429s.
5. **Hybrid to control cost:** defined-site crawl for Tier 1 (cheap, cacheable); reserve paid
   search-API calls for the Tier-2 tail.

### 9.6 Point-in-time carry-forward rule
At event `T`, the state carries the **most recent reported value with `document_date ≤ T`**
(last-known-value), tagged with its age. Legitimate evidence, not imputation — but staleness
lowers `confidence` so an old figure never masquerades as current.
```
