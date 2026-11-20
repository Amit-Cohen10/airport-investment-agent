"""Provider-neutral LLM interface.

Canonical conversation format = OpenAI chat messages (role/content/tool_calls/tool_call_id),
the de-facto standard that Gemini, Groq and OpenRouter all accept. Adapters for other APIs
(Anthropic) translate to and from it, so the agent runtime never knows which provider it uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from the model; validated by the tool registry, not trusted


@dataclass
class AssistantTurn:
    content: str | None
    tool_calls: list[ToolCall]
    message: dict[str, Any]  # assistant message to append to history (canonical format)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    usage: dict[str, int] = field(default_factory=dict)


class LLMError(RuntimeError):
    """Non-retryable provider error (bad request, auth)."""


class RetryableLLMError(LLMError):
    """Rate limit, timeout or 5xx: worth retrying or failing over to another provider."""

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class LLMClient(Protocol):
    name: str
    model: str

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> AssistantTurn: ...
