"""Unit tests for the pure analytics layer (hand-checkable expectations, no data files)."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from app.analytics import route_mix
from app.analytics.normalization import normalize_weights, percentile_rank, weighted_mean
from app.analytics.sensitivity import rank_stability
from app.analytics.unmet_demand import expected_carried, spill_for


class TestNormalization:
    def test_percentile_rank_orders_and_keeps_nan(self):
        s = pd.Series([10.0, 20.0, 30.0, np.nan], index=list("abcd"))
        p = percentile_rank(s)
        assert p["c"] > p["b"] > p["a"]
        assert math.isnan(p["d"])
        assert p["c"] == pytest.approx(100.0)

    def test_percentile_rank_lower_is_better(self):
        p = percentile_rank(pd.Series([1.0, 2.0, 3.0]), higher_is_better=False)
        assert p.iloc[0] == pytest.approx(100.0)

    def test_weighted_mean_renormalizes_missing(self):
        score, coverage = weighted_mean({"a": 80.0, "b": None}, {"a": 0.25, "b": 0.75})
        assert score == pytest.approx(80.0)
        assert coverage == pytest.approx(0.25)

    def test_weighted_mean_all_missing(self):
        assert weighted_mean({"a": float("nan")}, {"a": 1.0}) == (None, 0.0)

    def test_normalize_weights(self):
        assert normalize_weights({"a": 2, "b": 2}) == {"a": 0.5, "b": 0.5}
        with pytest.raises(ValueError):
            normalize_weights({"a": 0})


class TestSpillModel:
    def test_expected_carried_matches_closed_form_at_mu_equals_seats(self):
        # z = 0  ->  E[min(D,S)] = mu - sigma * phi(0)
        assert expected_carried(100, 100, 0.3) == pytest.approx(100 - 30 * norm.pdf(0))

    def test_spill_inverts_expected_carried(self):
        seats, cv = 100.0, 0.3
        carried = expected_carried(110, seats, cv)
        assert spill_for(carried, seats, cv) == pytest.approx(110 - carried, abs=0.6)

    def test_no_spill_when_empty(self):
        assert spill_for(0, 100, 0.3) == 0.0
        assert spill_for(50, 0, 0.3) == 0.0

    def test_spill_grows_with_load_factor_and_cv(self):
        assert spill_for(90, 100, 0.3) > spill_for(80, 100, 0.3)
        assert spill_for(85, 100, 0.4) > spill_for(85, 100, 0.2)


class TestSensitivity:
    def test_dominant_airport_always_first(self):
        scores = pd.DataFrame({"g": [90, 10, 20], "s": [80, 30, 10]}, index=["A", "B", "C"])
        out = rank_stability(scores, {"g": 0.5, "s": 0.5}, samples=200, seed=1)
        assert out["A"]["p_rank_1"] == 1.0
        assert out["A"]["rank_range_90"] == [1, 1]

    def test_tradeoff_produces_uncertainty_and_is_reproducible(self):
        scores = pd.DataFrame({"g": [90, 10], "s": [10, 90]}, index=["A", "B"])
        first = rank_stability(scores, {"g": 0.5, "s": 0.5}, samples=500, seed=3)
        assert 0.2 < first["A"]["p_rank_1"] < 0.8
        assert first == rank_stability(scores, {"g": 0.5, "s": 0.5}, samples=500, seed=3)


class TestRouteMix:
    def test_bands_and_cargo_split(self):
        seg = pd.DataFrame(
            {
                "dest": ["A", "B", "C", "D"],
                "dest_country": ["US"] * 4,
                "aircraft_config": [1, 1, 2, 2],
                "departures": [60, 20, 10, 10],
                "seats": [6000, 2000, 0, 0],
                "passengers": [5000, 1800, 0, 0],
                "distance_mi": [300, 3500, 3500, 5000],
            }
        )
        out = route_mix.analyze(seg, short_max_mi=700, long_min_mi=3000, alternative_thresholds=[3000, 4000])
        assert out["all_flights"]["long_share"] == pytest.approx(0.4)
        assert out["passenger_flights"]["long_share"] == pytest.approx(0.25)
        assert out["cargo_share_of_departures"] == pytest.approx(0.2)
        assert out["threshold_sensitivity"][1]["all_flights_long_share"] == pytest.approx(0.1)
