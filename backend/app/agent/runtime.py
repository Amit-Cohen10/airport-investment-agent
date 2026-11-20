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
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agent.context import build_messages, render_system_prompt, tool_message
from app.agent.guardrails import check_grounding
from app.agent.session_store import Session, SessionStore
from app.agent.tools.registry import ToolRegistry
from app.domain.models import ToolResult
from app.llm.base import AssistantTurn, LLMClient, LLMError

log = logging.getLogger(__name__)

Emit = Callable[[dict[str, Any]], Awaitable[None]]


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

    def _system_prompt(self) -> str:
        repo = self.registry.service.repo
        return render_system_prompt(self.registry.service.periods, repo.manifest)

    async def run(self, session_id: str | None, user_text: str, emit: Emit = _noop) -> AgentReply:
        session = self.sessions.get_or_create(session_id)
        await emit({"type": "session", "session_id": session.id})
        messages = build_messages(self._system_prompt(), session, user_text, self.history_max_chars)
        new_messages: list[dict[str, Any]] = [messages[-1]]  # appended to the session at the end
        evidence: list[ToolResult] = []
        trace = TurnTrace()
        tools = self.registry.schemas()
        answer: str | None = None

        for step in range(self.max_steps):
            await emit({"type": "status", "message": "Thinking..." if step == 0 else "Reviewing results..."})
            turn = await self.llm.chat(messages, tools)
            trace.record_llm(turn)
            messages.append(turn.message)
            new_messages.append(turn.message)
            if not turn.tool_calls:
                answer = (turn.content or "").strip()
                break
            results = await asyncio.gather(*(self._run_tool(c.name, c.arguments, emit, trace) for c in turn.tool_calls))
            for call, result in zip(turn.tool_calls, results, strict=True):
                msg = tool_message(call.id, call.name, result.for_llm())
                messages.append(msg)
                new_messages.append(msg)
                if result.ok:
                    evidence.append(result)

        if not answer:
            # Step budget exhausted or empty reply: force a final answer without tools.
            await emit({"type": "status", "message": "Summarizing..."})
            messages.append(
                {"role": "user", "content": "Write the final answer now using only the tool results above."}
            )
            turn = await self.llm.chat(messages, tools=None)
            trace.record_llm(turn)
            answer = (turn.content or "").strip() or "I could not complete this analysis. Please rephrase the question."
            new_messages.append({"role": "assistant", "content": answer})

        grounding = check_grounding(answer, [r.for_llm() for r in evidence], user_text)
        self._update_session(session, new_messages, evidence)
        return AgentReply(session.id, answer, evidence, grounding.to_dict(), trace.to_dict())

    async def _run_tool(self, name: str, arguments: str, emit: Emit, trace: TurnTrace) -> ToolResult:
        await emit({"type": "tool_start", "tool": name, "arguments": _safe_json(arguments)})
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
        await emit({"type": "tool_end", "tool": name, "ok": result.ok, "summary": result.summary, "ms": ms})
        return result

    def _update_session(self, session: Session, new_messages: list[dict[str, Any]], evidence: list[ToolResult]) -> None:
        session.messages.extend(new_messages)
        focus = []
        for r in evidence:
            focus += _airports_in(r.data)
        if focus:
            session.focus_airports = list(dict.fromkeys(session.focus_airports + focus))[-12:]
        session.last_tools = [r.tool for r in evidence]
        self.sessions.save(session)


def _airports_in(data: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if isinstance(data.get("airport"), str):
        codes.append(data["airport"])
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
