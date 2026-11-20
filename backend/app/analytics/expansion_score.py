"""Terminal Expansion Opportunity Score: a deterministic, explainable 0-100 score.

    metric value --percentile vs national peers--> metric score (0-100)
    metric scores --weighted mean--> pillar score (4 pillars)
    pillar scores --weighted mean--> total score; coverage --> confidence grade

Same inputs + same config = same output, every time. The LLM never touches this math;
it only explains the breakdown this module returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from app.analytics.normalization import normalize_weights, percentile_rank, weighted_mean
from app.domain.models import Confidence


@dataclass
class ScoreConfig:
    pillars: dict[str, dict[str, Any]]
    min_enplanements: float
    high_min_coverage: float
    medium_min_coverage: float
    missing_percentile: float = 50.0

    @classmethod
    def from_dict(cls, cfg: dict[str, Any]) -> ScoreConfig:
        es = cfg["expansion_score"]
        return cls(
            pillars=es["pillars"],
            min_enplanements=cfg["peer_universe"]["min_annual_enplanements"],
            high_min_coverage=es["confidence"]["high_min_coverage"],
            medium_min_coverage=es["confidence"]["medium_min_coverage"],
            missing_percentile=es.get("missing_metric_percentile", 50.0),
        )

    def pillar_weights(self, overrides: dict[str, float] | None = None) -> dict[str, float]:
        weights = {name: p["weight"] for name, p in self.pillars.items()}
        if overrides:
            unknown = set(overrides) - set(weights)
            if unknown:
                raise ValueError(f"unknown pillars {sorted(unknown)}; valid: {sorted(weights)}")
            weights.update(overrides)
        return normalize_weights(weights)


@dataclass
class ScoredUniverse:
    table: pd.DataFrame  # one row per peer airport: metric percentiles, pillar scores, total
    pillar_weights: dict[str, float]
    metric_columns: dict[str, list[str]] = field(default_factory=dict)


def peer_universe(features: pd.DataFrame, min_enplanements: float) -> pd.DataFrame:
    return features[features["enplanements_latest"] >= min_enplanements]


def confidence_for(coverage: float, cfg: ScoreConfig) -> Confidence:
    if coverage >= cfg.high_min_coverage:
        return Confidence.HIGH
    if coverage >= cfg.medium_min_coverage:
        return Confidence.MEDIUM
    return Confidence.LOW


def score_universe(
    features: pd.DataFrame, cfg: ScoreConfig, weight_overrides: dict[str, float] | None = None
) -> ScoredUniverse:
    peers = peer_universe(features, cfg.min_enplanements).copy()
    pillar_weights = cfg.pillar_weights(weight_overrides)
    out = pd.DataFrame(index=peers.index)
    metric_cols: dict[str, list[str]] = {}
    coverage_num = pd.Series(0.0, index=peers.index)

    for pillar, spec in cfg.pillars.items():
        metrics = spec["metrics"]
        cols = []
        for metric, mspec in metrics.items():
            col = f"pct__{metric}"
            out[col] = percentile_rank(peers[metric], mspec.get("higher_is_better", True))
            cols.append(col)
        metric_cols[pillar] = cols
        m_weights = {m: s["weight"] for m, s in metrics.items()}
        scores, covs = [], []
        for code in peers.index:
            raw = {m: out.at[code, f"pct__{m}"] for m in metrics}
            _, cov = weighted_mean(raw, m_weights)  # share of this pillar's weight backed by data
            # Missing data is neutral: imputed at the median percentile, so a gap is neither
            # rewarded (as re-normalizing over the remaining metrics would do) nor punished.
            # The gap shows up in coverage -> confidence instead.
            filled = {m: (cfg.missing_percentile if pd.isna(v) else v) for m, v in raw.items()}
            score, _ = weighted_mean(filled, m_weights)
            scores.append(score)
            covs.append(cov)
        out[f"pillar__{pillar}"] = scores
        coverage_num += pd.Series(covs, index=peers.index) * pillar_weights[pillar]

    totals = []
    for code in peers.index:
        vals = {p: out.at[code, f"pillar__{p}"] for p in cfg.pillars}
        total, _ = weighted_mean(vals, pillar_weights)
        totals.append(total)
    out["score"] = totals
    out["coverage"] = coverage_num
    out["confidence"] = [confidence_for(c, cfg).value for c in out["coverage"]]
    out["national_rank"] = out["score"].rank(ascending=False, method="min").astype("Int64")
    out["peer_count"] = len(out)
    return ScoredUniverse(table=out, pillar_weights=pillar_weights, metric_columns=metric_cols)


def explain_airport(scored: ScoredUniverse, features: pd.DataFrame, code: str, cfg: ScoreConfig) -> dict[str, Any]:
    """Full breakdown for one airport: raw value, percentile, and contribution of every metric."""
    row = scored.table.loc[code]
    pillars = []
    for pillar, spec in cfg.pillars.items():
        metrics = []
        for metric, mspec in spec["metrics"].items():
            raw = features.at[code, metric] if metric in features.columns else None
            pct = row[f"pct__{metric}"]
            metrics.append(
                {
                    "metric": metric,
                    "label": mspec["label"],
                    "value": None if pd.isna(raw) else float(raw),
                    "percentile": None if pd.isna(pct) else round(float(pct), 1),
                    "imputed": bool(pd.isna(pct)),  # missing -> scored at the neutral median
                    "weight_in_pillar": mspec["weight"],
                }
            )
        score = row[f"pillar__{pillar}"]
        weight = scored.pillar_weights[pillar]
        pillars.append(
            {
                "pillar": pillar,
                "label": spec["label"],
                "weight": round(weight, 3),
                "score": None if pd.isna(score) else round(float(score), 1),
                "contribution": None if pd.isna(score) else round(float(score) * weight, 1),
                "metrics": metrics,
            }
        )
    return {
        "airport": code,
        "score": round(float(row["score"]), 1),
        "national_rank": int(row["national_rank"]),
        "peer_count": int(row["peer_count"]),
        "coverage": round(float(row["coverage"]), 2),
        "confidence": row["confidence"],
        "pillars": pillars,
    }
