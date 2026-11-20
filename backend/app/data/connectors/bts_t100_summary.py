"""BTS T-100 Segment Summary by Origin Airport (Socrata `r495-tyji` on data.bts.gov).

Monthly, per origin airport, all carriers: departures, seats, passengers, freight,
domestic vs outbound-international split. Used for capacity utilisation and growth trends.
"""

from __future__ import annotations

import httpx
import pandas as pd

from app.data.connectors.socrata import fetch_soda

BASE_URL = "https://data.bts.gov"
DATASET_ID = "r495-tyji"
SOURCE_NAME = "BTS T-100 Segment Summary by Origin Airport"
SOURCE_URL = f"{BASE_URL}/d/{DATASET_ID}"

# Socrata column -> our column. The dataset mangles long names (e.g. `_1` = passengers).
COLUMNS = {
    "origin_airport_code": "airport",
    "reporting_month": "month",
    "total_departures": "departures",
    "total_passengers": "passengers",
    "total_seats": "seats",
    "total_freight_lbs": "freight_lbs",
    "domestic_departures": "dom_departures",
    "domestic_passengers": "dom_passengers",
    "outbound_international": "intl_departures",
    "outbound_international_1": "intl_passengers",
    "outbound_international_seats": "intl_seats",
}


def fetch(client: httpx.Client, since_year: int = 2019) -> pd.DataFrame:
    raw = fetch_soda(
        client,
        BASE_URL,
        DATASET_ID,
        select=list(COLUMNS),
        where=f"year >= '{since_year}'",
        order="reporting_month, origin_airport_code, :id",  # :id makes paging order unique
    )
    return normalize(raw)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=COLUMNS)[list(COLUMNS.values())].copy()
    df["month"] = pd.to_datetime(df["month"]).dt.to_period("M").dt.to_timestamp()
    numeric = [c for c in df.columns if c not in ("airport", "month")]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    df["airport"] = df["airport"].str.strip().str.upper()
    # Some airports appear more than once per month (multiple airport ids); collapse them.
    return df.groupby(["airport", "month"], as_index=False)[numeric].sum()
