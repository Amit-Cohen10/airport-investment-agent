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
            "pillar = weighted mean of its metrics; total = weighted mean of pillars. Missing metrics are "
            "dropped and weights re-normalized; confidence falls with data coverage.",
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
        "Known limitations: no public data on gate counts or terminal square footage, so terminal utilization "
        "is inferred from passengers, seats and load factors; on-time data covers reporting carriers' domestic "
        "flights only; CY2025 FAA enplanements are preliminary; the spill model assumes demand variability (cv); "
        "curated constraints are hand-maintained; scores are screening signals, not investment advice."
    )
