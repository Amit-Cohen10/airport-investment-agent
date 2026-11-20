"""BTS Airline On-Time Performance (Reporting Carrier), monthly PREZIP files.

Flight-level records for US reporting carriers' domestic flights. Each file is ~30 MB zipped,
~250 MB as CSV, so we stream only the needed columns and aggregate to airport-month at
ingest time. The snapshot never stores raw flights.

Per airport-month we derive:
  departures: flights, cancellations, delayed >=15 min, average departure delay, taxi-out
  arrivals:   flights, delayed >=15 min, taxi-in, delay minutes by cause (NAS, weather, ...)
  peak hour:  average over days of the busiest scheduled hour (departures + arrivals)
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from app.data.connectors.http import download_file

log = logging.getLogger(__name__)

SOURCE_NAME = "BTS Airline On-Time Performance (reporting carriers)"
SOURCE_URL = "https://www.transtats.bts.gov/Tables.asp?QO_VQ=EFD"
URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{year}_{month}.zip"
)

USECOLS = [
    "FlightDate",
    "Origin",
    "Dest",
    "CRSDepTime",
    "CRSArrTime",
    "DepDelay",
    "DepDel15",
    "TaxiOut",
    "TaxiIn",
    "ArrDel15",
    "ArrDelay",
    "Cancelled",
    "Diverted",
    "CarrierDelay",
    "WeatherDelay",
    "NASDelay",
    "SecurityDelay",
    "LateAircraftDelay",
]


def download_month(client: httpx.Client, raw_dir: Path, year: int, month: int) -> Path:
    dest = raw_dir / "ontime" / f"ontime_{year}_{month:02d}.zip"
    return download_file(client, URL_TEMPLATE.format(year=year, month=month), dest)


def _read_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        csv_name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
        with zf.open(csv_name) as fh:
            return pd.read_csv(fh, usecols=USECOLS, low_memory=False)


def aggregate_month(path: Path) -> pd.DataFrame:
    """Reduce one month of flight records to one row per airport."""
    df = _read_zip(path)
    df["month"] = pd.to_datetime(df["FlightDate"]).dt.to_period("M").dt.to_timestamp()
    operated = df[df["Cancelled"] == 0]

    dep = df.groupby(["Origin", "month"]).agg(
        dep_flights=("Cancelled", "size"),
        dep_cancelled=("Cancelled", "sum"),
    )
    dep_ops = operated.groupby(["Origin", "month"]).agg(
        dep_del15=("DepDel15", "sum"),
        dep_delay_avg_min=("DepDelay", lambda s: s.clip(lower=0).mean()),
        taxi_out_avg_min=("TaxiOut", "mean"),
    )
    dep = dep.join(dep_ops).reset_index().rename(columns={"Origin": "airport"})

    arrived = operated[operated["Diverted"] == 0]
    arr = (
        arrived.groupby(["Dest", "month"])
        .agg(
            arr_flights=("ArrDel15", "size"),
            arr_del15=("ArrDel15", "sum"),
            arr_delay_avg_min=("ArrDelay", lambda s: s.clip(lower=0).mean()),
            taxi_in_avg_min=("TaxiIn", "mean"),
            nas_delay_min=("NASDelay", "sum"),
            weather_delay_min=("WeatherDelay", "sum"),
            carrier_delay_min=("CarrierDelay", "sum"),
            late_aircraft_delay_min=("LateAircraftDelay", "sum"),
            security_delay_min=("SecurityDelay", "sum"),
        )
        .reset_index()
        .rename(columns={"Dest": "airport"})
    )

    peak = _peak_hour_movements(df)
    out = dep.merge(arr, on=["airport", "month"], how="outer").merge(peak, on=["airport", "month"], how="left")
    return out.fillna({c: 0 for c in out.columns if c.endswith(("_flights", "_cancelled"))})


def _peak_hour_movements(df: pd.DataFrame) -> pd.DataFrame:
    """Average (over days) of the busiest scheduled clock-hour, counting deps + arrs."""
    deps = pd.DataFrame(
        {
            "airport": df["Origin"],
            "date": df["FlightDate"],
            "month": df["month"],
            "hour": (pd.to_numeric(df["CRSDepTime"], errors="coerce") // 100) % 24,
        }
    )
    arrs = pd.DataFrame(
        {
            "airport": df["Dest"],
            "date": df["FlightDate"],
            "month": df["month"],
            "hour": (pd.to_numeric(df["CRSArrTime"], errors="coerce") // 100) % 24,
        }
    )
    moves = pd.concat([deps, arrs], ignore_index=True).dropna(subset=["hour"])
    hourly = moves.groupby(["airport", "month", "date", "hour"]).size()
    daily_peak = hourly.groupby(["airport", "month", "date"]).max()
    daily_hours = hourly.groupby(["airport", "month", "date"]).size()
    out = pd.DataFrame(
        {
            "peak_hour_movements": daily_peak.groupby(["airport", "month"]).mean(),
            "active_hours_per_day": daily_hours.groupby(["airport", "month"]).mean(),
        }
    )
    return out.reset_index()


def build(client: httpx.Client, raw_dir: Path, months: list[tuple[int, int]]) -> pd.DataFrame:
    frames = []
    for year, month in months:
        path = download_month(client, raw_dir, year, month)
        log.info("aggregating on-time %s-%02d", year, month)
        frames.append(aggregate_month(path))
    return pd.concat(frames, ignore_index=True)
