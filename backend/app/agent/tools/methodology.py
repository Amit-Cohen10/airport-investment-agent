"""Methodology explanations, generated from the live config so they can never drift from the code."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.insights import InsightService


def methodology_text(topic: str, svc: InsightService) -> str:
    cfg = svc.cfg
    if topic == "expansion_score":
        lines = [
            cfg["expansion_score"]["description"].strip(),
            "",
            f"Peers: US airports with >= {cfg['peer_universe']['min_annual_enplanements']:,} annual enplanements "
            f"({len(svc.default_scores.table)} airports). Each metric -> national percentile (0-100); "
            "pillar = weighted mean of its metrics; total = weighted mean of pillars. A missing metric is scored "
            f"at the neutral {cfg['expansion_score'].get('missing_metric_percentile', 50)}th percentile (a data gap is "
            "neither rewarded nor punished); confidence falls with data coverage "
            f"(High needs >= {cfg['expansion_score']['confidence']['high_min_coverage']:.0%} of the weight backed by data).",
            "",
            "The score is a screening priority for further diligence, not an estimate of investment returns. It does "
            "not prove that terminal capacity (rather than runways, airspace or a legal cap) is the binding constraint: "
            "each ranked airport carries a 'bottleneck check' for that.",
            "",
        ]
        for p in cfg["expansion_score"]["pillars"].values():
            metrics = ", ".join(f"{m['label']} ({m['weight']:.0%})" for m in p["metrics"].values())
            lines.append(f"- {p['label']} - weight {p['weight']:.0%}: {metrics}")
        lines += [
            "",
            "Rank stability: 1,000 Dirichlet re-weightings around the default pillar weights.",
            "Constraint flags (caps, curfews, land) are shown separately and never folded into the score.",
        ]
        return "\n".join(lines)
    if topic == "congestion_index":
        c = cfg["congestion_index"]
        return (
            c["description"].strip()
            + "\nComponents: "
            + ", ".join(f"{m['label']} ({m['weight']:.0%})" for m in c["metrics"].values())
        )
    if topic == "route_mix":
        r = cfg["route_mix"]
        return (
            f"Departures by great-circle segment distance (BTS T-100, all carriers incl. foreign and cargo). "
            f"Short < {r['short_haul_max_mi']} mi, medium {r['short_haul_max_mi']}-{r['long_haul_min_mi']} mi, "
            f"long >= {r['long_haul_min_mi']} mi. Reported separately for passenger and freighter aircraft, "
            f"and at alternative thresholds {r['alternative_thresholds_mi']} because no official definition exists."
        )
    if topic == "unmet_demand":
        u = cfg["unmet_demand"]
        return (
            "Spill model: per route-month, demand ~ Normal(mu, cv*mu). Observed passengers = E[min(demand, seats)]; "
            f"solve for mu; spill = mu - observed. cv = {u['demand_cv']} (sensitivity {u['cv_sensitivity']}). "
            f"Routes below {u['min_load_factor_for_spill']:.0%} LF assumed unconstrained. Spill converted to extra "
            f"daily departures at {u['target_load_factor']:.0%} LF. 'Why' combines constrained routes, fare premium, "
            "delay causes (NAS share), demand-vs-seat growth, and curated physical/regulatory constraints."
        )
    if topic == "data_sources":
        lines = ["Snapshot tables (refreshed by the ingest job):"]
        for name, meta in svc.repo.manifest.get("tables", {}).items():
            lines.append(f"- {name}: {meta.get('source')} | {meta.get('period')} | {meta.get('url')}")
        lines.append("Live: FAA NAS Status API (nasstatus.faa.gov) and aviationweather.gov METAR.")
        return "\n".join(lines)
    return (
        "Known limitations: gate counts and terminal capacity are not integrated (there is no standardized national "
        "API; airport master plans, e.g. SFO's Airport Development Plan, contain gate analyses and are the next "
        "diligence step), so terminal pressure is inferred from passengers, seats and load factors; the score is a "
        "screening priority, not a return estimate (costs, airline commitments, committed projects and concession "
        "economics are not modeled); on-time data covers reporting carriers' domestic "
        "flights only; CY2025 FAA enplanements are preliminary; the spill model assumes demand variability (cv); "
        "curated constraints are hand-maintained; scores are screening signals, not investment advice."
    )
