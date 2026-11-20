"""Feature engineering: raw snapshot tables -> one row of metrics per airport.

Pure function of its inputs (no I/O), so every metric is reproducible and unit-testable.
Windows:
  * "TTM" = the trailing 12 months ending at the newest month in each source,
    compared with the 12 months before that ("prior TTM").
  * FAA enplanements are calendar years (CY2025 is preliminary).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Window:
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def label(self) -> str:
        return f"{self.start:%b %Y} - {self.end:%b %Y}"


def ttm_windows(months: pd.Series) -> tuple[Window, Window]:
    end = pd.Timestamp(months.max())
    start = end - pd.DateOffset(months=11)
    prev_end = start - pd.DateOffset(months=1)
    prev_start = prev_end - pd.DateOffset(months=11)
    return Window(start, end), Window(prev_start, prev_end)


def _in(df: pd.DataFrame, w: Window, col: str = "month") -> pd.DataFrame:
    return df[(df[col] >= w.start) & (df[col] <= w.end)]


def enplanement_features(enpl: pd.DataFrame) -> pd.DataFrame:
    wide = enpl.pivot_table(index="airport", columns="year", values="enplanements", aggfunc="sum")
    latest = wide.columns.max()
    meta = enpl.sort_values("year").groupby("airport")[["name", "city", "state", "hub_class"]].last()
    out = pd.DataFrame(index=wide.index)
    out["enplanements_latest"] = wide.get(latest)
    out["enplanements_prev"] = wide.get(latest - 1)
    out["enplanements_2019"] = wide.get(2019)
    base = wide.get(latest - 3)
    out["enplanement_cagr_3y"] = (out["enplanements_latest"] / base) ** (1 / 3) - 1
    out["enplanement_yoy"] = out["enplanements_latest"] / out["enplanements_prev"] - 1
    out["recovery_vs_2019"] = out["enplanements_latest"] / out["enplanements_2019"]
    out["log_enplanements"] = np.log10(out["enplanements_latest"].where(out["enplanements_latest"] > 0))
    out = out.replace([np.inf, -np.inf], np.nan)
    return out.join(meta)


def traffic_features(t100: pd.DataFrame) -> tuple[pd.DataFrame, Window]:
    ttm, prev = ttm_windows(t100["month"])
    cols = ["departures", "dom_departures", "passengers", "seats", "freight_lbs", "intl_departures", "intl_passengers"]
    cur = _in(t100, ttm).groupby("airport")[cols].sum()
    old = _in(t100, prev).groupby("airport")[["passengers", "seats"]].sum()
    out = pd.DataFrame(index=cur.index)
    out["departures_ttm"] = cur["departures"]
    out["dom_departures_ttm"] = cur["dom_departures"]
    out["passengers_ttm"] = cur["passengers"]
    out["seats_ttm"] = cur["seats"]
    out["freight_lbs_ttm"] = cur["freight_lbs"]
    out["load_factor_ttm"] = (cur["passengers"] / cur["seats"]).where(cur["seats"] > 0)
    out["intl_departure_share"] = (cur["intl_departures"] / cur["departures"]).where(cur["departures"] > 0)
    pax_growth = cur["passengers"] / old["passengers"] - 1
    seat_growth = cur["seats"] / old["seats"] - 1
    out["passenger_growth_ttm"] = pax_growth
    out["seat_growth_ttm"] = seat_growth
    out["demand_supply_gap"] = (pax_growth - seat_growth) * 100  # percentage points
    return out.replace([np.inf, -np.inf], np.nan), ttm


def ontime_features(ontime: pd.DataFrame) -> tuple[pd.DataFrame, Window]:
    ttm, _ = ttm_windows(ontime["month"])
    df = _in(ontime, ttm).copy()
    df["dep_operated"] = df["dep_flights"] - df["dep_cancelled"]
    # Weighted averages need weight * value sums.
    df["taxi_out_x"] = df["taxi_out_avg_min"] * df["dep_operated"]
    df["dep_delay_x"] = df["dep_delay_avg_min"] * df["dep_operated"]
    df["arr_delay_x"] = df["arr_delay_avg_min"] * df["arr_flights"]
    g = df.groupby("airport")
    s = g[
        [
            "dep_flights",
            "dep_cancelled",
            "dep_operated",
            "dep_del15",
            "arr_flights",
            "arr_del15",
            "taxi_out_x",
            "dep_delay_x",
            "arr_delay_x",
            "nas_delay_min",
            "weather_delay_min",
            "carrier_delay_min",
            "late_aircraft_delay_min",
            "security_delay_min",
        ]
    ].sum()
    out = pd.DataFrame(index=s.index)
    out["ontime_departures_ttm"] = s["dep_flights"]
    out["taxi_out_avg_min"] = s["taxi_out_x"] / s["dep_operated"]
    out["dep_delay_rate"] = s["dep_del15"] / s["dep_operated"]
    out["dep_delay_avg_min"] = s["dep_delay_x"] / s["dep_operated"]
    out["arr_delay_rate"] = s["arr_del15"] / s["arr_flights"]
    out["arr_delay_avg_min"] = s["arr_delay_x"] / s["arr_flights"]
    out["cancellation_rate"] = s["dep_cancelled"] / s["dep_flights"]
    out["nas_delay_per_arrival_min"] = s["nas_delay_min"] / s["arr_flights"]
    causes = [
        "nas_delay_min",
        "weather_delay_min",
        "carrier_delay_min",
        "late_aircraft_delay_min",
        "security_delay_min",
    ]
    total = s[causes].sum(axis=1)
    for cause in causes:
        out[f"share_{cause.removesuffix('_delay_min')}"] = (s[cause] / total).where(total > 0)
    out["peak_hour_movements"] = g["peak_hour_movements"].mean()
    out["active_hours_per_day"] = g["active_hours_per_day"].mean()
    # Airports with very few reporting-carrier flights give noisy rates; drop them.
    out = out[out["ontime_departures_ttm"] >= 500]
    return out.replace([np.inf, -np.inf], np.nan), ttm


def fare_features(fares: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Fare premium: how much more passengers pay at an airport than the US norm for the distance.

    Fit log(fare) = a + b*log(distance) across all markets (passenger-weighted, last 4 quarters).
    An airport's premium is the passenger-weighted mean residual of its markets, as a %.
    A persistent premium is a classic sign of scarce supply (capacity or competition).
    """
    fares = fares.assign(period=fares["year"].astype(int) * 10 + fares["quarter"].astype(int))
    recent_periods = sorted(fares["period"].unique())[-4:]
    df = fares[fares["period"].isin(recent_periods)].copy()
    x, y, w = np.log(df["distance_mi"]), np.log(df["fare"]), df["pax_per_day"]
    b, a = np.polyfit(x, y, 1, w=np.sqrt(w))
    df["resid"] = y - (a + b * x)
    sides = pd.concat(
        [
            df[["airport_1", "resid", "pax_per_day", "largest_share"]].rename(columns={"airport_1": "airport"}),
            df[["airport_2", "resid", "pax_per_day", "largest_share"]].rename(columns={"airport_2": "airport"}),
        ]
    )
    sides["rw"] = sides["resid"] * sides["pax_per_day"]
    sides["sw"] = sides["largest_share"] * sides["pax_per_day"]
    g = sides.groupby("airport")[["rw", "sw", "pax_per_day"]].sum()
    out = pd.DataFrame(index=g.index)
    out["fare_premium"] = np.exp(g["rw"] / g["pax_per_day"]) - 1
    out["dominant_carrier_share"] = g["sw"] / g["pax_per_day"]
    out["od_pax_per_day"] = g["pax_per_day"]
    first, last = recent_periods[0], recent_periods[-1]
    label = f"{first // 10} Q{first % 10} - {last // 10} Q{last % 10}"
    return out, label


@dataclass
class FeatureSet:
    table: pd.DataFrame  # one row per airport code
    periods: dict[str, str]  # human-readable period per source family


ONTIME_METRICS = [
    "taxi_out_avg_min",
    "dep_delay_rate",
    "dep_delay_avg_min",
    "arr_delay_rate",
    "arr_delay_avg_min",
    "cancellation_rate",
    "nas_delay_per_arrival_min",
    "peak_hour_movements",
    "peak_hour_movements_per_runway",
    "share_nas",
    "share_weather",
    "share_carrier",
    "share_late_aircraft",
    "share_security",
    "active_hours_per_day",
]


def build_features(
    airports: pd.DataFrame,
    enplanements: pd.DataFrame,
    t100_monthly: pd.DataFrame,
    ontime_monthly: pd.DataFrame,
    airfare_markets: pd.DataFrame,
    min_ontime_coverage: float = 0.2,
) -> FeatureSet:
    enpl = enplanement_features(enplanements)
    traffic, traffic_w = traffic_features(t100_monthly)
    ontime, ontime_w = ontime_features(ontime_monthly)
    fares, fare_label = fare_features(airfare_markets)
    ref = airports.set_index("code")[["icao", "latitude", "longitude", "runways", "longest_runway_ft"]]
    table = enpl.join(ref, how="left").join(traffic, how="left").join(ontime, how="left").join(fares, how="left")
    runways = table["runways"].where(table["runways"] > 0)
    table["peak_hour_movements_per_runway"] = table["peak_hour_movements"] / runways
    # On-time data covers only BTS "reporting carriers" (large US airlines, domestic flights).
    # Coverage = their share of all domestic departures (T-100, which also counts cargo and small
    # carriers, so it is an approximate lower bound). Where coverage is tiny, on-time rates come
    # from an unrepresentative sample, so they are dropped rather than trusted.
    table["ontime_coverage"] = (table["ontime_departures_ttm"] / table["dom_departures_ttm"]).clip(upper=1.0)
    unreliable = table["ontime_coverage"] < min_ontime_coverage
    table.loc[unreliable, ONTIME_METRICS] = np.nan
    years = sorted(enplanements["year"].unique())
    periods = {
        "enplanements": f"CY{years[-1] - 3}-CY{years[-1]} (CY{years[-1]} preliminary), CY2019 baseline",
        "traffic": traffic_w.label,
        "ontime": ontime_w.label,
        "fares": fare_label,
    }
    return FeatureSet(table=table, periods=periods)
