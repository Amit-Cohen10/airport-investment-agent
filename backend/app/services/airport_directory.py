"""Deterministic entity resolution: free text ("LA", "Santa Ana", "New England") -> airport codes.

The LLM passes the user's words; this code decides what they mean, so "LA" always resolves the
same way and ambiguity is reported explicitly (e.g. "LA" -> LAX, but BUR/LGB/ONT/SNA are also
in the metro) instead of being guessed silently.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field

import pandas as pd

STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york state": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington state": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "puerto rico": "PR",
}

REGIONS: dict[str, list[str]] = {
    "new england": ["CT", "ME", "MA", "NH", "RI", "VT"],
    "mid-atlantic": ["NY", "NJ", "PA", "DE", "MD", "DC"],
    "northeast": ["CT", "ME", "MA", "NH", "RI", "VT", "NY", "NJ", "PA"],
    "southeast": ["VA", "WV", "NC", "SC", "GA", "FL", "AL", "MS", "TN", "KY"],
    "south central": ["AR", "LA", "OK", "TX"],
    "midwest": ["OH", "IN", "IL", "MI", "WI", "MN", "IA", "MO", "ND", "SD", "NE", "KS"],
    "mountain west": ["MT", "ID", "WY", "CO", "UT", "NV", "AZ", "NM"],
    "southwest": ["AZ", "NM", "NV", "TX"],
    "west coast": ["CA", "OR", "WA"],
    "pacific northwest": ["OR", "WA"],
    "alaska": ["AK"],
    "hawaii": ["HI"],
}
REGION_ALIASES = {
    "pacific": "west coast",
    "west": "west coast",
    "mountain": "mountain west",
    "rocky mountains": "mountain west",
    "south": "southeast",
    "pnw": "pacific northwest",
}

# Colloquial names -> primary airport(s). Metro alternatives are added automatically by distance.
ALIASES: dict[str, list[str]] = {
    "LA": ["LAX"],
    "L.A.": ["LAX"],
    "LOS ANGELES": ["LAX"],
    "SANTA ANA": ["SNA"],
    "ORANGE COUNTY": ["SNA"],
    "JOHN WAYNE": ["SNA"],
    "SF": ["SFO"],
    "SAN FRANCISCO": ["SFO"],
    "BAY AREA": ["SFO", "OAK", "SJC"],
    "NYC": ["JFK", "LGA", "EWR"],
    "NEW YORK": ["JFK", "LGA", "EWR"],
    "DC": ["DCA", "IAD", "BWI"],
    "WASHINGTON": ["DCA", "IAD", "BWI"],
    "CHICAGO": ["ORD", "MDW"],
    "LOGAN": ["BOS"],
    "ANCHORAGE": ["ANC"],
    "HOUSTON": ["IAH", "HOU"],
    "DALLAS": ["DFW", "DAL"],
    "MIAMI": ["MIA", "FLL"],
}
METRO_RADIUS_MI = 60
CITY_STOPWORDS = {"MOBILE"}  # city names that are also common English words


@dataclass
class Resolution:
    query: str
    codes: list[str]
    kind: str  # code | alias | city | name | fuzzy | region | state | none
    alternatives: list[str] = field(default_factory=list)
    note: str | None = None


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class AirportDirectory:
    """Commercial-service airports (those in the FAA enplanement data) with reference data."""

    def __init__(self, features: pd.DataFrame):
        df = features[features["enplanements_latest"].notna()].copy()
        df["city_u"] = df["city"].fillna("").str.upper()
        df["name_u"] = df["name"].fillna("").str.upper()
        self.df = df.sort_values("enplanements_latest", ascending=False)
        self.icao = {str(v).upper(): k for k, v in df["icao"].dropna().items()}

    # --- regions ------------------------------------------------------------
    def region_states(self, text: str) -> tuple[str, list[str]] | None:
        key = text.strip().lower()
        key = REGION_ALIASES.get(key, key)
        if key in REGIONS:
            return key, REGIONS[key]
        if key in STATE_NAMES:
            return key, [STATE_NAMES[key]]
        if key.upper() in set(STATE_NAMES.values()) and len(key) == 2:
            return key.upper(), [key.upper()]
        return None

    def airports_in_states(self, states: list[str], min_enplanements: float = 0) -> list[str]:
        sel = self.df[self.df["state"].isin(states) & (self.df["enplanements_latest"] >= min_enplanements)]
        return list(sel.index)

    # --- airports -----------------------------------------------------------
    def resolve(self, text: str) -> Resolution:
        q = text.strip().upper()
        if not q:
            return Resolution(text, [], "none", note="empty query")
        if q in self.df.index:
            return self._with_metro(Resolution(text, [q], "code"))
        if q in self.icao:
            return self._with_metro(Resolution(text, [self.icao[q]], "code"))
        if q in ALIASES:
            codes = [c for c in ALIASES[q] if c in self.df.index]
            return self._with_metro(Resolution(text, codes, "alias"))
        region = self.region_states(text)
        if region:
            name, states = region
            return Resolution(
                text, self.airports_in_states(states), "region", note=f"{name}: states {', '.join(states)}"
            )
        city = self.df[self.df["city_u"] == q]
        if len(city):
            return self._with_metro(Resolution(text, [city.index[0]], "city", alternatives=list(city.index[1:])))
        named = self.df[self.df["name_u"].str.contains(q, regex=False) | self.df["city_u"].str.contains(q, regex=False)]
        if len(named):
            return self._with_metro(Resolution(text, [named.index[0]], "name", alternatives=list(named.index[1:5])))
        candidates = list(self.df["city_u"]) + list(self.df["name_u"])
        close = difflib.get_close_matches(q, candidates, n=1, cutoff=0.75)
        if close:
            hit = self.df[(self.df["city_u"] == close[0]) | (self.df["name_u"] == close[0])].index[0]
            return self._with_metro(Resolution(text, [hit], "fuzzy", note=f"closest match: {close[0].title()}"))
        return Resolution(text, [], "none", note="no commercial-service airport matched")

    def _with_metro(self, res: Resolution) -> Resolution:
        if len(res.codes) != 1:
            return res
        primary = self.df.loc[res.codes[0]]
        if pd.isna(primary["latitude"]):
            return res
        nearby = []
        for code, row in self.df.iterrows():
            if code == res.codes[0] or pd.isna(row["latitude"]) or row["enplanements_latest"] < 100_000:
                continue
            if (
                haversine_mi(primary["latitude"], primary["longitude"], row["latitude"], row["longitude"])
                <= METRO_RADIUS_MI
            ):
                nearby.append(code)
        res.alternatives = list(dict.fromkeys(res.alternatives + nearby))
        return res

    def find_in_text(self, text: str) -> tuple[list[str], str | None]:
        """Airports and region mentioned anywhere in free text (used by the offline planner)."""
        lower = text.lower()
        region = None
        names = sorted(list(REGIONS) + list(REGION_ALIASES) + list(STATE_NAMES), key=len, reverse=True)
        for name in names:
            if re.search(rf"\b{re.escape(name)}\b", lower):
                region = name
                break
        hits: list[tuple[int, str]] = []
        for alias, codes in ALIASES.items():
            flags = 0 if len(alias) <= 3 else re.IGNORECASE  # "LA" only as a capitalized token
            for m in re.finditer(rf"(?<![\w.]){re.escape(alias)}(?![\w])", text, flags):
                hits += [(m.start(), c) for c in codes[:1]]
        for m in re.finditer(r"\b[A-Z]{3}\b", text):
            if m.group(0) in self.df.index:
                hits.append((m.start(), m.group(0)))
        peers = self.df[self.df["enplanements_latest"] >= 100_000]
        for code, city in peers["city_u"].items():
            if city and len(city) > 3 and city not in CITY_STOPWORDS:
                m = re.search(rf"\b{re.escape(city.lower())}\b", lower)
                if m:
                    hits.append((m.start(), code))
        codes = list(dict.fromkeys(c for _, c in sorted(hits)))
        if region and region in ("washington state", "new york state"):
            region = None if codes else region
        return codes, region

    def describe(self, code: str) -> dict:
        row = self.df.loc[code]
        return {
            "code": code,
            "name": row["name"],
            "city": row["city"],
            "state": row["state"],
            "hub_class": row["hub_class"],
            "enplanements_latest": None if pd.isna(row["enplanements_latest"]) else int(row["enplanements_latest"]),
        }
