# Airport Investment Intelligence Agent: Design

**One line:** a chat agent that helps investment analysts screen US airports for terminal-expansion opportunities. **Code computes every number** from public FAA/BTS data with a transparent scoring model. **The LLM** picks the analysis, explains the results, and states the assumptions and uncertainty.

---

## 1. Problem framing and scope

The firm earns its return when passenger growth fills new terminal capacity. A strong candidate airport therefore has:
1. **Demand that is growing.**
2. **Existing capacity that is already full** (high load factors, demand outrunning seats).
3. **Visible strain** (taxi-out queues, peak-hour pressure, system delays).
4. **A revenue base large enough to pay back the investment** (scale, pricing power).
5. **No hard constraint that blocks growth anyway.** Examples: a legal passenger cap, a runway bottleneck, no land.

The output is a **screening priority for further diligence**, not an estimate of returns. The score shows where terminal pressure looks strongest in public data. It does not prove that the terminal (rather than runways, airspace or a legal cap) is the binding constraint, or that terminal investment would capture the demand. Every ranked airport therefore carries a **bottleneck check** (§4.1).

**In scope**
- US commercial-service airports.
- Public, free data only.
- Screening and comparison, not valuation.

**Out of scope (stated in the UI and the answers)**
- Construction costs, financing, and concession contracts.
- Gate counts and terminal square footage. These are published per airport in master plans (for example, SFO's Airport Development Plan has a gate analysis), but there is no standardized national API, so they are not integrated. They are the next diligence step.
- Committed projects, airline commitments and who captures the incremental revenue.
- Investment advice.

---

## 2. Architecture

The system is a **modular monolith with ports-and-adapters**, not microservices. For a one-team, one-day tool, microservices add network hops and deployment overhead without any independent-scaling benefit. Clear module boundaries give the same explainability, and each module can be extracted later. The only real **process** split is where workloads genuinely differ:

| Process | Workload | Why separate |
|---|---|---|
| **Ingestion job** (`app/data/ingest.py`) | Batch ETL from slow government sources (minutes, ~400 MB) | Must never run on the request path |
| **API + agent** (FastAPI) | Interactive, sub-second analytics + LLM calls | Reads the local replica only |
| **Frontend** (React) | UI | Built into the same container for one-command deployment |

```mermaid
flowchart LR
  subgraph Sources["Public sources"]
    FAA[FAA enplanements xlsx]
    T100S[BTS T-100 summary<br/>Socrata API]
    T100[BTS T-100 segments<br/>TranStats form]
    FARE[BTS Airfare Table 1a<br/>Socrata API]
    OT[BTS On-Time PREZIP]
    OA[OurAirports CSV]
  end
  subgraph Live["Live APIs"]
    NAS[FAA NAS Status]
    MET[aviationweather METAR]
  end
  Sources --> ING[Ingest job<br/>validate + normalize] --> SNAP[(Parquet snapshot<br/>+ manifest)]
  SNAP --> REPO[Repository<br/>DuckDB]
  REPO --> AN[Analytics<br/>pure functions]
  AN --> SVC[InsightService]
  NOTES[Curated notes YAML] --> SVC
  Live --> SVC
  SVC --> TOOLS[Tool registry<br/>Pydantic-validated]
  TOOLS <--> RT[Agent runtime loop]
  RT <--> LLM[LLM chain<br/>Claude -> Gemini -> (Groq if keyed) -> offline]
  RT --> API[FastAPI SSE] --> UI[React chat + evidence panel]
```

**Layers** (`backend/app/`). Dependencies point one way only, downward.

| Layer | Responsibility | Has I/O? |
|---|---|---|
| `data/connectors/*` | One adapter per external source; shared retry/backoff HTTP client | yes |
| `data/ingest.py`, `validation.py` | Fetch, validate against contracts, write Parquet + `manifest.json`. A failed source keeps its last good snapshot. | yes |
| `data/repository.py` | The only place that knows about storage (DuckDB over Parquet) | read-only |
| `analytics/*` | Features, scores, spill model, rank stability. **Pure, deterministic, unit-tested.** | no |
| `services/insights.py` | Business logic: combines analytics, provenance, assumptions and curated notes | no |
| `agent/tools/*` | LLM-facing contract: argument schemas, validation, UI evidence blocks | no |
| `agent/runtime.py` | The loop: context, LLM, tool calls, grounding check | no |
| `llm/*` | Provider adapters + resilience (retry, failover, offline planner) | yes |
| `api/routes.py` | HTTP/SSE. Also exposes `/api/rankings`, which uses no LLM at all. | yes |

**Why "read from a replica":** the statistical APIs are slow, rate-limited and sometimes down. Answers come from a validated local snapshot, which makes them fast, reproducible and resilient. Only genuinely real-time questions call live APIs. The snapshot is committed (~6 MB), so the demo works offline.

---

## 3. Data sources (all free, no signup)

| Source | Access | Period in snapshot | Used for |
|---|---|---|---|
| FAA Passenger Boarding (Enplanement) data | xlsx | CY2019, CY2022-CY2025 (2025 preliminary) | Scale, growth, recovery vs 2019, hub class |
| BTS T-100 Segment Summary by Origin Airport (`r495-tyji`) | Socrata API | Jan 2019 - Aug 2026 | Passengers, seats, load factor, trailing-12-month (TTM) growth |
| BTS T-100 Segment, all carriers (incl. foreign + cargo) | TranStats form POST | Jan 2025 - Aug 2026 | Route distances, passenger vs freighter, route load factors |
| BTS Consumer Airfare Report Table 1a (`tfrh-tu9e`) | Socrata API | 2023 Q1 - 2026 Q1 | Fare premium, carrier concentration |
| BTS On-Time Performance (reporting carriers) | Monthly zips, aggregated at ingest | Oct 2025 - Sep 2026 | Taxi-out, delays, delay causes, peak-hour movements |
| OurAirports | CSV | current | Names, coordinates, runways >= 4,000 ft |
| FAA NAS Status | live JSON API | real time | Ground stops / delay programs, arrival rate, runway config |
| aviationweather.gov METAR | live JSON API | real time | Flight category (VFR/IFR), ceiling |
| Curated notes (`knowledge/airport_notes.yaml`) | hand-curated, cited | reviewed 2026-11 | Constraints data can't see: SNA passenger cap and curfew, SFO runway spacing, ANC cargo role |

Every number the agent shows carries its **source and period**.

**Joining the sources (a real data-engineering problem here):**
- All tables are keyed on IATA code. FAA files use FAA location ids, which usually match IATA but not always (Glacier Park: GPI vs FCA; State College: UNV vs SCE), so ingest bridges them through OurAirports, which carries both.
- Code changes are reconciled too: Palm Beach PBI became DJT in 2026, so older rows are mapped forward and the airport doesn't split in two.
- Territories (San Juan, Guam, Saipan, USVI) are included, so they have reference data like everyone else.

**Data coverage gaps (stated, not hidden):**
- Fares (Table 1a) cover only the largest domestic markets, so the fare premium is missing for about 40% of peers. Examples: Hawaii, Alaska, Puerto Rico and several small airports.
- On-time data covers only "reporting carriers". Its share of an airport's domestic departures ranges from ~2% (Bethel, AK) to ~90%. Where that share is below 20%, the on-time rates come from an unrepresentative sample and are dropped.

---

## 4. Scoring methodology (deterministic)

### 4.1 Terminal Expansion Opportunity Score (0-100)

`config/scoring.yaml` holds all weights; nothing is hard-coded.

| Pillar (weight) | Metrics (weight within pillar) | Rationale |
|---|---|---|
| **Demand momentum (30%)** | Enplanement CAGR CY22-25 (35%), passenger growth TTM vs prior TTM (35%), CY2025 vs CY2019 (30%) | Is demand growing, both recently and structurally? |
| **Capacity utilization (25%)** | Load factor TTM (60%), passenger growth minus seat growth (40%) | Is current capacity full? Is demand outrunning supply? |
| **Operational strain (20%)** | Avg taxi-out (35%), daily peak-hour movements (25%), departures delayed 15+ (20%), NAS delay per arrival (20%) | Is the facility visibly stressed? |
| **Revenue potential (25%)** | log enplanements (60%), fare premium vs distance-adjusted US norm (40%) | Will the investment pay back (scale, pricing power)? |

**Computation**
1. **Peer universe:** 233 US airports with at least 100k annual enplanements.
2. **Normalization:** each metric becomes a **percentile rank** against the peer universe. Percentiles are robust to outliers (e.g. HVN grew 15x vs 2019 after a new airline base), unit-free, and easy to explain ("82nd percentile"). The trade-off is that magnitude is lost, so raw values are always shown next to percentiles.
3. **Aggregation:** pillar score = weighted mean of its metric percentiles; total score = weighted mean of pillar scores.
4. **Missing data is neutral.** A missing metric is scored at the median (50th percentile), so a data gap is neither rewarded nor punished.
   - The obvious alternative, re-normalizing weights over the metrics that exist, is a trap. A review caught it ranking San Juan #1 nationally: SJU has no fare data, so its revenue pillar became "scale only".
   - **Coverage** (the share of total weight backed by real data) sets the confidence grade: High >= 95%, Medium >= 70%, otherwise Low. Any material gap therefore caps an airport at Medium.
   - Examples: SJU (no fare data) and HVN (no on-time data) are Medium.
5. **Rank stability:** 1,000 seeded Dirichlet re-weightings of the pillars around the defaults. The output reads like "BOS ranks #1 in 64% of scenarios". This measures **sensitivity to the subjective weights only**. It is not a 64% probability of being the best investment.
6. **What-if:** analysts can override pillar weights in chat ("weight growth twice as much").
7. **Constraint flags** (legal caps, curfews, land, runway geometry) are shown **next to** the score, never folded into it. The analyst sees the trade-off instead of a hidden penalty.
8. **Bottleneck check** (a deterministic heuristic, shown beside each ranked airport):
   - `regulatory`: a curated passenger cap or curfew. Example: SNA.
   - `airside risk`: a runway constraint in the curated notes, or NAS delay per arrival at the 80th percentile or above. Example: SFO. NAS delay mixes weather, volume and ATC, so this is a risk flag, not a diagnosis.
   - `unconfirmed`: nothing found. Terminal pressure is plausible, but confirm gate utilization in the master plan.

**Why a national peer set, including small airports?** The firm screens the whole US market, and a national percentile answers "how unusual is this airport?". Scale has its own metric (log enplanements), so a small regional airport cannot outrank a hub on scale. A hub-class peer set is a reasonable alternative; it is one config line.

**Fare premium:** fit `log(fare) = a + b*log(distance)` across all US markets (passenger-weighted, last 4 quarters). An airport's premium is the passenger-weighted mean residual of its markets. A persistent premium signals scarce supply.

### 4.2 Congestion Index (0-100, higher = more congested)

Percentile composite:

| Metric | Weight |
|---|---|
| Taxi-out | 25% |
| Peak-hour movements per runway | 20% |
| Departure delay rate | 15% |
| Arrival delay rate | 15% |
| NAS delay per arrival | 15% |
| Cancellation rate | 10% |

When a curated passenger cap exists, the tool also returns cap utilization (SNA is at about 91% of its 12.2M cap).

NAS delay, as BTS defines it, mixes non-extreme weather, traffic volume, airport operations and ATC. It is the best public proxy for airfield and airspace shortfall, but it cannot isolate a terminal bottleneck.

**Confidence comes from the on-time sample.** It is High only when reporting carriers fly at least 60% of the airport's domestic departures; LAX and SNA are both around 90%. Peak-hour movements count those carriers only, so gateways with much foreign or cargo traffic (JFK, MIA, ANC) read lower than reality. This is stated as a caveat.

### 4.3 Route mix / long-haul share

Departures are banded by segment great-circle distance:
- **Short:** under 700 mi
- **Medium:** 700-3,000 mi
- **Long:** 3,000 mi or more (configurable)

Results are reported separately for **all flights, passenger aircraft, and freighters**, and at alternative thresholds (2,000 / 3,000 / 4,000 mi), because no official definition exists. At ANC the definition matters: **29% of all departures are long-haul, but only 4% of passenger departures are**. ANC is a trans-Pacific cargo hub.

### 4.4 Unmet demand: spill model

Observed passengers are *constrained* demand. Per route-month:
- Assume demand `D ~ Normal(mu, cv*mu)`.
- Observed passengers = `E[min(D, seats)]`.
- Solve for `mu`.
- Spill = `mu - observed`.

The demand variability `cv` is a stated assumption (0.30). Results are also reported at 0.20 and 0.40, giving SFO a range of **0.9M-6.0M** spilled passengers a year around the 2.6M point estimate. Spill is converted to equivalent extra daily departures.

The model is steep near 100% load factor: demand recovered from a nearly full flight is unbounded. Route-months above 95% load factor are therefore capped at 95%. That cap is itself a consequential assumption.

The cv range is a **sensitivity range, not a statistical confidence interval**. Aggregate route-month load factors cannot identify the true flight-level demand distribution, so spill is a **scenario estimate**.

The "why" combines:
- constrained routes (load factor >= 85%)
- fare premium
- delay-cause attribution (NAS share)
- seat vs passenger growth
- live FAA arrival rate
- curated physical constraints

**Counter-signals are reported too.** For example, SFO enplanements are still 5% below 2019, so part of the gap is softer demand.

---

## 5. Where and how AI is used

| Concern | Who does it | Why |
|---|---|---|
| Understanding the question, choosing analyses, sequencing tools | **LLM** (function calling) | Flexible language understanding |
| Entity resolution ("LA", "Santa Ana", "New England") | **Code** (`airport_directory.py`) | Deterministic; ambiguity is surfaced (LA, with BUR/LGB/ONT/SNA as alternatives) |
| Every number, score, rank, model | **Code** (`analytics/`) | Reproducible, testable, auditable |
| Tables, charts, sources in the UI | **Code**, built directly from tool outputs | The LLM cannot misreport them |
| Narrative, reasoning, assumptions, confidence wording | **LLM**, constrained by the system prompt | Clear communication |
| Checking the narrative | **Code**: numeric grounding guardrail | Flags any number in the answer not found in the tool results the model was shown |

**How the grounding guardrail works, and its limits.**
- Every figure in the answer must match a value from the **successful** tool results the model was actually shown: this turn's results plus the history in its context.
- These never count as evidence:
  - failed results and error text;
  - queries the tool echoes back;
  - the user's own text;
  - free-form tool arguments.

  Only explicit what-if parameters of successful calls count, such as weights and thresholds.
- Matching respects units:
  - Only fraction fields, such as load factor or growth, may appear as a percentage.
  - "12 percentage points" is read as a unit.
  - "2.7M" must match a count.
  - An explicit sign must match.
- Small integers and years are exempt only in context: list markers or "top 3" for small integers; "in 2025" or "2019 levels" for years. So "SFO has 9 runways" or "2000 gates" is still checked.
- A separate check flags answers that state a **higher confidence than the analyses returned**.
  - Grades are kept per analysis **and** per airport: "High for SFO congestion; Medium for SFO unmet demand" checks each against SFO's grade for that analysis.
  - A clause that names only an airport or only an analysis is checked against those grades; otherwise against the lowest grade used.
  - A level carries across "and" and commas, so "High for congestion and unmet demand" checks both.
  - Claims are the "Confidence:" statement plus any explicit assertion ("I have high confidence in the SFO spill estimate"). A sentence that only mentions a level ("the other four are high") is not treated as a claim.
  - It is pattern-based: unusual phrasing can still slip through.
- The UI wording reflects what the check is: "N figures match the data", or "No figures to check". It does not say "verified".
- It is a set-membership check, so it catches invented or misread figures. In the eval it caught "NAS delay 11.9 min" when the data said 11.19.
- What it cannot catch: a wrong number that happens to equal some other value in the evidence. Set membership does not prove a number is attached to the right claim. Measured against a full New England ranking payload, a random percentage still passes about 20% of the time, down from about 75% before the unit rules.
- It is a safety net that makes errors visible, not a proof of correctness. The real protection is architectural: the tables and charts come straight from code.

**Agent runtime** (`agent/runtime.py`):
1. Build context: system prompt with data periods, session focus airports, and trimmed history made of whole turns.
2. Call the LLM with the tool schemas.
3. **Validate every tool call with Pydantic.** Invalid arguments go back to the model, which self-corrects.
4. Run the tools (in parallel, with timeouts).
5. Loop, up to 8 steps.
6. Run the grounding check on the final answer.

Sessions keep the history, so follow-ups like "what about Bangor?" or "weight growth more" work.
- **A what-if scenario stays active.** The weights of the latest scoring call are session state, and the model is told to pass them again, for example in "Why does Bangor score so high?", until the user asks for the defaults. The offline planner does the same.
- A follow-up answered from earlier results, with no new tool call, still shows that earlier evidence in the panel, labeled as coming from an earlier answer.
  - The evidence is chosen by the airports and analyses the answer names, then by matching figures, so a coincidental number in another airport's result does not count.
  - Qualitative follow-ups ("the SFO spill estimate is reliable") are tied to their evidence too, and their confidence is checked.
- Only one turn runs per session at a time.

**LLM provider chain:**
- Claude first when its key is set, then the Gemini free tier (`gemini-3.5-flash-lite`), then Groq (`gpt-oss-120b`) if keyed, then the offline planner. `LLM_PROVIDERS` sets the order, and providers without a key are skipped, so a free Gemini key alone is enough.
- The Claude model is configurable. The default is `claude-opus-5-5`; the evaluation below ran with `claude-sonnet-5-5`, set in `.env`.
- One OpenAI-compatible adapter plus an Anthropic adapter, with retry/backoff on 429/5xx and automatic failover. A provider that was exhausted is skipped for 60 seconds (for example while a free-tier quota is used up).
- **Offline planner (last in the chain):** if every LLM is unavailable, a keyword intent router runs the same tools and assembles the answer from tool summaries.
  - The answer is labeled as offline mode. It degrades gracefully instead of failing.
  - Follow-ups keep the previous analysis and its parameters ("weight growth twice as much", "what about Bangor?", "why does Bangor score so high?", "and at 4,000 miles?").

---

## 6. Key trade-offs

| Decision | Chosen | Alternative | Why |
|---|---|---|---|
| Architecture | Modular monolith + batch ingest process | Microservices | Same separation of concerns, far less overhead; boundaries allow later extraction |
| Data access | Local validated snapshot (replica) | Live API calls per question | Speed, reproducibility, resilience to flaky government sites. Cost: data is as fresh as the last ingest, and every answer states its periods |
| Normalization | Percentile ranks vs national peers | Min-max / z-scores | Robust to outliers and explainable. Cost: loses magnitude, so raw values are shown too |
| Weights | Expert defaults + rank-stability analysis + what-if overrides | "Learned" weights | No ground truth of past investment returns to learn from; transparency beats false precision |
| Constraints | Shown as flags beside the score | Penalty inside the score | Analysts must see *why*; a hidden penalty would be untraceable |
| Terminal capacity | Proxied by load factor, peak-hour movements, taxi-out + a bottleneck check | Gates / square footage from master plans | Not available through a standardized national API; integrating per-airport plans is the next diligence step. Stated as the main limitation |
| LLM | Claude when keyed, free Gemini as automatic fallback, offline last | A single model | Most reliable answers for the demo, yet anyone with only a free Gemini key (or no key) still gets a working app; resilient to rate limits and outages |
| Number safety | Tool-only numbers + grounding check + UI built from tool data | Trust the LLM | Hallucinated statistics are unacceptable in investment analysis |
| Missing data | Neutral (median) imputation + confidence from coverage | Re-normalize over available metrics | Re-normalizing rewards gaps (it put SJU at #1); neutral imputation is conservative and the gap stays visible |
| Curated notes | Small cited YAML | RAG over documents | Five airports' constraints don't justify a vector store; the notes are auditable |

---

## 7. Assumptions, uncertainty, scoping (how the system communicates them)

Every tool result carries `assumptions`, `caveats`, `confidence` and `provenance`. The UI shows them in a **Sources & assumptions** tab, and the answer format requires an "Assumptions & caveats" section and a confidence line. Examples:
- "Long-haul >= 3,000 mi (no official definition; 2,000/4,000 shown)."
- "Spill cv = 0.30, range shown."
- "On-time data = reporting carriers' domestic flights only."
- "CY2025 enplanements preliminary."
- "Curated note, source: County of Orange."

---

## 8. Quality

**Automated tests: 155 backend + 11 frontend** (`make test`, `npm test`). No network or API key is needed.
- Analytics math checked against closed-form values: spill-model inversion, growth, load factor, fare premium.
- Scoring on synthetic universes: neutral missing-data policy, confidence grades, weight overrides, bottleneck check.
- Connectors on synthetic files with mocked HTTP: retries, pagination, form-state changes, atomic downloads and snapshot writes.
- Validation contracts: nulls, duplicate keys, missing or truncated months; growth suppressed on incomplete windows.
- Entity resolution, including typos, ICAO codes and the refusal to guess unknown codes.
- Agent runtime with a scripted fake LLM:
  - self-correction after invalid arguments;
  - follow-ups, including evidence carried over from an earlier answer;
  - step budget;
  - per-turn provider stickiness;
  - session-lock cleanup.
- Grounding guardrail:
  - units, signs, ordinals and ranges;
  - echoed queries and errors never counting as evidence;
  - contextual exemptions;
  - overstated confidence.
- Claude and OpenAI-compatible adapters, SSE API, live-status outage handling, offline follow-ups.
- Frontend SSE parser tested at every chunk split.

**Golden-question eval** (`make eval`; `--hard` adds hard cases): 17 turns across 10 conversations against the real LLMs. Each turn checks:
- the expected tool was called **successfully**, with the required arguments where set (for example, the follow-up "Why does Bangor score so high?" must keep the what-if weights);
- every figure matches a value from the analyses;
- the stated confidence does not exceed the analyses';
- required content is present;
- for the injection case, no sentence asserts the injected number without a refusal.

Hard cases cover multi-tool questions, ambiguity ("Portland"), data the system does not have (gate counts, construction costs), and a prompt-injection attempt. Passing means passing these checks: it is a regression suite for agent behavior, not proof of factual correctness.

**Model comparison** (same 17 turns; `evals/report.md` and `evals/report_claude.md`):

| | Gemini `gemini-3.5-flash-lite` (free tier) | Claude `claude-sonnet-5-5` (paid) |
|---|---|---|
| Turns passed | 17/17 (previous checks, see below) | **16/17** (current checks) |
| Figures matching the data | 245/245 | 469/471 |
| Avg latency | **4.0 s** | 8.1 s |
| Cost | free (rate-limited) | ~$0.05 per question |
| Style | concise | more detailed (almost twice as many figures per answer) |

Notes on the runs:
- **Claude** ran on the current code and checks. Its one failure is real: it decoded a raw METAR remark ("light rain ended at about 12:45 UTC"), said itself that the tool does not state it, and the guardrail flagged the derived "45".
- **Gemini's saved report is from the previous round's checks.** Its "Why does Bangor score so high?" turn used the default weights, which the current eval fails (the follow-up must keep the what-if scenario). Re-running it after the round-3 changes hit the free tier's daily quota (HTTP 429 on every call), so that run is pending.
- Results vary from run to run: earlier runs scored 15-17/17 (Gemini) and 14-17/17 (Claude). The failures were of two kinds:
  - **Checker gaps**, each fixed with a regression test: a number that existed only in a field name (`rank_range_90`, now an explicit `rank_range_interval_pct`), explanatory sentences read as confidence claims, a bolded confidence line, a refusal list that missed "will not", and follow-ups that silently dropped the what-if scenario.
  - **Real model slips the guardrail is meant to catch:** derived numbers ("less than 1.2 million passengers of headroom", "over 120,000 spilled passengers each", the METAR "45"), a truncation ("~9" for 9.7), and a confidence level the model gave to curated notes. Prompt rules were added: grade only what the tools graded, refuse dictated figures explicitly, and keep an active what-if scenario.
- Explicit units on every metric (`unit` field) removed Claude's earlier recurring slip, a 0.005 pp gap reported as "0.5 pp".
- **Decision: Claude is primary, Gemini is the automatic backup.** Claude passed the current checks with fuller answers, and the free Gemini tier ran out of daily quota during this testing, which is exactly the risk to avoid in a live demo. The cost is latency (about 8 s vs 4 s per answer) and about $0.05 per question. A reviewer with only a free Gemini key still gets a working app, because providers without a key are skipped. Swapping the order is one env var (`LLM_PROVIDERS`). An exhausted provider is also skipped for 60 s, so later turns go straight to the backup instead of paying the retry delay each time.

Iterating on the eval surfaced real issues:
- Follow-ups re-derived numbers instead of re-running tools (now a prompt rule).
- The model computed a derived "96%" share itself (flagged).
- A misread was caught: "NAS delay 11.9 min" when the data says 11.19.
- Live status was served stale from history (now always re-queried).

**Provider failover verified live:** with Gemini forced to fail, Claude (`claude-sonnet-5-5`) answered the same questions with every number grounded.

**Five review rounds** (two independent code reviews, then three external reviews with Codex: a full review and two verification re-reviews), each finding fixed with a regression test. The most important:
- **Security:** API keys were being copied into the Docker image; `.env` is now excluded.
- **Data joins:** FAA and IATA codes differed, renamed airports split their history, and territories were missing.
- **Scoring:** re-weighting rewarded data gaps; replaced by neutral imputation.
- **Guardrail precision:** unit- and sign-aware matching.
- **Robustness:** structural truncation of tool results, linear history trimming, per-session locks.
- **Evidence integrity:**
  - Failed tool results and echoed queries can no longer "verify" a number.
  - An FAA outage reads "unavailable", not "no programs".
  - Incomplete months suppress growth instead of distorting it.
- **Framing:** the score is a screening priority with a bottleneck check, not a return estimate.
- **Verification round (Codex #2):**
  - Tool arguments are strict (unknown fields are rejected, so an invented parameter cannot become evidence).
  - Null required values in T-100 files fail ingest instead of being read as zero.
  - Small counts at the start of a sentence are checked, not exempted.
  - Every confidence statement is checked, clause by clause, not only the first; explanatory sentences that merely mention a level are not treated as claims.
  - A follow-up answered from history shows the analysis it actually used.
  - The offline planner keeps what-if weights on airport follow-ups.
- **Second verification round (Codex #3):**
  - The what-if scenario stays active across follow-ups (the suggested "Why does Bangor score so high?" had reset it), and the eval now requires it.
  - Confidence grades are matched per airport and per analysis; a shared level covers every coordinated target; explicit confidence sentences are claims.
  - Earlier evidence is chosen by airport and analysis, not by coincidental numbers, including for qualitative follow-ups.
  - The injection check handles coordinated clauses ("I will not make up data and BOS has a score of 99" is an assertion) and introduced quotations ("I cannot assert the following: ..." is not).
  - Scored airports such as USA (Concord-Padgett) always render as airport codes.

**Ingest validation contracts:**
- Checks: required columns, no nulls in required fields, unique keys, no missing or truncated months, anchor airports present in the latest month, row counts, non-negative values.
- Growth is suppressed (scored neutral, lower coverage) when an airport's 12-month comparison windows are incomplete.
- Snapshot and manifest writes are atomic (temp file plus swap), so a failed refresh really does keep the last good snapshot.

## 9. Known limitations (deliberately out of scope for a one-day build)

**Investment economics are not modeled:**
- construction costs;
- committed projects;
- airline commitments;
- gate availability;
- concession economics;
- who captures the incremental revenue.

**Deployment:**
- There is no authentication, rate limiting or aggregate request budget.
- A tool that exceeds its timeout stops being waited on, but its worker thread runs to completion.
- SSE streams progress events and the finished answer, not tokens.

**The eval is a regression suite for agent behavior**, covering tool choice, numeric matching, confidence consistency and selected content. It is not proof of factual correctness or of robust injection resistance.

## 10. With more time

- Scheduled ingest (Airflow/cron) with data-quality alerts.
- Redis session store.
- Gate and terminal capacity data from airport master plans (RAG over planning PDFs), to turn the bottleneck check from a heuristic into a measurement.
- FAA TAF (Terminal Area Forecast) for forward-looking demand.
- Backtest the score against past expansions.
- Per-flight spill using schedule data.
- Auth and multi-user audit log.
- LLM tracing (Langfuse).
