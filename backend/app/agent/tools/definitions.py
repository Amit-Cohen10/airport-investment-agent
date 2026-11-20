"""The agent's tools: argument models (what the LLM may ask for) + handlers + UI evidence.

Each handler: validated args -> InsightService (deterministic analysis) -> ToolResult,
then attaches UI blocks (tables/charts) built from the computed numbers.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from app.agent.tools.formatting import compact, fmt_metric, pct, r1
from app.agent.tools.methodology import methodology_text
from app.agent.tools.registry import Tool
from app.domain.models import BarChartBlock, BarSeries, Kpi, KpiBlock, TableBlock, ToolResult
from app.services.insights import AnalysisError, InsightService

AirportCode = Annotated[str, Field(description="US airport IATA/FAA code, e.g. 'SFO'. Resolve names first.")]


def _code(v: str) -> str:
    v = v.strip().upper()
    if not (2 <= len(v) <= 4) or not v.isalnum():
        raise ValueError("expected an airport code like 'SFO'; call resolve_airports for names")
    return v


class WeightOverrides(BaseModel):
    demand_momentum_weight: float | None = Field(
        None, ge=0, le=10, description="Relative weight for demand growth pillar (default 0.30)"
    )
    capacity_utilization_weight: float | None = Field(
        None, ge=0, le=10, description="Relative weight for load-factor/utilization pillar (default 0.25)"
    )
    operational_strain_weight: float | None = Field(
        None, ge=0, le=10, description="Relative weight for congestion/strain pillar (default 0.20)"
    )
    revenue_potential_weight: float | None = Field(
        None, ge=0, le=10, description="Relative weight for scale/fare pillar (default 0.25)"
    )

    def overrides(self) -> dict[str, float] | None:
        o = {
            k.removesuffix("_weight"): v
            for k, v in self.model_dump().items()
            if k.endswith("_weight") and v is not None
        }
        return o or None


# --- 1. resolve -------------------------------------------------------------------------------
class ResolveArgs(BaseModel):
    queries: list[str] = Field(
        min_length=1,
        max_length=10,
        description="Place names, codes or regions as the user wrote them, e.g. ['LA', 'Santa Ana'] or ['New England']",
    )


def resolve_handler(args: ResolveArgs, svc: InsightService) -> ToolResult:
    result = svc.resolve(args.queries)
    rows = []
    for r in result.data["results"]:
        for a in r["airports"][:12]:
            rows.append([r["query"], a["code"], a["name"], a["state"], compact(a["enplanements_latest"])])
    result.ui = [
        TableBlock(title="Resolved airports", columns=["Query", "Code", "Airport", "State", "Enplanements"], rows=rows)
    ]
    return result


# --- 2. rank ----------------------------------------------------------------------------------
class RankArgs(WeightOverrides):
    region: str | None = Field(None, description="US region or state, e.g. 'New England', 'California', 'Midwest'")
    airports: list[str] = Field(default_factory=list, max_length=40, description="Explicit airport codes to rank")
    top_n: int = Field(10, ge=1, le=25)

    @field_validator("airports")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        return [_code(c) for c in v]


def rank_handler(args: RankArgs, svc: InsightService) -> ToolResult:
    if args.airports:
        codes, label = args.airports, ", ".join(args.airports)
    elif args.region:
        region = svc.directory.region_states(args.region)
        if not region:
            raise AnalysisError(
                f"Unknown region '{args.region}'. Use a US state, or e.g. New England, Midwest, West Coast."
            )
        codes, label = svc.directory.airports_in_states(region[1]), f"{args.region} ({', '.join(region[1])})"
    else:
        codes, label = list(svc.default_scores.table.index), "all US peer airports"
    result = svc.rank_expansion(codes, label, args.overrides(), args.top_n)
    ranking = result.data["ranking"]
    weights = result.data["pillar_weights"]
    pillars = list(weights)
    labels = {p: svc.score_cfg.pillars[p]["label"] for p in pillars}
    result.ui = [
        TableBlock(
            title=f"Terminal Expansion Opportunity Score - {label}",
            columns=["#", "Airport", "Score", "US rank", "P(#1)", "Confidence", "Constraint flags"],
            rows=[
                [
                    r["rank"],
                    f"{r['code']} - {r['name']}",
                    r1(r["score"]),
                    f"{r['national_rank']}/{result.data['national_peer_count']}",
                    pct((r["rank_stability"] or {}).get("p_rank_1"), 0) if r["rank_stability"] else "-",
                    r["confidence"],
                    ", ".join(r["constraint_flags"]) or "-",
                ]
                for r in ranking
            ],
            note="Score 0-100 = weighted percentile vs national peers. P(#1) = share of 1,000 weight scenarios "
            "in which the airport ranks first within this set.",
        ),
        BarChartBlock(
            title="Score composition (pillar score x weight)",
            categories=[r["code"] for r in ranking],
            series=[
                BarSeries(name=labels[p], values=[round((r["pillars"][p] or 0) * weights[p], 1) for r in ranking])
                for p in pillars
            ],
            stacked=True,
            unit="points",
        ),
        TableBlock(
            title="Key metrics",
            columns=["Airport", "Enplanements", "3y CAGR", "Pax growth TTM", "Load factor", "Taxi-out", "Fare premium"],
            rows=[
                [
                    r["code"],
                    compact(m["enplanements_cy_latest"]),
                    fmt_metric("cagr", m["enplanement_cagr_3y"]),
                    fmt_metric("growth", m["passenger_growth_ttm"]),
                    fmt_metric("load_factor", m["load_factor_ttm"]),
                    fmt_metric("taxi_min", m["taxi_out_avg_min"]),
                    fmt_metric("fare_premium", m["fare_premium"]),
                ]
                for r in ranking
                for m in [r["key_metrics"]]
            ],
        ),
    ]
    return result


# --- 3. explain score -------------------------------------------------------------------------
class ExplainArgs(WeightOverrides):
    airport: AirportCode

    @field_validator("airport")
    @classmethod
    def _airport(cls, v: str) -> str:
        return _code(v)


def explain_handler(args: ExplainArgs, svc: InsightService) -> ToolResult:
    result = svc.explain_score(args.airport, args.overrides())
    d = result.data
    rows = []
    for p in d["pillars"]:
        for m in p["metrics"]:
            rows.append(
                [
                    p["label"],
                    m["label"],
                    fmt_metric(m["metric"], m["value"]),
                    "n/a" if m["percentile"] is None else f"{m['percentile']:.0f}",
                ]
            )
    result.ui = [
        KpiBlock(
            title=f"{d['airport']} expansion score",
            items=[
                Kpi(label="Score", value=f"{d['score']:.1f}"),
                Kpi(label="US rank", value=f"#{d['national_rank']} of {d['peer_count']}"),
                Kpi(label="Confidence", value=d["confidence"], hint=f"data coverage {d['coverage']:.0%}"),
            ]
            + [
                Kpi(
                    label=p["label"],
                    value="n/a" if p["score"] is None else f"{p['score']:.0f}",
                    hint=f"weight {p['weight']:.0%}",
                )
                for p in d["pillars"]
            ],
        ),
        TableBlock(
            title="Metric breakdown (percentile vs national peers)",
            columns=["Pillar", "Metric", "Value", "Percentile"],
            rows=rows,
        ),
    ]
    return result


# --- 4. profile -------------------------------------------------------------------------------
class AirportArgs(BaseModel):
    airport: AirportCode

    @field_validator("airport")
    @classmethod
    def _airport(cls, v: str) -> str:
        return _code(v)


def profile_handler(args: AirportArgs, svc: InsightService) -> ToolResult:
    result = svc.airport_profile(args.airport)
    d = result.data
    e, t, o = d["enplanements"], d["traffic_ttm"], d["operations_ttm"]
    items = [
        Kpi(label="Enplanements (latest CY)", value=compact(e["latest_cy"]), hint=f"YoY {pct(e['yoy'], 1, True)}"),
        Kpi(label="vs 2019", value=fmt_metric("recovery_vs_2019", e["vs_2019"])),
        Kpi(label="Load factor TTM", value=pct(t["load_factor"])),
        Kpi(label="Pax growth TTM", value=pct(t["passenger_growth"], 1, True)),
        Kpi(label="Taxi-out", value=fmt_metric("x_min", o["taxi_out_avg_min"])),
        Kpi(label="Arrivals delayed 15+", value=pct(o["arr_delay_rate"])),
    ]
    if d["expansion_score"]:
        s = d["expansion_score"]
        items.append(
            Kpi(label="Expansion score", value=f"{s['score']:.1f}", hint=f"#{s['national_rank']} of {s['peer_count']}")
        )
    result.ui = [KpiBlock(title=f"{d['airport']['code']} - {d['airport']['name']}", items=items)]
    return result


# --- 5. congestion ----------------------------------------------------------------------------
class CongestionArgs(BaseModel):
    airports: list[str] = Field(min_length=1, max_length=6, description="Airport codes to compare")

    @field_validator("airports")
    @classmethod
    def _codes(cls, v: list[str]) -> list[str]:
        return [_code(c) for c in v]


def congestion_handler(args: CongestionArgs, svc: InsightService) -> ToolResult:
    result = svc.compare_congestion(args.airports)
    airports = result.data["airports"]
    codes = [a["airport"] for a in airports]
    metrics = airports[0]["components"]
    rows = []
    for i, m in enumerate(metrics):
        cells = []
        for a in airports:
            c = a["components"][i]
            cells.append(
                f"{fmt_metric(c['metric'], c['value'])} (p{c['percentile']:.0f})"
                if c["percentile"] is not None
                else "n/a"
            )
        rows.append([m["label"]] + cells)
    rows.append(["Runways (>=4,000 ft)"] + [str(int(a["runways"] or 0)) for a in airports])
    rows.append(["Scheduled hours/day"] + [f"{a['active_schedule_hours_per_day']:.1f}" for a in airports])
    ui = [
        BarChartBlock(
            title="Congestion Index (0-100, percentile vs US peers)",
            categories=codes,
            series=[BarSeries(name="Congestion Index", values=[a["congestion_index"] or 0 for a in airports])],
        ),
        TableBlock(title="Congestion components (value, national percentile)", columns=["Metric"] + codes, rows=rows),
    ]
    caps = [a for a in airports if a["regulatory_cap"]]
    if caps:
        ui.append(
            KpiBlock(
                title="Regulatory passenger caps (curated)",
                items=[
                    Kpi(
                        label=f"{a['airport']} cap utilization",
                        value=pct(a["regulatory_cap"]["cap_utilization"], 0),
                        hint=f"~{compact(a['regulatory_cap']['estimated_total_passengers_latest_cy'])} of "
                        f"{compact(a['regulatory_cap']['annual_passenger_cap'])} cap",
                    )
                    for a in caps
                ],
            )
        )
    result.ui = ui
    return result


# --- 6. route mix -----------------------------------------------------------------------------
class RouteMixArgs(AirportArgs):
    long_haul_threshold_mi: float | None = Field(
        None, ge=500, le=8000, description="Override long-haul distance threshold in statute miles (default 3000)"
    )


def route_mix_handler(args: RouteMixArgs, svc: InsightService) -> ToolResult:
    result = svc.route_mix(args.airport, args.long_haul_threshold_mi)
    d = result.data
    groups = [("All flights", d["all_flights"]), ("Passenger", d["passenger_flights"]), ("Cargo", d["cargo_flights"])]
    result.ui = [
        KpiBlock(
            title=f"{d['airport']} long-haul share ({d['period']})",
            items=[
                Kpi(
                    label="All departures",
                    value=pct(d["all_flights"]["long_share"]),
                    hint=f"{d['all_flights']['long_departures']:,} of {d['all_flights']['departures']:,}",
                ),
                Kpi(label="Passenger departures", value=pct(d["passenger_flights"].get("long_share"))),
                Kpi(label="Cargo share of departures", value=pct(d["cargo_share_of_departures"])),
                Kpi(label="Long-haul definition", value=f">= {d['definition']['long_haul_from_mi']:,.0f} mi"),
            ],
        ),
        BarChartBlock(
            title="Departures by distance band",
            categories=[g[0] for g in groups],
            stacked=True,
            unit="%",
            series=[
                BarSeries(name=band.title(), values=[round((g[1].get(f"{band}_share") or 0) * 100, 1) for g in groups])
                for band in ("short", "medium", "long")
            ],
        ),
        TableBlock(
            title="Top long-haul routes",
            columns=["Destination", "Country", "Type", "Departures", "Distance (mi)"],
            rows=[
                [r["dest"], r["dest_country"], r["kind"], f"{r['departures']:,.0f}", f"{r['distance_mi']:,.0f}"]
                for r in d["top_long_haul_routes"]
            ],
        ),
        TableBlock(
            title="Sensitivity to the long-haul definition",
            columns=["Threshold", "All flights", "Passenger flights"],
            rows=[
                [
                    f">= {t['threshold_mi']:,.0f} mi",
                    pct(t["all_flights_long_share"]),
                    pct(t["passenger_flights_long_share"]),
                ]
                for t in d["threshold_sensitivity"]
            ],
        ),
    ]
    return result


# --- 7. unmet demand --------------------------------------------------------------------------
def unmet_handler(args: AirportArgs, svc: InsightService) -> ToolResult:
    result = svc.unmet_demand(args.airport)
    d = result.data
    s, drv = d["spill_model"], d["drivers"]
    shares = drv["delay_cause_shares"]
    result.ui = [
        KpiBlock(
            title=f"{d['airport']} unmet demand ({d['period']})",
            items=[
                Kpi(
                    label="Spilled passengers / yr",
                    value=compact(s["spilled_passengers_annualized"]),
                    hint=f"range {compact(s['cv_sensitivity'][0]['spilled_passengers'])}-{compact(s['cv_sensitivity'][-1]['spilled_passengers'])} (cv)",
                ),
                Kpi(label="Spill rate", value=pct(s["spill_rate"]), hint="of carried passengers"),
                Kpi(
                    label="Extra daily departures",
                    value=f"~{s['equivalent_extra_departures_per_day']:.0f}",
                    hint=f"at {s['avg_seats_per_departure']:.0f} seats, 85% LF",
                ),
                Kpi(label="Load factor", value=pct(s["load_factor"])),
                Kpi(
                    label="Constrained routes",
                    value=f"{s['constrained_route_count']} / {s['route_count']}",
                    hint="LF >= 85%",
                ),
                Kpi(
                    label="Fare premium",
                    value=pct(drv["fare_premium_vs_us_norm"], 1, True),
                    hint="vs distance-adjusted US norm",
                ),
            ],
        ),
        TableBlock(
            title="Routes with the most spilled demand",
            columns=["Destination", "Load factor", "Spilled pax / yr", "Distance (mi)"],
            rows=[
                [r["dest"], pct(r["load_factor"]), compact(r["spilled_passengers_annualized"]), f"{r['distance_mi']:,}"]
                for r in s["top_spill_routes"]
            ],
        ),
        BarChartBlock(
            title="Arrival delay minutes by cause",
            categories=[k.replace("_", " ").title() for k in shares],
            series=[BarSeries(name="Share", values=[round((v or 0) * 100, 1) for v in shares.values()])],
            unit="%",
        ),
        TableBlock(
            title="Spill sensitivity to demand variability (cv)",
            columns=["cv", "Spilled passengers / yr"],
            rows=[[c["cv"], compact(c["spilled_passengers"])] for c in s["cv_sensitivity"]],
        ),
    ]
    return result


# --- 8. live ----------------------------------------------------------------------------------
def live_handler(args: AirportArgs, svc: InsightService) -> ToolResult:
    result = svc.live_status(args.airport)
    d = result.data
    nas, wx = d.get("faa_nas") or {}, d.get("weather") or {}
    programs = nas.get("active_programs") or []
    result.ui = [
        KpiBlock(
            title=f"{d['airport']} right now ({d['as_of']})",
            items=[
                Kpi(label="FAA programs", value=", ".join(p["type"] for p in programs) or "none active"),
                Kpi(
                    label="Arrival rate (FAA)",
                    value=str(nas.get("arrival_rate_per_hour") or "n/a"),
                    hint="aircraft / hour",
                ),
                Kpi(
                    label="Runways in use",
                    value=f"arr {nas.get('arrival_runways') or '?'} / dep {nas.get('departure_runways') or '?'}",
                ),
                Kpi(
                    label="Flight category",
                    value=str(wx.get("flight_category") or "n/a"),
                    hint=f"ceiling {wx.get('ceiling_ft') or 'none'} ft, vis {wx.get('visibility_sm') or '?'} sm",
                ),
            ],
        )
    ]
    return result


# --- 9. methodology ---------------------------------------------------------------------------
class MethodologyArgs(BaseModel):
    topic: Literal["expansion_score", "congestion_index", "route_mix", "unmet_demand", "data_sources", "limitations"]


def methodology_handler(args: MethodologyArgs, svc: InsightService) -> ToolResult:
    text = methodology_text(args.topic, svc)
    return ToolResult(
        tool="explain_methodology", summary=f"methodology: {args.topic}", data={"topic": args.topic, "text": text}
    )


def build_tools() -> list[Tool]:
    return [
        Tool(
            "resolve_airports",
            "Map place names, colloquial names, codes or US regions to US commercial airports (e.g. 'LA' -> LAX, "
            "'Santa Ana' -> SNA, 'New England' -> its airports). Also returns nearby metro alternatives. "
            "Use whenever the user names a place rather than a code.",
            ResolveArgs,
            resolve_handler,
        ),
        Tool(
            "rank_expansion_candidates",
            "Rank airports by the deterministic Terminal Expansion Opportunity Score (0-100) with pillar breakdown, "
            "national rank, confidence, rank stability under weight uncertainty, and constraint flags. "
            "Give a region OR airport codes (or neither for national top list). Optional relative pillar weights "
            "for what-if questions.",
            RankArgs,
            rank_handler,
        ),
        Tool(
            "explain_airport_score",
            "Explain one airport's expansion score: every metric's raw value, national percentile, and pillar "
            "contribution, plus curated constraints. Use for 'why' questions about a score.",
            ExplainArgs,
            explain_handler,
        ),
        Tool(
            "get_airport_profile",
            "Key facts for one airport: enplanement trend, traffic, load factor, delays, fares, scores and curated notes.",
            AirportArgs,
            profile_handler,
        ),
        Tool(
            "compare_congestion",
            "Compare airfield/schedule congestion between airports with the Congestion Index and its components "
            "(taxi-out, peak-hour movements per runway, delays, NAS delay, cancellations), plus regulatory caps.",
            CongestionArgs,
            congestion_handler,
        ),
        Tool(
            "analyze_route_mix",
            "Share of short/medium/long-haul departures from an airport (all flights, passenger-only, cargo), top "
            "long-haul routes, and sensitivity to the long-haul definition.",
            RouteMixArgs,
            route_mix_handler,
        ),
        Tool(
            "estimate_unmet_demand",
            "Estimate unmet (spilled) passenger demand at an airport with a spill model on route load factors, "
            "plus the drivers (constrained routes, fare premium, delay causes, growth) and counter-signals.",
            AirportArgs,
            unmet_handler,
        ),
        Tool(
            "get_live_status",
            "Real-time FAA airport status (ground stops/delay programs, current arrival rate, runway config) and "
            "current weather (METAR flight category). Use to illustrate current conditions.",
            AirportArgs,
            live_handler,
        ),
        Tool(
            "explain_methodology",
            "Return the exact definition of a score/model, the data sources, or known limitations.",
            MethodologyArgs,
            methodology_handler,
        ),
    ]
