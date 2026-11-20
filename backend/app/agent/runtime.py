"""The agent runtime: a deterministic loop around a non-deterministic model.

    user message
      -> build context (system prompt + session state + history)
      -> LLM call with tool schemas
      -> model asks for tools?  validate args in code -> run deterministic handlers -> append results -> loop
      -> model answers?        numeric grounding check -> assemble answer + evidence -> save session

The model decides *which* analysis to run and *how to explain it*; code decides *what is true*.
Hard limits (max steps, result truncation, timeouts) keep a confused model from looping forever.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.context import build_messages, render_system_prompt, tool_message
from app.agent.guardrails import CLAUSE_TOPICS, Grade, check_grounding, confidence_overstated
from app.agent.session_store import Session, SessionStore
from app.agent.tools.registry import ToolRegistry
from app.domain.models import ToolResult
from app.llm.base import AssistantTurn, LLMClient, LLMError

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]
# The only tool arguments that count as evidence: what-if parameters that change a computed result.
# Tool argument models forbid unknown fields, so these are always parameters that were applied.
WHAT_IF_PARAMS = {
    "demand_momentum_weight",
    "capacity_utilization_weight",
    "operational_strain_weight",
    "revenue_potential_weight",
    "long_haul_threshold_mi",
}
WEIGHT_PARAMS = {p for p in WHAT_IF_PARAMS if p.endswith("_weight")}
SCORING_TOOLS = {"rank_expansion_candidates", "explain_airport_score"}  # the tools that take pillar weights


async def _noop(_: dict[str, Any]) -> None:
    return None


@dataclass
class TurnTrace:
    steps: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    providers: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def record_llm(self, turn: AssistantTurn) -> None:
        self.llm_calls += 1
        self.prompt_tokens += turn.usage.get("prompt_tokens", 0)
        self.completion_tokens += turn.usage.get("completion_tokens", 0)
        self.providers.append(f"{turn.provider}:{turn.model}")
        self.steps.append(
            {
                "type": "llm",
                "provider": turn.provider,
                "ms": turn.latency_ms,
                "tool_calls": [c.name for c in turn.tool_calls],
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "providers": sorted(set(self.providers)),
            "total_ms": int((time.perf_counter() - self.started) * 1000),
        }


@dataclass
class AgentReply:
    session_id: str
    answer: str
    evidence: list[ToolResult]
    grounding: dict[str, Any]
    trace: dict[str, Any]
    evidence_from_earlier_turn: bool = False

    def to_dict(self) -> dict[str, Any]:
        sources, seen = [], set()
        for result in self.evidence:
            for p in result.provenance:
                key = (p.source, p.period)
                if key not in seen:
                    seen.add(key)
                    sources.append(p.model_dump(exclude_none=True))
        return {
            "session_id": self.session_id,
            "answer": self.answer,
            "evidence": [r.model_dump(exclude_none=True) for r in self.evidence if r.ok],
            "sources": sources,
            "grounding": self.grounding,
            "trace": self.trace,
            "evidence_from_earlier_turn": self.evidence_from_earlier_turn,
        }


class AgentRuntime:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        sessions: SessionStore,
        *,
        max_steps: int = 8,
        history_max_chars: int = 60_000,
        tool_timeout_s: float = 45.0,
    ):
        self.llm = llm
        self.registry = registry
        self.sessions = sessions
        self.max_steps = max_steps
        self.history_max_chars = history_max_chars
        self.tool_timeout_s = tool_timeout_s
        # Weak values: a session's lock disappears once no request holds or awaits it, so the map
        # never outgrows the live sessions (waiting requests keep a strong reference).
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def _system_prompt(self) -> str:
        repo = self.registry.service.repo
        return render_system_prompt(self.registry.service.periods, repo.manifest)

    async def run(self, session_id: str | None, user_text: str, emit: Emit = _noop) -> AgentReply:
        session = self.sessions.get_or_create(session_id)
        # One turn at a time per session, so concurrent requests can't interleave their history.
        lock = self._locks.get(session.id)
        if lock is None:
            lock = self._locks[session.id] = asyncio.Lock()
        async with lock:
            return await self._run_locked(session, session_id, user_text, emit)

    async def _run_locked(self, session: Session, requested_id: str | None, user_text: str, emit: Emit) -> AgentReply:
        # resumed=False with a requested id means the server no longer had that conversation
        # (expired or restarted): the UI tells the user the context was reset.
        await emit({"type": "session", "session_id": session.id, "resumed": session.resumed or requested_id is None})
        llm = self.llm.for_turn() if hasattr(self.llm, "for_turn") else self.llm
        messages = build_messages(self._system_prompt(), session, user_text, self.history_max_chars)
        new_messages: list[dict[str, Any]] = [messages[-1]]  # appended to the session at the end
        evidence: list[ToolResult] = []
        trace = TurnTrace()
        tools = self.registry.schemas()
        answer: str | None = None

        for step in range(self.max_steps):
            await emit({"type": "status", "message": "Thinking..." if step == 0 else "Reviewing results..."})
            turn = await llm.chat(messages, tools)
            trace.record_llm(turn)
            if not turn.tool_calls:
                answer = (turn.content or "").strip()
                if answer:  # never persist an empty assistant message (some providers reject it later)
                    messages.append(turn.message)
                    new_messages.append(turn.message)
                break
            messages.append(turn.message)
            new_messages.append(turn.message)
            results = await asyncio.gather(
                *(self._run_tool(c.id, c.name, c.arguments, emit, trace) for c in turn.tool_calls)
            )
            for call, result in zip(turn.tool_calls, results, strict=True):
                msg = tool_message(call.id, call.name, result.for_llm())
                messages.append(msg)
                new_messages.append(msg)
                if result.ok:
                    evidence.append(result)

        if not answer:
            # Step budget exhausted or empty reply: force a final answer without tools.
            await emit({"type": "status", "message": "Summarizing..."})
            # The nudge is marked synthetic: it is never persisted, and the offline planner ignores it.
            messages.append(
                {
                    "role": "user",
                    "content": "Write the final answer now using only the tool results above.",
                    "synthetic": True,
                }
            )
            turn = await llm.chat(messages, tools, tool_choice="none")
            trace.record_llm(turn)
            answer = (turn.content or "").strip() or "I could not complete this analysis. Please rephrase the question."
            new_messages.append({"role": "assistant", "content": answer})

        # Evidence = the *successful* tool results the model had in its context (this turn and the
        # history it was shown). Never evidence: failed results and error text, echoed queries, the
        # user's text, or free-form tool arguments. The only arguments that count are explicit
        # what-if parameters (weights, thresholds) of successful calls, since they define the result.
        seen = [_safe_json(m["content"]) for m in messages if m.get("role") == "tool"]
        results = [p for p in seen if isinstance(p, dict) and p.get("ok", True) is not False]
        params = [
            {k: v for k, v in s["arguments"].items() if k in WHAT_IF_PARAMS}
            for s in trace.steps
            if s["type"] == "tool" and s.get("ok") and isinstance(s["arguments"], dict)
        ]
        grounding = check_grounding(answer, results + params)
        # A follow-up answered from earlier results (no new analysis this turn) still shows the evidence
        # it relies on: the earlier analyses whose figures the answer actually uses, labeled as such.
        # Selection is by the airports and analyses the answer names, then by matching figures, so a
        # qualitative follow-up ("the SFO spill estimate is reliable") is tied to its evidence too.
        shown = evidence
        from_earlier = False
        if not evidence:
            shown = _supporting_results(answer, session.evidence_history)
            from_earlier = bool(shown)
        # Confidence claims are checked against the evidence the answer rests on (this turn's or carried),
        # graded per analysis and per airport.
        grounding.confidence_overstated = confidence_overstated(answer, [], graded=_grades(shown))
        self._update_session(session, new_messages, evidence, trace)
        return AgentReply(session.id, answer, shown, grounding.to_dict(), trace.to_dict(), from_earlier)

    async def _run_tool(self, call_id: str, name: str, arguments: str, emit: Emit, trace: TurnTrace) -> ToolResult:
        await emit({"type": "tool_start", "id": call_id, "tool": name, "arguments": _safe_json(arguments)})
        started = time.perf_counter()
        try:
            # Tools are sync (pandas/DuckDB/httpx); run them off the event loop.
            result = await asyncio.wait_for(
                asyncio.to_thread(self.registry.execute, name, arguments), self.tool_timeout_s
            )
        except TimeoutError:
            result = ToolResult(
                tool=name,
                ok=False,
                summary="error: timed out",
                data={"error": "The analysis timed out. Tell the user; do not guess."},
            )
        ms = int((time.perf_counter() - started) * 1000)
        trace.steps.append(
            {
                "type": "tool",
                "tool": name,
                "arguments": _safe_json(arguments),
                "ok": result.ok,
                "summary": result.summary,
                "ms": ms,
            }
        )
        await emit(
            {"type": "tool_end", "id": call_id, "tool": name, "ok": result.ok, "summary": result.summary, "ms": ms}
        )
        return result

    def _update_session(
        self, session: Session, new_messages: list[dict[str, Any]], evidence: list[ToolResult], trace: TurnTrace
    ) -> None:
        session.messages.extend(new_messages)
        # The what-if scenario is whatever weights the latest successful scoring call used (none = defaults).
        # Later scoring questions keep it until the user resets it (the context tells the model so).
        scoring = [
            s
            for s in trace.steps
            if s["type"] == "tool" and s.get("ok") and s["tool"] in SCORING_TOOLS and isinstance(s["arguments"], dict)
        ]
        if scoring:
            session.scenario_weights = {
                k: v for k, v in scoring[-1]["arguments"].items() if k in WEIGHT_PARAMS and v is not None
            }
        focus = []
        for r in evidence:
            focus += _airports_in(r.data)
        if focus:
            session.focus_airports = list(dict.fromkeys(session.focus_airports + focus))[-12:]
        session.last_tools = [r.tool for r in evidence]
        if evidence:
            # Most recent last; bounded so selection stays cheap and memory stays flat.
            session.evidence_history = (session.evidence_history + [r for r in evidence if r.ok])[-EVIDENCE_HISTORY:]
        self.sessions.save(session)


EVIDENCE_HISTORY = 12


def _supporting_results(answer: str, history: list[ToolResult], limit: int = 3) -> list[ToolResult]:
    """Earlier results the answer rests on.

    When the answer names airports, only results about those airports qualify (a coincidental figure in
    another airport's result is not support). Among them: results whose figures the answer uses, or whose
    analysis it names; most matched figures first, then most recent.
    """
    named = set(re.findall(r"\b[A-Z]{3}\b", answer))
    about = [r for r in history if named & set(_airports_in(r.data))]
    pool = about or history
    topics = {t for t, pattern in CLAUSE_TOPICS.items() if pattern.search(answer)}
    scored = []
    for order, result in enumerate(pool):
        report = check_grounding(answer, [result.for_llm()])
        matched = report.checked - len(report.unverified)
        on_topic = result.tool in topics
        if matched > 0 or (on_topic and (about or not named)):
            scored.append((matched, on_topic, order, result))
    scored.sort(key=lambda t: (-t[0], -t[1], -t[2]))
    return [r for *_, r in scored[:limit]]


def _grades(evidence: list[ToolResult]) -> list[Grade]:
    """Every confidence grade in the results: per analysis, and per airport within it."""
    grades: list[Grade] = []
    for r in evidence:
        if r.confidence:
            grades.append(Grade(r.tool, _primary_airport(r.data), r.confidence.value))
        for key in ("ranking", "airports"):
            for row in r.data.get(key, []) or []:
                if isinstance(row, dict) and row.get("confidence"):
                    code = row.get("code") or row.get("airport")
                    if isinstance(code, str):
                        grades.append(Grade(r.tool, code, str(row["confidence"])))
    return grades


def _primary_airport(data: dict[str, Any]) -> str | None:
    """The single airport a result is about: "airport" is a code, or (profiles) an object with one."""
    airport = data.get("airport")
    if isinstance(airport, dict):
        airport = airport.get("code")
    return airport if isinstance(airport, str) else None


def _airports_in(data: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if primary := _primary_airport(data):
        codes.append(primary)
    for key in ("ranking", "airports"):
        for item in data.get(key, []) or []:
            if isinstance(item, dict):
                code = item.get("code") or item.get("airport")
                if isinstance(code, str):
                    codes.append(code)
    return codes[:10]


def _safe_json(raw: str) -> Any:
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return raw


__all__ = ["AgentRuntime", "AgentReply", "LLMError"]
