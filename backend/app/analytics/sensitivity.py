"""Rank stability under weight uncertainty.

Pillar weights are a judgment call. Instead of pretending they are exact, we sample
many plausible weight vectors around the defaults (Dirichlet distribution) and re-rank
each time. "BOS is #1 in 94% of scenarios" says the ranking is robust; "#1 in 41%"
says it depends on how much you value growth vs. scale, and the analyst should know that.
Seeded, so the output is reproducible.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def rank_stability(
    pillar_scores: pd.DataFrame,
    base_weights: dict[str, float],
    *,
    samples: int = 1000,
    concentration: float = 40.0,
    seed: int = 7,
) -> dict[str, dict[str, Any]]:
    """pillar_scores: rows = airports, columns = pillar names (0-100, NaN = missing)."""
    names = list(base_weights)
    x = pillar_scores[names].to_numpy(dtype=float)
    available = ~np.isnan(x)
    x0 = np.nan_to_num(x)
    rng = np.random.default_rng(seed)
    alpha = np.array([base_weights[n] for n in names]) * concentration
    w = rng.dirichlet(alpha, size=samples)  # (S, P)

    # Score per sample with per-airport re-normalization over available pillars.
    num = x0 @ w.T  # (N, S)
    den = available.astype(float) @ w.T
    scores = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    # Rank 1 = best. argsort twice gives ranks; NaN scores go last.
    filled = np.where(np.isnan(scores), -np.inf, scores)
    ranks = (-filled).argsort(axis=0).argsort(axis=0) + 1  # (N, S)

    n = len(pillar_scores)
    out: dict[str, dict[str, Any]] = {}
    for i, code in enumerate(pillar_scores.index):
        r = ranks[i]
        out[code] = {
            "p_rank_1": round(float((r == 1).mean()), 3),
            "p_top_3": round(float((r <= min(3, n)).mean()), 3),
            "median_rank": int(np.median(r)),
            "rank_range_90": [int(np.percentile(r, 5)), int(np.percentile(r, 95))],
        }
    return out
