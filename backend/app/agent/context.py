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
    content = json.dumps(payload, default=str)
    if len(content) > MAX_TOOL_RESULT_CHARS:
        content = content[:MAX_TOOL_RESULT_CHARS] + '..."(truncated)"'
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


def trim_history(messages: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Keep the most recent whole turns (a turn starts at a user message) within a char budget."""
    turn_starts = [i for i, m in enumerate(messages) if m["role"] == "user"]
    for start in turn_starts:
        window = messages[start:]
        if sum(len(json.dumps(m, default=str)) for m in window) <= max_chars:
            return window
    return messages[turn_starts[-1] :] if turn_starts else []


def build_messages(system_prompt: str, session: Session, user_text: str, max_chars: int) -> list[dict[str, Any]]:
    system = system_prompt
    if session.focus_airports:
        system += (
            f"\n\n## Session state\nAirports discussed so far (most recent last): {', '.join(session.focus_airports)}."
        )
    history = trim_history(session.messages, max_chars)
    return [{"role": "system", "content": system}, *history, {"role": "user", "content": user_text}]
