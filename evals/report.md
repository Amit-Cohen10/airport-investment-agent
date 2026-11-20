# Evaluation report

Model chain: gemini:gemini-3.5-flash-lite  
Run: 2026-11-20 10:26  
**Result: 10/11 turns passed**

Checks per turn: expected tool called, every number in the answer grounded in tool output, required content present.

| Conversation | Question | Result | Tools | Grounded numbers | Latency | Provider | Problems |
|---|---|---|---|---|---|---|---|
| New England expansion + follow-ups | Which airports in New England are strong candidates for terminal expansion? | PASS | resolve_airports, rank_expansion_candidates | 35/35 | 3.8s | gemini:gemini-3.5-flash-lite | - |
| New England expansion + follow-ups | What if we weight demand growth twice as much? | PASS | rank_expansion_candidates | 16/16 | 2.5s | gemini:gemini-3.5-flash-lite | - |
| New England expansion + follow-ups | Why does Bangor score so high? | PASS | explain_airport_score | 25/25 | 2.6s | gemini:gemini-3.5-flash-lite | - |
| LA vs Santa Ana congestion | Compare LA and Santa Ana airport congestion levels. | PASS | resolve_airports, compare_congestion | 19/19 | 3.7s | gemini:gemini-3.5-flash-lite | - |
| LA vs Santa Ana congestion | So which of the two has more room to grow? | FAIL | rank_expansion_candidates, get_airport_profile, get_airport_profile | 18/19 | 4.3s | gemini:gemini-3.5-flash-lite | unverified numbers: ['30'] |
| Anchorage long-haul | What is the percentage of long haul flights out of Anchorage airport? | PASS | resolve_airports, analyze_route_mix | 20/20 | 4.0s | gemini:gemini-3.5-flash-lite | - |
| Anchorage long-haul | And if long-haul means over 4,000 miles? | PASS | analyze_route_mix | 17/17 | 3.2s | gemini:gemini-3.5-flash-lite | - |
| SFO unmet demand | What is the unmet flight demand in SFO airport and why? | PASS | estimate_unmet_demand, get_live_status | 27/27 | 6.9s | gemini:gemini-3.5-flash-lite | - |
| SFO unmet demand | What are conditions like there right now? | PASS | get_live_status | 4/4 | 1.9s | gemini:gemini-3.5-flash-lite | - |
| Methodology & scope | How is the expansion score calculated? | PASS | explain_methodology | 12/12 | 2.8s | gemini:gemini-3.5-flash-lite | - |
| Methodology & scope | What's a good restaurant in Paris? | PASS | - | 0/0 | 0.9s | gemini:gemini-3.5-flash-lite | - |

## Transcripts

### Which airports in New England are strong candidates for terminal expansion?

_tools: resolve_airports, rank_expansion_candidates_

**Boston Logan (BOS), Portland (PWM), and Bangor (BGR)** lead the New England region as strong terminal expansion and investment candidates. 

* **BOS (Rank #1 in NE, National #19, Score 66.2):** High operational strain (operational strain pillar 90.9) and strong revenue potential (75.3) driven by 21.0M enplanements (CY latest), a 6.4% 3-year enplanement CAGR, and an average taxi-out of 21.7 minutes.
* **PWM (Rank #2 in NE, National #30, Score 63.8):** Healthy demand momentum (62.3) and solid load factors (81.7%), backed by 1.3M enplanements and a 9.4% 3-year enplanement CAGR.
* **BGR (Rank #3 in NE, National #32, Score 63.3):** Exceptional demand momentum (80.9) with 13.8% TTM passenger growth and a 3-year enplanement CAGR of 8.4%, paired with an 80.1% load factor.
* **BTV (Rank #4 in NE, National #36, Score 62.5):** Strong load factor utilization (83.6 capacity utilization pillar) and 80.9% load factor, alongside high operational strain (73.2).
* **HVN (Rank #5 in NE, National #39, Score 62.1):** High growth surge with a 28.0% 3-year enplanement CAGR and 737.8K enplanements (demand momentum pillar 85.4).

### Assumptions & Caveats
* **Scoring Methodology:** The score is a weighted average of four pillars (Demand Momentum 30%, Capacity Utilization 25%, Operational Strain 20%, Revenue Potential 25%) scored as percentiles against 233 US commercial airports.
* **Data Sources:** FAA preliminary CY2025 enplanements, BTS T-100 traffic (Sep 2025–Aug 2026), on-time data (Oct 2025–Sep 2026), and fares (2025 Q2–2026 Q1).
* **Curated Context:** BOS carries a curated land constraint flag. Physical gate and terminal capacity are proxied rather than measured directly.

**Confidence:** Medium (solid data coverage, though terminal-specific physical sizing constraints are unobserved).

*Would you like to examine the spill and unmet demand estimates for Boston (BOS)?*

### What if we weight demand growth twice as much?

_tools: rank_expansion_candidates_

**Tweed/New Haven (HVN)** and **Bangor (BGR)** jump to the top of the New England rankings when demand growth (`demand_momentum_weight` set to 0.6) is emphasized.

* **HVN (Rank #1):** Scores 68.5, driven by an exceptional 28.0% 3-year enplanement CAGR and high demand momentum pillar (85.4).
* **BGR (Rank #2):** Scores 67.3, supported by 13.8% TTM passenger growth and an 80.9 demand momentum pillar.
* **PWM (Rank #3):** Scores 63.4, backed by a 9.4% 3-year CAGR and a 62.3 demand momentum pillar.
* **BOS (Rank #4):** Slips to #4 with a score of 59.7 because its 3-year CAGR (6.4%) and negative TTM passenger growth (-1.9%) weigh less favorably under high-growth weighting.

### Assumptions & Caveats
* **Methodology:** The pillar weight for demand momentum was overridden from the default 0.30 to 0.60, with remaining weights proportionally adjusted.
* **Data Sources:** FAA preliminary CY2025 enplanements and BTS T-100 traffic (Sep 2025–Aug 2026).

**Confidence:** Medium (reflects robust enplanement growth metrics, but relies on modeled pillar weight redistribution).

*Would you like to examine Tweed/New Haven's (HVN) unmet demand or passenger spill estimates?*

### Why does Bangor score so high?

_tools: explain_airport_score_

**Bangor (BGR)** ranks high (#32 nationally, score 63.3) primarily due to **exceptional demand momentum (pillar score 80.9)** and strong load factor utilization.

* **Rapid Passenger Growth:** BGR ranks in the 96th national percentile for trailing 12-month passenger growth, with a 13.8% increase over the prior 12 months.
* **Strong Recovery:** CY2025 preliminary enplanements stand at 1.34x 2019 baseline levels (80.6th national percentile), backed by an 8.4% 3-year CAGR (66.1st percentile).
* **High Load Factors:** Capacity utilization sits at 71.2, driven by an 80.1% trailing load factor (72.2nd percentile).
* **Operational Strain:** BGR registers notable strain (65.2 score), including a 19.5-minute average taxi-out (78.9th percentile) and a 24.1% departure delay rate (77.1st percentile).
* **Scale Trade-off:** Revenue potential is lower (32.6 score) due to smaller passenger scale (437K enplanements) and slightly below-average fares (-4.9% vs. distance-adjusted US norm).

### Assumptions & Caveats
* **Methodology:** Scores are percentile-ranked against 233 US peer airports with ≥100,000 annual enplanements.
* **Data Sources:** FAA preliminary CY2025 enplanements, BTS T-100 traffic (Sep 2025–Aug 2026), and BTS on-time data (Oct 2025–Sep 2026).

**Confidence:** High (complete data coverage across all reporting models).

*Would you like to analyze Bangor's route mix to see if its growth is driven by short- or long-haul flights?*

### Compare LA and Santa Ana airport congestion levels.

_tools: resolve_airports, compare_congestion_

**LAX shows higher overall operational congestion than SNA**, ranking #83 nationally (Congestion Index **56.8**) compared to SNA at #135 (**46.0** out of 223 peer airports).

* **Peak-Hour Intensity:** Both airports face extreme runway scheduling concentration, with peak-hour movements per runway in the ~96th national percentile (LAX **22.6**, SNA **22.0** movements/runway).
* **Taxi-Out Delays:** LAX has substantially longer average taxi-out times (**18.3 min**, 68th percentile) than SNA (**16.1 min**, 38th percentile) due to complex parallel-runway ground traffic.
* **Delay Rates:** Departure delay rates (15+ min) are nearly identical (**21.0%** at LAX, **21.0%** at SNA), while arrival delays are slightly higher at LAX (**20.9%** vs **19.3%**).
* **Regulatory Cap & Curfew (SNA):** SNA operates under a strict County settlement cap of **12.2M** annual passengers (currently at **90.5%** utilization) alongside a noise curfew restricting departures after 10 p.m.
* **Modernization (LAX):** LAX is undergoing a massive multi-billion-dollar terminal and Automated People Mover modernization program.

**Assumptions & Caveats:** 
* Congestion Index is a weighted percentile of taxi-out, peak-hour movements, delay rates, NAS delays, and cancellations for Oct 2025 – Sep 2026.
* *Curated context:* SNA's cap is legally binding through 2030 (Orange County settlement agreement).

**Confidence:** High (complete BTS on-time and FAA operational data).

*Would you like to compare terminal investment scores for LAX and SNA instead?*

### So which of the two has more room to grow?

_tools: rank_expansion_candidates, get_airport_profile, get_airport_profile_

**LAX has significantly more physical and operational room to grow** than SNA, even though both tie closely on overall expansion screening scores (**58.5** for LAX vs **58.1** for SNA, ranked #59 and #62 nationally).

* **Legal Growth Ceiling (SNA):** SNA is legally capped at **12.2 million** annual passengers through 2030 by a County settlement agreement, and it is already operating at **90.5%** of that limit (11.0M total passengers). Growth beyond this cap is legally blocked.
* **Physical & Airfield Capacity (LAX):** LAX operates four parallel runways and handles **36.5M** enplanements (vs SNA's **5.5M**), with no passenger cap and 24-hour airfield operations.
* **Recovery vs. Baseline:** SNA has fully recovered and surpassed 2019 traffic (**107.2%** of 2019 levels), leaving little remaining cyclical rebound room under its cap. LAX sits at **85.0%** of its 2019 enplanement baseline, leaving substantial structural upside as long-haul and international traffic continue to normalize.
* **Yield & Momentum:** SNA scores higher on demand momentum and fare premium (**12.1%** above national average vs LAX's **2.8%**), but its single short runway (~5,700 ft) and strict noise curfews severely constrain aircraft gauge and long-haul potential.

**Assumptions & Caveats:**
* Terminal expansion scores synthesize demand growth, capacity utilization, operational strain, and revenue potential (weights: 30/25/20/25).
* *Curated context:* SNA's cap and curfew are legally binding through 2030/2035 respectively.

**Confidence:** High (backed by FAA enplanement history and regulatory constraint tracking).

*Would you like to examine unmet passenger demand or spill estimates for LAX?*

### What is the percentage of long haul flights out of Anchorage airport?

_tools: resolve_airports, analyze_route_mix_

At Ted Stevens Anchorage International Airport (ANC), **29.2% of all departures** (flights with great-circle distance $\ge 3,000$ statute miles) are long-haul, but **only 4.3% of passenger departures** meet this threshold due to a heavy concentration of long-haul cargo operations.

* **All flights:** 29.2% long-haul share (25,608 out of 87,705 total departures).
* **Passenger flights:** 4.3% long-haul share (1,778 out of 41,176 departures; 5.2% on a passenger-seat basis).
* **Cargo flights:** 51.2% long-haul share (23,830 out of 46,529 departures). Cargo represents 53.1% of all ANC departures.
* **Top long-haul routes:** Dominated by intercontinental and cross-country cargo flights to hubs such as Louisville (SDF), New York (JFK), Miami (MIA), Hong Kong (HKG), Seoul (ICN), and Taipei (TPE).

### Assumptions & Caveats
* **Distance threshold:** Long-haul is defined as $\ge 3,000$ statute miles (short-haul $< 700$ mi). If the threshold is lowered to 2,000 miles, the all-flight long-haul share rises to 45.8% and passenger long-haul share to 16.4%.
* **Data period:** Sep 2025 – Aug 2026 (BTS T-100 Segment data, including domestic and international passenger and cargo carriers).
* **Curated context:** ANC ranks #1 in the U.S. and #3 globally for air cargo (ACI, 2026 release via Alaska DOT&PF), serving as a vital great-circle tech stop between Asia and North America.

### Confidence
**High** (complete BTS carrier-segment coverage).

*Would you like to analyze the unmet passenger demand or congestion profile for Anchorage?*

### And if long-haul means over 4,000 miles?

_tools: analyze_route_mix_

If long-haul is defined as **over 4,000 statute miles**, **11.3% of all departures** and **0.8% of passenger departures** out of Anchorage (ANC) qualify.

* **All flights:** 11.3% long-haul share (9,943 out of 87,705 total departures).
* **Passenger flights:** 0.8% long-haul share (345 out of 41,176 departures; 0.2% on a passenger-seat basis).
* **Cargo flights:** 20.6% long-haul share (9,598 out of 46,529 departures).
* **Top $\ge$ 4,000-mile routes:** Dominated by long-haul freighters to Miami (MIA) and Asian gateways such as Hong Kong (HKG), Taipei (TPE), Shenzhen (SZX), and Shanghai (PVG).

### Assumptions & Caveats
* **Distance threshold:** Long-haul redefined here as $\ge 4,000$ statute miles. 
* **Data period:** Sep 2025 – Aug 2026 (BTS T-100 Segment data).
* **Curated context:** ANC's great-circle positioning makes it a prime technical stop for trans-Pacific freighters, but passenger routes $\ge 4,000$ miles are extremely rare.

### Confidence
**High** (BTS carrier-segment data).

*Would you like to compare ANC's route mix or congestion index with another airport?*

### What is the unmet flight demand in SFO airport and why?

_tools: estimate_unmet_demand, get_live_status_

At San Francisco International (SFO), unmet passenger demand is estimated at **~2,665,790 spilled passengers annually** (about 10.1% of carried traffic), equivalent to **~49.9 extra daily departures**.

### Why
- **High Route Load Factors:** SFO's system-wide load factor stands at **81.7%**, with **65 out of 214 routes** classified as constrained (high load factors driving passenger spill). Top spill routes include JFK (168k spilled passengers/yr, 89.0% LF) and EWR (162k spilled/yr, 90.2% LF).
- **Severe Airfield Congestion:** SFO ranks **#6 nationally** on the Congestion Index (87.8), with an arrival delay rate of **34.9%** and National Airspace System (NAS) delays averaging **11.2 minutes per arrival**. NAS delays account for **48.6%** of all delay causes.
- **Fare Premium:** SFO fares run **10.9% above the U.S. national norm**, indicating pricing power and willing demand that cannot be accommodated by added frequencies.
- **Physical & Regulatory Constraints (Curated Context):** 
  - **Runway geometry:** SFO's parallel runways (28L/28R, 1L/1R) are spaced ~750 ft apart, too close for independent parallel approaches in low ceilings.
  - **Procedure shifts:** Trade press (Aviation Week, April 2026) reported the FAA restricted SFO's side-by-side arrival procedures, reducing arrival rates from 54 to 36 per hour.
  - **Geography:** Bounded by San Francisco Bay, new runway construction is environmentally and politically constrained.

### Assumptions & Caveats
- **Model basis:** Estimated via a demand-spill model assuming route demand variation ($\text{CV} = 0.3$), treating routes with average load factors above ~82% as spilling passengers.
- **Counter-signals:** SFO enplanements remain **5.5% below CY2019 levels** (94.5% recovery), and seats are growing slightly faster (+1.05%) than passengers (+0.33%), suggesting part of the gap reflects shifting post-pandemic demand (e.g., business travel) rather than purely rationed capacity.
- **Confidence:** **Medium** (derived from BTS T-100 segment models and standard spill estimation parameters; spill is unobservable by definition and order-of-magnitude).

*Suggestion: Would you like to compare SFO's congestion or unmet demand metrics directly with another West Coast hub like LAX or SEA?*

### What are conditions like there right now?

_tools: get_live_status_

At SFO right now (**2026-11-20 08:25 UTC**), operating conditions are **clear and unrestricted**.

### Why
- **No Active FAA Programs:** There are currently no active Ground Stops, Ground Delay Programs, or FAA traffic management advisories in effect.
- **VFR Weather:** The METAR reports Visual Flight Rules (**VFR**) conditions with 10+ miles visibility, scattered clouds at 3,800 ft, and light winds (4 knots).

### Assumptions & Caveats
- **Single Snapshot:** This represents a single real-time operational snapshot and does not reflect long-run congestion patterns.

*Suggestion: Would you like to check the live status or congestion index for another airport to compare?*

### How is the expansion score calculated?

_tools: explain_methodology_

The **Terminal Expansion Opportunity Score** is a 0–100 screening signal measuring how strongly operational and market data support investing in terminal capacity at any of the 233 US commercial airports with at least 100,000 annual enplanements.

* **Demand Momentum (30% weight)**: Measures multi-year enplanement CAGR (CY2022–25), trailing 12-month passenger growth, and recovery relative to the CY2019 pre-pandemic baseline.
* **Capacity Utilization (25% weight)**: Evaluates trailing 12-month load factors and the gap between passenger growth and seat growth.
* **Operational Strain (20% weight)**: Captures airfield congestion via average taxi-out times, peak-hour movements per runway, departure delays, and NAS delay minutes per arrival.
* **Revenue Potential (25% weight)**: Assesses facility scale (log of CY2025 enplanements) and fare premiums relative to distance-adjusted US norms.

**Assumptions & Caveats:**
* **Methodology:** Metrics are converted to national percentiles (0–100), aggregated into pillar scores, and combined using default weights. Missing metrics are dropped and weights re-normalized.
* **Curated Context:** Constraint flags (such as curfews, physical land limits, or regulatory caps) are tracked separately and not folded into the numerical score.
* **Data Sources:** Uses BTS T-100 traffic, schedule, on-time performance, and DB1B fare databases across CY2019–CY2025 snapshot periods.

**Confidence:** High — based on standardized federal aviation datasets and robust multi-pillar modeling across national peer groups.

*Would you like to run an expansion screening for a specific region or airport next?*

### What's a good restaurant in Paris?

_tools: none_

I can only assist with US commercial airports and public aviation data, so I cannot recommend restaurants in Paris. 

Would you like to analyze terminal expansion opportunities or review capacity metrics for a US airport instead?
