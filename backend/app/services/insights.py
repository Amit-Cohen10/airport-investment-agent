"""InsightService: the business logic behind every agent tool.

It wires repository data + deterministic analytics + curated notes into analysis results.
Each method returns a `ToolResult` with data, provenance, assumptions, and caveats.
Agent tools are thin adapters on top: they validate LLM arguments and add UI blocks.
This layer is also used directly by the REST API (e.g. /api/rankings) without any LLM.
"""

from __future__ import annotations

import math
from functools import cached_property
from typing import Any

import pandas as pd
import yaml

from app.analytics import congestion, route_mix, unmet_demand
from app.analytics.expansion_score import (
    ScoreConfig,
    ScoredUniverse,
    explain_airport,
    score_universe,
)
from app.analytics.sensitivity import rank_stability
from app.data.connectors.live import METAR_SOURCE, NAS_SOURCE, LiveAviationClient
from app.data.repository import Repository
from app.domain.models import Confidence, Provenance, ToolResult
from app.services.airport_directory import AirportDirectory
from app.services.knowledge import AirportNotes


class AnalysisError(ValueError):
    """User-facing problem with a request (unknown airport, no data...). Sent back to the LLM."""


def _clean(value: Any, digits: int = 4) -> Any:
    """Make numbers JSON/LLM friendly: round floats, NaN -> None."""
    if value is pd.NA or value is pd.NaT:
        return None
    if hasattr(value, "item") and not isinstance(value, (list, dict, str)):  # numpy scalar
        value = value.item()
    if isinstance(value, float):
        return None if math.isnan(value) else round(float(value), digits)
    if isinstance(value, dict):
        return {k: _clean(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v, digits) for v in value]
    return value


class InsightService:
    def __init__(
        self,
        repo: Repository,
        scoring_config: dict[str, Any],
        notes: AirportNotes,
        live: LiveAviationClient | None = None,
    ):
        self.repo = repo
        self.cfg = scoring_config
        self.score_cfg = ScoreConfig.from_dict(scoring_config)
        self.notes = notes
        self.live = live

    @classmethod
    def from_paths(cls, repo: Repository, scoring_path, notes_path, live=None) -> InsightService:
        return cls(repo, yaml.safe_load(scoring_path.read_text()), AirportNotes(notes_path), live)

    # --- shared state -------------------------------------------------------
    @property
    def features(self) -> pd.DataFrame:
        return self.repo.features.table

    @property
    def periods(self) -> dict[str, str]:
        return self.repo.features.periods

    @cached_property
    def directory(self) -> AirportDirectory:
        return AirportDirectory(self.features)

    @cached_property
    def default_scores(self) -> ScoredUniverse:
        return score_universe(self.features, self.score_cfg)

    @cached_property
    def congestion_scores(self) -> pd.DataFrame:
        return congestion.congestion_universe(
            self.features, self.cfg["congestion_index"], self.score_cfg.min_enplanements
        )

    def _score_with(self, weight_overrides: dict[str, float]) -> ScoredUniverse:
        try:
            return score_universe(self.features, self.score_cfg, weight_overrides)
        except ValueError as exc:  # e.g. all weights zero, unknown pillar -> fixable by the model
            raise AnalysisError(f"Invalid weights: {exc}") from exc

    def _prov(self, table: str, period: str | None = None, note: str | None = None) -> Provenance:
        meta = self.repo.source(table)
        return Provenance(
            source=meta.get("source", table), period=period or meta.get("period", "?"), url=meta.get("url"), note=note
        )

    def _require(self, code: str) -> str:
        code = code.strip().upper()
        if code not in self.directory.df.index:
            res = self.directory.resolve(code)
            # Only unambiguous identifiers are auto-accepted (ICAO, alias); never a fuzzy/substring guess.
            if len(res.codes) == 1 and res.kind in ("code", "alias"):
                return res.codes[0]
            raise AnalysisError(
                f"'{code}' is not a US commercial-service airport in the data. Call resolve_airports first."
            )
        return code

    def _notes_payload(self, code: str) -> list[dict[str, Any]]:
        return [
            {"kind": n["kind"], "topic": n["topic"], "text": " ".join(n["text"].split()), "source": n["source"]}
            for n in self.notes.for_airport(code)
        ]

    def _note_provenance(self, code: str) -> list[Provenance]:
        return [
            Provenance(
                source=f"Curated context: {n['source']}", period="see note", url=n.get("source_url"), note=n["topic"]
            )
            for n in self.notes.for_airport(code)
        ]

    # --- 1. resolution ------------------------------------------------------
    def resolve(self, queries: list[str]) -> ToolResult:
        results = []
        for q in queries:
            res = self.directory.resolve(q)
            results.append(
                {
                    "query": q,
                    "match_type": res.kind,
                    "airports": [self.directory.describe(c) for c in res.codes[:25]],
                    "airport_count": len(res.codes),
                    "nearby_alternatives": res.alternatives[:6],
                    "note": res.note,
                }
            )
        unresolved = [r["query"] for r in results if not r["airports"]]
        summary = "; ".join(
            f"{r['query']} -> {', '.join(a['code'] for a in r['airports'][:6]) or 'no match'}" for r in results
        )
        return ToolResult(
            tool="resolve_airports",
            ok=not unresolved,
            summary=summary,
            data=_clean({"results": results, "unresolved": unresolved}),
            provenance=[self._prov("enplanements"), self._prov("airports")],
        )

    # --- 2. expansion ranking -----------------------------------------------
    def rank_expansion(
        self, codes: list[str], scope_label: str, weight_overrides: dict[str, float] | None = None, top_n: int = 10
    ) -> ToolResult:
        scored = self._score_with(weight_overrides) if weight_overrides else self.default_scores
        table = scored.table
        in_scope = [c for c in dict.fromkeys(c.upper() for c in codes) if c in table.index]
        excluded = [c for c in codes if c.upper() not in table.index]
        if not in_scope:
            raise AnalysisError(
                f"None of {codes} meet the peer threshold of "
                f"{self.score_cfg.min_enplanements:,.0f} annual enplanements."
            )
        scope = table.loc[in_scope].sort_values("score", ascending=False)
        pillars = list(self.score_cfg.pillars)
        pillar_frame = scope[[f"pillar__{p}" for p in pillars]].set_axis(pillars, axis=1)
        sens_cfg = self.cfg["expansion_score"]["sensitivity"]
        stability = (
            rank_stability(
                pillar_frame,
                scored.pillar_weights,
                samples=sens_cfg["samples"],
                concentration=sens_cfg["concentration"],
                seed=sens_cfg["seed"],
            )
            if len(scope) > 1
            else {}
        )

        ranking = []
        for i, (code, row) in enumerate(scope.head(top_n).iterrows(), start=1):
            f = self.features.loc[code]
            ranking.append(
                {
                    "rank": i,
                    "code": code,
                    "name": f["name"],
                    "state": f["state"],
                    "score": row["score"],
                    "national_rank": int(row["national_rank"]),
                    "confidence": row["confidence"],
                    "pillars": {p: row[f"pillar__{p}"] for p in pillars},
                    "key_metrics": {
                        "enplanements_cy_latest": f["enplanements_latest"],
                        "enplanement_cagr_3y": f["enplanement_cagr_3y"],
                        "passenger_growth_ttm": f["passenger_growth_ttm"],
                        "load_factor_ttm": f["load_factor_ttm"],
                        "taxi_out_avg_min": f["taxi_out_avg_min"],
                        "fare_premium": f["fare_premium"],
                    },
                    "rank_stability": stability.get(code),
                    "constraint_flags": self.notes.constraint_flags(code),
                    "bottleneck_check": self._bottleneck_check(code),
                }
            )
        return ToolResult(
            tool="rank_expansion_candidates",
            summary=f"Ranked {len(scope)} airports in {scope_label}; top: "
            + ", ".join(f"{r['code']} ({r['score']:.0f})" for r in ranking[:3]),
            data=_clean(
                {
                    "scope": scope_label,
                    "pillar_weights": scored.pillar_weights,
                    "weights_overridden": bool(weight_overrides),
                    "national_peer_count": int(len(table)),
                    "ranking": ranking,
                    "excluded_below_threshold_or_no_data": excluded,
                },
                3,
            ),
            provenance=self._core_provenance(),
            assumptions=[
                "Score = weighted mean of 4 pillar scores; each metric is a percentile vs "
                f"{len(table)} US airports with >= {self.score_cfg.min_enplanements:,.0f} annual enplanements.",
                "The score is a screening priority for further diligence, not an estimate of investment returns: it proxies "
                "terminal pressure with demand growth, utilization, strain and revenue base.",
                "Gate counts and terminal capacity are not integrated (no standardized national API; airport master "
                "plans contain gate analyses and are the next diligence step), so terminal utilization is not measured directly.",
                "Bottleneck check: flags when the binding constraint is likely airside (runways/airspace) or regulatory, "
                "where terminal expansion alone may not unlock growth.",
                "Rank stability: 1,000 random re-weightings of the pillars (Dirichlet around the default weights).",
            ],
            caveats=[
                "CY2025 FAA enplanements are preliminary.",
                "On-time metrics cover reporting carriers' domestic flights only.",
                "Constraint flags (caps, curfews, land) come from curated notes and are NOT part of the score.",
            ],
            confidence=_min_confidence([r["confidence"] for r in ranking]),
        )

    def explain_score(self, code: str, weight_overrides: dict[str, float] | None = None) -> ToolResult:
        code = self._require(code)
        scored = self._score_with(weight_overrides) if weight_overrides else self.default_scores
        if code not in scored.table.index:
            raise AnalysisError(f"{code} is below the peer threshold or lacks data for scoring.")
        breakdown = explain_airport(scored, self.features, code, self.score_cfg)
        return ToolResult(
            tool="explain_airport_score",
            summary=f"{code}: score {breakdown['score']} (#{breakdown['national_rank']} of {breakdown['peer_count']})",
            data=_clean({**breakdown, "notes": self._notes_payload(code)}, 4),
            provenance=self._core_provenance() + self._note_provenance(code),
            assumptions=["Percentile = share of national peers with a lower value (higher = stronger signal)."],
            confidence=Confidence(breakdown["confidence"]),
        )

    # --- 3. profile ---------------------------------------------------------
    def airport_profile(self, code: str) -> ToolResult:
        code = self._require(code)
        f = self.features.loc[code]
        scored = self.default_scores.table
        cong = self.congestion_scores
        data = {
            "airport": self.directory.describe(code),
            "runways": {"count": f["runways"], "counted_if_paved_and_at_least_ft": 4000},
            "enplanements": {
                "latest_cy": f["enplanements_latest"],
                "prev_cy": f["enplanements_prev"],
                "cy2019": f["enplanements_2019"],
                "yoy": f["enplanement_yoy"],
                "cagr_3y": f["enplanement_cagr_3y"],
                "vs_2019": f["recovery_vs_2019"],
            },
            "traffic_ttm": {
                "departures": f["departures_ttm"],
                "passengers": f["passengers_ttm"],
                "seats": f["seats_ttm"],
                "load_factor": f["load_factor_ttm"],
                "passenger_growth": f["passenger_growth_ttm"],
                "seat_growth": f["seat_growth_ttm"],
                "international_departure_share": f["intl_departure_share"],
                "freight_lbs": f["freight_lbs_ttm"],
            },
            "operations_ttm": {
                "taxi_out_avg_min": f["taxi_out_avg_min"],
                "dep_delay_rate": f["dep_delay_rate"],
                "arr_delay_rate": f["arr_delay_rate"],
                "cancellation_rate": f["cancellation_rate"],
                "nas_delay_per_arrival_min": f["nas_delay_per_arrival_min"],
                "peak_hour_movements": f["peak_hour_movements"],
            },
            "market": {
                "fare_premium": f["fare_premium"],
                "dominant_carrier_share": f["dominant_carrier_share"],
                "domestic_od_pax_per_day": f["od_pax_per_day"],
            },
            "expansion_score": None
            if code not in scored.index
            else {
                "score": scored.at[code, "score"],
                "national_rank": int(scored.at[code, "national_rank"]),
                "peer_count": int(scored.at[code, "peer_count"]),
                "confidence": scored.at[code, "confidence"],
            },
            "congestion_index": None
            if code not in cong.index
            else {"index": cong.at[code, "congestion_index"], "national_rank": cong.at[code, "congestion_rank"]},
            "notes": self._notes_payload(code),
        }
        return ToolResult(
            tool="get_airport_profile",
            summary=f"Profile for {code} ({f['name']})",
            data=_clean(data),
            provenance=self._core_provenance() + self._note_provenance(code),
            caveats=["TTM = trailing 12 months; periods differ by source (see sources)."],
        )

    # --- 4. congestion ------------------------------------------------------
    def compare_congestion(self, codes: list[str]) -> ToolResult:
        codes = [self._require(c) for c in codes]
        cfg = self.cfg["congestion_index"]
        scores = self.congestion_scores
        no_data = [c for c in codes if c not in scores.index or pd.isna(scores.at[c, "congestion_index"])]
        codes = [c for c in codes if c not in no_data]
        if not codes:
            raise AnalysisError(
                f"No on-time (delay/taxi) data for {', '.join(no_data)}: its airlines are not BTS reporting "
                "carriers or traffic is too small, so congestion cannot be measured."
            )
        airports = []
        for code in codes:
            entry = congestion.explain(self.congestion_scores, self.features, code, cfg)
            f = self.features.loc[code]
            cap = self.notes.structured(code, "annual_passenger_cap")
            total_pax_est = f["enplanements_latest"] * 2  # enplaned + deplaned ~ 2x enplanements
            entry.update(
                {
                    "runways": f["runways"],
                    "active_schedule_hours_per_day": f["active_hours_per_day"],
                    "regulatory_cap": None
                    if cap is None
                    else {
                        "annual_passenger_cap": cap,
                        "estimated_total_passengers_latest_cy": total_pax_est,
                        "cap_utilization": total_pax_est / cap,
                    },
                    "notes": self._notes_payload(code),
                }
            )
            airports.append(entry)
        ordered = sorted(airports, key=lambda a: a["congestion_index"] or 0, reverse=True)
        return ToolResult(
            tool="compare_congestion",
            summary="Congestion index: " + ", ".join(f"{a['airport']} {a['congestion_index']}" for a in ordered),
            data=_clean({"airports": ordered, "airports_without_ontime_data": no_data}),
            provenance=[
                self._prov("ontime_monthly", self.periods["ontime"]),
                self._prov("airports"),
                self._prov("enplanements"),
            ]
            + [p for c in codes for p in self._note_provenance(c)],
            assumptions=[
                "Congestion Index = weighted percentile of taxi-out, peak-hour movements per runway, delay rates, "
                "NAS delay per arrival and cancellations, vs national peers (100 = most congested).",
                "Peak-hour movements = average over days of the busiest scheduled clock hour (deps + arrs).",
                "Total passengers estimated as 2x enplanements when comparing against a passenger cap.",
            ],
            caveats=[
                "On-time data covers reporting carriers' domestic flights only (no international/cargo); "
                "'ontime_sample_coverage' is their share of the airport's domestic departures. Peak-hour "
                "movements count those flights only, so gateways with much foreign/cargo traffic read low.",
            ],
            confidence=_min_confidence([a["confidence"] for a in ordered]),
        )

    # --- 5. route mix -------------------------------------------------------
    def route_mix(self, code: str, long_haul_min_mi: float | None = None) -> ToolResult:
        code = self._require(code)
        cfg = self.cfg["route_mix"]
        long_min = long_haul_min_mi or cfg["long_haul_min_mi"]
        if long_min <= cfg["short_haul_max_mi"]:
            raise AnalysisError(
                f"The long-haul threshold ({long_min:,.0f} mi) must be above the short-haul boundary "
                f"({cfg['short_haul_max_mi']} mi). Use e.g. 2000, 3000 or 4000."
            )
        thresholds = sorted(set(cfg["alternative_thresholds_mi"]) | {long_min})
        window = self.repo.segment_window
        segments = self.repo.segments_from(code, window)
        if segments.empty:
            raise AnalysisError(f"No T-100 segment data for departures from {code}.")
        result = route_mix.analyze(
            segments, short_max_mi=cfg["short_haul_max_mi"], long_min_mi=long_min, alternative_thresholds=thresholds
        )
        return ToolResult(
            tool="analyze_route_mix",
            summary=(
                f"{code}: {result['all_flights']['long_share']:.1%} of all departures and "
                f"{result['passenger_flights'].get('long_share', 0):.1%} of passenger departures are long-haul "
                f"(>= {long_min:,.0f} mi)"
            ),
            data=_clean({"airport": code, "period": window.label, **result, "notes": self._notes_payload(code)}),
            provenance=[self._prov("t100_segments", window.label)] + self._note_provenance(code),
            assumptions=[
                f"Long-haul = great-circle segment distance >= {long_min:,.0f} statute miles; short-haul < "
                f"{cfg['short_haul_max_mi']} mi (no single official definition; alternatives shown).",
                "Share is by departures performed (flights), all carriers incl. foreign and cargo airlines.",
                "Passenger flights = passenger/combi aircraft configurations; cargo = freighter configuration.",
            ],
            confidence=Confidence.HIGH,
        )

    # --- 6. unmet demand ----------------------------------------------------
    def unmet_demand(self, code: str) -> ToolResult:
        code = self._require(code)
        ucfg = self.cfg["unmet_demand"]
        window = self.repo.segment_window
        seg = self.repo.segments_from(code, window)
        sched_pax = seg[seg["aircraft_config"].isin(route_mix.PASSENGER_CONFIGS) & (seg["service_class"] == "F")]
        if sched_pax.empty:
            raise AnalysisError(f"No scheduled passenger segment data for {code}.")
        spill = unmet_demand.estimate(
            sched_pax,
            cv=ucfg["demand_cv"],
            cv_sensitivity=ucfg["cv_sensitivity"],
            min_lf=ucfg["min_load_factor_for_spill"],
            max_lf=ucfg["max_load_factor"],
            target_lf=ucfg["target_load_factor"],
            constrained_lf=ucfg["constrained_route_lf"],
        )
        f = self.features.loc[code]
        cong = self.congestion_scores
        drivers = {
            "load_factor_ttm": f["load_factor_ttm"],
            "demand_vs_supply_growth": unmet_demand.growth_signal(f["passenger_growth_ttm"], f["seat_growth_ttm"]),
            "passenger_growth_ttm": f["passenger_growth_ttm"],
            "seat_growth_ttm": f["seat_growth_ttm"],
            "fare_premium_vs_us_norm": f["fare_premium"],
            "delay_cause_shares": {
                k.removeprefix("share_"): f[k]
                for k in ("share_nas", "share_weather", "share_carrier", "share_late_aircraft")
            },
            "arr_delay_rate": f["arr_delay_rate"],
            "nas_delay_per_arrival_min": f["nas_delay_per_arrival_min"],
            "congestion_index": None if code not in cong.index else cong.at[code, "congestion_index"],
            "congestion_national_rank": None if code not in cong.index else cong.at[code, "congestion_rank"],
            "enplanements_vs_2019": f["recovery_vs_2019"],
        }
        counter = []
        if not pd.isna(f["recovery_vs_2019"]) and f["recovery_vs_2019"] < 1:
            counter.append(
                f"Enplanements are still {1 - f['recovery_vs_2019']:.0%} below 2019: part of the gap "
                "is weaker demand (e.g. business travel), not only constrained capacity."
            )
        if (
            not pd.isna(f["seat_growth_ttm"])
            and not pd.isna(f["passenger_growth_ttm"])
            and f["seat_growth_ttm"] > f["passenger_growth_ttm"]
        ):
            counter.append("Seats are growing faster than passengers, so airlines are not visibly rationing capacity.")
        return ToolResult(
            tool="estimate_unmet_demand",
            summary=(
                f"{code}: ~{spill['spilled_passengers_annualized']:,} spilled passengers/yr "
                f"({(spill['spill_rate'] or 0):.1%} of carried), ~{spill['equivalent_extra_departures_per_day']} "
                "extra daily departures equivalent"
            ),
            data=_clean(
                {
                    "airport": code,
                    "period": window.label,
                    "spill_model": spill,
                    "drivers": drivers,
                    "counter_signals": counter,
                    "notes": self._notes_payload(code),
                }
            ),
            provenance=[
                self._prov("t100_segments", window.label),
                self._prov("t100_monthly", self.periods["traffic"]),
                self._prov("ontime_monthly", self.periods["ontime"]),
                self._prov("airfare_markets", self.periods["fares"]),
                self._prov("enplanements"),
            ]
            + self._note_provenance(code),
            assumptions=[
                f"Spill model per route-month (all carriers combined): demand ~ Normal(mu, {ucfg['demand_cv']}*mu); "
                "unconstrained demand recovered from observed load factor. cv sensitivity reported.",
                f"Routes below {ucfg['min_load_factor_for_spill']:.0%} load factor are assumed to have no spill.",
                f"Extra departures = spilled passengers / (avg seats per departure x {ucfg['target_load_factor']:.0%} LF).",
                "Scheduled passenger service only (T-100 class F, passenger aircraft); departing segments only.",
            ],
            caveats=[
                "Spill is a model estimate of demand that is unobservable by definition; treat it as an order of magnitude.",
                "Each route-month is treated as identical departures at the average load factor; day-of-week and "
                "peak-hour concentration is ignored, so spill on peak flights is understated and off-peak overstated.",
                "BTS 'NAS' delay includes non-extreme weather (e.g. low ceilings that cut arrival rates), traffic volume "
                "and ATC; it is the best public proxy for airfield/airspace capacity shortfall.",
                "Curated notes explain physical/regulatory causes; they are cited context, not computed data.",
            ],
            confidence=Confidence.MEDIUM,
        )

    # --- 7. live ------------------------------------------------------------
    def live_status(self, code: str) -> ToolResult:
        code = self._require(code)
        if self.live is None:
            raise AnalysisError("Live status client is not configured.")
        icao = self.features.at[code, "icao"]
        status = self.live.airport_status(code, None if pd.isna(icao) else str(icao))
        now = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")
        # Each source reports its own availability: an FAA outage must read "unavailable",
        # never "no active programs", and confidence reflects how many sources answered.
        sources_ok = {"faa_nas": status.nas is not None, "weather": status.weather is not None}
        answered = sum(sources_ok.values())
        caveats = ["A single real-time snapshot: illustrates current conditions, not long-run performance."]
        if not sources_ok["faa_nas"]:
            caveats.append("FAA NAS status was unavailable: delay programs and arrival rate are unknown, not zero.")
        if not sources_ok["weather"]:
            caveats.append("METAR weather was unavailable.")
        return ToolResult(
            tool="get_live_status",
            ok=answered > 0,
            summary=f"{code} live: " + ", ".join(f"{k} {'ok' if v else 'unavailable'}" for k, v in sources_ok.items()),
            data=_clean(
                {
                    "airport": code,
                    "as_of": now,
                    "sources_available": sources_ok,
                    "faa_nas": status.nas if sources_ok["faa_nas"] else "unavailable",
                    "weather": status.weather if sources_ok["weather"] else "unavailable",
                }
            ),
            provenance=[
                Provenance(source=NAS_SOURCE, period=now, url="https://nasstatus.faa.gov/"),
                Provenance(source=METAR_SOURCE, period=now, url="https://aviationweather.gov/"),
            ],
            caveats=caveats,
            confidence=[Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH][answered],
        )

    # --- helpers ------------------------------------------------------------
    def _bottleneck_check(self, code: str) -> dict[str, str]:
        """Heuristic: which constraint is most likely binding (a terminal investment only pays if it is the terminal).

        Deterministic and deliberately conservative: it only names airside/regulatory constraints the data or
        curated notes show; otherwise it says the terminal is plausible but unconfirmed without facility data.
        """
        topics = " ".join(self.notes.constraint_flags(code)).lower()
        if "cap" in topics or "curfew" in topics:
            return {
                "likely_binding": "regulatory",
                "why": "A legal passenger cap or curfew limits growth regardless of terminal size.",
            }
        cong = self.congestion_scores
        nas_pct = cong.at[code, "pct__nas_delay_per_arrival_min"] if code in cong.index else float("nan")
        if "runway" in topics or "arrival" in topics or (not pd.isna(nas_pct) and nas_pct >= 80):
            return {
                "likely_binding": "airside risk",
                "why": "Runway/airspace capacity (high NAS delay or a runway constraint) may bind before the "
                "terminal; terminal expansion alone may not unlock growth.",
            }
        return {
            "likely_binding": "unconfirmed",
            "why": "No airside or regulatory constraint detected; confirm terminal/gate utilization with the "
            "airport's master plan before investing.",
        }

    def _core_provenance(self) -> list[Provenance]:
        return [
            self._prov("enplanements"),
            self._prov("t100_monthly", self.periods["traffic"]),
            self._prov("ontime_monthly", self.periods["ontime"]),
            self._prov("airfare_markets", self.periods["fares"]),
        ]


def _min_confidence(values: list[str]) -> Confidence:
    order = [Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH]
    present = [Confidence(v) for v in values if v]
    return min(present, key=order.index) if present else Confidence.LOW
