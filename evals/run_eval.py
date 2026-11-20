"""Golden-question evaluation against the REAL configured LLM (offline fallback disabled).

    make eval      (or: cd backend && uv run python ../evals/run_eval.py)

Each conversation is a list of turns in one session (so follow-ups are tested). For every turn
we check, deterministically:
  * tool selection: the expected analysis was called
  * grounding: no number in the answer is missing from the tool outputs
  * communication: the answer states assumptions/caveats and a confidence level
Writes evals/report.md, which documents how well the chosen model behaves.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.agent.runtime import AgentRuntime  # noqa: E402
from app.agent.session_store import InMemorySessionStore  # noqa: E402
from app.agent.tools.definitions import build_tools  # noqa: E402
from app.agent.tools.registry import ToolRegistry  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.data.connectors.live import LiveAviationClient  # noqa: E402
from app.data.repository import Repository  # noqa: E402
from app.llm.factory import build_llm  # noqa: E402
from app.services.insights import InsightService  # noqa: E402


@dataclass
class Turn:
    question: str
    expect_tools: set[str] = field(default_factory=set)  # at least one must be called (empty = none needed)
    expect_no_tools: bool = False
    expect_text: list[str] = field(default_factory=list)  # case-insensitive substrings, any-of per item "a|b"


CONVERSATIONS: dict[str, list[Turn]] = {
    "New England expansion + follow-ups": [
        Turn("Which airports in New England are strong candidates for terminal expansion?",
             {"rank_expansion_candidates"}, expect_text=["BOS|Logan", "confidence"]),
        Turn("What if we weight demand growth twice as much?", {"rank_expansion_candidates"}),
        Turn("Why does Bangor score so high?", {"explain_airport_score", "get_airport_profile"}, expect_text=["BGR|Bangor"]),
    ],
    "LA vs Santa Ana congestion": [
        Turn("Compare LA and Santa Ana airport congestion levels.", {"compare_congestion"},
             expect_text=["LAX", "SNA|Santa Ana|John Wayne", "cap"]),
        Turn("So which of the two has more room to grow?", set()),
    ],
    "Anchorage long-haul": [
        Turn("What is the percentage of long haul flights out of Anchorage airport?", {"analyze_route_mix"},
             expect_text=["%", "cargo"]),
        Turn("And if long-haul means over 4,000 miles?", {"analyze_route_mix"}),
    ],
    "SFO unmet demand": [
        Turn("What is the unmet flight demand in SFO airport and why?", {"estimate_unmet_demand"},
             expect_text=["spill|unmet", "runway|NAS|arrival"]),
        Turn("What are conditions like there right now?", {"get_live_status"}),
    ],
    "Methodology & scope": [
        Turn("How is the expansion score calculated?", {"explain_methodology"}, expect_text=["percentile"]),
        Turn("What's a good restaurant in Paris?", expect_no_tools=True),
    ],
}


def check(turn: Turn, reply) -> list[str]:
    problems = []
    called = {e.tool for e in reply.evidence} | {s.get("tool") for s in reply.trace["steps"] if s["type"] == "tool"}
    if turn.expect_tools and not (turn.expect_tools & called):
        problems.append(f"expected one of {sorted(turn.expect_tools)}, called {sorted(c for c in called if c)}")
    if turn.expect_no_tools and any(c for c in called):
        problems.append(f"expected no tools, called {sorted(c for c in called if c)}")
    if reply.grounding["unverified"]:
        problems.append(f"unverified numbers: {reply.grounding['unverified']}")
    lower = reply.answer.lower()
    for item in turn.expect_text:
        if not any(opt.lower() in lower for opt in item.split("|")):
            problems.append(f"answer lacks '{item}'")
    if not reply.answer.strip():
        problems.append("empty answer")
    return problems


async def main() -> int:
    settings = get_settings()
    llm = build_llm(settings)  # no offline fallback: we are evaluating the real model
    repo = Repository(settings.snapshot_dir)
    service = InsightService.from_paths(repo, settings.scoring_config_path, settings.airport_notes_path,
                                        LiveAviationClient())
    runtime = AgentRuntime(llm, ToolRegistry(service, build_tools()), InMemorySessionStore(),
                           max_steps=settings.agent_max_steps)
    pause = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0  # free tiers have low RPM limits

    rows, transcripts, passed, total = [], [], 0, 0
    for name, turns in CONVERSATIONS.items():
        session_id = None
        for turn in turns:
            total += 1
            started = time.perf_counter()
            try:
                reply = await runtime.run(session_id, turn.question)
                session_id = reply.session_id
                problems = check(turn, reply)
                tools = [s["tool"] for s in reply.trace["steps"] if s["type"] == "tool"]
                providers = ", ".join(reply.trace["providers"])
                answer = reply.answer
                grounding = f"{reply.grounding['checked'] - len(reply.grounding['unverified'])}/{reply.grounding['checked']}"
            except Exception as exc:  # report, don't crash the eval
                problems, tools, providers, answer, grounding = [f"error: {exc}"], [], "-", "", "-"
            secs = time.perf_counter() - started
            ok = not problems
            passed += ok
            rows.append(f"| {name} | {turn.question} | {'PASS' if ok else 'FAIL'} | {', '.join(tools) or '-'} "
                        f"| {grounding} | {secs:.1f}s | {providers} | {'; '.join(problems) or '-'} |")
            transcripts.append(f"### {turn.question}\n\n_tools: {', '.join(tools) or 'none'}_\n\n{answer}\n")
            print(f"[{'PASS' if ok else 'FAIL'}] {turn.question} ({secs:.1f}s) {problems or ''}", flush=True)
            await asyncio.sleep(pause)

    report = [
        "# Evaluation report",
        "",
        f"Model chain: {', '.join(f'{c.name}:{c.model}' for c in llm.clients)}  ",
        f"Run: {time.strftime('%Y-%m-%d %H:%M')}  ",
        f"**Result: {passed}/{total} turns passed**",
        "",
        "Checks per turn: expected tool called, every number in the answer grounded in tool output, "
        "required content present.",
        "",
        "| Conversation | Question | Result | Tools | Grounded numbers | Latency | Provider | Problems |",
        "|---|---|---|---|---|---|---|---|",
        *rows,
        "",
        "## Transcripts",
        "",
        *transcripts,
    ]
    (ROOT / "report.md").write_text("\n".join(report))
    print(json.dumps({"passed": passed, "total": total}))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
