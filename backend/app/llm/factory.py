"""Builds the provider chain and adds resilience: retry with backoff, then fail over.

primary (e.g. Gemini free tier) --429/5xx/timeout--> retry with backoff
                                --still failing---> next provider (e.g. Groq, Claude)
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from app.config import Settings
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import AssistantTurn, LLMClient, LLMError, RetryableLLMError
from app.llm.openai_compat import OpenAICompatClient

log = logging.getLogger(__name__)


class NoLLMConfiguredError(LLMError):
    pass


class ResilientLLM:
    """Implements the LLMClient protocol over an ordered list of providers."""

    def __init__(self, clients: list[LLMClient], attempts_per_provider: int = 2, base_delay_s: float = 2.0):
        if not clients:
            raise NoLLMConfiguredError(
                "No LLM provider configured. Set GEMINI_API_KEY (or GROQ_API_KEY / ANTHROPIC_API_KEY) in backend/.env"
            )
        self.clients = clients
        self.attempts = attempts_per_provider
        self.base_delay_s = base_delay_s

    def for_turn(self) -> _TurnLLM:
        """A per-turn view: once a fallback provider has answered, the rest of the turn stays on it
        (no re-hammering a rate-limited primary on every step, and no mixing providers mid-turn,
        which can break provider-specific state such as Gemini thought signatures).
        The next turn starts from the primary again."""
        return _TurnLLM(self)

    @property
    def name(self) -> str:
        return self.clients[0].name

    @property
    def model(self) -> str:
        return self.clients[0].model

    async def chat(self, messages: list[dict[str, Any]], tools=None, tool_choice: str = "auto") -> AssistantTurn:
        turn, _ = await self.chat_from(0, messages, tools, tool_choice)
        return turn

    async def chat_from(
        self, start: int, messages: list[dict[str, Any]], tools=None, tool_choice: str = "auto"
    ) -> tuple[AssistantTurn, int]:
        last: Exception | None = None
        for index in range(start, len(self.clients)):
            client = self.clients[index]
            for attempt in range(1, self.attempts + 1):
                try:
                    return await client.chat(messages, tools, tool_choice), index
                except RetryableLLMError as exc:
                    last = exc
                    if attempt < self.attempts:
                        delay = min(exc.retry_after_s or self.base_delay_s * 2 ** (attempt - 1), 20)
                        log.warning("%s retryable error (%s); retry in %.1fs", client.name, exc, delay)
                        await asyncio.sleep(delay + random.uniform(0, 0.5))
                except Exception as exc:  # non-retryable (LLMError) or adapter bug: fail over immediately
                    last = exc
                    log.error("%s failed: %s", client.name, exc)
                    break
            log.warning("provider %s exhausted, failing over", client.name)
        raise LLMError(f"All LLM providers failed. Last error: {last}")


class _TurnLLM:
    def __init__(self, chain: ResilientLLM):
        self.chain = chain
        self.start = 0
        self.name, self.model = chain.name, chain.model

    async def chat(self, messages: list[dict[str, Any]], tools=None, tool_choice: str = "auto") -> AssistantTurn:
        turn, self.start = await self.chain.chat_from(self.start, messages, tools, tool_choice)
        return turn


def build_llm(settings: Settings, fallback: LLMClient | None = None) -> ResilientLLM:
    """Provider chain from settings; `fallback` (the offline planner) is appended last."""
    clients: list[LLMClient] = []
    common = {"temperature": settings.llm_temperature, "timeout_s": settings.llm_timeout_s}
    for provider in settings.llm_providers:
        if provider == "gemini" and settings.gemini_api_key:
            clients.append(OpenAICompatClient("gemini", settings.gemini_api_key, settings.gemini_model, **common))
        elif provider == "groq" and settings.groq_api_key:
            clients.append(OpenAICompatClient("groq", settings.groq_api_key, settings.groq_model, **common))
        elif provider == "openrouter" and settings.openrouter_api_key:
            clients.append(
                OpenAICompatClient("openrouter", settings.openrouter_api_key, settings.openrouter_model, **common)
            )
        elif provider == "anthropic" and settings.anthropic_api_key:
            clients.append(
                AnthropicClient(
                    settings.anthropic_api_key,
                    settings.anthropic_model,
                    timeout_s=settings.llm_timeout_s,
                    effort=settings.anthropic_effort,
                )
            )
    if fallback is not None:
        clients.append(fallback)
    return ResilientLLM(clients)
