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

from app.agent.session_store import RESET_WEIGHTS
from app.llm.base import AssistantTurn, ToolCall

INTENTS: list[tuple[str, re.Pattern[str]]] = [
    ("explain_methodology", re.compile(r"\b(how (is|are|do)|methodolog|calculat|formula)\b", re.I)),
    ("estimate_unmet_demand", re.compile(r"\b(unmet|spill|pent[- ]up|latent|turned away)\b", re.I)),
    ("analyze_route_mix", re.compile(r"\b(long[- ]?haul|route mix|distance|short[- ]?haul)\b", re.I)),
    ("compare_congestion", re.compile(r"\b(congest|delay|crowd|busy|bottleneck)", re.I)),
    ("get_live_status", re.compile(r"\b(right now|currently|live|today|weather)\b", re.I)),
    ("rank_expansion_candidates", re.compile(r"\b(candidate|expan|rank|best|top|invest|opportunit)", re.I)),
    ("explain_airport_score", re.compile(r"\bwhy\b.*\b(score|rank|high|low)", re.I)),
]

WEIGHT_WHAT_IF = re.compile(
    r"\b(weight|emphasi[sz]e|prioriti[sz]e|value)\b.*\b(growth|demand)\b|\b(growth|demand)\b.*\b(more|higher|double|twice)\b",
    re.I,
)
THRESHOLD = re.compile(r"\b(\d{1,2},?\d{3})\s*-?\s*(?:mi\b|miles?\b|mile\b)", re.I)


def _threshold_mi(text: str) -> float | None:
    m = THRESHOLD.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def _last_analysis(history: list[dict[str, Any]]) -> tuple[str, dict[str, Any]] | None:
    """The most recent analysis tool call in earlier turns (resolution calls don't count)."""
    for msg in reversed(history):
        for call in reversed(msg.get("tool_calls") or []):
            name = call["function"]["name"]
            if name != "resolve_airports":
                try:
                    return name, json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    return None
    return None


SCENARIO = re.compile(r"Active what-if scenario[^:]*: (.+?)\. Pass these")


def _scenario(system_prompt: str) -> dict[str, float]:
    """The active what-if weights the runtime put in the session state, e.g. {"demand_momentum_weight": 0.6}."""
    m = SCENARIO.search(system_prompt)
    if not m:
        return {}
    pairs = (item.split("=") for item in m.group(1).split(", ") if "=" in item)
    return {k.strip(): float(v) for k, v in pairs if k.strip().endswith("_weight")}


# Resolver returns (airport codes, region name or None) found in free text.
Resolver = Callable[[str], tuple[list[str], str | None]]


class OfflinePlanner:
    name = "offline"
    model = "rule-based"

    def __init__(self, resolver: Resolver):
        self.resolve = resolver

    async def chat(self, messages: list[dict[str, Any]], tools=None, tool_choice: str = "auto") -> AssistantTurn:
        last_user = max(i for i, m in enumerate(messages) if m["role"] == "user" and not m.get("synthetic"))
        tool_msgs = [m for m in messages[last_user + 1 :] if m["role"] == "tool"]
        if tool_msgs or not tools:
            return self._answer(tool_msgs)
        question = messages[last_user]["content"]
        calls = self._plan(question, messages[0].get("content", ""), _last_analysis(messages[:last_user]))
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

    def _plan(
        self, question: str, system_prompt: str, last: tuple[str, dict[str, Any]] | None = None
    ) -> list[tuple[str, dict[str, Any]]]:
        codes, region = self.resolve(question)
        named_airport = bool(codes)
        if not codes and not region:  # follow-up: reuse airports from the session state
            focus = re.search(r"Airports discussed so far[^:]*: ([A-Z, ]+)\.", system_prompt)
            codes = [c.strip() for c in focus.group(1).split(",")][-2:][::-1] if focus else []  # most recent first
        intent = next((name for name, pattern in INTENTS if pattern.search(question)), None)
        threshold = _threshold_mi(question)
        # An explicit request for the default weights ends the what-if scenario: no overrides are sent,
        # and the runtime then records the default (empty) scenario for the session.
        reset = bool(RESET_WEIGHTS.search(question))
        what_if_weights = bool(WEIGHT_WHAT_IF.search(question)) and not reset
        scenario = {} if reset else _scenario(system_prompt)
        if (
            reset
            and last
            and last[0] in ("rank_expansion_candidates", "explain_airport_score")
            and not region
            and not named_airport
        ):
            # "Use the default weights": re-run the previous scoring analysis without the overrides.
            return [(last[0], {k: v for k, v in last[1].items() if not k.endswith("_weight")})]

        # 1. Explicit parameter changes to the previous analysis come first ("weight growth twice as much",
        #    "and at 4,000 miles?"): re-run the same tool with the same scope and the new parameter.
        if last and what_if_weights and last[0] == "rank_expansion_candidates":
            return [(last[0], {**last[1], "demand_momentum_weight": 0.6})]
        if last and threshold and last[0] == "analyze_route_mix" and intent in (None, "analyze_route_mix"):
            return [(last[0], {**last[1], "long_haul_threshold_mi": threshold})]
        # 2. "What about Bangor?": same analysis as before, for the newly named airport.
        if last and named_airport and intent is None and not region:
            name, args = last
            if name == "compare_congestion":
                return [(name, {"airports": codes[:6]})]
            if name in ("rank_expansion_candidates", "explain_airport_score"):
                # Keep the scenario: a what-if weighting set earlier still applies to the explanation.
                weights = {} if reset else {k: v for k, v in args.items() if k.endswith("_weight")}
                return [("explain_airport_score", {"airport": codes[0], **weights})]
            if "airport" in args:
                return [(name, {**args, "airport": codes[0]})]
        # 3. Fresh questions, by keyword intent.
        if intent == "explain_methodology":
            topic = "unmet_demand" if "spill" in question.lower() or "unmet" in question.lower() else "expansion_score"
            return [(intent, {"topic": topic})]
        if intent == "rank_expansion_candidates" or what_if_weights or (region and intent is None):
            rank_args: dict[str, Any] = {"region": region} if region else {"airports": codes} if codes else {}
            rank_args |= scenario  # an active what-if scenario carries over, unless the user reset it
            if what_if_weights:
                rank_args["demand_momentum_weight"] = 0.6
            return [("rank_expansion_candidates", rank_args)]
        if not codes:
            return []
        if intent == "compare_congestion":
            return [(intent, {"airports": codes[:6]})]
        if intent == "analyze_route_mix":
            return [(intent, {"airport": codes[0], **({"long_haul_threshold_mi": threshold} if threshold else {})})]
        if intent == "explain_airport_score":
            return [(intent, {"airport": codes[0], **scenario})]
        if intent in ("estimate_unmet_demand", "get_live_status"):
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
