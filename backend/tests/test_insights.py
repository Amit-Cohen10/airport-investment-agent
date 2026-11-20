"""Integration tests over the real data snapshot: the answers to the brief's questions are sane."""

from __future__ import annotations

import pytest

from app.services.insights import AnalysisError


def test_resolution_handles_colloquial_names(service):
    res = service.resolve(["LA", "Santa Ana", "Anchorage", "KSFO"]).data["results"]
    assert [r["airports"][0]["code"] for r in res] == ["LAX", "SNA", "ANC", "SFO"]
    assert "SNA" in res[0]["nearby_alternatives"]  # LA metro ambiguity is surfaced


def test_new_england_region_resolves_to_its_states(service):
    res = service.resolve(["New England"]).data["results"][0]
    codes = {a["code"] for a in res["airports"]}
    assert {"BOS", "BDL", "PVD", "PWM", "MHT", "BTV"} <= codes
    assert "JFK" not in codes


def test_expansion_ranking_is_deterministic_and_explained(service):
    codes = service.directory.airports_in_states(["CT", "ME", "MA", "NH", "RI", "VT"])
    a = service.rank_expansion(codes, "New England")
    b = service.rank_expansion(codes, "New England")
    assert a.data["ranking"] == b.data["ranking"]
    top = a.data["ranking"][0]
    assert 0 <= top["score"] <= 100 and top["rank_stability"]["p_rank_1"] > 0
    assert a.provenance and a.assumptions


def test_weight_override_changes_scores(service):
    codes = ["BOS", "PWM", "BGR", "BTV"]
    base = service.rank_expansion(codes, "x").data["ranking"]
    growth = service.rank_expansion(codes, "x", {"demand_momentum": 5}).data["ranking"]
    assert {r["code"]: r["score"] for r in base} != {r["code"]: r["score"] for r in growth}
    with pytest.raises(ValueError):
        service.rank_expansion(codes, "x", {"bogus": 1})


def test_congestion_includes_sna_passenger_cap(service):
    airports = {a["airport"]: a for a in service.compare_congestion(["LAX", "SNA"]).data["airports"]}
    cap = airports["SNA"]["regulatory_cap"]
    assert cap and 0.5 < cap["cap_utilization"] < 1.2
    assert airports["LAX"]["regulatory_cap"] is None


def test_anchorage_long_haul_is_cargo_driven(service):
    d = service.route_mix("ANC").data
    assert d["all_flights"]["long_share"] > 3 * d["passenger_flights"]["long_share"]
    assert d["cargo_share_of_departures"] > 0.3


def test_sfo_unmet_demand_has_range_and_drivers(service):
    d = service.unmet_demand("SFO").data
    sens = [c["spilled_passengers"] for c in d["spill_model"]["cv_sensitivity"]]
    assert sens == sorted(sens) and sens[0] > 0
    assert d["drivers"]["delay_cause_shares"]["nas"] > d["drivers"]["delay_cause_shares"]["weather"]
    assert any(n["topic"] == "runway geometry" for n in d["notes"])


def test_unknown_airport_raises_analysis_error(service):
    with pytest.raises(AnalysisError):
        service.airport_profile("ZZZ")


def test_methodology_text_matches_configured_missing_data_policy(service):
    from app.agent.tools.methodology import methodology_text

    text = methodology_text("expansion_score", service)
    pct = service.cfg["expansion_score"]["missing_metric_percentile"]
    assert f"neutral {pct}th percentile" in text and "re-normalized" not in text
    assert "screening priority" in text


def test_bottleneck_check_flags_non_terminal_constraints(service):
    assert service._bottleneck_check("SNA")["likely_binding"] == "regulatory"
    assert service._bottleneck_check("SFO")["likely_binding"] == "airside risk"
    ranking = service.rank_expansion(["SFO", "SNA", "PWM"], "x").data["ranking"]
    assert all("bottleneck_check" in r for r in ranking)
