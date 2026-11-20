"""Context building: what the model sees on each call.

  system prompt (role, rules, data periods, date)
+ session state (airports in focus from earlier turns)
+ trimmed conversation history (whole turns only; tool calls stay paired with their results)
+ the new user message
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from app.agent.session_store import Session

PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
MAX_TOOL_RESULT_CHARS = 12_000


def render_system_prompt(periods: dict[str, str], manifest: dict[str, Any]) -> str:
    lines = [f"- {k}: {v}" for k, v in periods.items()]
    stale = [n for n, m in manifest.get("tables", {}).items() if not str(m.get("status", "ok")).startswith("ok")]
    if stale:
        lines.append(f"- WARNING: stale tables (last refresh failed): {', '.join(stale)}")
    return PROMPT_PATH.read_text().format(today=date.today().isoformat(), data_periods="\n".join(lines))


def tool_message(call_id: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Tool result for the model, always valid JSON and within budget.

    The small, important fields (summary, confidence, assumptions, caveats, sources) go first; if
    the whole thing is too big, long lists inside `data` are shortened step by step (never a raw
    string cut, which would break the JSON and drop the caveats the model must cite).
    """
    ordered = {
        k: payload[k] for k in ("ok", "summary", "confidence", "assumptions", "caveats", "sources") if k in payload
    }
    ordered |= {k: v for k, v in payload.items() if k not in ordered}
    content = json.dumps(ordered, default=str)
    for cap in (15, 8, 4, 2):
        if len(content) <= MAX_TOOL_RESULT_CHARS:
            break
        trimmed = dict(ordered, data=_cap_lists(ordered.get("data", {}), cap), truncated=f"lists capped at {cap} items")
        content = json.dumps(trimmed, default=str)
    if len(content) > MAX_TOOL_RESULT_CHARS:  # last resort: keep the envelope, drop the data
        content = json.dumps({k: v for k, v in ordered.items() if k != "data"} | {"truncated": "data omitted"})
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def _cap_lists(node: Any, cap: int) -> Any:
    if isinstance(node, list):
        return [_cap_lists(v, cap) for v in node[:cap]]
    if isinstance(node, dict):
        return {k: _cap_lists(v, cap) for k, v in node.items()}
    return node


def trim_history(messages: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Keep the most recent whole turns (a turn starts at a user message) within a char budget.

    Walks backwards once, summing sizes, so cost is linear in history length.
    """
    total, keep_from = 0, None
    for i in range(len(messages) - 1, -1, -1):
        total += len(json.dumps(messages[i], default=str))
        if messages[i]["role"] == "user":
            if total > max_chars and keep_from is not None:
                break
            keep_from = i
            if total > max_chars:  # even the newest turn is too big: keep it anyway
                break
    return messages[keep_from:] if keep_from is not None else []


def build_messages(system_prompt: str, session: Session, user_text: str, max_chars: int) -> list[dict[str, Any]]:
    system = system_prompt
    if session.focus_airports:
        system += (
            f"\n\n## Session state\nAirports discussed so far (most recent last): {', '.join(session.focus_airports)}."
        )
    history = trim_history(session.messages, max_chars)
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]
