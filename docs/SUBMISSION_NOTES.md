# Text for the Ashby "Notes" field

Airport Investment Intelligence Agent: a chat agent (React + FastAPI) that screens US airports for terminal-expansion opportunities using public FAA/BTS data. Core principle: code computes, the LLM explains.

- Deterministic scoring (a screening priority, not a return estimate). Terminal Expansion Opportunity Score built from percentile ranks against 233 US peer airports, with four weighted pillars: demand momentum, capacity utilization, operational strain, revenue potential. Also a Congestion Index, a long-haul route-mix analysis, and a spill model for unmet demand. All weights live in one config file.
- Uncertainty made explicit. Each result includes a confidence grade based on data coverage, rank stability across 1,000 weight scenarios, sensitivity ranges (spill cv, long-haul threshold), and cited sources with data periods. Curated constraints (e.g. SNA's passenger cap) are shown as flags beside the score, not hidden inside it.
- Agent runtime. Pydantic-validated tool calls with self-correction, multi-turn memory for follow-ups, and a numeric-grounding guardrail that flags any number not produced by a tool. Uses Claude when a key is set, fails over automatically to the Gemini free tier (or Groq), and falls back to a deterministic offline mode if every LLM is unavailable. A free Gemini key alone is enough to run it.
- Interface. Streaming progress, an evidence panel (tables and charts built from tool output), a reasoning trace, and voice input/output.
- Run. `docker compose up --build` (http://localhost:8000); see the README. 155 backend + 11 frontend tests; golden-question eval (17 turns incl. ambiguity, missing data and prompt injection) against both Claude and Gemini.

Design doc: docs/DESIGN.md
