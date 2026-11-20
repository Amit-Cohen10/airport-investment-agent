"""One adapter for every OpenAI-compatible endpoint: Gemini, Groq, OpenRouter."""

from __future__ import annotations

import time
from typing import Any

import openai
from openai import AsyncOpenAI

from app.llm.base import AssistantTurn, LLMError, RetryableLLMError, ToolCall

BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}

# Gemini attaches "thought signatures" in `extra_content`; it must be echoed back to Gemini,
# but other providers may reject unknown fields, so it is stripped for them.
_MESSAGE_KEYS = {  # anything else (e.g. our "synthetic" marker) is stripped
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "name",
    "extra_content",
}
_TOOL_CALL_KEYS = {"id", "type", "function", "extra_content"}


class OpenAICompatClient:
    def __init__(self, provider: str, api_key: str, model: str, *, temperature: float = 0.1, timeout_s: float = 60.0):
        self.name = provider
        self.model = model
        self.temperature = temperature
        self._client = AsyncOpenAI(api_key=api_key, base_url=BASE_URLS[provider], timeout=timeout_s, max_retries=0)

    def _sanitize(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        keep_extra = self.name == "gemini"
        clean = []
        for msg in messages:
            m = {k: v for k, v in msg.items() if k in _MESSAGE_KEYS and (keep_extra or k != "extra_content")}
            m.pop("name", None)  # optional in the spec, and Groq rejects it
            if "tool_calls" in m:
                m["tool_calls"] = [
                    {k: v for k, v in tc.items() if k in _TOOL_CALL_KEYS and (keep_extra or k != "extra_content")}
                    for tc in m["tool_calls"]
                ]
            if m.get("role") == "assistant" and not m.get("content") and not m.get("tool_calls"):
                continue  # empty assistant turns are rejected by some endpoints
            clean.append(m)
        return clean

    async def chat(self, messages, tools=None, tool_choice: str = "auto") -> AssistantTurn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._sanitize(messages),
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except openai.RateLimitError as exc:
            retry_after = _retry_after(exc)
            raise RetryableLLMError(f"{self.name} rate limited: {exc}", retry_after) from exc
        except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as exc:
            raise RetryableLLMError(f"{self.name} unavailable: {exc}") from exc
        except openai.APIStatusError as exc:
            if exc.status_code in (408, 409, 425, 500, 502, 503, 504):
                raise RetryableLLMError(f"{self.name} HTTP {exc.status_code}") from exc
            raise LLMError(f"{self.name} HTTP {exc.status_code}: {exc.message}") from exc
        latency = int((time.perf_counter() - started) * 1000)
        if not response.choices:
            raise RetryableLLMError(f"{self.name} returned no choices")
        msg = response.choices[0].message
        raw = msg.model_dump(exclude_none=True)
        raw["role"] = "assistant"
        calls = [
            ToolCall(id=tc.id or f"call_{i}", name=tc.function.name, arguments=tc.function.arguments or "{}")
            for i, tc in enumerate(msg.tool_calls or [])
        ]
        if calls:  # make sure ids in history match what we return
            for tc, call in zip(raw.get("tool_calls", []), calls, strict=False):
                tc["id"] = call.id
        usage = {}
        if response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens or 0,
                "completion_tokens": response.usage.completion_tokens or 0,
            }
        return AssistantTurn(
            content=msg.content,
            tool_calls=calls,
            message=raw,
            provider=self.name,
            model=self.model,
            latency_ms=latency,
            usage=usage,
        )


def _retry_after(exc: openai.APIStatusError) -> float | None:
    try:
        value = exc.response.headers.get("retry-after")
        return float(value) if value else None
    except (AttributeError, ValueError):
        return None
