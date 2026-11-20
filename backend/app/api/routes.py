"""HTTP API.

POST /api/chat              SSE stream: session -> status/tool_start/tool_end ... -> answer | error
POST /api/chat/sync         same, single JSON response (handy for evals and curl)
GET  /api/health            snapshot manifest + configured LLM providers
GET  /api/rankings          deterministic ranking with NO LLM involved (proves the scoring stands alone)
GET  /api/airports/resolve  entity resolution
DELETE /api/sessions/{id}   forget a conversation
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.agent.runtime import AgentRuntime
from app.llm.base import LLMError
from app.services.insights import AnalysisError, InsightService

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(None, max_length=64)


def _runtime(request: Request) -> AgentRuntime:
    runtime = request.app.state.runtime
    if runtime is None:
        raise HTTPException(503, request.app.state.llm_error or "LLM not configured")
    return runtime


def _service(request: Request) -> InsightService:
    return request.app.state.service


@router.post("/chat")
async def chat_stream(body: ChatRequest, request: Request) -> EventSourceResponse:
    runtime = _runtime(request)
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(event: dict[str, Any]) -> None:
        await queue.put(event)

    async def worker() -> None:
        try:
            reply = await runtime.run(body.session_id, body.message, emit)
            await queue.put({"type": "answer", **reply.to_dict()})
        except LLMError as exc:
            log.warning("LLM failure: %s", exc)
            await queue.put(
                {
                    "type": "error",
                    "message": "The language model is unavailable right now "
                    "(rate limit or outage). Please retry in a minute.",
                }
            )
        except Exception:
            log.exception("chat failed")
            await queue.put({"type": "error", "message": "Unexpected server error."})
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(worker())
        try:
            while (event := await queue.get()) is not None:
                yield {"event": event["type"], "data": json.dumps(event, default=str)}
        finally:
            if not task.done():
                task.cancel()

    return EventSourceResponse(events())


@router.post("/chat/sync")
async def chat_sync(body: ChatRequest, request: Request) -> dict[str, Any]:
    try:
        reply = await _runtime(request).run(body.session_id, body.message)
    except LLMError as exc:
        log.warning("LLM failure: %s", exc)  # details stay in server logs, not in the response
        raise HTTPException(503, "The language model is unavailable right now. Please retry shortly.") from exc
    return reply.to_dict()


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    service = _service(request)
    runtime = request.app.state.runtime
    return {
        "status": "ok",
        "llm": None
        if runtime is None
        else {"providers": [f"{c.name}:{c.model}" for c in getattr(runtime.llm, "clients", [runtime.llm])]},
        "llm_error": request.app.state.llm_error,
        "data_periods": service.periods,
        "snapshot": service.repo.manifest,
    }


@router.get("/rankings")
def rankings(request: Request, region: str | None = None, top_n: int = Query(10, ge=1, le=50)) -> dict[str, Any]:
    service = _service(request)
    if region:
        found = service.directory.region_states(region)
        if not found:
            raise HTTPException(404, f"unknown region '{region}'")
        codes, label = service.directory.airports_in_states(found[1]), region
    else:
        codes, label = list(service.default_scores.table.index), "US"
    try:
        return service.rank_expansion(codes, label, top_n=top_n).model_dump(exclude_none=True)
    except AnalysisError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/airports/resolve")
def resolve(request: Request, q: str = Query(..., min_length=1, max_length=80)) -> dict[str, Any]:
    return _service(request).resolve([q]).model_dump(exclude_none=True)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request) -> dict[str, str]:
    _runtime(request).sessions.delete(session_id)
    return {"status": "deleted"}
