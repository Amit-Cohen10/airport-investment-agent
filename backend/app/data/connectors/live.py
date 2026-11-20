"""Live (real-time) public APIs. These are queried on demand, never stored in the snapshot.

  * FAA NAS Status `airport-events`: ground stops, ground delay programs, delays, and the
    current runway configuration / arrival rate (a direct read of airfield capacity).
  * aviationweather.gov METAR: current weather and flight category (VFR / IFR ...).

Both are best-effort: on failure we return `available=False` with the reason, and the
agent says the live view is unavailable instead of failing the whole answer.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger(__name__)

NAS_URL = "https://nasstatus.faa.gov/api/airport-events"
NAS_SOURCE = "FAA NAS Status (live)"
METAR_URL = "https://aviationweather.gov/api/data/metar"
METAR_SOURCE = "aviationweather.gov METAR (live)"

# Routine NOTAMs that look like "closures" but are not congestion signals.
ROUTINE_NOTAM_MARKERS = ("NON SKED TRANSIENT GA", "CLSD DLY", "PPR")


@dataclass
class LiveStatus:
    airport: str
    available: bool
    fetched_at: float = field(default_factory=time.time)
    nas: dict[str, Any] | None = None
    weather: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)


class LiveAviationClient:
    """Small TTL cache in front of the live APIs so repeated questions don't re-hit them."""

    def __init__(self, timeout_s: float = 10.0, user_agent: str = "airport-agent", ttl_s: int = 120):
        self._client = httpx.Client(timeout=timeout_s, headers={"User-Agent": user_agent})
        self._ttl_s = ttl_s
        self._nas_cache: tuple[float, list[dict[str, Any]]] | None = None

    def _nas_events(self) -> list[dict[str, Any]]:
        if self._nas_cache and time.time() - self._nas_cache[0] < self._ttl_s:
            return self._nas_cache[1]
        response = self._client.get(NAS_URL)
        response.raise_for_status()
        events = response.json()
        self._nas_cache = (time.time(), events)
        return events

    def airport_status(self, code: str, icao: str | None) -> LiveStatus:
        status = LiveStatus(airport=code, available=False)
        try:
            events = self._nas_events()
            event = next((e for e in events if e.get("airportId") == code), None)
            status.nas = summarize_nas_event(event)
        except (httpx.HTTPError, ValueError) as exc:
            status.errors.append(f"FAA NAS Status unavailable: {exc}")
        if icao:
            try:
                response = self._client.get(METAR_URL, params={"ids": icao, "format": "json"})
                response.raise_for_status()
                metars = response.json()
                status.weather = summarize_metar(metars[0]) if metars else None
            except (httpx.HTTPError, ValueError, IndexError) as exc:
                status.errors.append(f"METAR unavailable: {exc}")
        status.available = status.nas is not None or status.weather is not None
        return status


def summarize_nas_event(event: dict[str, Any] | None) -> dict[str, Any]:
    """Reduce a NAS airport event to the congestion-relevant facts."""
    if event is None:
        return {"active_programs": [], "note": "No active FAA delay programs or events listed."}
    programs = []
    for key, label in (
        ("groundStop", "Ground stop"),
        ("groundDelay", "Ground delay program"),
        ("arrivalDelay", "Arrival delays"),
        ("departureDelay", "Departure delays"),
        ("deicing", "De-icing"),
    ):
        if event.get(key):
            programs.append({"type": label, "detail": _compact(event[key])})
    closure = event.get("airportClosure")
    if closure and not any(m in (closure.get("simpleText") or "") for m in ROUTINE_NOTAM_MARKERS):
        programs.append({"type": "Airport closure", "detail": closure.get("simpleText")})
    config = event.get("airportConfig") or {}
    return {
        "active_programs": programs,
        "arrival_rate_per_hour": config.get("arrivalRate"),
        "arrival_runways": config.get("arrivalRunwayConfig"),
        "departure_runways": config.get("departureRunwayConfig"),
        "routine_notams_ignored": bool(closure) and not any(p["type"] == "Airport closure" for p in programs),
    }


def summarize_metar(metar: dict[str, Any]) -> dict[str, Any]:
    clouds = metar.get("clouds") or []
    ceiling = next((c.get("base") for c in clouds if c.get("cover") in ("BKN", "OVC", "OVX")), None)
    return {
        "flight_category": metar.get("fltCat"),
        "visibility_sm": metar.get("visib"),
        "ceiling_ft": ceiling,
        "cloud_layers": [{"cover": c.get("cover"), "base_ft": c.get("base")} for c in clouds],
        "wind_kt": metar.get("wspd"),
        "observed": metar.get("reportTime"),
        "raw": metar.get("rawOb"),
    }


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        keep = ("reason", "avgDelay", "maxDelay", "startTime", "endTime", "trend", "simpleText")
        return {k: v for k, v in value.items() if k in keep and v} or value
    return value
