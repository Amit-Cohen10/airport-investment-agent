"""Agent-layer tests: tool registry contract, grounding guardrail, runtime loop, LLM failover."""

from __future__ import annotations

import json

import pytest

from app.agent.guardrails import check_grounding
from app.agent.runtime import AgentRuntime
from app.agent.session_store import InMemorySessionStore
from app.agent.tools.definitions import build_tools
from app.agent.tools.registry import ToolRegistry
from app.llm.base import AssistantTurn, LLMError, RetryableLLMError
from app.llm.factory import ResilientLLM


@pytest.fixture(scope="module")
def registry(service):
    return ToolRegistry(service, build_tools())


class TestRegistry:
    def test_schemas_are_portable(self, registry):
        text = json.dumps(registry.schemas())
        assert "$ref" not in text and "anyOf" not in text and '"title"' not in text
        assert {s["function"]["name"] for s in registry.schemas()} >= {
            "resolve_airports",
            "rank_expansion_candidates",
            "compare_congestion",
            "analyze_route_mix",
            "estimate_unmet_demand",
        }

    def test_unknown_tool_returns_error_not_exception(self, registry):
        result = registry.execute("drop_tables", "{}")
        assert not result.ok and "Unknown tool" in result.data["error"]

    def test_invalid_arguments_are_explained(self, registry):
        result = registry.execute("compare_congestion", json.dumps({"airports": []}))
        assert not result.ok and "Invalid arguments" in result.data["error"]

    def test_malformed_json(self, registry):
        assert not registry.execute("get_airport_profile", "{not json").ok

    def test_unknown_airport_is_a_clean_error(self, registry):
        result = registry.execute("get_airport_profile", json.dumps({"airport": "ZZZ"}))
        assert not result.ok and "resolve_airports" in result.data["error"]

    def test_tool_builds_ui_evidence(self, registry):
        result = registry.execute("analyze_route_mix", json.dumps({"airport": "ANC"}))
        assert result.ok and result.ui and result.provenance


class TestGrounding:
    def test_numbers_from_tools_pass_with_unit_scaling(self):
        payload = [{"data": {"long_share": 0.2919, "spilled": 2665790, "taxi": 23.63}}]
        report = check_grounding("**29.2%** of flights; ~2.7M spilled; taxi-out 23.6 min.", payload)
        assert report.checked == 3 and report.unverified == []

    def test_invented_number_is_flagged(self):
        report = check_grounding("Load factor is 97.5%.", [{"data": {"lf": 0.8165}}])
        assert report.unverified == ["97.5%"]

    def test_years_and_small_ordinals_ignored(self):
        assert check_grounding("Top 3 airports in 2025.", []).checked == 0


class TestRuntime:
    async def test_tool_loop_then_grounded_answer(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("rank_expansion_candidates", {"region": "New England", "top_n": 3})]),
                ("text", "BOS ranks first."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        events = []

        async def emit(e):
            events.append(e["type"])

        reply = await runtime.run(None, "Which New England airports are strong candidates?", emit)
        assert reply.answer == "BOS ranks first."
        assert [r.tool for r in reply.evidence] == ["rank_expansion_candidates"]
        assert reply.evidence[0].data["ranking"][0]["code"] == "BOS"
        assert events[:3] == ["session", "status", "tool_start"]
        # The tool result was fed back to the model as a tool message
        assert llm.calls[1][-1]["role"] == "tool"

    async def test_model_self_corrects_after_invalid_args(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("compare_congestion", {"airports": "LAX"})]),  # wrong type
                ("tools", [("compare_congestion", {"airports": ["LAX", "SNA"]})]),
                ("text", "Compared."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        reply = await runtime.run(None, "Compare LA and Santa Ana congestion", _noop)
        assert "Invalid arguments" in llm.calls[1][-1]["content"]
        assert len(reply.evidence) == 1 and reply.evidence[0].tool == "compare_congestion"

    async def test_follow_up_keeps_history_and_focus(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("analyze_route_mix", {"airport": "ANC"})]),
                ("text", "First."),
                ("text", "Follow-up."),
            ]
        )
        store = InMemorySessionStore()
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), store)
        first = await runtime.run(None, "long haul share at Anchorage?", _noop)
        await runtime.run(first.session_id, "and with a 4000 mile threshold?", _noop)
        second_call = llm.calls[-1]
        assert any(m["role"] == "tool" for m in second_call)  # earlier tool results are in context
        assert "ANC" in second_call[0]["content"]  # session focus is in the system prompt

    async def test_step_budget_forces_final_answer(self, service, fake_llm_factory):
        loop = [("tools", [("get_airport_profile", {"airport": "BOS"})])] * 2
        llm = fake_llm_factory(loop + [("text", "Summary.")])
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore(), max_steps=2)
        reply = await runtime.run(None, "profile BOS", _noop)
        assert reply.answer == "Summary."


class TestFailover:
    async def test_retryable_error_fails_over_to_next_provider(self):
        class Broken:
            name, model = "broken", "x"

            async def chat(self, *a, **k):
                raise RetryableLLMError("429")

        class Works:
            name, model = "works", "y"

            async def chat(self, *a, **k):
                return AssistantTurn(
                    content="ok", tool_calls=[], message={"role": "assistant", "content": "ok"}, provider="works"
                )

        llm = ResilientLLM([Broken(), Works()], attempts_per_provider=2, base_delay_s=0)
        assert (await llm.chat([])).provider == "works"

    async def test_all_providers_failing_raises(self):
        class Broken:
            name, model = "broken", "x"

            async def chat(self, *a, **k):
                raise LLMError("401")

        with pytest.raises(LLMError):
            await ResilientLLM([Broken()]).chat([])


async def _noop(_):
    return None


class TestOfflinePlanner:
    async def test_offline_mode_answers_brief_questions_with_tools(self, service):
        from app.llm.offline import OfflinePlanner

        runtime = AgentRuntime(
            OfflinePlanner(service.directory.find_in_text),
            ToolRegistry(service, build_tools()),
            InMemorySessionStore(),
        )
        expected = {
            "Which airports in New England are strong candidates for terminal expansion?": "rank_expansion_candidates",
            "Compare LA and Santa Ana airport congestion levels.": "compare_congestion",
            "What is the percentage of long haul flights out of Anchorage airport?": "analyze_route_mix",
            "What is the unmet flight demand in SFO airport and why?": "estimate_unmet_demand",
        }
        for question, tool in expected.items():
            reply = await runtime.run(None, question, _noop)
            assert [e.tool for e in reply.evidence] == [tool], question
            assert "Offline mode" in reply.answer
            assert reply.grounding["unverified"] == []


class TestReviewRegressions:
    def test_anthropic_tolerates_malformed_tool_arguments_in_history(self):
        from app.llm.anthropic_client import to_anthropic

        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "x", "arguments": '{"airport": "SFO"'}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "{}"},
            {"role": "assistant", "content": ""},
        ]
        _, out = to_anthropic(msgs)
        assert out[1]["content"][0]["input"] == {}
        assert all(m["content"] for m in out)  # empty assistant turn dropped

    async def test_unexpected_adapter_exception_fails_over(self):
        class Crashes:
            name, model = "crash", "x"

            async def chat(self, *a, **k):
                raise KeyError("bug")

        class Works:
            name, model = "works", "y"

            async def chat(self, *a, **k):
                return AssistantTurn(
                    content="ok", tool_calls=[], message={"role": "assistant", "content": "ok"}, provider="works"
                )

        assert (await ResilientLLM([Crashes(), Works()]).chat([])).provider == "works"

    def test_congestion_skips_airports_without_ontime_data(self, registry):
        result = registry.execute("compare_congestion", json.dumps({"airports": ["HVN", "SFO"]}))
        assert result.ok
        assert result.data["airports_without_ontime_data"] == ["HVN"]

    def test_all_zero_weights_is_a_fixable_error(self, registry):
        args = {
            "region": "New England",
            "demand_momentum_weight": 0,
            "capacity_utilization_weight": 0,
            "operational_strain_weight": 0,
            "revenue_potential_weight": 0,
        }
        result = registry.execute("rank_expansion_candidates", json.dumps(args))
        assert not result.ok and "Invalid weights" in result.data["error"]

    def test_grounding_rejects_unrelated_percentages(self):
        payload = [{"data": {"lf": 0.8165, "enplanements": 21021153, "rank": 19}}]
        report = check_grounding("Load factor 81.7%, 21.0M passengers, but growth of 37% and 2,050 flights.", payload)
        assert report.unverified == ["37%", "2,050"]

    async def test_grounding_accepts_numbers_from_earlier_turns(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("analyze_route_mix", {"airport": "ANC"})]),
                ("text", "First."),
                ("text", "At 4,000 miles the share is 11.3% of all flights."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        first = await runtime.run(None, "ANC long haul?", _noop)
        second = await runtime.run(first.session_id, "and at 4000 miles?", _noop)
        assert second.grounding["unverified"] == []


def test_grounding_understands_units_written_in_tool_text():
    payload = [{"notes": ["cap of 12.2 or 12.5 million annual passengers; still 5% below 2019"]}]
    report = check_grounding("The cap is 12.5M passengers and traffic is 5% below 2019.", payload)
    assert report.unverified == []


def test_grounding_handles_metric_value_pairs_ordinals_and_ranges():
    payload = [
        {
            "data": {
                "components": [{"metric": "dep_delay_rate", "value": 0.241, "percentile": 78.4}],
                "pillars": {"operational_strain": 95.6},
                "airports": [{"ontime_sample_coverage": 0.9}, {"ontime_sample_coverage": 0.93}],
                "recovery_vs_2019": 1.344,
                "seat_growth_ttm": 0.0105,
            }
        }
    ]
    answer = "Delays hit 24.1% (78th percentile), strain in the 95th percentile, coverage ~90-93%, 134.4% of 2019, seats +1.1%."
    assert check_grounding(answer, payload).unverified == []
    assert check_grounding("A derived 96% share.", payload).unverified == ["96%"]


def test_ordinal_tolerance_is_one_unit_not_more():
    payload = [{"data": {"percentile": 95.6}}]
    assert check_grounding("the 95th percentile", payload).unverified == []
    assert check_grounding("the 93rd percentile", payload).unverified == ["93rd"]


def test_compound_descriptors_are_not_statistics():
    assert check_grounding("Trailing-12-month growth was flat.", []).checked == 0
    assert check_grounding("Growth of 12 points.", []).unverified == ["12"]


class TestGuardrailHardening:
    def test_echoed_queries_and_errors_are_not_evidence(self):
        payload = [
            {
                "ok": True,
                "data": {
                    "results": [{"query": "SFO load factor 99%", "airports": []}],
                    "error": "load factor 99% not found",
                },
            }
        ]
        assert check_grounding("SFO load factor is 99%.", payload).unverified == ["99%"]

    def test_contextual_exemptions(self):
        assert check_grounding("SFO has 9 runways.", []).unverified == ["9"]
        assert check_grounding("SFO has 2000 gates.", []).unverified == ["2000"]
        assert check_grounding("SFO has 999-gate capacity.", []).unverified == ["999"]
        assert check_grounding("Traffic in 2025 beat 2019 levels; top 3 airports.", []).checked == 0

    def test_spelled_out_percentage_points(self):
        assert check_grounding("a premium of 12 percentage points", [{"data": {"fare_premium": 0.12}}]).unverified == []

    def test_confidence_overstatement_is_flagged(self):
        from app.agent.guardrails import confidence_overstated

        assert confidence_overstated("**Confidence:** High because...", ["medium", "high"])
        assert not confidence_overstated("Confidence: Medium", ["medium"])

    async def test_failed_tool_echo_cannot_ground_an_answer(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("resolve_airports", {"queries": ["SFO load factor 99%"]})]),
                ("text", "SFO load factor is 99%."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        reply = await runtime.run(None, "q", _noop)
        assert reply.grounding["unverified"] == ["99%"]


async def test_follow_up_without_new_tools_shows_earlier_evidence(service, fake_llm_factory):
    llm = fake_llm_factory(
        [
            ("tools", [("analyze_route_mix", {"airport": "ANC"})]),
            ("text", "First."),
            ("text", "As shown, 29.2% of departures are long-haul."),
        ]
    )
    runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
    first = await runtime.run(None, "ANC long haul?", _noop)
    second = await runtime.run(first.session_id, "remind me of the share?", _noop)
    body = second.to_dict()
    assert body["evidence_from_earlier_turn"] and body["evidence"][0]["tool"] == "analyze_route_mix"
    assert body["sources"]


async def test_session_locks_do_not_accumulate(service, fake_llm_factory):
    import gc

    llm = fake_llm_factory([("text", "ok")] * 6)
    runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore(max_sessions=2))
    for _ in range(6):
        await runtime.run(None, "hi", _noop)
    gc.collect()
    assert len(runtime._locks) == 0


def test_confidence_check_uses_named_airports_grade():
    from app.agent.guardrails import confidence_overstated

    grades = {"BOS": "high", "SJU": "medium"}
    assert not confidence_overstated("**Confidence:** High for BOS.", ["medium"], grades)
    assert confidence_overstated("**Confidence:** High for SJU.", ["medium"], grades)
    assert confidence_overstated("Confidence: High overall.", ["high", "medium"], grades)


def test_cv_can_be_shown_as_percent():
    assert (
        check_grounding("a 30% coefficient of variation", [{"data": {"cv_sensitivity": [{"cv": 0.3}]}}]).unverified
        == []
    )


def test_confidence_statement_with_several_levels_uses_the_lowest():
    from app.agent.guardrails import confidence_overstated

    answer = "**Confidence:** High for congestion comparisons; Medium for unmet demand models."
    assert not confidence_overstated(answer, {"compare_congestion": "high", "estimate_unmet_demand": "medium"})


class TestCodexRound2:
    def test_unknown_tool_arguments_are_rejected(self, registry):
        result = registry.execute("get_airport_profile", json.dumps({"airport": "SFO", "invented_weight": 0.99}))
        assert not result.ok and "Invalid arguments" in result.data["error"]

    async def test_invented_parameter_cannot_ground_a_number(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("get_airport_profile", {"airport": "SFO", "invented_weight": 0.99})]),
                ("text", "SFO load factor is 99%."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        reply = await runtime.run(None, "q", _noop)
        assert reply.grounding["unverified"] == ["99%"]

    def test_sentence_initial_counts_and_of_years_are_checked(self):
        assert check_grounding("9 runways are available at SFO.", []).unverified == ["9"]
        assert check_grounding("The airport has a total of 2000 gates.", []).unverified == ["2000"]
        assert check_grounding("1. First point\n2) Second point", []).checked == 0

    def test_every_confidence_clause_is_checked(self):
        from app.agent.guardrails import confidence_overstated

        grades = {"SFO": "medium", "ANC": "low"}
        assert confidence_overstated("Confidence: High for SFO; Low for ANC.", ["medium", "low"], grades)
        assert confidence_overstated("Confidence: Medium overall.\nHigh for SFO congestion.", ["medium"], grades)
        tools = {"compare_congestion": "high", "estimate_unmet_demand": "medium"}
        assert not confidence_overstated("Confidence: High for congestion; Medium for unmet demand.", tools, {})

    async def test_history_follow_up_shows_the_evidence_it_uses(self, service, fake_llm_factory):
        lf = service.airport_profile("SFO").data["traffic_ttm"]["load_factor"]
        llm = fake_llm_factory(
            [
                ("tools", [("get_airport_profile", {"airport": "SFO"})]),
                ("text", "Profile."),
                ("tools", [("analyze_route_mix", {"airport": "ANC"})]),
                ("text", "Route mix."),
                ("text", f"**Confidence:** High. SFO's load factor is {lf * 100:.1f}%."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        sid = (await runtime.run(None, "SFO?", _noop)).session_id
        await runtime.run(sid, "ANC long haul?", _noop)
        reply = (await runtime.run(sid, "remind me SFO's load factor", _noop)).to_dict()
        assert reply["evidence_from_earlier_turn"]
        assert [e["tool"] for e in reply["evidence"]] == ["get_airport_profile"]


def test_confidence_explanations_are_not_claims():
    from app.agent.guardrails import confidence_overstated

    tools = {"rank_expansion_candidates": "medium"}
    answer = (
        "**Confidence: Medium.** MRY's confidence is medium. The other four are high, and the ranking takes the lowest."
    )
    assert not confidence_overstated(answer, tools, {"MRY": "medium"})
    answer2 = "**Confidence:** Medium. The spill model is always Medium, and the ranking results were High."
    assert not confidence_overstated(answer2, {"estimate_unmet_demand": "medium"}, {})


def test_rank_interval_width_is_evidence(service):
    data = service.rank_expansion(["BOS", "PWM", "BGR"], "x").for_llm()
    assert check_grounding("BOS's 90% rank range is 1-3.", [data]).unverified == []


def test_bold_confidence_statement_ends_at_its_sentence():
    from app.agent.guardrails import confidence_overstated

    answer = "**Confidence: Medium.** MRY's confidence is medium, and the other four airports are high."
    assert not confidence_overstated(answer, {"rank_expansion_candidates": "medium"}, {"MRY": "medium"})
    assert confidence_overstated(
        "**Confidence: High.** Based on the ranking.", {"rank_expansion_candidates": "medium"}, {}
    )


class TestCodexRound3:
    def test_confidence_is_graded_per_airport_and_analysis(self):
        from app.agent.guardrails import Grade, confidence_overstated

        graded = [Grade("compare_congestion", "SFO", "high"), Grade("estimate_unmet_demand", "SFO", "medium")]
        ok = "Confidence: High for SFO congestion; Medium for SFO unmet demand."
        assert not confidence_overstated(ok, [], graded=graded)
        assert confidence_overstated("Confidence: High for SFO unmet demand.", [], graded=graded)

    def test_a_shared_level_covers_every_coordinated_target(self):
        from app.agent.guardrails import Grade, confidence_overstated

        tools = {"compare_congestion": "high", "estimate_unmet_demand": "medium"}
        assert confidence_overstated("Confidence: High for congestion and unmet demand.", tools)
        graded = [Grade("compare_congestion", "SFO", "high"), Grade("compare_congestion", "ANC", "low")]
        assert confidence_overstated("Confidence: High for SFO and ANC.", [], graded=graded)
        # A clause after ";" that names no level is not covered by the earlier one.
        assert not confidence_overstated("Confidence: High for congestion; the spill model is an estimate.", tools)

    def test_explicit_confidence_sentences_are_claims(self):
        from app.agent.guardrails import Grade, confidence_overstated

        graded = [Grade("estimate_unmet_demand", "SFO", "medium")]
        answer = "Confidence: Medium. I have high confidence in the SFO spill estimate."
        assert confidence_overstated(answer, [], graded=graded)
        # Mentioning grades ("the other four are high") is still not a claim.
        rank = [Grade("rank_expansion_candidates", "MRY", "medium"), Grade("rank_expansion_candidates", None, "medium")]
        answer = "**Confidence: Medium.** MRY's confidence is medium, and the other four airports are high."
        assert not confidence_overstated(answer, [], graded=rank)

    async def test_history_evidence_is_chosen_by_airport_not_coincidence(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("get_airport_profile", {"airport": "SFO"})]),
                ("text", "SFO profile."),
                ("tools", [("get_airport_profile", {"airport": "LAX"})]),
                ("text", "LAX profile."),
                ("text", "SFO has 4 runways."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        sid = (await runtime.run(None, "SFO?", _noop)).session_id
        await runtime.run(sid, "LAX?", _noop)
        reply = (await runtime.run(sid, "How many runways at SFO?", _noop)).to_dict()
        shown = [e["data"]["airport"]["code"] for e in reply["evidence"]]
        assert shown == ["SFO"], shown

    async def test_qualitative_follow_up_confidence_is_checked(self, service, fake_llm_factory):
        llm = fake_llm_factory(
            [
                ("tools", [("estimate_unmet_demand", {"airport": "SFO"})]),
                ("text", "Spill estimate."),
                ("text", "Confidence: High. The SFO spill estimate is reliable."),
            ]
        )
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore())
        sid = (await runtime.run(None, "SFO unmet demand?", _noop)).session_id
        reply = (await runtime.run(sid, "How sure are you?", _noop)).to_dict()
        assert [e["tool"] for e in reply["evidence"]] == ["estimate_unmet_demand"]
        assert reply["grounding"]["confidence_overstated"]

    async def test_scenario_weights_carry_into_context_until_reset(self, service, fake_llm_factory):
        from app.agent.context import build_messages

        llm = fake_llm_factory(
            [
                ("tools", [("rank_expansion_candidates", {"region": "New England", "demand_momentum_weight": 0.6})]),
                ("text", "Re-ranked."),
                ("tools", [("rank_expansion_candidates", {"region": "New England"})]),
                ("text", "Back to defaults."),
            ]
        )
        sessions = InMemorySessionStore()
        runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), sessions)
        sid = (await runtime.run(None, "Weight growth twice as much", _noop)).session_id
        session = sessions.get_or_create(sid)
        assert session.scenario_weights == {"demand_momentum_weight": 0.6}
        assert "demand_momentum_weight=0.6" in build_messages("SYS", session, "Why BGR?", 50_000)[0]["content"]
        await runtime.run(sid, "Use the default weights", _noop)
        assert sessions.get_or_create(sid).scenario_weights == {}
