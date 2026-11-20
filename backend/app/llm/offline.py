"""Offline planner: a deterministic, keyword-based stand-in for the LLM (graceful degradation).

Used as the LAST provider in the chain. If every real LLM is rate-limited or down (or no key is
configured), the agent still answers: intents and airports are detected with rules, the same
deterministic tools run, and the answer is assembled from the tools' own summaries, assumptions
and caveats. Answers are clearly labeled as offline mode. Less fluent, never wrong about numbers.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from app.llm.base import AssistantTurn, ToolCall

INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("explain_methodology", re.compile(r"\b(how (is|are|do)|methodolog|calculat|formula|weights?)\b", re.I)),
    ("estimate_unmet_demand", re.compile(r"\b(unmet|spill|pent[- ]up|latent|turned away)\b", re.I)),
    ("analyze_route_mix", re.compile(r"\b(long[- ]?haul|route mix|distance|short[- ]?haul)\b", re.I)),
    ("compare_congestion", re.compile(r"\b(congest|delay|crowd|busy|bottleneck)", re.I)),
    ("get_live_status", re.compile(r"\b(right now|currently|live|today|weather)\b", re.I)),
    ("rank_expansion_candidates", re.compile(r"\b(candidate|expan|rank|best|top|invest|opportunit)", re.I)),
    ("explain_airport_score", re.compile(r"\bwhy\b.*\b(score|rank|high|low)", re.I)),
]

# Resolver returns (airport codes, region name or None) found in free text.
Resolver = Callable[[str], tuple[list[str], str | None]]


class OfflinePlanner:
    name = "offline"
    model = "rule-based"

    def __init__(self, resolver: Resolver):
        self.resolve = resolver

    async def chat(self, messages: list[dict[str, Any]], tools=None, tool_choice: str = "auto") -> AssistantTurn:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user")
        tool_msgs = [m for m in messages[last_user + 1 :] if m["role"] == "tool"]
        if tool_msgs or not tools:
            return self._answer(tool_msgs)
        question = messages[last_user]["content"]
        calls = self._plan(question, messages[0].get("content", ""))
        if not calls:
            return self._text(
                "_Offline mode (LLM unavailable)._ I could not map this question to an analysis. Try e.g. "
                "'Compare LAX and SNA congestion' or 'Which New England airports are expansion candidates?'"
            )
        tool_calls = [ToolCall(id=f"off_{uuid.uuid4().hex[:8]}", name=n, arguments=json.dumps(a)) for n, a in calls]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                for c in tool_calls
            ],
        }
        return AssistantTurn(content=None, tool_calls=tool_calls, message=message, provider=self.name, model=self.model)

    def _plan(self, question: str, system_prompt: str) -> list[tuple[str, dict[str, Any]]]:
        codes, region = self.resolve(question)
        if not codes and not region:  # follow-up: reuse airports from the session state
            focus = re.search(r"Airports discussed so far[^:]*: ([A-Z, ]+)\.", system_prompt)
            codes = [c.strip() for c in focus.group(1).split(",")][-2:] if focus else []
        intent = next((name for name, pattern in INTENTS if pattern.search(question)), None)
        if intent == "explain_methodology":
            topic = "unmet_demand" if "spill" in question.lower() or "unmet" in question.lower() else "expansion_score"
            return [(intent, {"topic": topic})]
        if intent == "rank_expansion_candidates" or (region and intent is None):
            args: dict[str, Any] = {"region": region} if region else {"airports": codes} if codes else {}
            if re.search(r"\b(growth|demand)\b.*\b(more|higher|double|twice)", question, re.I):
                args["demand_momentum_weight"] = 0.6
            return [("rank_expansion_candidates", args)]
        if not codes:
            return []
        if intent == "compare_congestion":
            return [(intent, {"airports": codes[:6]})]
        if intent in ("estimate_unmet_demand", "analyze_route_mix", "get_live_status", "explain_airport_score"):
            return [(intent, {"airport": codes[0]})]
        return [("get_airport_profile", {"airport": c}) for c in codes[:3]]

    def _answer(self, tool_msgs: list[dict[str, Any]]) -> AssistantTurn:
        parts = [
            "_Offline mode: the language model is unavailable, so this answer is assembled directly from the analyses._",
            "",
        ]
        assumptions, caveats = [], []
        for msg in tool_msgs:
            try:
                payload = json.loads(msg["content"] or "{}")
            except json.JSONDecodeError:  # truncated large result: fall back to its summary line
                match = re.search(r'"summary": "([^"]*)"', msg["content"])
                payload = {"summary": match.group(1) if match else "analysis completed"}
            if not payload.get("ok", True):
                parts.append(f"- Analysis failed: {payload.get('data', {}).get('error', 'unknown error')}")
                continue
            parts.append(f"- **{payload.get('summary', '')}**")
            text = payload.get("data", {}).get("text")
            if text:
                parts.append(text)
            assumptions += payload.get("assumptions", [])
            caveats += payload.get("caveats", [])
        if assumptions:
            parts += ["", "**Assumptions**"] + [f"- {a}" for a in dict.fromkeys(assumptions)]
        if caveats:
            parts += ["", "**Caveats**"] + [f"- {c}" for c in dict.fromkeys(caveats)]
        parts += ["", "See the evidence panel for the full tables and charts."]
        return self._text("\n".join(parts))

    def _text(self, text: str) -> AssistantTurn:
        return AssistantTurn(
            content=text,
            tool_calls=[],
            message={"role": "assistant", "content": text},
            provider=self.name,
            model=self.model,
        )
