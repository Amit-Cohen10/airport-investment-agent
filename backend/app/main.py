"""FastAPI app factory: wires the layers together (composition root).

    Repository (snapshot) -> InsightService (analytics) -> ToolRegistry (agent tools)
    LLM provider chain    -> AgentRuntime (loop)         -> API routes

Run:  uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.runtime import AgentRuntime
from app.agent.session_store import InMemorySessionStore
from app.agent.tools.definitions import build_tools
from app.agent.tools.registry import ToolRegistry
from app.api.routes import router
from app.config import Settings, get_settings
from app.data.connectors.live import LiveAviationClient
from app.data.repository import Repository
from app.llm.base import LLMClient
from app.llm.factory import NoLLMConfiguredError, build_llm
from app.llm.offline import OfflinePlanner
from app.services.insights import InsightService

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        repo = Repository(settings.snapshot_dir)
        live = LiveAviationClient(settings.live_api_timeout_s, settings.user_agent)
        service = InsightService.from_paths(repo, settings.scoring_config_path, settings.airport_notes_path, live)
        _ = service.default_scores  # warm caches (features + national scores) before the first request
        app.state.service = service
        app.state.llm_error = None
        app.state.runtime = None
        try:
            offline = OfflinePlanner(service.directory.find_in_text) if settings.offline_fallback else None
            model = llm or build_llm(settings, fallback=offline)
            app.state.runtime = AgentRuntime(
                model,
                ToolRegistry(service, build_tools()),
                InMemorySessionStore(),
                max_steps=settings.agent_max_steps,
                history_max_chars=settings.history_max_chars,
            )
        except NoLLMConfiguredError as exc:
            # The deterministic API (/api/rankings etc.) still works without an LLM key.
            app.state.llm_error = str(exc)
            log.warning(str(exc))
        yield

    app = FastAPI(title="Airport Investment Intelligence Agent", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(router)

    # In Docker the built frontend is served by the same process (single container, single port).
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
