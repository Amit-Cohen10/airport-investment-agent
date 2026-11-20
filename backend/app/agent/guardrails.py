"""Numeric grounding guardrail.

The LLM is allowed to *talk about* numbers, never to *produce* them. After the final answer
is written, every number in it is matched against the numbers returned by the tools in
this turn (with rounding tolerance and unit scaling: 0.292 <-> 29.2%, 2665790 <-> 2.7M).
Numbers that match nothing are flagged as "unverified" in the UI, so a hallucinated
statistic is visible instead of silently trusted.

Precision rules: the evidence is only what the model was actually shown (the tool messages in
its context), conversions follow each field's unit (only fraction fields like `load_factor` may
appear as %), explicit signs must match, and the user's own text is never evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

NUMBER = re.compile(
    r"(?<![\w.])(?P<sign>[-+−])?\$?(?P<num>\d{1,3}(?:,\d{3})+|\d+)(?P<dec>\.\d+)?(?P<ord>st|nd|rd|th)?"
    r"\s?(?P<unit>%|pp(?![a-z])|percent(?![a-z])|million|billion|thousand|[kKmMbB](?![a-zA-Z]))?"
)


RANGE_TAIL = re.compile(
    r"\s?[-–]\s?\d[\d,]*(?:\.\d+)?\s?(?P<unit>%|pp(?![a-z])|percent(?![a-z])|million|billion|thousand|[kKmMbB](?![a-zA-Z]))"
)
TEXT_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}

# Field names whose values are fractions (0.292) that answers show as percent (29.2%).
FRACTION_KEY = re.compile(
    r"rate|share|growth|cagr|yoy|load_factor|(?:^|\.)lf(?:$|\.)|premium|utilization|vs_2019|recovery|p_rank|p_top|weight|coverage"
)
# Fields already in percent / percentage-point units (82.3 = 82.3%).
PERCENT_KEY = re.compile(r"percentile|gap|_pct$")
# Ratios that answers usually express as a change ("0.945 of 2019" -> "5.5% below 2019").
RATIO_KEY = re.compile(r"vs_2019|recovery|utilization")


@dataclass(frozen=True)
class Evidence:
    value: float
    kind: str  # "fraction" | "percent" | "plain"
    ratio: bool = False


@dataclass
class GroundingReport:
    checked: int = 0
    unverified: list[str] = field(default_factory=list)

    @property
    def grounded_ratio(self) -> float:
        return 1.0 if self.checked == 0 else 1 - len(self.unverified) / self.checked

    def to_dict(self) -> dict[str, Any]:
        return {"checked": self.checked, "unverified": self.unverified, "grounded_ratio": round(self.grounded_ratio, 3)}


def _collect(node: Any, out: list[Evidence], path: str = "") -> None:
    """Walk a tool payload, remembering which field each number came from (that decides its unit)."""
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        kind = "fraction" if FRACTION_KEY.search(path) else "percent" if PERCENT_KEY.search(path) else "plain"
        out.append(Evidence(float(node), kind, bool(RATIO_KEY.search(path))))
    elif isinstance(node, dict):
        # {"metric": "dep_delay_rate", "value": 0.241}: the unit is named by the sibling "metric" field.
        named = str(node.get("metric", "")).lower()
        for k, v in node.items():
            key = f"{k}.{named}" if named and k == "value" else str(k)
            _collect(v, out, f"{path}.{key}".lower() if path else key.lower())
    elif isinstance(node, list):
        for v in node:
            _collect(v, out, path)
    elif isinstance(node, str):
        for m in NUMBER.finditer(node):  # numbers written inside tool text (notes, summaries, assumptions)
            value = float(m.group("num").replace(",", "") + (m.group("dec") or ""))
            if m.group("sign") in ("-", "\u2212"):
                value = -value
            unit = (m.group("unit") or "").lower()
            if unit in ("%", "percent", "pp"):
                out.append(Evidence(value, "percent"))
            else:  # "12.5 million" stored in base units so it matches "12.5M"
                out.append(Evidence(value * TEXT_SCALE.get(unit, 1.0), "plain"))


def evidence_pool(tool_payloads: list[Any]) -> list[Evidence]:
    pool: list[Evidence] = []
    for payload in tool_payloads:
        _collect(payload, pool)
    return pool


def _candidates(pool: list[Evidence], unit: str) -> list[float]:
    """Tool values expressed in the unit the answer displays. Only unit-consistent conversions."""
    if unit in ("%", "percent", "pp"):
        out = [e.value * 100 for e in pool if e.kind == "fraction"]
        out += [e.value for e in pool if e.kind == "percent"]
        out += [(e.value - 1) * 100 for e in pool if e.ratio]
        return out
    scale = {"m": 1e6, "million": 1e6, "k": 1e3, "thousand": 1e3, "b": 1e9, "billion": 1e9}.get(unit)
    if scale:
        return [e.value / scale for e in pool if e.kind == "plain"]
    return [e.value for e in pool]


def _matches(value: float, signed: bool, decimals: int, candidates: list[float], ordinal: bool = False) -> bool:
    tol = max(0.5 * 10 ** (-decimals), abs(value) * 0.005) + 1e-9  # display rounding, 0.5% slack, float eps
    if ordinal:  # "95th" may be 95.6 truncated: allow exactly one unit
        tol = 1.0 + 1e-9
    if signed:  # "+5%" must match +0.05, not -0.05
        return any(abs(value - c) <= tol for c in candidates)
    # No explicit sign: direction is usually in words ("5% below 2019"), so compare magnitudes.
    return any(abs(abs(value) - abs(c)) <= tol for c in candidates)


def check_grounding(answer: str, tool_payloads: list[Any]) -> GroundingReport:
    """Every number in `answer` must match a value from the tool results the model was shown.

    This is a set-membership check: it catches invented or misread figures, but a number that
    happens to equal some other value in the evidence will pass. It is a safety net, not a proof.
    """
    pool = evidence_pool(tool_payloads)
    report = GroundingReport()
    for m in NUMBER.finditer(answer):
        digits = m.group("num").replace(",", "")
        dec = m.group("dec") or ""
        unit = (m.group("unit") or "").lower()
        if not unit:  # a range like "90-93%" or "1.2-1.5M": the first number takes the second's unit
            rng = RANGE_TAIL.match(answer, m.end())
            unit = (rng.group("unit") or "").lower() if rng else ""
        value = float(digits + dec)
        is_year = "," not in m.group("num") and 1900 <= value <= 2100
        if not unit and not dec and (is_year or value <= 10):
            continue  # years, small counts/ordinals ("top 3", "4 pillars")
        sign = m.group("sign")
        if sign in ("-", "\u2212"):
            value = -value
        report.checked += 1
        # Ordinals ("95th percentile" for 95.6) are often truncated rather than rounded: allow 1 unit.
        decimals = len(dec) - 1 if dec else 0
        if not _matches(value, sign is not None, decimals, _candidates(pool, unit), ordinal=bool(m.group("ord"))):
            report.unverified.append(m.group(0).strip())
    return report
