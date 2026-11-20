"""FAA Passenger Boarding (Enplanement) Data, calendar years, commercial-service airports.

The authoritative annual passenger count per US airport, plus the FAA hub class.
Each yearly xlsx contains two years (current and previous), so 3 files give us
CY2019, CY2022-CY2025. URLs move between upload folders, so they are pinned here.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pandas as pd

from app.data.connectors.http import download_file

log = logging.getLogger(__name__)

SOURCE_NAME = "FAA Passenger Boarding (Enplanement) Data"
SOURCE_URL = "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger"
_BASE = "https://www.faa.gov/sites/faa.gov/files/"
FILES = {
    # file label -> (url, years contained)
    "cy2025": (
        "https://www.faa.gov/airports/planning_capacity/passenger_allcargo_stats/passenger/"
        "arp-cy2025-commercial-service-enplanements.xlsx",
        (2025, 2024),
    ),
    "cy2023": (_BASE + "2024-10/cy23-commercial-service-enplanements.xlsx", (2023, 2022)),
    "cy2019": (
        _BASE + "airports/planning_capacity/passenger_allcargo_stats/passenger/"
        "cy19-commercial-service-enplanements.xlsx",
        (2019, 2018),
    ),
}
PRELIMINARY_YEARS = {2025}


def _parse(path: Path, years: tuple[int, int]) -> pd.DataFrame:
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    cur, prev = years
    cur_col = f"CY {cur % 100:02d} Enplanements"
    prev_col = f"CY {prev % 100:02d} Enplanements"
    base = df[["Locid", "ST", "City", "Airport Name", "Hub"]].rename(
        columns={
            "Locid": "airport",
            "ST": "state",
            "City": "city",
            "Airport Name": "name",
            "Hub": "hub_class",
        }
    )
    rows = []
    for year, col in ((cur, cur_col), (prev, prev_col)):
        part = base.copy()
        part["year"] = year
        part["enplanements"] = pd.to_numeric(df[col], errors="coerce")
        rows.append(part)
    out = pd.concat(rows, ignore_index=True).dropna(subset=["airport", "enplanements"])
    out["airport"] = out["airport"].astype(str).str.strip().str.upper()
    return out


def build(client: httpx.Client, raw_dir: Path) -> pd.DataFrame:
    frames = []
    for label, (url, years) in FILES.items():
        # The newest file is revised (preliminary -> final); older ones are immutable.
        max_age = 30 if label == next(iter(FILES)) else None
        path = download_file(client, url, raw_dir / "faa" / f"enplanements_{label}.xlsx", max_age_days=max_age)
        frames.append(_parse(path, years))
    df = pd.concat(frames, ignore_index=True)
    # A year can appear in two files (as "current" and as "previous"); prefer the newer file,
    # which is the first occurrence because FILES is ordered newest first.
    df = df.drop_duplicates(subset=["airport", "year"], keep="first")
    df["hub_class"] = df["hub_class"].astype(str).str.strip().replace({"nan": None})
    df["preliminary"] = df["year"].isin(PRELIMINARY_YEARS)
    return df.sort_values(["airport", "year"]).reset_index(drop=True)
