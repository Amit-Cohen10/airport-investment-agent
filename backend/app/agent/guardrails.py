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
    return sorted({abs(v) for v in raw})


def _candidates(pool: list[float], unit: str) -> list[float]:
    """Scale tool values into the unit the answer displays (only that unit: no blanket x100)."""
    if unit in ("%", "percent", "pp"):
        # fractions shown as percent (0.292 -> 29.2%), values already in percent/pp, ratios as change (1.12 -> 12%)
        return pool + [v * 100 for v in pool] + [abs(v - 1) * 100 for v in pool if 0 < v < 5]
    if unit in ("m", "million"):
        return [v / 1e6 for v in pool]
    if unit in ("k", "thousand"):
        return [v / 1e3 for v in pool]
    if unit in ("b", "billion"):
        return [v / 1e9 for v in pool]
    return pool


def _matches(value: float, decimals: int, candidates: list[float]) -> bool:
    tol = max(0.5 * 10 ** (-decimals), abs(value) * 0.005)  # display rounding, plus 0.5% slack
    return any(abs(value - c) <= tol for c in candidates)


def check_grounding(answer: str, tool_payloads: list[dict[str, Any]], user_text: str = "") -> GroundingReport:
    pool = evidence_pool(tool_payloads, user_text)
    report = GroundingReport()
    for m in NUMBER.finditer(answer):
        digits = m.group("num").replace(",", "")
        dec = m.group("dec") or ""
        unit = (m.group("unit") or "").lower()
        value = float(digits + dec)
        is_year = "," not in m.group("num") and 1900 <= value <= 2100
        if not unit and not dec and (is_year or value <= 10):
            continue  # years, small counts/ordinals ("top 3", "4 pillars")
        # Compared in the displayed scale: "2.7M" matches 2665790 because the pool holds v/1e6;
        # "29.2%" matches 0.292 because the pool holds v*100.
        report.checked += 1
        if not _matches(value, len(dec) - 1 if dec else 0, _candidates(pool, unit)):
            report.unverified.append(m.group(0).strip())
    return report
