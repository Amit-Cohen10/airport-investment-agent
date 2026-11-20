"""Claude adapter: translates canonical (OpenAI-format) messages to the Anthropic Messages API."""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from app.llm.base import AssistantTurn, LLMError, RetryableLLMError, ToolCall


def to_anthropic(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    system_parts, out = [], []
    for msg in messages:
        role = msg["role"]
        if role == "system":
            system_parts.append(msg["content"])
        elif role == "user":
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append({"type": "text", "text": msg["content"]})
            else:
                out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if msg.get("content"):
                blocks.append({"type": "text", "text": msg["content"]})
            for tc in msg.get("tool_calls", []):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["function"]["name"],
                        "input": _parse_args(tc["function"].get("arguments")),
                    }
                )
            if blocks:  # empty assistant turns are rejected by the Messages API
                out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": msg["tool_call_id"], "content": msg["content"]}
            # Consecutive tool results belong in one user message.
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return "\n\n".join(system_parts), out


def _parse_args(raw: str | None) -> dict[str, Any]:
    """Tool arguments in history may be malformed (from another provider); never crash on them."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, temperature: float = 0.1, timeout_s: float = 60.0):
        self.model = model
        self.temperature = temperature
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    async def chat(self, messages, tools=None, tool_choice: str = "auto") -> AssistantTurn:
        system, msgs = to_anthropic(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "system": system,
            "messages": msgs,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]
            kwargs["tool_choice"] = {"type": "none" if tool_choice == "none" else "auto"}
        started = time.perf_counter()
        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise RetryableLLMError(f"anthropic rate limited: {exc}") from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise RetryableLLMError(f"anthropic unavailable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic HTTP {exc.status_code}: {exc.message}") from exc
        text = "".join(b.text for b in response.content if b.type == "text") or None
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=json.dumps(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        return AssistantTurn(
            content=text,
            tool_calls=calls,
            message=message,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage={"prompt_tokens": response.usage.input_tokens, "completion_tokens": response.usage.output_tokens},
        )
