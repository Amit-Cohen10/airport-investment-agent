"""Data-layer tests with small synthetic inputs: connectors' parsing, HTTP resilience, validation, code canonicalization.

No network: HTTP is mocked with respx, files are generated in tmp_path.
"""

from __future__ import annotations

import zipfile

import httpx
import numpy as np
import pandas as pd
import pytest
import respx

from app.data import validation
from app.data.codes import canonicalize
from app.data.connectors import (
    bts_airfare,
    bts_ontime,
    bts_t100_segment,
    bts_t100_summary,
    faa_enplanements,
    ourairports,
)
from app.data.connectors.http import SourceUnavailableError, download_file, request_with_retry
from app.data.connectors.live import summarize_metar, summarize_nas_event
from app.data.connectors.socrata import fetch_soda


# --- HTTP resilience -----------------------------------------------------------------------------
class TestHttp:
    @respx.mock
    def test_retries_on_5xx_then_succeeds(self):
        route = respx.get("https://x.test/a").mock(
            side_effect=[httpx.Response(503), httpx.Response(200, json={"ok": 1})]
        )
        with httpx.Client() as client:
            assert request_with_retry(client, "GET", "https://x.test/a", base_delay_s=0).json() == {"ok": 1}
        assert route.call_count == 2

    @respx.mock
    def test_non_retryable_4xx_fails_fast(self):
        route = respx.get("https://x.test/b").mock(return_value=httpx.Response(404))
        with httpx.Client() as client, pytest.raises(SourceUnavailableError):
            request_with_retry(client, "GET", "https://x.test/b", base_delay_s=0)
        assert route.call_count == 1

    @respx.mock
    def test_gives_up_after_attempts(self):
        respx.get("https://x.test/c").mock(return_value=httpx.Response(429))
        with httpx.Client() as client, pytest.raises(SourceUnavailableError):
            request_with_retry(client, "GET", "https://x.test/c", attempts=2, base_delay_s=0)

    @respx.mock
    def test_download_is_atomic_and_cached(self, tmp_path):
        route = respx.get("https://x.test/f.csv").mock(return_value=httpx.Response(200, content=b"a,b\n1,2\n"))
        dest = tmp_path / "f.csv"
        with httpx.Client() as client:
            download_file(client, "https://x.test/f.csv", dest)
            download_file(client, "https://x.test/f.csv", dest)  # cache hit
        assert dest.read_bytes() == b"a,b\n1,2\n" and route.call_count == 1
        assert not (tmp_path / "f.csv.part").exists()


class TestSocrata:
    @respx.mock
    def test_pages_until_short_page(self):
        pages = [[{"x": str(i)} for i in range(3)], [{"x": "9"}]]
        route = respx.get("https://soda.test/resource/abc.json").mock(
            side_effect=[httpx.Response(200, json=p) for p in pages]
        )
        with httpx.Client() as client:
            df = fetch_soda(client, "https://soda.test", "abc", page_size=3)
        assert len(df) == 4 and route.call_count == 2

    @respx.mock
    def test_soda_error_payload_raises(self):
        respx.get("https://soda.test/resource/abc.json").mock(
            return_value=httpx.Response(200, json={"error": True, "message": "bad column"})
        )
        with httpx.Client() as client, pytest.raises(ValueError, match="bad column"):
            fetch_soda(client, "https://soda.test", "abc")


# --- Connector parsing ---------------------------------------------------------------------------
def test_t100_summary_normalize_collapses_duplicate_ids_and_casts():
    raw = pd.DataFrame(
        {
            "origin_airport_code": ["bos ", "BOS"],
            "reporting_month": ["2026-08-01T00:00:00.000"] * 2,
            "total_departures": ["10", "5"],
            "total_passengers": ["1000", "500"],
            "total_seats": ["1200", "600"],
            "total_freight_lbs": ["0", None],
            "domestic_departures": ["10", "5"],
            "domestic_passengers": ["1000", "500"],
            "outbound_international": ["0", "0"],
            "outbound_international_1": ["0", "0"],
            "outbound_international_seats": ["0", "0"],
        }
    )
    df = bts_t100_summary.normalize(raw)
    assert len(df) == 1 and df.iloc[0]["airport"] == "BOS"
    assert df.iloc[0]["departures"] == 15 and df.iloc[0]["seats"] == 1800


def test_airfare_normalize_drops_invalid_rows():
    raw = pd.DataFrame(
        {
            "year": ["2026", "2026", "x"],
            "quarter": ["1", "1", "1"],
            "airport_1": ["BOS", "BOS", "BOS"],
            "airport_2": ["SFO", "LAX", "JFK"],
            "nsmiles": ["2704", "0", "187"],
            "passengers": ["100", "50", "10"],
            "fare": ["300", "250", "120"],
            "carrier_lg": ["UA", "DL", "B6"],
            "large_ms": ["0.5", "0.6", "0.7"],
        }
    )
    df = bts_airfare.normalize(raw)
    assert list(df["airport_2"]) == ["SFO"]  # zero distance and bad year removed


def test_ourairports_counts_only_open_paved_long_runways():
    airports = pd.DataFrame(
        {
            "ident": ["KSNA"],
            "type": ["large_airport"],
            "name": ["John Wayne"],
            "latitude_deg": [33.6],
            "longitude_deg": [-117.8],
            "iso_country": ["US"],
            "iso_region": ["US-CA"],
            "municipality": ["Santa Ana"],
            "iata_code": ["SNA"],
            "local_code": ["SNA"],
            "icao_code": ["KSNA"],
            "gps_code": ["KSNA"],
            "keywords": [None],
        }
    )
    runways = pd.DataFrame(
        {
            "airport_ident": ["KSNA", "KSNA", "KSNA"],
            "length_ft": [5700, 2887, 6000],
            "surface": ["ASP", "ASP", "ASP"],
            "closed": [0, 0, 1],
        }
    )
    out = ourairports.normalize(airports, runways)
    assert out.iloc[0]["runways"] == 1 and out.iloc[0]["state"] == "CA"


def test_faa_parse_reads_both_years(tmp_path):
    df = pd.DataFrame(
        {
            "Rank": [1],
            "RO": ["NE"],
            "ST": ["MA"],
            "Locid": ["BOS"],
            "City": ["Boston"],
            "Airport Name": ["Logan"],
            "S/L": ["P"],
            "Hub": ["L"],
            "CY 25 Enplanements": [21_000_000],
            "CY 24 Enplanements": [20_000_000],
            "% Change": [0.05],
        }
    )
    path = tmp_path / "f.xlsx"
    df.to_excel(path, index=False)
    out = faa_enplanements._parse(path, (2025, 2024))
    assert sorted(out["year"]) == [2024, 2025]
    assert out.set_index("year").loc[2025, "enplanements"] == 21_000_000


def _zip_csv(path, name: str, df: pd.DataFrame) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(name, df.to_csv(index=False))


def test_ontime_aggregation_and_peak_hour(tmp_path):
    flights = pd.DataFrame(
        {
            "FlightDate": ["2026-09-01"] * 4,
            "Origin": ["SFO", "SFO", "SFO", "LAX"],
            "Dest": ["LAX", "LAX", "SEA", "SFO"],
            "CRSDepTime": [800, 830, 900, 815],
            "CRSArrTime": [930, 1000, 1100, 945],
            "DepDelay": [-5, 20, 10, 0],
            "DepDel15": [0, 1, 0, 0],
            "TaxiOut": [20, 30, 25, 15],
            "TaxiIn": [5, 6, 7, 8],
            "ArrDel15": [0, 1, 0, 0],
            "ArrDelay": [-2, 25, 3, 0],
            "Cancelled": [0, 0, 1, 0],
            "Diverted": [0, 0, 0, 0],
            "CarrierDelay": [0, 10, 0, 0],
            "WeatherDelay": [0, 0, 0, 0],
            "NASDelay": [0, 15, 0, 0],
            "SecurityDelay": [0, 0, 0, 0],
            "LateAircraftDelay": [0, 0, 0, 0],
        }
    )
    path = tmp_path / "m.zip"
    _zip_csv(path, "On_Time_2026_9.csv", flights)
    out = bts_ontime.aggregate_month(path).set_index("airport")
    sfo = out.loc["SFO"]
    assert sfo["dep_flights"] == 3 and sfo["dep_cancelled"] == 1
    assert sfo["taxi_out_avg_min"] == pytest.approx(25.0)  # cancelled flight excluded
    assert sfo["dep_delay_avg_min"] == pytest.approx(10.0)  # early departure counts as 0
    # SFO 08:00 hour: deps at 800, 830 -> 2 movements; LAX->SFO arrives 9:45 (09 hour) with dep 900 -> 2
    assert sfo["peak_hour_movements"] == 2
    assert out.loc["LAX", "nas_delay_min"] == 15


def test_t100_segment_read_year_filters_and_aggregates(tmp_path):
    rows = pd.DataFrame(
        {
            "DEPARTURES_PERFORMED": [10, 5, 0],
            "SEATS": [1000, 500, 0],
            "PASSENGERS": [900, 400, 0],
            "FREIGHT": [0, 0, 0],
            "DISTANCE": [3500, 3500, 100],
            "ORIGIN": ["ANC"] * 3,
            "DEST": ["NRT", "NRT", "FAI"],
            "DEST_COUNTRY": ["JP", "JP", "US"],
            "AIRCRAFT_CONFIG": [1, 1, 1],
            "CLASS": ["F", "F", "F"],
            "YEAR": [2026] * 3,
            "MONTH": [8] * 3,
        }
    )
    path = tmp_path / "t.zip"
    _zip_csv(path, "T_T100_SEGMENT_ALL_CARRIER.csv", rows)
    out = bts_t100_segment.read_year(path)
    assert len(out) == 1  # zero-departure row dropped, two carriers merged
    assert out.iloc[0]["departures"] == 15 and out.iloc[0]["passengers"] == 1300


def test_t100_form_state_detects_changed_form():
    html = '<input name="__VIEWSTATE" value="a"/><input name="__VIEWSTATEGENERATOR" value="b"/>'
    with pytest.raises(SourceUnavailableError, match="__EVENTVALIDATION"):
        bts_t100_segment._form_state(html)


# --- Live API summaries --------------------------------------------------------------------------
def test_nas_summary_ignores_routine_notams_but_keeps_programs():
    event = {
        "airportClosure": {"simpleText": "SNA AD AP CLSD DLY 0730-1415"},
        "groundDelay": {"reason": "low ceilings", "avgDelay": "45 minutes"},
        "airportConfig": {"arrivalRate": 30, "arrivalRunwayConfig": "28R", "departureRunwayConfig": "1L"},
    }
    out = summarize_nas_event(event)
    assert [p["type"] for p in out["active_programs"]] == ["Ground delay program"]
    assert out["arrival_rate_per_hour"] == 30 and out["routine_notams_ignored"]
    assert summarize_nas_event(None)["active_programs"] == []


def test_metar_summary_ceiling_is_lowest_broken_layer():
    metar = {"fltCat": "IFR", "visib": "2", "clouds": [{"cover": "SCT", "base": 400}, {"cover": "BKN", "base": 800}]}
    out = summarize_metar(metar)
    assert out["ceiling_ft"] == 800 and out["cloud_layers"][0] == {"cover": "SCT", "base_ft": 400}


# --- Validation & codes --------------------------------------------------------------------------
class TestValidation:
    def _airports(self, codes):
        return pd.DataFrame(
            {"code": codes, "name": "x", "state": "CA", "latitude": 1.0, "longitude": 1.0, "runways": 1}
        )

    def test_missing_anchor_airport_fails(self):
        df = self._airports([f"A{i:03d}" for i in range(600)])
        with pytest.raises(validation.ValidationError, match="anchor"):
            validation.validate("airports", df)

    def test_too_few_rows_fails(self):
        with pytest.raises(validation.ValidationError, match="rows"):
            validation.validate("airports", self._airports(["BOS", "SFO", "LAX", "SNA", "ANC"]))

    def test_negative_values_fail(self):
        df = pd.DataFrame(
            {"airport": ["BOS", "SFO", "LAX", "SNA", "ANC"] * 300, "year": 2025, "enplanements": -1.0, "hub_class": "L"}
        )
        with pytest.raises(validation.ValidationError, match="negative"):
            validation.validate("enplanements", df)


def test_canonicalize_merges_renamed_codes():
    df = pd.DataFrame({"airport_1": ["PBI", "BOS"], "airport_2": ["JFK", "PBI"]})
    out = canonicalize("airfare_markets", df)
    assert list(out["airport_1"]) == ["DJT", "BOS"] and list(out["airport_2"]) == ["JFK", "DJT"]
    airports = canonicalize("airports", pd.DataFrame({"code": ["DJT", "PBI"], "name": ["new", "old"]}))
    assert list(airports["name"]) == ["new"]


def test_snapshot_manifest_lists_every_table(settings):
    import json

    manifest = json.loads((settings.snapshot_dir / "manifest.json").read_text())
    from app.data.repository import TABLES

    assert set(TABLES) <= set(manifest["tables"])
    assert all(t["status"] == "ok" for t in manifest["tables"].values())


def test_atomic_write_keeps_previous_snapshot_on_failure(tmp_path, monkeypatch):
    from app.data import ingest

    path = tmp_path / "t.parquet"
    good = pd.DataFrame({"a": [1, 2, 3]})
    ingest.write_table_atomically(good, path)

    def broken(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", broken)
    with pytest.raises(OSError):
        ingest.write_table_atomically(pd.DataFrame({"a": [9]}), path)
    monkeypatch.undo()
    assert pd.read_parquet(path)["a"].tolist() == [1, 2, 3]
    assert not list(tmp_path.glob("*.tmp"))


class TestCompleteness:
    def _monthly(self, months: int = 14, drop: tuple[str, str] | None = None) -> pd.DataFrame:
        rows = [
            {"airport": a, "month": m, "departures": 10.0, "passengers": 900.0, "seats": 1000.0}
            for m in pd.date_range("2025-01-01", periods=months, freq="MS")
            for a in ["BOS", "SFO", "ANC"] + [f"X{i:03d}" for i in range(800)]
        ]
        df = pd.DataFrame(rows)
        if drop:
            df = df[~((df["airport"] == drop[0]) & (df["month"] == pd.Timestamp(drop[1])))]
        return df

    def test_anchor_missing_latest_month_fails(self):
        with pytest.raises(validation.ValidationError, match="latest month"):
            validation.validate("t100_monthly", self._monthly(drop=("SFO", "2026-02-01")))

    def test_null_column_fails(self):
        df = self._monthly()
        df["passengers"] = None
        with pytest.raises(validation.ValidationError, match="null"):
            validation.validate("t100_monthly", df)

    def test_duplicate_keys_fail(self):
        df = self._monthly()
        with pytest.raises(validation.ValidationError, match="duplicate"):
            validation.validate("t100_monthly", pd.concat([df, df.head(1)]))

    def test_missing_month_suppresses_growth(self):
        from app.analytics.features import traffic_features

        months = pd.date_range("2024-09-01", "2026-08-01", freq="MS")
        t = pd.DataFrame(
            {
                "airport": "X",
                "month": months,
                "passengers": 100.0,
                "seats": 120.0,
                "departures": 1.0,
                "dom_departures": 1.0,
                "freight_lbs": 0.0,
                "intl_departures": 0.0,
                "intl_passengers": 0.0,
            }
        )
        t = pd.concat([t, t.assign(airport="Y")])
        t = t[~((t["airport"] == "X") & (t["month"] == pd.Timestamp("2026-08-01")))]
        f, _ = traffic_features(t)
        assert np.isnan(f.at["X", "passenger_growth_ttm"]) and f.at["Y", "passenger_growth_ttm"] == 0


def test_live_status_marks_faa_outage_as_unavailable(service):
    from app.agent.tools.definitions import AirportArgs, live_handler
    from app.data.connectors.live import LiveStatus

    class HalfDown:
        def airport_status(self, code, icao):
            return LiveStatus(
                airport=code,
                available=True,
                nas=None,
                weather={"flight_category": "VFR"},
                errors=["FAA NAS Status unavailable: timeout"],
            )

    service.live = HalfDown()
    try:
        result = live_handler(AirportArgs(airport="SFO"), service)
    finally:
        service.live = None
    kpis = {k.label: k.value for k in result.ui[0].items}
    assert kpis["FAA programs"].startswith("unavailable") and result.confidence.value == "medium"
    assert result.data["faa_nas"] == "unavailable" and "errors" not in result.data


def test_long_haul_threshold_below_short_haul_is_a_fixable_error(service):
    from app.services.insights import AnalysisError

    for bad in (500, 700):
        with pytest.raises(AnalysisError, match="must be above"):
            service.route_mix("ANC", bad)
