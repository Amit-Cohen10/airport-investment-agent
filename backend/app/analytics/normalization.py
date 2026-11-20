"""Normalization and weighted aggregation primitives used by every score.

Why percentile ranks (not min-max or z-scores)?
  * Robust to outliers: ATL or a tiny airport with +400% growth can't squash everyone else.
  * Unit-free and explainable: "BOS is in the 82nd percentile for load factor" needs no math.
  * Trade-off: percentiles discard magnitude (a 1% gap and a 20% gap can be one rank apart),
    which is why the raw values are always shown next to the percentiles.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd


def percentile_rank(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """0-100 percentile of each value within the series. NaN stays NaN (missing data)."""
    ranked = values.rank(pct=True, method="average", ascending=higher_is_better) * 100.0
    return ranked.clip(0, 100)


def weighted_mean(values: Mapping[str, float | None], weights: Mapping[str, float]) -> tuple[float | None, float]:
    """Weighted mean over the components that have data.

    Missing components are dropped and the remaining weights re-normalized. Returns
    (score, coverage) where coverage is the share of total weight that had data; a low
    coverage lowers the confidence grade instead of silently biasing the score.
    """
    total_weight = sum(weights.values())
    used = {k: w for k, w in weights.items() if _present(values.get(k))}
    used_weight = sum(used.values())
    if total_weight <= 0 or used_weight <= 0:
        return None, 0.0
    score = sum(float(values[k]) * w for k, w in used.items()) / used_weight
    return score, used_weight / total_weight


def normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    clean = {k: max(float(v), 0.0) for k, v in weights.items()}
    total = sum(clean.values())
    if total <= 0:
        raise ValueError("weights must contain at least one positive value")
    return {k: v / total for k, v in clean.items()}


def _present(value: float | None) -> bool:
    return value is not None and not (isinstance(value, float) and math.isnan(value))
