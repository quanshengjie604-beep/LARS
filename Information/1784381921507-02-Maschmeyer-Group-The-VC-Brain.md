In collaboration with MIT Club of Northern California and MIT Club of Germany

C H A L L E N G E

02

· · · · · · · · · · · · · · · · · · · ·

The VC Brain:

Deploying $100K Checks in 24 Hours

P O W E R E D   B Y

Maschmeyer Group — Investing in Exceptional Founders

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

1. Motivation / Goal to Achieve

Imagine running the world's largest Shark Tank for AI innovation. Thousands of
ambitious builders, any one of whom could be building the next Cursor, whose founders
met at an MIT hackathon before anyone knew to look. Your job: find them first, understand
what they're capable of, and back them before the rest of the world catches on.

Right now, that's nearly impossible. Founders stay invisible until they know the right
person. Their story is scattered across pitch decks, GitHub repos, half-built websites, and
social posts nobody's reading closely. Diligence takes weeks. Capital flows through
networks, not merit. By the time a fund finally sees a founder clearly, dozens of equally
strong ones have already given up waiting. Great ideas are dying not for lack of merit, but for
lack of a fair shot at being seen.

The fix has to work both ways. Some founders will be spotted first — for example through
a GitHub commit, a hackathon win, a paper worth a phone call. Others won't wait to be
discovered: a student with an idea, a first-time founder with zero connections, an engineer
who's never pitched anyone in their life, simply applying. Either way, the same thing happens
next: within 24 hours, they know if they just got $100K to build it. Not because of who they
know, but because of what the system already knows about them — a living profile of skills,
experience, and track record that gets sharper with every milestone — a credit score for
founders, the Founder Score. This is not just a narrative device: your system should actually
produce this score, and use it as one input into every investment decision. Ship something
once, and your next idea starts from a stronger position. The system never forgets, and it
never stops updating.

And you're not an outside observer here — most of you are exactly the founder profile this
system is built to find. Build it well, and you'll understand better than almost anyone what
actually gets noticed, and walk away with the system you wish had found you.

Ambitious Goal: Build a data- and AI-first operating system that transforms how venture
capital works — discovering exceptional founders, deploying capital at 100× speed, and
supporting the next generation of AI entrepreneurs. Identify and track potential founders and
teams, deploying $100K checks within 24 hours.

Done right, this doesn't just make venture capital faster — it makes it accessible: a founder's
potential becomes something the system can see and reason about directly.

The North Star — a fund that runs itself: The ultimate vision is an autonomous venture
fund spanning the entire lifecycle, with one human in the loop for oversight, not execution.
This challenge covers Sourcing → Screening → Diligence → Decision. Downstream stages
(portfolio monitoring, follow-on, fund ops, exit) are real but out of scope — don't spend
hackathon time designing UI for them.

2. Build the VC Brain (MVP)

The VC Brain is built on three transformative pillars that work in concert to give a single
investor the reach and analytical power of an entire organisation.

Sourcing — how do you find the best founders? The most important part of your MVP.

–  Surface the strongest founders before they formally begin fundraising.

–  Judged on data richness and smart sourcing ideas, not polish.

–  Least commercial competition today — go further here than anywhere else.

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 2

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

Assessment & Intelligence — the reasoning layer. Makes or supports every investment
decision.

–  Operates on top of Memory to produce insights, challenge assumptions, and

recommend next steps.

–  Triggered by an inbound application, or by signals crossing a conviction threshold on

their own.

–  Transparent about confidence, uncertainty, and the evidence behind every conclusion.

Memory — the data foundation. Nothing discarded.

–  Ingests pitch decks, interviews, launches, GitHub activity, and social traction.

–  Deduplicates, enriches, timestamps, and tags everything by source.

–  Houses the Founder Score — persists across applications, never resets.

–  Surfaces the trend over time, not just the latest snapshot.

VC Brain architecture: pipeline stages (Sourcing → Screening → Diligence → Decision) mapped onto the
Memory, Intelligence, and Experience layers.

The MVP should demonstrate:
1. Thesis Engine: Investor sets sectors, stage, geography, check size, ownership targets,

and risk appetite. Every recommendation is filtered and scored through this fund-specific
lens.

2. Smart Data Collection & Management: Actively collect, validate, and structure founder

and company data from heterogeneous sources — the data layer matters as much as
the intelligence built on top of it.

3. Multi-Attribute Reasoning: Move beyond keyword search. Support complex,

natural-language queries, e.g. “technical founder, Berlin, AI infra, enterprise traction, no
prior VC backing, top-tier accelerator.”

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 3

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

4. Inbound: Application & Automated Screening

–  Apply: deck + company name is the minimum bar; any further fields are the minimum

needed for a confident 24-hour decision.

–  Screen: a fast first-pass filter removes clearly non-viable ideas before full analysis

begins.

5. Outbound: Founder Identification & Activation

–  Identify: continuously scan GitHub, launches, hackathons, papers / patents, and

accelerator cohorts — scored the same way as an inbound application.

–  Activate: reach out to the strongest matches directly. Cold outreach, not cold

investment — the goal is to trigger a real application.

–  Converge: activated applications flow into the same Screening step as inbound, so

both tracks feed one funnel.

6. Multi-Axis Screening: Every opportunity is scored along three independent axes — not

averaged:

–  Founder: who they are, their traits and track record.

–  Market: sizing, competitors, SWOT — rated bullish, neutral, or bear.

–  Idea vs. Market: does the idea survive scrutiny as-is, or is the team strong enough to

pivot?

–  Each axis also shows trend (improving / declining / stable) and feeds back into

Memory to sharpen future scoring.

7. Evidence-Backed Investment Memos & Trust Score: Every claim — traction, revenue,
team background, market size — must trace to evidence with a confidence level: a Trust
Score. Verify externally where possible and flag contradictions before they reach the
investor.

8. Investor-Grade UX: Intuitive enough to use without technical support. Notion-level

approachability, Bloomberg-level analytical depth — clarity and usability are
non-negotiable.

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 4

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

End-to-end flow from inbound and outbound sourcing through 3-axis screening, diligence, and the final
investment decision.

3. Stretch Goals

We know that you can do it!

1. Agentic Traceability: Every recommendation must cite the exact data point — pitch deck

slide, web signal, or interview excerpt — that drove the conclusion. If the system
recommends a founder, it must show precisely what justified the Trust Score.

Hint: implement step-level chain-of-thought logging to visualise the full reasoning
process.

2. Self-Correction Loops: Implement a Validator Agent that cross-references extracted

founder claims against market databases, comparable funding rounds, and observable
evidence to ensure the primary agent is not hallucinating.

3. Sourcing & Network Intelligence: Model the sourcing graph — the network of programs,
institutions, and individuals through which promising founders become visible — and
track which channels historically produce the strongest opportunities. Proactively suggest
underexplored sourcing channels based on what has worked before, and once a founder
converts into a funded deal, feed that outcome back into the model so it learns which
channels generate quality, not just volume.

4. Areas of Research

Genuinely open problems. Solving them robustly could be industry-defining — document
your approach if you crack one.

1. Confidence Scoring: Founder data is messy and incomplete. Can prediction intervals be

built around soft-skill assessments like resilience or founder-market fit?

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 5

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

2. Data Quality vs. Volume: More data isn't always better — how do you decide what's

worth collecting vs. flagging as low-confidence?

3. Founder Traits & Success: How much can public footprints (Twitter, LinkedIn) predict

founder success — and how would you test it?

5. Hints and Resources

Data
No dedicated dataset is provided. Bring or synthesise your own founder and company data
— public web data (Crunchbase, LinkedIn, GitHub, ProductHunt, Hacker News, arXiv,
patents), synthetic founder profiles with seeded contradictions, or anonymised / fictional
pitch decks. Quality of ingestion matters more than dataset size.

Investment Memo — see Appendix 1: A sample checklist for the structure and depth
expected — not a template to fill in mechanically.

6. Evaluation Criteria

Data Architecture and Intelligence (30%): How well does the system collect, structure,
and manage founder data? We look for smart ingestion, deduplication, enrichment, and a
reasoning layer that is honest about what it knows and what it does not.

Note: generic ingestion / enrichment quality alone will not score highly here if it doesn't
address the cold-start, pre-track-record case.

Intelligent Analysis and Trust (25%): How effectively does the solution synthesise
fragmented signals into decision-ready insights? Do the memo's Trust Scores surface
evidence and uncertainty transparently?

Investment Utility & Execution (30%): Does the tool produce a recommendation a human
investor could genuinely act on within 24 hours? This includes any progress on
instrumenting how fast and reliably an opportunity moves from first signal to decision. Could
it transform the speed and quality of capital allocation?

User Experience and Design (15%): Is the interface intuitive, clear, and beautifully
designed? Does it make complex AI reasoning feel effortless and trustworthy for a
non-technical investor?

7. Why It Matters

Not just another investment tool: transformative infrastructure for the venture capital
industry.

•  Today, capital flows to who you know — not what you're building.
•  A system that surfaces exceptional founders before anyone else sees them could

reshape how the world's most important companies get their start.

•  You are building infrastructure for Equitable Capital Allocation — turning a

relationship-gated landscape into a living intelligence network that knows who the next
Cursor is, and ensures they get funded.

Build the data layer. Build the reasoning layer. Build the experience. Build the
infrastructure that gives exceptional founders the capital to begin.

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 6

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

Appendix 1 — Investment Memo

Length rule: As detailed as the decision requires, as brief as clarity allows. Length is not a
proxy for rigor — padding counts against you.

How to use this checklist:
The sections below represent what a complete investment memo should cover. For this
challenge, treat Company snapshot, Investment hypotheses, SWOT, Problem & product,
and Traction & KPIs as required. Some sections — particularly Financials & round structure,
Cap table, and anything involving internal company data — depend on information a real
fund would treat as confidential or simply might not have at the time of drafting. You are not
expected to fabricate this data convincingly. Where a data point is missing, unavailable, or
intentionally left out, it must be explicitly flagged in the memo (e.g. “Cap table: not disclosed”
or “Customer references: unavailable at this stage”) rather than silently omitted or guessed.
A memo that clearly marks its own gaps is more trustworthy — and more useful to an
investor — than one that fills them in invisibly.

Section

Should include

Company snapshot

One-paragraph “in a nutshell”: market size, the structural problem, why
it's urgent, and how the product solves it

Investment hypotheses

The explicit “why we want to invest” bullets — team quality, market
wedge, stickiness / retention mechanics, traction signal, defensibility,
expansion path

SWOT

Team & history

Strengths, weaknesses, opportunities, risks — each as short,
evidence-backed bullets

Founder background, exec team pedigree, why the fund is comfortable
with any red flags (e.g. single-founder), company timeline from founding
to today

Problem & product

The core problem(s) in plain language, then the step-by-step product /
process solving it

Technology & defensibility  What's proprietary vs. commoditizable, the data moat, model /
architecture choices, why the advantage compounds over time

Market sizing

Competition

Traction & KPIs

Top-down and / or bottom-up TAM / SAM / SOM, with the assumptions
stated explicitly

Named competitor clusters, how each differs from the company, and who
could become a threat later

Customer count, ARR / revenue, growth trajectory, unit economics (CAC,
sales cycle, churn), usage metrics (e.g. DAU)

Financials & round
structure

Historical + projected P&L (revenue, EBITDA, opex, COGS), round size,
runway, next-round timing

Cap table

Due diligence log

Pre- and post-round ownership by party, dilution assumptions, VSOP
allocation

What was checked (commercial, people, financial, legal, technical),
what's still open

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 7

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

Section

Should include

Exit perspective

Plausible exit paths (strategic acquirers, PE roll-up, category
comparables) and why they'd pay a premium

FAQ

1. What does a winning sourcing layer actually do that a filtered list doesn't?
It surfaces founders before they start fundraising. Outbound scans GitHub, launches,
hackathons, papers/patents, and accelerator cohorts, scores them like an inbound
application, and triggers real outreach. The brief flags this as the area with the least
commercial competition — go further here than anywhere else.

2. If we can only nail one thing, sourcing or the reasoning layer, which wins?
Sourcing carries the most weight (Data Architecture 30%) and is explicitly the priority. But a
rich data layer with no honest reasoning on top scores poorly too. Build sourcing deep, then
a thin-but-transparent Intelligence layer over it — not a polished reasoner over shallow data.

3. Is there a provided dataset?
No. Bring or synthesize your own — public web data (Crunchbase, LinkedIn, GitHub,
ProductHunt, Hacker News, arXiv, patents), synthetic profiles with seeded contradictions, or
fictional decks. Ingestion quality beats dataset size.

4. What's the minimum application input?
Deck + company name. Add fields only if genuinely needed for a confident 24-hour decision.
Over-collecting works against you.

5. Can we just average the three screening axes into one number?
No — the brief says explicitly not averaged. Founder, Market, and Idea-vs-Market are scored
independently, each with a trend direction, and each feeds the memo separately. Collapsing
them hides exactly the disagreement an investor needs to see.

6. Founder Score vs. the 3-axis score — aren't these the same thing?
No. The Founder Score lives in Memory, persists across applications, and never resets — it
follows the person across different startups over time. The 3-axis score is per-opportunity.
The Founder Score is one input into the Founder axis, not a substitute for it.

7. Is the Trust Score one number for the company?
No — it's per claim. Each assertion (traction, revenue, team, market size) traces to evidence
with a confidence level, verified externally where possible, with contradictions flagged before
they reach the investor.

8. Which memo sections are actually required?
Company snapshot, Investment hypotheses, SWOT, Problem & product, Traction & KPIs.
The rest are welcome but optional — and padding counts against you.

9. What do we do when real financials or cap table data don't exist?
Don't fabricate. Flag the gap explicitly ("Cap table: not disclosed"). A memo that marks its
own gaps is scored as more trustworthy, not less.

10. How do you score a first-time founder with no GitHub, no funding, no network?

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 8

THE VC BRAIN · MASCHMEYER GROUP × Hack-Nation · Challenge Brief

This is the cold-start case, and the brief warns that generic ingestion/enrichment won't score
highly if it ignores it. You need an explicit method for pre-track-record founders — otherwise
you've just rebuilt the network-gated system the challenge exists to replace. Don't leave this
as an afterthought.

11. Is there a way to push the cold-start problem further, beyond just handling it
competently?
Yes — worth attempting Area of Research 3: how much can public footprints (Twitter,
LinkedIn) actually predict founder success, and how would you test it. It's framed as
genuinely open, but it's also the most direct lever on the cold-start weakness in Q10 — a
founder with no funding or GitHub history often still has a public footprint. Teams that take a
real stab at this, even partially, are documenting exactly the kind of approach the brief says
could be industry-defining.

12. How natural-language does Multi-Attribute Reasoning need to be?
Beyond keyword/filter search. It should resolve a compound query like "technical founder,
Berlin, AI infra, enterprise traction, no prior VC backing, top-tier accelerator" in one pass —
not as five manual filters.

13. Which stretch goal is worth it if we only have time for one?
Agentic Traceability — citing the exact data point (deck slide, web signal, interview line)
behind each conclusion. It directly reinforces the core Trust Score requirement, so it's the
highest-leverage add.

14. How much does UI polish matter under time pressure?
UX is 15% — real, but the smallest slice. "Notion-level approachability, Bloomberg-level
analytical depth," usable without technical support. If forced to trade, protect the data and
reasoning layers (55% combined) first.

15. Should the Thesis Engine be hardcoded to one fund, or configurable?
Configurable. The investor sets sectors, stage, geography, check size, ownership targets,
and risk appetite, and every recommendation is filtered and scored through that lens. A
hardcoded thesis misses the point of the pillar.

16. Do we need to build toward the "fund that runs itself"?
No — that's ambition framing, not a deliverable. The scope is Sourcing → Screening →
Diligence → Decision. The brief explicitly says don't spend hackathon time on downstream
stages (monitoring, follow-on, fund ops, exit).

Hack-Nation × MIT Club of Northern California × MIT Club of Germany  ·  6th Global AI Hackathon  ·  Page 9

