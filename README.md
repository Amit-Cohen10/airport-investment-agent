# Airport Investment Intelligence Agent

An AI agent that helps analysts at an airport-modernization investment firm find **US airports where terminal expansion is most likely to pay off**. It answers questions such as:

- *Which airports in New England are strong candidates for terminal expansion?*
- *Compare LA and Santa Ana airport congestion levels.*
- *What is the percentage of long-haul flights out of Anchorage airport?*
- *What is the unmet flight demand at SFO, and why?*

The design principle: **code computes, the LLM explains.**
- **Code:** every score, rank and statistic comes from deterministic, unit-tested analytics on public FAA/BTS data.
- **LLM:** chooses which analysis to run, explains the result, and states assumptions and confidence.
- **Guardrail:** checks that every number in the answer traces back to a tool output.

Design document (scoring methodology, trade-offs, where AI is used): **[docs/DESIGN.md](docs/DESIGN.md)**

## Quick start

### Option A: Docker (one command)

```bash
cp backend/.env.example backend/.env    # add a free GEMINI_API_KEY (optional, see below)
docker compose up --build               # then open http://localhost:8000
```

### Option B: local development

Requires [uv](https://docs.astral.sh/uv/) and Node 20+.

```bash
make setup          # backend deps (uv, Python 3.12) + frontend deps + backend/.env
make dev-backend    # FastAPI on :8000
make dev-frontend   # React on :5173 (proxies /api), open http://localhost:5173
```

**LLM keys.** Put at least one key in `backend/.env`. The first configured provider is primary; the rest are automatic fallbacks.
- `GEMINI_API_KEY`: free at https://aistudio.google.com/apikey
- `GROQ_API_KEY`: free at https://console.groq.com/keys
- `ANTHROPIC_API_KEY`: paid, optional. An automatic backup if Gemini is rate-limited or down.

With **no key at all**, the app still works in **offline mode**: a deterministic intent router runs the same analyses and assembles answers from their results.

### Other commands

```bash
make test      # 126 backend tests, no network or API key needed (frontend: cd frontend && npm test)
make eval      # golden questions against the real LLM -> evals/report.md
make ingest    # refresh the data snapshot from public sources (~10 min, ~400 MB download)
```

The processed data snapshot (`backend/data/snapshot/`, ~6 MB Parquet) is committed, so ingest is optional.

## What's inside

```
backend/app/
  data/connectors/   one adapter per public source (FAA, BTS Socrata, TranStats, OurAirports, live FAA/METAR)
  data/ingest.py     batch ETL -> validated Parquet snapshot + manifest (periods, sources)
  data/repository.py DuckDB over the snapshot (the only storage-aware module)
  analytics/         pure functions: features, expansion score, congestion index, route mix, spill model, rank stability
  services/          InsightService (business logic + provenance), airport/region resolution, curated notes
  agent/             runtime loop, context building, tool registry + tools, numeric grounding guardrail, sessions
  llm/               OpenAI-compatible adapter (Gemini/Groq/OpenRouter), Claude adapter, failover, offline planner
  api/               FastAPI: SSE chat, deterministic /api/rankings, health
backend/config/scoring.yaml   all weights, thresholds and model assumptions
backend/app/knowledge/airport_notes.yaml   curated, cited constraints (e.g. SNA passenger cap)
frontend/            React + TypeScript: chat, live tool progress, evidence panel (tables/charts), sources, trace, voice
evals/               golden-question evaluation harness
docs/DESIGN.md       design / architecture document
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/chat` | Chat. Server-Sent Events: `session`, `status`, `tool_start`, `tool_end`, `answer` |
| `POST /api/chat/sync` | Same, as a single JSON response |
| `GET /api/rankings?region=New England` | Deterministic ranking, no LLM involved |
| `GET /api/airports/resolve?q=LA` | Entity resolution |
| `GET /api/health` | Data periods, snapshot manifest, LLM chain |

## Data sources

- FAA Passenger Boarding (Enplanement) Data
- BTS T-100 Segment (summary via Socrata, all-carrier segments via TranStats)
- BTS Consumer Airfare Report Table 1a
- BTS Airline On-Time Performance
- OurAirports
- FAA NAS Status API (live)
- aviationweather.gov METAR (live)

Details and periods are in [docs/DESIGN.md](docs/DESIGN.md#3-data-sources-all-free-no-signup).

*Scores are screening priorities computed from public data, for further diligence: not return estimates or investment advice. Each ranked airport carries a bottleneck check (is the terminal, the airfield, or a legal cap the binding constraint?).*
