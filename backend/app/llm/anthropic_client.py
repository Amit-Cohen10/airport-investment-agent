"""Claude adapter: translates canonical (OpenAI-format) messages to the Anthropic Messages API.

Used as the paid backup behind the free Gemini tier (see factory.py). Current Claude models
differ from OpenAI-style APIs in ways this adapter handles explicitly:

* Thinking is always on (adaptive). Within one agent turn, the assistant's raw content blocks,
  thinking blocks included, are replayed unchanged on the next step. They are kept on the
  canonical message under `anthropic_blocks`; other providers' sanitizers drop that key.
* "Preserved thinking": a thinking block is bound to the exact conversation prefix (system
  prompt, tools, earlier messages) that produced it. Our system prompt carries per-session state
  and history is trimmed from the front, so thinking blocks from *earlier* turns are never
  replayed, only the current turn's. As a safety net, the request also asks the API to drop
  (not reject) any block whose prefix no longer matches.
* Sampling parameters (temperature) are rejected by current models, so none are sent.
* Refusals: the server-side fallback lets the API retry a declined request on another Claude
  model; a final refusal raises so the chain fails over to the offline planner.
"""

from __future__ import annotations

import json
import time
from typing import Any

import anthropic

from app.llm.base import AssistantTurn, LLMError, RetryableLLMError, ToolCall

BETAS = ["thinking-binding-controls-2026-08-01", "server-side-fallback-2026-07-01"]
REPLAYABLE_BLOCKS = {"text", "thinking", "redacted_thinking", "tool_use"}


def _current_turn_start(messages: list[dict[str, Any]]) -> int:
    """Index of the last real user message (the synthetic 'write the final answer' nudge doesn't count)."""
    real = [i for i, m in enumerate(messages) if m["role"] == "user" and not m.get("synthetic")]
    return real[-1] if real else 0


def to_anthropic(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    turn_start = _current_turn_start(messages)
    system_parts, out = [], []
    for index, msg in enumerate(messages):
        role = msg["role"]
        if role == "system":
            system_parts.append(msg["content"])
        elif role == "user":
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append({"type": "text", "text": msg["content"]})
            else:
                out.append({"role": "user", "content": msg["content"]})
        elif role == "assistant":
            raw = msg.get("anthropic_blocks")
            if raw and index > turn_start:
                blocks = raw  # this turn's own blocks, replayed byte-for-byte (thinking included)
            else:
                blocks = [{"type": "text", "text": msg["content"]}] if msg.get("content") else []
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

    def __init__(self, api_key: str, model: str, *, timeout_s: float = 60.0, effort: str | None = None, **_: Any):
        self.model = model
        self.effort = effort  # None = the model's default effort
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout_s, max_retries=0)

    async def chat(self, messages, tools=None, tool_choice: str = "auto") -> AssistantTurn:
        system, msgs = to_anthropic(messages)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,  # thinking shares this budget; too low truncates mid-answer
            "system": system,
            "messages": msgs,
            "betas": BETAS,
            "thinking": {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            "fallbacks": "default",
        }
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if tools:
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]
            # Only auto/none: current models reject forced tool_choice ("any"/"tool").
            kwargs["tool_choice"] = {"type": "none" if tool_choice == "none" else "auto"}
        started = time.perf_counter()
        try:
            response = await self._client.beta.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            raise RetryableLLMError(f"anthropic rate limited: {exc}") from exc
        except (anthropic.APITimeoutError, anthropic.APIConnectionError, anthropic.InternalServerError) as exc:
            raise RetryableLLMError(f"anthropic unavailable: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"anthropic HTTP {exc.status_code}: {exc.message}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("anthropic declined the request (refusal)")

        text = "".join(b.text for b in response.content if b.type == "text") or None
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=json.dumps(b.input))
            for b in response.content
            if b.type == "tool_use"
        ]
        message: dict[str, Any] = {
            "role": "assistant",
            "content": text,
            "anthropic_blocks": [
                b.model_dump(mode="json", exclude_none=True) for b in response.content if b.type in REPLAYABLE_BLOCKS
            ],
        }
        if calls:
            message["tool_calls"] = [
                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}} for c in calls
            ]
        return AssistantTurn(
            content=text,
            tool_calls=calls,
            message=message,
            provider=self.name,
            model=response.model or self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            usage={"prompt_tokens": response.usage.input_tokens, "completion_tokens": response.usage.output_tokens},
        )
