"""Feature engineering, scoring on a synthetic universe, entity resolution, context/memory, LLM adapters."""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import pytest

from app.agent.context import build_messages, render_system_prompt, tool_message, trim_history
from app.agent.session_store import InMemorySessionStore, Session
from app.analytics.expansion_score import ScoreConfig, explain_airport, score_universe
from app.analytics.features import enplanement_features, fare_features, traffic_features, ttm_windows
from app.config import Settings
from app.llm.factory import NoLLMConfiguredError, ResilientLLM, build_llm
from app.llm.offline import OfflinePlanner
from app.llm.openai_compat import OpenAICompatClient


# --- Features ------------------------------------------------------------------------------------
def test_ttm_windows_are_consecutive_12_month_blocks():
    cur, prev = ttm_windows(pd.Series(pd.date_range("2024-01-01", "2026-08-01", freq="MS")))
    assert (cur.start, cur.end) == (pd.Timestamp("2025-09-01"), pd.Timestamp("2026-08-01"))
    assert (prev.start, prev.end) == (pd.Timestamp("2024-09-01"), pd.Timestamp("2025-08-01"))


def test_enplanement_features_growth_math():
    enpl = pd.DataFrame(
        {
            "airport": ["X"] * 5,
            "year": [2019, 2022, 2023, 2024, 2025],
            "enplanements": [100.0, 80.0, 90.0, 100.0, 110.0],
            "name": "X",
            "city": "c",
            "state": "MA",
            "hub_class": "S",
        }
    )
    f = enplanement_features(enpl).loc["X"]
    assert f["enplanement_yoy"] == pytest.approx(0.10)
    assert f["recovery_vs_2019"] == pytest.approx(1.10)
    assert f["enplanement_cagr_3y"] == pytest.approx((110 / 80) ** (1 / 3) - 1)


def test_traffic_features_load_factor_and_demand_supply_gap():
    months = pd.date_range("2024-09-01", "2026-08-01", freq="MS")
    t100 = pd.DataFrame(
        {
            "airport": "X",
            "month": months,
            "passengers": [80.0] * 12 + [90.0] * 12,
            "seats": [100.0] * 24,
            "departures": 1.0,
            "dom_departures": 1.0,
            "freight_lbs": 0.0,
            "intl_departures": 0.0,
            "intl_passengers": 0.0,
        }
    )
    f, window = traffic_features(t100)
    row = f.loc["X"]
    assert row["load_factor_ttm"] == pytest.approx(0.9)
    assert row["passenger_growth_ttm"] == pytest.approx(0.125)
    assert row["demand_supply_gap"] == pytest.approx(12.5)  # pp: pax +12.5%, seats +0%
    assert window.label == "Sep 2025 - Aug 2026"


def test_fare_premium_is_positive_for_expensive_airport():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(200):
        d = float(rng.uniform(200, 2500))
        rows.append(
            {
                "year": 2026,
                "quarter": 1,
                "airport_1": f"A{i % 20}",
                "airport_2": f"B{i % 15}",
                "distance_mi": d,
                "pax_per_day": 100.0,
                "fare": 50 + 0.1 * d,
                "largest_share": 0.5,
            }
        )
    rows.append(
        {
            "year": 2026,
            "quarter": 1,
            "airport_1": "PRICY",
            "airport_2": "B1",
            "distance_mi": 1000.0,
            "pax_per_day": 100.0,
            "fare": 300.0,
            "largest_share": 1.0,
        }
    )
    out, label = fare_features(pd.DataFrame(rows))
    assert out.loc["PRICY", "fare_premium"] > 0.5
    assert label == "2026 Q1 - 2026 Q1"


# --- Scoring on a synthetic universe -------------------------------------------------------------
def _cfg() -> ScoreConfig:
    metric = {"weight": 1.0, "higher_is_better": True, "label": "m"}
    return ScoreConfig(
        pillars={
            "a": {"weight": 0.5, "label": "A", "metrics": {"m1": metric}},
            "b": {"weight": 0.5, "label": "B", "metrics": {"m2": metric}},
        },
        min_enplanements=10,
        high_min_coverage=0.9,
        medium_min_coverage=0.7,
    )


def test_score_universe_peer_filter_coverage_and_confidence():
    feats = pd.DataFrame(
        {
            "enplanements_latest": [100, 100, 100, 5],
            "m1": [1.0, 2.0, 3.0, 99.0],
            "m2": [3.0, np.nan, 1.0, 99.0],
        },
        index=["P", "Q", "R", "TINY"],
    )
    scored = score_universe(feats, _cfg())
    t = scored.table
    assert "TINY" not in t.index  # below the peer threshold
    assert t.at["Q", "coverage"] == pytest.approx(0.5) and t.at["Q", "confidence"] == "low"
    assert t.at["P", "confidence"] == "high"
    assert explain_airport(scored, feats, "P", _cfg())["pillars"][0]["metrics"][0]["value"] == 1.0


def test_weight_override_is_normalized_and_changes_order():
    feats = pd.DataFrame({"enplanements_latest": [100, 100], "m1": [1.0, 2.0], "m2": [2.0, 1.0]}, index=["P", "Q"])
    a_heavy = score_universe(feats, _cfg(), {"a": 9.0})
    assert a_heavy.pillar_weights == pytest.approx({"a": 9 / 9.5, "b": 0.5 / 9.5})
    assert a_heavy.table["score"].idxmax() == "Q"
    assert score_universe(feats, _cfg(), {"b": 9.0}).table["score"].idxmax() == "P"


# --- Entity resolution ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("sfo", "SFO"),
        ("KBOS", "BOS"),
        ("PANC", "ANC"),
        ("John Wayne", "SNA"),
        ("Bangor", "BGR"),
        ("Burlington", "BTV"),
        ("Logan", "BOS"),
        ("Pheonix", "PHX"),
    ],
)
def test_resolution_variants(service, query, first):
    assert service.directory.resolve(query).codes[0] == first


def test_resolution_regions_and_states(service):
    d = service.directory
    assert d.resolve("Vermont").kind == "region" and "BTV" in d.resolve("Vermont").codes
    assert d.region_states("pnw")[1] == ["OR", "WA"]
    assert d.resolve("Atlantis").kind == "none"
    assert d.resolve("").codes == []


def test_find_in_text(service):
    codes, region = service.directory.find_in_text("Compare LA, SFO and Santa Ana for the Midwest")
    assert codes[:3] == ["LAX", "SFO", "SNA"] and region == "midwest"
    assert service.directory.find_in_text("my mobile phone")[0] == []


# --- Context & memory ----------------------------------------------------------------------------
def _turn(i: int, size: int = 10) -> list[dict]:
    return [
        {"role": "user", "content": f"q{i}"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size},
        {"role": "assistant", "content": f"a{i}"},
    ]


def test_trim_history_keeps_whole_recent_turns():
    history = _turn(1, 5000) + _turn(2, 10) + _turn(3, 10)
    trimmed = trim_history(history, max_chars=2000)
    assert trimmed[0] == {"role": "user", "content": "q2"}
    tool_ids = {m["tool_call_id"] for m in trimmed if m["role"] == "tool"}
    call_ids = {c["id"] for m in trimmed for c in m.get("tool_calls", [])}
    assert tool_ids == call_ids  # no orphaned tool results


def test_tool_message_truncates_structurally_and_keeps_caveats():
    payload = {
        "ok": True,
        "summary": "s",
        "data": {"rows": [{"x": "y" * 500} for _ in range(100)]},
        "caveats": ["important caveat"],
    }
    msg = tool_message("c1", "t", payload)
    parsed = json.loads(msg["content"])  # still valid JSON
    assert len(msg["content"]) <= 12_000
    assert parsed["caveats"] == ["important caveat"] and parsed["summary"] == "s"
    assert len(parsed["data"]["rows"]) < 100 and "truncated" in parsed
    huge = tool_message("c2", "t", {"ok": True, "summary": "s", "data": {"blob": "z" * 50_000}})
    assert json.loads(huge["content"])["truncated"] == "data omitted"


def test_system_prompt_includes_periods_and_stale_warning():
    prompt = render_system_prompt(
        {"traffic": "Sep 2025 - Aug 2026"}, {"tables": {"ontime_monthly": {"status": "stale: timeout"}}}
    )
    assert "Sep 2025 - Aug 2026" in prompt and "stale tables" in prompt and "ontime_monthly" in prompt


def test_build_messages_adds_session_focus():
    session = Session(id="s", focus_airports=["BOS", "BGR"])
    msgs = build_messages("SYS", session, "what about it?", 10_000)
    assert msgs[0]["role"] == "system" and "BOS, BGR" in msgs[0]["content"]
    assert msgs[-1] == {"role": "user", "content": "what about it?"}


def test_session_store_eviction_and_ttl():
    store = InMemorySessionStore(max_sessions=2, ttl_s=3600)
    a, b, c = (store.get_or_create(None) for _ in range(3))
    assert store.get_or_create(a.id) is not a  # oldest evicted by LRU: a fresh, empty session
    expired = InMemorySessionStore(ttl_s=0)
    s = expired.get_or_create("x")
    s.updated_at = time.time() - 10
    assert expired.get_or_create("y") and "x" not in expired._sessions
    assert store.get_or_create(c.id) is c


# --- LLM adapters & chain ------------------------------------------------------------------------
def _history():
    return [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q", "synthetic": True},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "extra_content": {"g": 1},
                    "function": {"name": "t", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "name": "t", "content": "{}"},
        {"role": "assistant", "content": ""},
    ]


def test_openai_compat_sanitize_per_provider():
    gemini = OpenAICompatClient("gemini", "k", "m")._sanitize(_history())
    groq = OpenAICompatClient("groq", "k", "m")._sanitize(_history())
    assert gemini[2]["tool_calls"][0]["extra_content"] == {"g": 1}  # thought signature echoed to Gemini
    assert "extra_content" not in groq[2]["tool_calls"][0]
    for msgs in (gemini, groq):
        assert all("name" not in m and "synthetic" not in m for m in msgs)
        assert msgs[-1]["role"] == "tool"  # empty assistant turn dropped


def test_build_llm_orders_providers_and_appends_fallback():
    settings = Settings(
        _env_file=None,
        llm_providers=["groq", "gemini", "anthropic"],
        gemini_api_key="g",
        groq_api_key="q",
        anthropic_api_key=None,
    )
    chain = build_llm(settings, fallback=OfflinePlanner(lambda t: ([], None)))
    assert [c.name for c in chain.clients] == ["groq", "gemini", "offline"]
    with pytest.raises(NoLLMConfiguredError):
        build_llm(
            Settings(
                _env_file=None, gemini_api_key=None, groq_api_key=None, anthropic_api_key=None, openrouter_api_key=None
            )
        )


async def test_offline_planner_routes_intents_and_follow_ups():
    planner = OfflinePlanner(lambda text: (["SFO"] if "SFO" in text else [], None))
    tools = [{"type": "function"}]

    async def plan(question: str, system: str = "sys") -> list[tuple[str, dict]]:
        turn = await planner.chat([{"role": "system", "content": system}, {"role": "user", "content": question}], tools)
        return [(c.name, json.loads(c.arguments)) for c in turn.tool_calls]

    assert await plan("How is the score calculated?") == [("explain_methodology", {"topic": "expansion_score"})]
    assert await plan("SFO congestion please") == [("compare_congestion", {"airports": ["SFO"]})]
    follow = await plan(
        "and the unmet demand?", "## Session state\nAirports discussed so far (most recent last): BOS, SFO."
    )
    assert follow == [("estimate_unmet_demand", {"airport": "SFO"})]  # most recent airport
    assert await plan("tell me a joke") == []


async def test_resilient_llm_requires_clients():
    with pytest.raises(NoLLMConfiguredError):
        ResilientLLM([])


# --- Regressions from the second review round ---------------------------------------------------
def test_missing_metric_is_neutral_not_rewarded():
    metric = {"weight": 0.5, "higher_is_better": True, "label": "m"}
    cfg = ScoreConfig(
        pillars={"p": {"weight": 1.0, "label": "P", "metrics": {"scale": metric, "fare": metric}}},
        min_enplanements=0,
        high_min_coverage=0.95,
        medium_min_coverage=0.7,
    )
    feats = pd.DataFrame(
        {"enplanements_latest": 1, "scale": [10.0, 9.0, 1.0], "fare": [np.nan, 9.0, 1.0]}, index=["GAP", "FULL", "LOW"]
    )
    t = score_universe(feats, cfg).table
    # GAP has the best scale but no fare: its fare counts as the median (50), not as its scale score.
    assert t.at["GAP", "score"] == pytest.approx((100 + 50) / 2)
    assert t.at["GAP", "confidence"] == "low"


def test_grounding_signs_and_units():
    from app.agent.guardrails import check_grounding

    payload = [{"data": {"passenger_growth_ttm": -0.05, "score": 66.2, "percentile": 82.3}}]
    assert check_grounding("Passengers fell 5% (-5.0%).", payload).unverified == []
    assert check_grounding("Passengers grew +5%.", payload).unverified == ["+5%"]  # wrong sign
    assert check_grounding("Score 66.2, the 82nd percentile.", payload).unverified == []
    assert check_grounding("Score 66.2%.", payload).unverified == ["66.2%"]  # a score is not a percent


def test_resolution_never_guesses_unknown_codes(service):
    assert service.directory.resolve("XQZ").codes == []
    from app.services.insights import AnalysisError

    with pytest.raises(AnalysisError):
        service.airport_profile("XQZ")


def test_faa_lid_codes_are_bridged_to_iata(service):
    # FAA files use GPI (Glacier Park) and UNV (State College); BTS/OurAirports use FCA and SCE.
    assert {"FCA", "SCE"} <= set(service.features.index)
    assert "GPI" not in service.features.index
    assert service.features.at["FCA", "passengers_ttm"] > 0


def test_territories_have_reference_data(service):
    assert service.features.at["SJU", "runways"] >= 1 and service.features.at["SJU", "state"] == "PR"


def test_unreliable_ontime_samples_are_dropped(service):
    f = service.features
    low = f[f["ontime_coverage"] < 0.2]
    assert len(low) and low["taxi_out_avg_min"].isna().all()


def test_trim_history_is_linear_on_long_sessions():
    history = [m for i in range(2000) for m in _turn(i, 100)]
    started = time.perf_counter()
    trimmed = trim_history(history, 60_000)
    assert time.perf_counter() - started < 0.5 and trimmed[0]["role"] == "user"


def test_session_store_caps_turns():
    from app.agent.session_store import MAX_TURNS

    store = InMemorySessionStore()
    s = store.get_or_create("s")
    s.messages = [m for i in range(MAX_TURNS + 10) for m in _turn(i)]
    store.save(s)
    assert sum(m["role"] == "user" for m in s.messages) == MAX_TURNS


async def test_turn_sticks_to_fallback_provider():
    from app.llm.base import AssistantTurn, RetryableLLMError

    calls = []

    class Primary:
        name, model = "primary", "x"

        async def chat(self, *a, **k):
            calls.append("primary")
            raise RetryableLLMError("429")

    class Backup:
        name, model = "backup", "y"

        async def chat(self, *a, **k):
            calls.append("backup")
            return AssistantTurn(content="ok", tool_calls=[], message={}, provider="backup")

    chain = ResilientLLM([Primary(), Backup()], attempts_per_provider=1, base_delay_s=0)
    turn = chain.for_turn()
    await turn.chat([])
    await turn.chat([])
    assert calls == ["primary", "backup", "backup"]  # second step does not retry the primary
    await chain.for_turn().chat([])
    assert calls[-2:] == ["primary", "backup"]  # a new turn tries the primary again


def test_anthropic_replays_only_current_turn_blocks_and_drops_temperature():
    from app.llm.anthropic_client import AnthropicClient, to_anthropic

    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    old_turn = {
        "role": "assistant",
        "content": "old answer",
        "anthropic_blocks": [thinking, {"type": "text", "text": "old answer"}],
    }
    current = {
        "role": "assistant",
        "content": None,
        "anthropic_blocks": [thinking, {"type": "tool_use", "id": "t1", "name": "x", "input": {}}],
        "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
    }
    msgs = [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "q1"},
        old_turn,
        {"role": "user", "content": "q2"},
        current,
        {"role": "tool", "tool_call_id": "t1", "content": "{}"},
        {"role": "user", "content": "write the final answer", "synthetic": True},
    ]
    system, out = to_anthropic(msgs)
    assert system == "S"
    assert out[1]["content"] == [{"type": "text", "text": "old answer"}]  # earlier turn: no thinking replayed
    assert out[3]["content"][0] == thinking  # current turn: replayed unchanged
    assert out[4]["content"][-1] == {"type": "text", "text": "write the final answer"}
    assert not hasattr(AnthropicClient("k", "m"), "temperature")


def test_other_providers_never_see_anthropic_blocks():
    msgs = [{"role": "assistant", "content": "a", "anthropic_blocks": [{"type": "thinking"}]}]
    assert "anthropic_blocks" not in OpenAICompatClient("gemini", "k", "m")._sanitize(msgs)[0]
