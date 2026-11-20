"""Human-readable formatting of metric values for UI evidence blocks (deterministic, never the LLM)."""

from __future__ import annotations

import math
from typing import Any

PERCENT_KEYS = ("_rate", "load_factor", "_share", "growth", "cagr", "yoy", "fare_premium", "utilization")


def compact(n: float | None) -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "n/a"
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:.1f}B"
    if a >= 1e6:
        return f"{n / 1e6:.1f}M"
    if a >= 1e4:
        return f"{n / 1e3:.0f}K"
    return f"{n:,.0f}"


def pct(v: float | None, digits: int = 1, signed: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    return f"{v * 100:+.{digits}f}%" if signed else f"{v * 100:.{digits}f}%"


def fmt_metric(metric: str, v: Any) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/a"
    if metric == "recovery_vs_2019":
        return f"{v * 100:.0f}% of 2019"
    if metric == "demand_supply_gap":
        return f"{v:+.1f} pp"
    if metric == "log_enplanements":
        return f"{compact(10**v)} enpl."
    if metric == "hours":
        return f"{v:.1f}"
    if metric.endswith("_min"):
        return f"{v:.1f} min"
    if metric.startswith("peak_hour"):
        return f"{v:.1f}"
    if any(k in metric for k in PERCENT_KEYS):
        signed = "growth" in metric or "cagr" in metric or "yoy" in metric or metric == "fare_premium"
        return pct(v, 1, signed)
    if isinstance(v, (int, float)) and abs(v) >= 1000:
        return compact(v)
    return f"{v:.2f}" if isinstance(v, float) else str(v)


def r1(v: float | None) -> float | None:
    return None if v is None else round(float(v), 1)
