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
    unique_keys: list[str] = field(default_factory=list)  # each combination must appear once
    not_null: list[str] = field(default_factory=list)  # columns that must be fully populated
    monthly_column: str | None = None  # monthly tables: no missing months, no truncated last month


CONTRACTS: dict[str, TableContract] = {
    "airports": TableContract(
        ["code", "name", "state", "latitude", "longitude", "runways"],
        500,
        must_contain_airports=["BOS", "SFO", "LAX", "SNA", "ANC"],
        airport_column="code",
        unique_keys=["code"],
        not_null=["code", "name", "state"],
    ),
    "enplanements": TableContract(
        ["airport", "year", "enplanements", "hub_class"],
        1000,
        ["enplanements"],
        must_contain_airports=["BOS", "SFO", "LAX", "SNA", "ANC"],
        unique_keys=["airport", "year"],
        not_null=["airport", "year", "enplanements"],
    ),
    "t100_monthly": TableContract(
        ["airport", "month", "departures", "passengers", "seats"],
        10_000,
        ["departures", "passengers", "seats"],
        must_contain_airports=["BOS", "SFO", "ANC"],
        unique_keys=["airport", "month"],
        not_null=["airport", "month", "departures", "passengers", "seats"],
        monthly_column="month",
    ),
    "t100_segments": TableContract(
        ["origin", "dest", "aircraft_config", "month", "departures", "seats", "passengers", "distance_mi"],
        50_000,
        ["departures", "seats", "passengers", "distance_mi"],
        must_contain_airports=["ANC", "SFO"],
        airport_column="origin",
        unique_keys=["origin", "dest", "dest_country", "aircraft_config", "service_class", "month"],
        not_null=["origin", "dest", "month", "departures", "seats", "passengers", "distance_mi"],
        monthly_column="month",
    ),
    "airfare_markets": TableContract(
        ["year", "quarter", "airport_1", "airport_2", "distance_mi", "pax_per_day", "fare"],
        5_000,
        ["pax_per_day", "fare"],
        unique_keys=["year", "quarter", "airport_1", "airport_2"],
        not_null=["year", "quarter", "airport_1", "airport_2", "distance_mi", "pax_per_day", "fare"],
    ),
    "ontime_monthly": TableContract(
        ["airport", "month", "dep_flights", "taxi_out_avg_min", "arr_flights"],
        2_000,
        ["dep_flights", "arr_flights"],
        must_contain_airports=["LAX", "SNA", "SFO"],
        unique_keys=["airport", "month"],
        not_null=["airport", "month", "dep_flights"],
        monthly_column="month",
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
    for col in contract.not_null:
        nulls = int(df[col].isna().sum())
        if nulls:
            raise ValidationError(f"{name}: {nulls} null values in required column {col}")
    if contract.unique_keys and df.duplicated(subset=contract.unique_keys).any():
        dupes = int(df.duplicated(subset=contract.unique_keys).sum())
        raise ValidationError(f"{name}: {dupes} duplicate rows for key {contract.unique_keys}")
    if contract.monthly_column:
        _check_months(name, df, contract)
    if contract.must_contain_airports:
        present = set(df[contract.airport_column].astype(str))
        absent = [a for a in contract.must_contain_airports if a not in present]
        if absent:
            raise ValidationError(f"{name}: anchor airports missing: {absent}")


def _check_months(name: str, df: pd.DataFrame, contract: TableContract) -> None:
    """Every month in the covered range is present, and the newest month is not a partial load."""
    months = pd.to_datetime(df[contract.monthly_column]).dt.to_period("M")
    expected = pd.period_range(months.min(), months.max(), freq="M")
    gaps = sorted(set(expected) - set(months.unique()))
    if gaps:
        raise ValidationError(f"{name}: missing months {[str(g) for g in gaps[:6]]}")
    per_month = months.value_counts()
    if per_month[months.max()] < 0.5 * per_month.median():
        raise ValidationError(f"{name}: latest month {months.max()} looks truncated ({per_month[months.max()]} rows)")
    latest = df[months == months.max()]
    absent = [a for a in contract.must_contain_airports if a not in set(latest[contract.airport_column])]
    if absent:
        raise ValidationError(f"{name}: anchor airports missing from the latest month: {absent}")
