"""Generic client for Socrata Open Data (SODA) APIs used by data.bts.gov / datahub.transportation.gov."""

from __future__ import annotations

import logging

import httpx
import pandas as pd

from app.data.connectors.http import request_with_retry

log = logging.getLogger(__name__)


def fetch_soda(
    client: httpx.Client,
    base_url: str,
    dataset_id: str,
    *,
    select: list[str] | None = None,
    where: str | None = None,
    order: str = ":id",
    page_size: int = 50_000,
    max_rows: int = 2_000_000,
) -> pd.DataFrame:
    """Page through a SODA resource and return all rows as a DataFrame (all values are strings)."""
    url = f"{base_url.rstrip('/')}/resource/{dataset_id}.json"
    frames: list[pd.DataFrame] = []
    offset = 0
    while offset < max_rows:
        params: dict[str, str | int] = {"$limit": page_size, "$offset": offset, "$order": order}
        if select:
            params["$select"] = ",".join(select)
        if where:
            params["$where"] = where
        rows = request_with_retry(client, "GET", url, params=params).json()
        if isinstance(rows, dict) and rows.get("error"):
            raise ValueError(f"SODA error for {dataset_id}: {rows.get('message')}")
        if not rows:
            break
        frames.append(pd.DataFrame.from_records(rows))
        log.info("%s: fetched %s rows (offset %s)", dataset_id, len(rows), offset)
        if len(rows) < page_size:
            break
        offset += page_size
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=select or [])
