"""Shared fixtures. Integration tests run against the committed data snapshot (real public data)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.config import get_settings
from app.data.repository import Repository
from app.llm.base import AssistantTurn, ToolCall
from app.services.insights import InsightService


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def service(settings) -> InsightService:
    repo = Repository(settings.snapshot_dir)
    return InsightService.from_paths(repo, settings.scoring_config_path, settings.airport_notes_path, live=None)


class FakeLLM:
    """Scripted LLM: each entry is either ('tools', [(name, args), ...]) or ('text', '...').

    Lets us test the runtime loop deterministically, with no network and no API key.
    """

    name = "fake"
    model = "scripted"

    def __init__(self, script: list[tuple[str, Any]]):
        self.script = list(script)
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages, tools=None, tool_choice="auto") -> AssistantTurn:
        self.calls.append([dict(m) for m in messages])
        kind, payload = self.script.pop(0)
        if kind == "text":
            return AssistantTurn(
                content=payload,
                tool_calls=[],
                message={"role": "assistant", "content": payload},
                provider=self.name,
                model=self.model,
            )
        calls = [
            ToolCall(id=f"c{i}", name=n, arguments=a if isinstance(a, str) else json.dumps(a))
            for i, (n, a) in enumerate(payload)
        ]
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ],
        }
        return AssistantTurn(content=None, tool_calls=calls, message=message, provider=self.name, model=self.model)


@pytest.fixture
def fake_llm_factory():
    return FakeLLM
