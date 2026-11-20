"""Ingest-time data validation. Fail loudly on bad data instead of serving wrong numbers.

Each table has a small contract: required columns, a minimum row count, value ranges,
and a few "known anchor" sanity checks (for example, BOS must exist).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


class ValidationError(ValueError):
    pass


@dataclass
class TableContract:
    required_columns: list[str]
    min_rows: int
    non_negative: list[str] = field(default_factory=list)
    must_contain_airports: list[str] = field(default_factory=list)
    airport_column: str = "airport"


CONTRACTS: dict[str, TableContract] = {
    "airports": TableContract(
        ["code", "name", "state", "latitude", "longitude", "runways"],
        500,
        must_contain_airports=["BOS", "SFO", "LAX", "SNA", "ANC"],
        airport_column="code",
    ),
    "enplanements": TableContract(
        ["airport", "year", "enplanements", "hub_class"],
        1000,
        ["enplanements"],
        must_contain_airports=["BOS", "SFO", "LAX", "SNA", "ANC"],
    ),
    "t100_monthly": TableContract(
        ["airport", "month", "departures", "passengers", "seats"],
        10_000,
        ["departures", "passengers", "seats"],
        must_contain_airports=["BOS", "SFO", "ANC"],
    ),
    "t100_segments": TableContract(
        ["origin", "dest", "aircraft_config", "month", "departures", "seats", "passengers", "distance_mi"],
        50_000,
        ["departures", "seats", "passengers", "distance_mi"],
        must_contain_airports=["ANC", "SFO"],
        airport_column="origin",
    ),
    "airfare_markets": TableContract(
        ["year", "quarter", "airport_1", "airport_2", "distance_mi", "pax_per_day", "fare"],
        5_000,
        ["pax_per_day", "fare"],
    ),
    "ontime_monthly": TableContract(
        ["airport", "month", "dep_flights", "taxi_out_avg_min", "arr_flights"],
        2_000,
        ["dep_flights", "arr_flights"],
        must_contain_airports=["LAX", "SNA", "SFO"],
    ),
}


def validate(name: str, df: pd.DataFrame) -> None:
    contract = CONTRACTS[name]
    missing = [c for c in contract.required_columns if c not in df.columns]
    if missing:
        raise ValidationError(f"{name}: missing columns {missing}")
    if len(df) < contract.min_rows:
        raise ValidationError(f"{name}: only {len(df)} rows (expected >= {contract.min_rows})")
    for col in contract.non_negative:
        if (pd.to_numeric(df[col], errors="coerce") < 0).any():
            raise ValidationError(f"{name}: negative values in {col}")
    if contract.must_contain_airports:
        present = set(df[contract.airport_column].astype(str))
        absent = [a for a in contract.must_contain_airports if a not in present]
        if absent:
            raise ValidationError(f"{name}: anchor airports missing: {absent}")
