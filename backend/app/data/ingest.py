"""Batch ingestion: public sources -> validated, normalized Parquet snapshot ("our replica").

    uv run python -m app.data.ingest                 # refresh everything
    uv run python -m app.data.ingest --only airfare  # refresh one table

Design choices:
  * The agent never calls slow statistical APIs at question time; it reads this snapshot.
    That gives fast, reproducible answers that don't break when a government site is down.
  * Each table is fetched, validated, and written independently. If one source fails,
    the last good Parquet for that table is kept and the failure is recorded in the manifest.
  * `manifest.json` records source, URL, data period, row count, and fetch time per table.
    The agent cites these periods in every answer.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from app.config import get_settings
from app.data import validation
from app.data.connectors import (
    bts_airfare,
    bts_ontime,
    bts_t100_segment,
    bts_t100_summary,
    faa_enplanements,
    ourairports,
)
from app.data.connectors.http import build_client

log = logging.getLogger("ingest")


def _month_period(df: pd.DataFrame, col: str = "month") -> str:
    months = pd.to_datetime(df[col])
    return f"{months.min():%b %Y} - {months.max():%b %Y}"


def latest_ontime_months(client: httpx.Client, count: int = 12) -> list[tuple[int, int]]:
    """Find the newest published on-time month (HEAD probe), then return the `count` months up to it."""
    probe = date.today().replace(day=1)
    for _ in range(6):
        url = bts_ontime.URL_TEMPLATE.format(year=probe.year, month=probe.month)
        if client.head(url).status_code == 200:
            break
        probe = (pd.Timestamp(probe) - pd.DateOffset(months=1)).date()
    else:
        raise RuntimeError("no recent on-time file found")
    end = pd.Timestamp(probe)
    return [
        ((end - pd.DateOffset(months=i)).year, (end - pd.DateOffset(months=i)).month) for i in reversed(range(count))
    ]


def build_tables(client: httpx.Client, raw_dir: Path) -> dict[str, tuple[Callable[[], pd.DataFrame], dict]]:
    """Table name -> (builder, source metadata). Builders are lazy so `--only` works."""
    this_year = date.today().year
    return {
        "airports": (
            lambda: ourairports.build(client, raw_dir),
            {"source": ourairports.SOURCE_NAME, "url": ourairports.SOURCE_URL},
        ),
        "enplanements": (
            lambda: faa_enplanements.build(client, raw_dir),
            {"source": faa_enplanements.SOURCE_NAME, "url": faa_enplanements.SOURCE_URL},
        ),
        "t100_monthly": (
            lambda: bts_t100_summary.fetch(client, since_year=2019),
            {"source": bts_t100_summary.SOURCE_NAME, "url": bts_t100_summary.SOURCE_URL},
        ),
        "t100_segments": (
            lambda: bts_t100_segment.build(client, raw_dir, [this_year - 1, this_year]),
            {"source": bts_t100_segment.SOURCE_NAME, "url": bts_t100_segment.SOURCE_URL},
        ),
        "airfare_markets": (
            lambda: bts_airfare.fetch(client, since_year=this_year - 3),
            {"source": bts_airfare.SOURCE_NAME, "url": bts_airfare.SOURCE_URL},
        ),
        "ontime_monthly": (
            lambda: bts_ontime.build(client, raw_dir, latest_ontime_months(client)),
            {"source": bts_ontime.SOURCE_NAME, "url": bts_ontime.SOURCE_URL},
        ),
    }


def describe_period(name: str, df: pd.DataFrame) -> str:
    if name == "enplanements":
        years = sorted(df["year"].unique())
        return f"CY{years[0]}-CY{years[-1]} (CY2025 preliminary)"
    if name == "airfare_markets":
        first = df.sort_values(["year", "quarter"]).iloc[0]
        last = df.sort_values(["year", "quarter"]).iloc[-1]
        return f"{first.year} Q{first.quarter} - {last.year} Q{last.quarter}"
    if name in ("t100_monthly", "t100_segments", "ontime_monthly"):
        return _month_period(df)
    return f"as of {date.today():%b %Y}"


def run(only: list[str] | None = None) -> dict:
    settings = get_settings()
    snapshot_dir, raw_dir = settings.snapshot_dir, settings.raw_dir
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"tables": {}}

    with build_client(timeout_s=300) as client:
        for name, (builder, meta) in build_tables(client, raw_dir).items():
            if only and name not in only:
                continue
            started = time.time()
            try:
                df = builder()
                validation.validate(name, df)
                df.to_parquet(snapshot_dir / f"{name}.parquet", index=False, compression="zstd")
                manifest["tables"][name] = {
                    **meta,
                    "period": describe_period(name, df),
                    "rows": len(df),
                    "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "status": "ok",
                }
                log.info("%s: %s rows in %.0fs", name, len(df), time.time() - started)
            except Exception as exc:  # keep the last good snapshot, record the failure
                log.exception("%s failed; keeping previous snapshot", name)
                previous = manifest["tables"].get(name, {})
                manifest["tables"][name] = {**previous, **meta, "status": f"stale: {exc}"[:300]}

    manifest["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="table names to refresh")
    args = parser.parse_args()
    manifest = run(args.only)
    for name, meta in manifest["tables"].items():
        print(f"{name:16s} {meta.get('status', '?'):8.8s} {meta.get('rows', 0):>9} rows  {meta.get('period', '')}")


if __name__ == "__main__":
    main()
