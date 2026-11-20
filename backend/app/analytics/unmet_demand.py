"""Unmet ("spilled") demand estimation with a classic airline spill model.

Observed passengers are *constrained* demand: a flight can't carry more people than it has
seats. When flights run full, some demand is turned away ("spilled") and never shows up in
the data. The spill model recovers it:

    demand D on a route-month ~ Normal(mu, sigma = cv * mu)
    carried passengers        = E[min(D, S)]   with S = seats offered
    solve for mu such that E[min(D, S)] = observed passengers
    spilled demand            = mu - observed passengers

    with z = (S - mu) / sigma:   E[min(D, S)] = mu - sigma * (phi(z) - z * (1 - Phi(z)))

cv (demand variability) is an assumption (config: 0.30; industry range ~0.25-0.40). We report
the result at several cv values so the uncertainty is visible, not hidden.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.optimize import brentq
from scipy.stats import norm


def expected_carried(mu: float, seats: float, cv: float) -> float:
    sigma = cv * mu
    if sigma <= 0:
        return min(mu, seats)
    z = (seats - mu) / sigma
    expected_excess = sigma * (norm.pdf(z) - z * (1 - norm.cdf(z)))  # E[(D - S)+]
    return mu - expected_excess


def spill_for(passengers: float, seats: float, cv: float, max_lf: float = 0.985) -> float:
    """Spilled passengers for one route-month (0 if the route is far from full)."""
    if seats <= 0 or passengers <= 0:
        return 0.0
    carried = min(passengers, seats * max_lf)  # guard: LF ~100% implies unbounded demand
    lo, hi = carried, carried * 10
    if expected_carried(hi, seats, cv) < carried:
        return float(hi - carried)
    mu = brentq(lambda m: expected_carried(m, seats, cv) - carried, lo, hi, xtol=0.5)
    return float(max(mu - carried, 0.0))


def estimate(
    segments: pd.DataFrame,
    *,
    cv: float,
    cv_sensitivity: list[float],
    min_lf: float,
    max_lf: float,
    target_lf: float,
    constrained_lf: float,
    top_n: int = 10,
) -> dict[str, Any]:
    """segments: scheduled passenger route-months departing ONE airport in the analysis window."""
    df = segments[(segments["seats"] > 0)].copy()
    df["lf"] = df["passengers"] / df["seats"]
    eligible = df["lf"] >= min_lf

    def total_spill(c: float) -> pd.Series:
        spill = pd.Series(0.0, index=df.index)
        spill[eligible] = [
            spill_for(p, s, c, max_lf)
            for p, s in zip(df.loc[eligible, "passengers"], df.loc[eligible, "seats"], strict=True)
        ]
        return spill

    df["spill"] = total_spill(cv)
    carried = float(df["passengers"].sum())
    spilled = float(df["spill"].sum())
    months = max(df["month"].nunique(), 1)
    seats_per_departure = float(df["seats"].sum() / max(df["departures"].sum(), 1))
    extra_departures_per_day = spilled / (seats_per_departure * target_lf) / (months * 30.4)

    routes = (
        df.groupby("dest", as_index=False)
        .agg(
            passengers=("passengers", "sum"),
            seats=("seats", "sum"),
            departures=("departures", "sum"),
            spill=("spill", "sum"),
            distance_mi=("distance_mi", "max"),
        )
        .assign(load_factor=lambda d: d["passengers"] / d["seats"])
        .sort_values("spill", ascending=False)
    )
    constrained = routes[routes["load_factor"] >= constrained_lf]
    sensitivity = [{"cv": c, "spilled_passengers": round(float(total_spill(c).sum()))} for c in cv_sensitivity]
    annualize = 12 / months
    return {
        "months_analyzed": months,
        "carried_passengers": round(carried),
        "spilled_passengers": round(spilled),
        "spilled_passengers_annualized": round(spilled * annualize),
        "spill_rate": round(spilled / carried, 4) if carried else None,
        "load_factor": round(carried / float(df["seats"].sum()), 4) if len(df) else None,
        "equivalent_extra_departures_per_day": round(extra_departures_per_day, 1),
        "avg_seats_per_departure": round(seats_per_departure, 1),
        "constrained_route_count": int(len(constrained)),
        "route_count": int(len(routes)),
        "top_spill_routes": [
            {
                "dest": r.dest,
                "load_factor": round(float(r.load_factor), 3),
                "spilled_passengers_annualized": round(float(r.spill) * annualize),
                "departures": int(r.departures),
                "distance_mi": int(r.distance_mi),
            }
            for r in routes.head(top_n).itertuples()
        ],
        "cv_sensitivity": sensitivity,
    }


def growth_signal(passenger_growth: float | None, seat_growth: float | None) -> str | None:
    if passenger_growth is None or seat_growth is None or np.isnan(passenger_growth) or np.isnan(seat_growth):
        return None
    gap = (passenger_growth - seat_growth) * 100
    if gap > 1:
        return f"passengers grew {gap:.1f} pp faster than seats (demand outrunning supply)"
    if gap < -1:
        return f"seats grew {-gap:.1f} pp faster than passengers (supply outrunning demand)"
    return "passengers and seats grew at roughly the same rate"
