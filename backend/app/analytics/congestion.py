"""Congestion Index: percentile composite of airfield/schedule congestion signals (0-100, higher = more congested).

Components and why they matter:
  * taxi-out time: queueing for the runway; the most direct surface-congestion signal
  * peak-hour movements per runway: how hard the airfield is driven at its busiest hour
  * departure / arrival delay rates, NAS delay per arrival: system-level capacity shortfalls
    ("NAS" delay = attributed to the National Airspace System: volume, ATC, airport ops)
  * cancellation rate: the extreme end of disruption
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from app.analytics.normalization import percentile_rank, weighted_mean


def congestion_universe(features: pd.DataFrame, cfg: dict[str, Any], min_enplanements: float) -> pd.DataFrame:
    metrics: dict[str, dict[str, Any]] = cfg["metrics"]
    peers = features[features["enplanements_latest"] >= min_enplanements]
    out = pd.DataFrame(index=peers.index)
    for metric in metrics:
        out[f"pct__{metric}"] = percentile_rank(peers[metric], higher_is_better=True)
    weights = {m: s["weight"] for m, s in metrics.items()}
    idx, cov = [], []
    for code in peers.index:
        raw = {m: out.at[code, f"pct__{m}"] for m in metrics}
        _, coverage = weighted_mean(raw, weights)
        if coverage < 0.5:  # mostly no on-time data: congestion cannot be measured
            score = None
        else:  # a few missing components are scored at the neutral median (not re-weighted)
            score, _ = weighted_mean({m: 50.0 if pd.isna(v) else v for m, v in raw.items()}, weights)
        idx.append(score)
        cov.append(coverage)
    out["congestion_index"] = idx
    out["coverage"] = cov
    ontime_cov = features.reindex(peers.index)["ontime_coverage"] if "ontime_coverage" in features else None
    out["ontime_coverage"] = ontime_cov
    out["confidence"] = [_confidence(c, oc) for c, oc in zip(out["coverage"], out["ontime_coverage"], strict=True)]
    out["congestion_rank"] = out["congestion_index"].rank(ascending=False, method="min").astype("Int64")
    out["peer_count"] = int(out["congestion_index"].notna().sum())
    return out


def _confidence(coverage: float, ontime_coverage: float | None) -> str:
    """High only when the on-time sample is representative of the airport's traffic."""
    if coverage < 0.5 or ontime_coverage is None or pd.isna(ontime_coverage):
        return "low"
    if coverage >= 0.95 and ontime_coverage >= 0.6:
        return "high"
    return "medium" if ontime_coverage >= 0.3 else "low"


def explain(universe: pd.DataFrame, features: pd.DataFrame, code: str, cfg: dict[str, Any]) -> dict[str, Any]:
    row = universe.loc[code]
    components = []
    for metric, spec in cfg["metrics"].items():
        raw = features.at[code, metric]
        pct = row[f"pct__{metric}"]
        components.append(
            {
                "metric": metric,
                "label": spec["label"],
                "value": None if pd.isna(raw) else float(raw),
                "percentile": None if pd.isna(pct) else round(float(pct), 1),
                "weight": spec["weight"],
            }
        )
    index = row["congestion_index"]
    return {
        "airport": code,
        "congestion_index": None if pd.isna(index) else round(float(index), 1),
        "national_rank": None if pd.isna(row["congestion_rank"]) else int(row["congestion_rank"]),
        "peer_count": int(row["peer_count"]),
        "coverage": round(float(row["coverage"]), 2),
        "ontime_sample_coverage": None if pd.isna(row["ontime_coverage"]) else round(float(row["ontime_coverage"]), 2),
        "confidence": row["confidence"],
        "components": components,
    }
