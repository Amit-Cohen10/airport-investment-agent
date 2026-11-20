"""API tests with a scripted LLM (no network)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import FakeLLM


def _client(script):
    return TestClient(create_app(llm=FakeLLM(script)))


def test_health_and_deterministic_rankings_without_llm_call():
    with _client([]) as client:
        health = client.get("/api/health").json()
        assert health["status"] == "ok" and "traffic" in health["data_periods"]
        ranking = client.get("/api/rankings", params={"region": "New England", "top_n": 3}).json()
        assert len(ranking["data"]["ranking"]) == 3
        assert client.get("/api/rankings", params={"region": "Atlantis"}).status_code == 404


def test_chat_sync_returns_answer_evidence_sources():
    script = [("tools", [("compare_congestion", {"airports": ["LAX", "SNA"]})]), ("text", "LAX is more congested.")]
    with _client(script) as client:
        body = client.post("/api/chat/sync", json={"message": "Compare LA and Santa Ana congestion"}).json()
        assert body["answer"] == "LAX is more congested."
        assert body["evidence"][0]["tool"] == "compare_congestion"
        assert body["sources"] and body["session_id"]


def test_chat_stream_emits_progress_then_answer():
    script = [("tools", [("analyze_route_mix", {"airport": "ANC"})]), ("text", "Done.")]
    with _client(script) as client, client.stream("POST", "/api/chat", json={"message": "ANC long haul?"}) as resp:
        events = [line.split(":", 1)[1].strip() for line in resp.iter_lines() if line.startswith("event:")]
    assert events[0] == "session" and "tool_start" in events and events[-1] == "answer"


def test_validation_rejects_empty_message():
    with _client([]) as client:
        assert client.post("/api/chat/sync", json={"message": ""}).status_code == 422


def test_answer_payload_is_json_serializable():
    script = [("tools", [("estimate_unmet_demand", {"airport": "SFO"})]), ("text", "ok")]
    with _client(script) as client:
        body = client.post("/api/chat/sync", json={"message": "SFO unmet demand"}).json()
        json.dumps(body)
        assert body["evidence"][0]["ui"]
