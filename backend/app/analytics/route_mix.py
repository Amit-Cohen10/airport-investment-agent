"""Route mix by distance band (short / medium / long haul) from T-100 segment data.

"Long-haul" has no single official definition, so the threshold is configurable and the
answer is also reported at alternative thresholds. Passenger and cargo flights are
separated because at cargo hubs (e.g. ANC) the two give very different answers.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

PASSENGER_CONFIGS = {1, 3, 4}  # passenger, combi, seaplane (BTS AIRCRAFT_CONFIG)
FREIGHT_CONFIG = 2


def _band(distance: pd.Series, short_max: float, long_min: float) -> pd.Series:
    return pd.cut(
        distance,
        bins=[-1, short_max, long_min, float("inf")],
        labels=["short", "medium", "long"],
        right=False,
    ).astype(str)


def _share_table(df: pd.DataFrame, short_max: float, long_min: float) -> dict[str, Any]:
    total = float(df["departures"].sum())
    if total == 0:
        return {"departures": 0}
    bands = df.assign(band=_band(df["distance_mi"], short_max, long_min)).groupby("band")["departures"].sum()
    return {
        "departures": int(total),
        "short_share": round(float(bands.get("short", 0) / total), 4),
        "medium_share": round(float(bands.get("medium", 0) / total), 4),
        "long_share": round(float(bands.get("long", 0) / total), 4),
        "long_departures": int(bands.get("long", 0)),
    }


def analyze(
    segments: pd.DataFrame,
    *,
    short_max_mi: float,
    long_min_mi: float,
    alternative_thresholds: list[float],
    top_n: int = 8,
) -> dict[str, Any]:
    """segments: T-100 rows for ONE origin airport, already restricted to the analysis window."""
    pax = segments[segments["aircraft_config"].isin(PASSENGER_CONFIGS)]
    cargo = segments[segments["aircraft_config"] == FREIGHT_CONFIG]
    long_routes = (
        segments[segments["distance_mi"] >= long_min_mi]
        .assign(kind=lambda d: d["aircraft_config"].map(lambda c: "cargo" if c == FREIGHT_CONFIG else "passenger"))
        .groupby(["dest", "dest_country", "kind"], as_index=False)
        .agg(departures=("departures", "sum"), distance_mi=("distance_mi", "max"), passengers=("passengers", "sum"))
        .sort_values("departures", ascending=False)
        .head(top_n)
    )
    by_threshold = []
    for threshold in alternative_thresholds:
        all_long = segments.loc[segments["distance_mi"] >= threshold, "departures"].sum()
        pax_long = pax.loc[pax["distance_mi"] >= threshold, "departures"].sum()
        by_threshold.append(
            {
                "threshold_mi": threshold,
                "all_flights_long_share": round(float(all_long / max(segments["departures"].sum(), 1)), 4),
                "passenger_flights_long_share": round(float(pax_long / max(pax["departures"].sum(), 1)), 4),
            }
        )
    seats_total = pax["seats"].sum()
    return {
        "definition": {"short_haul_below_mi": short_max_mi, "long_haul_from_mi": long_min_mi},
        "all_flights": _share_table(segments, short_max_mi, long_min_mi),
        "passenger_flights": _share_table(pax, short_max_mi, long_min_mi),
        "cargo_flights": _share_table(cargo, short_max_mi, long_min_mi),
        "passenger_seat_long_share": round(
            float(pax.loc[pax["distance_mi"] >= long_min_mi, "seats"].sum() / seats_total), 4
        )
        if seats_total
        else None,
        "cargo_share_of_departures": round(float(cargo["departures"].sum() / max(segments["departures"].sum(), 1)), 4),
        "top_long_haul_routes": long_routes.to_dict(orient="records"),
        "threshold_sensitivity": by_threshold,
    }
