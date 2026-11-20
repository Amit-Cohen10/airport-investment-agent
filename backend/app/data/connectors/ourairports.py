"""OurAirports open data (public domain): airport reference data and runways.

Gives names, ICAO codes, coordinates (for great-circle distances), state, and runway counts.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pandas as pd

from app.data.connectors.http import download_file

SOURCE_NAME = "OurAirports open data"
SOURCE_URL = "https://ourairports.com/data/"
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
RUNWAYS_URL = "https://davidmegginson.github.io/ourairports-data/runways.csv"
MIN_RUNWAY_FT = 4000  # runways shorter than this cannot serve typical airline jets
PAVED = ("ASP", "CON", "PEM", "ASPH", "CONC", "BIT")


def build(client: httpx.Client, raw_dir: Path) -> pd.DataFrame:
    airports = pd.read_csv(
        download_file(client, AIRPORTS_URL, raw_dir / "ourairports" / "airports.csv"),
        keep_default_na=False,
        na_values=[""],
        low_memory=False,
    )
    runways = pd.read_csv(
        download_file(client, RUNWAYS_URL, raw_dir / "ourairports" / "runways.csv"),
        keep_default_na=False,
        na_values=[""],
        low_memory=False,
    )
    return normalize(airports, runways)


def normalize(airports: pd.DataFrame, runways: pd.DataFrame) -> pd.DataFrame:
    us = airports[
        (airports["iso_country"] == "US")
        & airports["iata_code"].notna()
        & airports["type"].isin(["large_airport", "medium_airport", "small_airport"])
    ].copy()
    us["state"] = us["iso_region"].str.split("-").str[1]

    rw = runways[runways["closed"].fillna(0).astype(int) == 0].copy()
    rw["length_ft"] = pd.to_numeric(rw["length_ft"], errors="coerce")
    surface = rw["surface"].fillna("").str.upper()
    rw["paved"] = surface.str.startswith(PAVED)
    usable = rw[rw["paved"] & (rw["length_ft"] >= MIN_RUNWAY_FT)]
    rw_stats = usable.groupby("airport_ident").agg(
        runways=("length_ft", "size"), longest_runway_ft=("length_ft", "max")
    )

    out = us.merge(rw_stats, left_on="ident", right_index=True, how="left")
    out = out.rename(
        columns={
            "iata_code": "code",
            "icao_code": "icao",
            "latitude_deg": "latitude",
            "longitude_deg": "longitude",
            "municipality": "city",
        }
    )
    out["icao"] = out["icao"].fillna(out["gps_code"])
    out["runways"] = out["runways"].fillna(0).astype(int)
    cols = [
        "code",
        "icao",
        "name",
        "city",
        "state",
        "latitude",
        "longitude",
        "type",
        "runways",
        "longest_runway_ft",
        "keywords",
    ]
    out = out[cols].drop_duplicates(subset=["code"]).reset_index(drop=True)
    out["keywords"] = out["keywords"].fillna("")
    return out
