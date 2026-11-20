"""Numeric grounding guardrail.

The LLM is allowed to *talk about* numbers, never to *produce* them. After the final answer
is written, every number in it is matched against the numbers returned by the tools in
this turn (with rounding tolerance and unit scaling: 0.292 <-> 29.2%, 2665790 <-> 2.7M).
Numbers that match nothing are flagged as "unverified" in the UI, so a hallucinated
statistic is visible instead of silently trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?\$?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?"
    r"\s?(?P<unit>%|pp|percent|million|billion|thousand|[kKmMbB](?![a-zA-Z]))?"
)


@dataclass
class GroundingReport:
    checked: int = 0
    unverified: list[str] = field(default_factory=list)

    @property
    def grounded_ratio(self) -> float:
        return 1.0 if self.checked == 0 else 1 - len(self.unverified) / self.checked

    def to_dict(self) -> dict[str, Any]:
        return {"checked": self.checked, "unverified": self.unverified, "grounded_ratio": round(self.grounded_ratio, 3)}


def _collect(node: Any, out: list[float]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        out.append(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _collect(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect(v, out)
    elif isinstance(node, str):
        for m in NUMBER.finditer(node):  # numbers embedded in tool text (notes, summaries)
            out.append(float(m.group("num").replace(",", "") + (m.group("dec") or "")))


def evidence_pool(tool_payloads: list[dict[str, Any]], extra_text: str = "") -> list[float]:
    raw: list[float] = []
    for payload in tool_payloads:
        _collect(payload, raw)
    _collect(extra_text, raw)
    pool = set()
    for v in raw:
        pool.update({v, abs(v), v * 100, abs(v) * 100, v / 1e3, v / 1e6, v / 1e9, abs(v) / 1e6, abs(v) / 1e3})
        if 0 < abs(v) < 5:  # ratios like 0.905 (cap utilization) or 1.12 (vs 2019) shown as "+12%"
            pool.add(abs(v - 1) * 100)
    return sorted(pool)


def _matches(value: float, decimals: int, pool: list[float]) -> bool:
    tol = max(0.5 * 10 ** (-decimals), abs(value) * 0.015)
    return any(abs(value - p) <= tol for p in pool)


def check_grounding(answer: str, tool_payloads: list[dict[str, Any]], user_text: str = "") -> GroundingReport:
    pool = evidence_pool(tool_payloads, user_text)
    report = GroundingReport()
    for m in NUMBER.finditer(answer):
        digits = m.group("num").replace(",", "")
        dec = m.group("dec") or ""
        unit = (m.group("unit") or "").lower()
        value = float(digits + dec)
        if not unit and not dec and (1900 <= value <= 2100 or value <= 10):
            continue  # years, small counts/ordinals ("top 3", "4 pillars")
        # Compared in the displayed scale: "2.7M" matches 2665790 because the pool holds v/1e6;
        # "29.2%" matches 0.292 because the pool holds v*100.
        report.checked += 1
        if not _matches(value, len(dec) - 1 if dec else 0, pool):
            report.unverified.append(m.group(0).strip())
    return report
