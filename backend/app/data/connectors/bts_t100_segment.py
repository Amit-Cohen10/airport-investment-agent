"""BTS T-100 Segment, All Carriers (TranStats table `FMG`).

The only public source with per-route segment data for *all* carriers, including foreign
carriers and all-cargo operators: departures, seats, passengers, freight, great-circle
distance, aircraft configuration (passenger / freighter) and service class.

TranStats has no static download URL for T-100, only an ASP.NET form. We GET the form,
replay its hidden state fields (__VIEWSTATE etc.), and POST the column selection.
This is the most fragile connector, so:
  * the result is cached on disk per year,
  * ingest validates the file, and
  * on failure the previous snapshot is kept (see ingest.py).
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path

import httpx
import pandas as pd

from app.data.connectors.http import SourceUnavailableError, download_file, request_with_retry

log = logging.getLogger(__name__)

SOURCE_NAME = "BTS T-100 Segment (all carriers, incl. foreign & cargo)"
FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FMG&QO_fu146_anzr=Nv4%20Pn44vr45"
SOURCE_URL = FORM_URL
FIELDS = [
    "DEPARTURES_PERFORMED",
    "SEATS",
    "PASSENGERS",
    "FREIGHT",
    "DISTANCE",
    "ORIGIN",
    "DEST",
    "DEST_COUNTRY",
    "AIRCRAFT_CONFIG",
    "CLASS",
    "YEAR",
    "MONTH",
]
HIDDEN_FIELDS = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")

# AIRCRAFT_CONFIG codes (BTS lookup L_AIRCRAFT_CONFIG)
AIRCRAFT_CONFIG = {1: "passenger", 2: "freight", 3: "combi", 4: "seaplane"}


def _form_state(html: str) -> dict[str, str]:
    state = {}
    for name in HIDDEN_FIELDS:
        match = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
        if not match:
            raise SourceUnavailableError(f"TranStats form changed: hidden field {name} missing")
        state[name] = match.group(1)
    return state


def download_year(client: httpx.Client, raw_dir: Path, year: int) -> Path:
    dest = raw_dir / "t100_segment" / f"t100_segment_{year}.zip"
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    form = request_with_retry(client, "GET", FORM_URL).text
    payload = {
        **_form_state(form),
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "cboGeography": "All",
        "cboYear": str(year),
        "cboPeriod": "All",
        "chkDownloadZip": "on",
        "btnDownload": "Download",
        **{field: "on" for field in FIELDS},
    }
    log.info("requesting T-100 segment %s from TranStats (takes ~1-2 min)", year)
    path = download_file(client, FORM_URL, dest, method="POST", data=payload, headers={"Referer": FORM_URL})
    if not zipfile.is_zipfile(path):
        path.unlink(missing_ok=True)
        raise SourceUnavailableError("TranStats returned a non-zip response (form rejected)")
    return path


def read_year(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        csv_name = next(n for n in zf.namelist() if n.upper().startswith("T_T100"))
        with zf.open(csv_name) as fh:
            df = pd.read_csv(fh, usecols=FIELDS, low_memory=False)
    df = df[df["DEPARTURES_PERFORMED"] > 0]
    df = df.rename(columns=str.lower).rename(
        columns={
            "departures_performed": "departures",
            "class": "service_class",
            "distance": "distance_mi",
            "freight": "freight_lbs",
        }
    )
    df["month"] = pd.to_datetime(dict(year=df["year"], month=df["month"], day=1))
    keys = ["origin", "dest", "dest_country", "aircraft_config", "service_class", "month"]
    agg = df.groupby(keys, as_index=False).agg(
        departures=("departures", "sum"),
        seats=("seats", "sum"),
        passengers=("passengers", "sum"),
        freight_lbs=("freight_lbs", "sum"),
        distance_mi=("distance_mi", "max"),
    )
    return agg


def build(client: httpx.Client, raw_dir: Path, years: list[int]) -> pd.DataFrame:
    return pd.concat([read_year(download_year(client, raw_dir, y)) for y in years], ignore_index=True)
