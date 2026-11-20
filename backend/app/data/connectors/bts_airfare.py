"""BTS Consumer Airfare Report, Table 1a: All U.S. Airport Pair Markets (Socrata `tfrh-tu9e`).

Quarterly, per airport pair (non-directional): average daily O&D passengers, average fare,
distance, and dominant carrier share. Used for fare premium (pricing power / supply scarcity)
and market concentration.
"""

from __future__ import annotations

import httpx
import pandas as pd

from app.data.connectors.socrata import fetch_soda

BASE_URL = "https://datahub.transportation.gov"
DATASET_ID = "tfrh-tu9e"
SOURCE_NAME = "BTS Consumer Airfare Report (Table 1a, airport-pair markets)"
SOURCE_URL = f"{BASE_URL}/d/{DATASET_ID}"

COLUMNS = {
    "year": "year",
    "quarter": "quarter",
    "airport_1": "airport_1",
    "airport_2": "airport_2",
    "nsmiles": "distance_mi",
    "passengers": "pax_per_day",
    "fare": "fare",
    "carrier_lg": "carrier_largest",
    "large_ms": "largest_share",
}


def fetch(client: httpx.Client, since_year: int = 2023) -> pd.DataFrame:
    raw = fetch_soda(
        client,
        BASE_URL,
        DATASET_ID,
        select=list(COLUMNS),
        where=f"year >= '{since_year}'",
        order="year, quarter, airport_1, airport_2",
    )
    return normalize(raw)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=COLUMNS)[list(COLUMNS.values())].copy()
    for col in ("year", "quarter"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    for col in ("distance_mi", "pax_per_day", "fare", "largest_share"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["year", "quarter", "distance_mi", "pax_per_day", "fare"])
    df = df[(df["distance_mi"] > 0) & (df["fare"] > 0)]
    return df.reset_index(drop=True)
