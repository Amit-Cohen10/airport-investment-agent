"""Tool registry: the contract between the LLM and deterministic code.

For every tool call the model emits, the runtime asks the registry to:
  1. look the tool up by name (unknown name -> structured error back to the model),
  2. parse + validate the JSON arguments with the tool's Pydantic model
     (invalid -> the validation message goes back so the model can self-correct),
  3. run the handler and return a `ToolResult`.
The model never executes anything itself; it can only request one of these functions.
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.domain.models import ToolResult
from app.services.insights import AnalysisError, InsightService

log = logging.getLogger(__name__)


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel, InsightService], ToolResult]

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": simplify_schema(self.args_model.model_json_schema()),
            },
        }


class ToolRegistry:
    def __init__(self, service: InsightService, tools: list[Tool]):
        self.service = service
        self.tools = {t.name: t for t in tools}

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values()]

    def execute(self, name: str, raw_arguments: str) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return _error(name, f"Unknown tool '{name}'. Available: {', '.join(self.tools)}")
        try:
            payload = json.loads(raw_arguments or "{}")
            if not isinstance(payload, dict):
                raise ValueError("arguments must be a JSON object")
            args = tool.args_model.model_validate(payload)
        except (json.JSONDecodeError, ValueError, ValidationError) as exc:
            return _error(name, f"Invalid arguments: {_short(exc)}. Fix the arguments and call again.")
        try:
            return tool.handler(args, self.service)
        except AnalysisError as exc:
            return _error(name, str(exc))
        except Exception:  # never crash the conversation on a tool bug; log it and tell the model
            log.exception("tool %s crashed", name)
            return _error(
                name, "Internal error while running this analysis. Tell the user it failed; do not guess numbers."
            )


def _error(tool: str, message: str) -> ToolResult:
    return ToolResult(tool=tool, ok=False, summary=f"error: {message[:160]}", data={"error": message})


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['msg']}" for e in exc.errors()[:5])
    return str(exc)[:300]


def simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make Pydantic JSON schema portable across providers.

    Inline $refs, collapse Optional (anyOf [X, null]) to X, and drop titles/defaults that some
    providers (notably Gemini's OpenAPI subset) reject.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
        if "anyOf" in node:
            options = [o for o in node["anyOf"] if o.get("type") != "null"]
            merged = {**walk(options[0]), **{k: v for k, v in node.items() if k not in ("anyOf", "title", "default")}}
            return walk(merged)
        return {k: walk(v) for k, v in node.items() if k not in ("title", "default", "$defs")}

    return walk(schema)
