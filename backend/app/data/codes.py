"""Airport code changes over time, applied to every source at ingest so history joins up.

Example: Palm Beach International (PBI) was renamed and re-coded DJT in 2026. FAA CY2024+,
OurAirports and the back-filled T-100 summary use DJT, while older FAA years, on-time,
segment and fare data still carry PBI. Without this map the airport splits in two.
"""

from __future__ import annotations

import pandas as pd

CODE_RENAMES = {"PBI": "DJT"}

CODE_COLUMNS = {
    "airports": ["code"],
    "enplanements": ["airport"],
    "t100_monthly": ["airport"],
    "t100_segments": ["origin", "dest"],
    "airfare_markets": ["airport_1", "airport_2"],
    "ontime_monthly": ["airport"],
}


def canonicalize(table: str, df: pd.DataFrame) -> pd.DataFrame:
    for col in CODE_COLUMNS.get(table, []):
        if col in df.columns:
            df[col] = df[col].replace(CODE_RENAMES)
    if table == "airports":
        df = df.drop_duplicates(subset=["code"], keep="first")
    return df
