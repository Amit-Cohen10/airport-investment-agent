"""Core domain types shared by every layer.

The two ideas that run through the whole system:
  * every number carries its `Provenance` (which public source, which period), and
  * every analysis result carries its `assumptions` and a `Confidence` grade,
so the agent can explain *where a number came from* and *how much to trust it*.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Provenance(BaseModel):
    """Where a number comes from. Rendered as a citation in the UI."""

    source: str  # human-readable source name, e.g. "BTS T-100 Segment (All Carriers)"
    period: str  # data period, e.g. "Sep 2025 - Aug 2026"
    url: str | None = None
    note: str | None = None


class Airport(BaseModel):
    code: str  # IATA / FAA location id, e.g. "BOS"
    icao: str | None = None
    name: str
    city: str | None = None
    state: str | None = None  # two-letter US state
    latitude: float | None = None
    longitude: float | None = None
    hub_class: str | None = None  # FAA hub type: L / M / S / N
    runways: int | None = None  # open, paved runways >= 4,000 ft


# ---------------------------------------------------------------------------
# UI evidence blocks. Built deterministically from tool outputs (never by the LLM),
# so tables and charts in the UI always show computed numbers.
# ---------------------------------------------------------------------------


class Kpi(BaseModel):
    label: str
    value: str
    hint: str | None = None


class KpiBlock(BaseModel):
    type: Literal["kpis"] = "kpis"
    title: str
    items: list[Kpi]


class TableBlock(BaseModel):
    type: Literal["table"] = "table"
    title: str
    columns: list[str]
    rows: list[list[Any]]
    note: str | None = None


class BarSeries(BaseModel):
    name: str
    values: list[float]


class BarChartBlock(BaseModel):
    type: Literal["bar_chart"] = "bar_chart"
    title: str
    categories: list[str]
    series: list[BarSeries]
    stacked: bool = False
    unit: str | None = None


UIBlock = KpiBlock | TableBlock | BarChartBlock


class ToolResult(BaseModel):
    """Uniform envelope returned by every agent tool."""

    tool: str
    ok: bool = True
    summary: str  # one line, used in the reasoning trace
    data: dict[str, Any] = Field(default_factory=dict)  # what the LLM reasons over
    provenance: list[Provenance] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    confidence: Confidence | None = None
    ui: list[UIBlock] = Field(default_factory=list)  # what the evidence panel renders

    def for_llm(self) -> dict[str, Any]:
        """The compact view sent back to the model (UI blocks are not needed there)."""
        payload: dict[str, Any] = {"ok": self.ok, "summary": self.summary, "data": self.data}
        if self.provenance:
            payload["sources"] = [p.model_dump(exclude_none=True) for p in self.provenance]
        if self.assumptions:
            payload["assumptions"] = self.assumptions
        if self.caveats:
            payload["caveats"] = self.caveats
        if self.confidence:
            payload["confidence"] = self.confidence.value
        return payload
