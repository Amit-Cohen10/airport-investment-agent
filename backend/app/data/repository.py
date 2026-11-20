"""Read-only data access over the Parquet snapshot ("our replica"), via DuckDB.

The single place that knows how data is stored. Everything above this layer works with
DataFrames and domain objects, so swapping Parquet+DuckDB for Postgres/BigQuery later
would only touch this file.
"""

from __future__ import annotations

import json
import threading
from functools import cached_property
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from app.analytics.features import FeatureSet, Window, build_features, ttm_windows

TABLES = ("airports", "enplanements", "t100_monthly", "t100_segments", "airfare_markets", "ontime_monthly")


class SnapshotMissingError(RuntimeError):
    pass


class Repository:
    def __init__(self, snapshot_dir: Path):
        self.snapshot_dir = snapshot_dir
        missing = [t for t in TABLES if not (snapshot_dir / f"{t}.parquet").exists()]
        if missing:
            raise SnapshotMissingError(f"snapshot tables missing in {snapshot_dir}: {missing}. Run `make ingest`.")
        self._con = duckdb.connect(database=":memory:")
        for table in TABLES:
            path = (snapshot_dir / f"{table}.parquet").as_posix()
            self._con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{path}')")
        self._lock = threading.Lock()  # DuckDB connections are not thread-safe

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            return self._con.execute(sql, params or []).df()

    # --- metadata -----------------------------------------------------------
    @cached_property
    def manifest(self) -> dict[str, Any]:
        path = self.snapshot_dir / "manifest.json"
        return json.loads(path.read_text()) if path.exists() else {"tables": {}}

    def source(self, table: str) -> dict[str, Any]:
        return self.manifest.get("tables", {}).get(table, {})

    # --- tables -------------------------------------------------------------
    @cached_property
    def airports(self) -> pd.DataFrame:
        return self.query("SELECT * FROM airports")

    @cached_property
    def enplanements(self) -> pd.DataFrame:
        return self.query("SELECT * FROM enplanements")

    @cached_property
    def features(self) -> FeatureSet:
        """Per-airport metric table. Computed once (~1 s) and cached for the process lifetime."""
        return build_features(
            self.airports,
            self.enplanements,
            self.query("SELECT * FROM t100_monthly"),
            self.query("SELECT * FROM ontime_monthly"),
            self.query("SELECT * FROM airfare_markets"),
        )

    @cached_property
    def segment_window(self) -> Window:
        months = self.query("SELECT DISTINCT month FROM t100_segments")["month"]
        return ttm_windows(months)[0]

    def segments_from(self, origin: str, window: Window | None = None) -> pd.DataFrame:
        w = window or self.segment_window
        return self.query(
            "SELECT * FROM t100_segments WHERE origin = ? AND month BETWEEN ? AND ?",
            [origin, w.start, w.end],
        )

    def monthly_traffic(self, airport: str) -> pd.DataFrame:
        return self.query("SELECT * FROM t100_monthly WHERE airport = ? ORDER BY month", [airport])
