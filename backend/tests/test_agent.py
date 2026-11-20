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
