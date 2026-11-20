"""Airport code reconciliation, applied to every source at ingest so all tables join on one key (IATA).

Two problems are handled:
  1. FAA location ids vs IATA codes. FAA enplanement files use FAA LIDs, BTS/OurAirports use IATA.
     They are usually identical but not always (Glacier Park: FAA "GPI" vs IATA "FCA";
     State College: "UNV" vs "SCE"). OurAirports carries both, so we bridge with it.
  2. Code changes over time (below).

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


def lid_to_iata(airports: pd.DataFrame) -> dict[str, str]:
    """FAA location id -> IATA code, only where they differ and the LID is not itself another IATA code."""
    if "local_code" not in airports.columns:
        return {}
    iata = set(airports["code"])
    pairs = airports.dropna(subset=["local_code"])
    return {
        lid: code
        for lid, code in zip(pairs["local_code"], pairs["code"], strict=True)
        if lid != code and lid not in iata
    }


def canonicalize(table: str, df: pd.DataFrame, lid_map: dict[str, str] | None = None) -> pd.DataFrame:
    renames = {**(lid_map or {}), **CODE_RENAMES} if table == "enplanements" else CODE_RENAMES
    for col in CODE_COLUMNS.get(table, []):
        if col in df.columns:
            df[col] = df[col].replace(renames)
    if table == "airports":
        df = df.drop_duplicates(subset=["code"], keep="first")
    return df
