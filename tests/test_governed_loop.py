"""End-to-end: a real ``AgentLoop`` governed against an in-process FakeCore."""

from __future__ import annotations

import asyncio

import pytest
from agent_rt import (
    AgentConfig,
    AgentLoop,
    ModelRequest,
    ModelSettings,
    ToolDefinition,
    ToolRegistry,
)
from openbox_core.errors import GovernanceBlockedError

from tests.fakes import (
    FakeProvider,
    build_governance,
    event_types,
    final,
    make_registry,
    tool_turn,
    user,
)

AGENT = AgentConfig(name="a", instructions="be brief", model=ModelSettings(model="fake-model"))


async def test_allowed_run_emits_full_lifecycle_in_order() -> None:
    gov, core = build_governance(tool_type_map={"echo": "http"})
    calls: list[dict] = []
    provider = FakeProvider(tool_turn("echo", {"text": "hi"}), final("done"))
    registry = make_registry(calls)
    loop = gov.govern_loop(AgentLoop(provider, tool_registry=registry))

    result = await loop.run(AGENT, [user("hello")])

    assert result.termination_reason == "completed"
    assert calls == [{"text": "hi"}]
    types = event_types(core)
    assert types[:3] == ["WorkflowStarted", "SignalReceived", "ActivityStarted"]
    assert core.payloads[2]["activity_id"].endswith("-pre")
    assert types.count("ActivityStarted") == 3  # pre-screen + tool + 2nd llm call
    assert types[-1] == "WorkflowCompleted"
    assert core.payloads[-1]["workflow_output"]["result"] == "done"
    tool_started = next(
        p
        for p in core.payloads
        if p["event_type"] == "ActivityStarted" and p["activity_type"] == "echo"
    )
    assert tool_started["activity_input"][-1] == {
        "__openbox": {"tool_type": "http", "side_effect": "read"}
    }


async def test_pre_screen_is_reused_by_first_model_call() -> None:
    gov, core = build_governance()
    loop = gov.govern_loop(AgentLoop(FakeProvider(final("ok"))))
    await loop.run(AGENT, [user("hello")])
    llm_started = [
        p
        for p in core.payloads
        if p["event_type"] == "ActivityStarted" and p["activity_type"] == "llm_call"
    ]
    assert len(llm_started) == 1  # no duplicate evaluation of the same prompt
    completed = next(p for p in core.payloads if p["event_type"] == "ActivityCompleted")
    assert completed["activity_id"] == llm_started[0]["activity_id"]
    assert completed["result"]["total_tokens"] == 5


async def test_block_on_signal_never_calls_model_and_closes_workflow() -> None:
    gov, core = build_governance({"verdict": "allow"}, {"verdict": "block", "reason": "no"})
    provider = FakeProvider(final("should not run"))
    loop = gov.govern_loop(AgentLoop(provider))

    with pytest.raises(GovernanceBlockedError):
        await loop.run(AGENT, [user("bad")])

    assert provider.requests == []
    types = event_types(core)
    assert types[-1] == "WorkflowFailed"
    assert "WorkflowCompleted" not in types
    assert types.count("WorkflowFailed") == 1


async def test_block_on_pre_screen_stops_the_run() -> None:
    gov, core = build_governance(
        {"verdict": "allow"}, {"verdict": "allow"}, {"verdict": "block", "reason": "pre"}
    )
    provider = FakeProvider(final("nope"))
    loop = gov.govern_loop(AgentLoop(provider))
    with pytest.raises(GovernanceBlockedError):
        await loop.run(AGENT, [user("x")])
    assert provider.requests == []
    assert event_types(core)[-1] == "WorkflowFailed"


async def test_blocked_tool_never_executes_and_orphan_start_is_closed() -> None:
    # Queue: WorkflowStarted, Signal, pre-screen, LLMCompleted (all allow); tool start -> block.
    allow = {"verdict": "allow"}
    gov, core = build_governance(
        allow,
        allow,
        allow,
        allow,
        {"verdict": "block", "reason": "tool denied"},
    )
    calls: list[dict] = []
    provider = FakeProvider(tool_turn("echo", {"text": "hi"}), final("never"))
    loop = gov.govern_loop(AgentLoop(provider, tool_registry=make_registry(calls)))

    with pytest.raises(GovernanceBlockedError):
        await loop.run(AGENT, [user("go")])

    assert calls == []
    tool_events = [p for p in core.payloads if p.get("activity_type") == "echo"]
    assert [p["event_type"] for p in tool_events] == ["ActivityStarted", "ActivityCompleted"]
    assert tool_events[1]["error"]["type"] == "GovernanceBlockedError"
    assert tool_events[0]["activity_id"] == tool_events[1]["activity_id"]
    assert event_types(core)[-1] == "WorkflowFailed"


async def test_tool_body_failure_is_recorded_without_governance_closure() -> None:
    gov, core = build_governance()
    registry = ToolRegistry()

    async def boom(arguments, token):
        raise RuntimeError("kaboom")

    registry.register(
        ToolDefinition(name="boom", description="d", input_schema={"type": "object"}),
        handler=boom,
    )
    loop = gov.govern_loop(
        AgentLoop(FakeProvider(tool_turn("boom", {}), final("x")), tool_registry=registry)
    )
    with pytest.raises(RuntimeError):
        await loop.run(AGENT, [user("go")])
    done = [p for p in core.payloads if p.get("activity_type") == "boom"][-1]
    assert done["event_type"] == "ActivityCompleted"
    assert done["error"] == {"type": "RuntimeError", "message": "kaboom"}


async def test_executor_path_is_governed_too() -> None:
    gov, core = build_governance()

    class Executor:
        async def execute(self, call, cancellation_token=None):
            return {"ok": True}

    loop = gov.govern_loop(
        AgentLoop(FakeProvider(tool_turn("ext", {}), final("fin")), tool_executor=Executor())
    )
    await loop.run(AGENT, [user("go")])
    assert any(p.get("activity_type") == "ext" for p in core.payloads)


async def test_guardrail_redaction_reaches_the_model() -> None:
    redacting = {
        "verdict": "allow",
        "guardrails_result": {"redacted_input": [{"prompt": "my SSN is [REDACTED]"}]},
    }
    # Queue order: WorkflowStarted, SignalReceived, pre-screen (redacting).
    gov, _core = build_governance({"verdict": "allow"}, {"verdict": "allow"}, redacting)
    provider = FakeProvider(final("ok"))
    await gov.govern_loop(AgentLoop(provider)).run(AGENT, [user("my SSN is 123-45-6789")])
    sent = provider.requests[0].messages[-1].content[0].text
    assert sent == "my SSN is [REDACTED]"


async def test_require_approval_without_poller_fails_safe() -> None:
    from openbox_core.errors import ApprovalRejectedError

    gov, _ = build_governance(
        {"verdict": "allow"}, {"verdict": "allow"}, {"verdict": "require_approval"}
    )
    provider = FakeProvider(final("never"))
    with pytest.raises(ApprovalRejectedError):
        await gov.govern_loop(AgentLoop(provider)).run(AGENT, [user("x")])
    assert provider.requests == []


async def test_streaming_run_is_governed() -> None:
    from tests.fakes import FakeStreamingProvider

    gov, core = build_governance()
    seen: list[str] = []

    async def on_event(event) -> None:
        seen.append(event.type)

    loop = gov.govern_loop(AgentLoop(FakeStreamingProvider(final("streamed"))))
    result = await loop.run_streaming(AGENT, [user("hi")], on_event)
    assert result.termination_reason == "completed"
    assert "text_delta" in seen
    assert event_types(core)[-1] == "WorkflowCompleted"
    assert any(p["event_type"] == "ActivityCompleted" for p in core.payloads)


async def test_concurrent_runs_do_not_share_identity() -> None:
    gov, core = build_governance()
    loops = [gov.govern_loop(AgentLoop(FakeProvider(final(f"r{i}")))) for i in range(5)]
    await asyncio.gather(*(lp.run(AGENT, [user(f"q{i}")]) for i, lp in enumerate(loops)))
    started = [p for p in core.payloads if p["event_type"] == "WorkflowStarted"]
    assert len({p["workflow_id"] for p in started}) == 5
    for p in started:
        mine = [q for q in core.payloads if q["workflow_id"] == p["workflow_id"]]
        assert mine[-1]["event_type"] == "WorkflowCompleted"


async def test_wrapped_provider_outside_run_fails_closed() -> None:
    gov, _ = build_governance()
    provider = gov.wrap_provider(FakeProvider(final("x")))
    with pytest.raises(RuntimeError, match="no active run"):
        await provider.complete(ModelRequest(messages=[user("x")]))


async def test_original_loop_is_not_mutated() -> None:
    gov, _ = build_governance()
    raw = FakeProvider(final("x"))
    loop = AgentLoop(raw)
    gov.govern_loop(loop)
    assert loop.provider is raw


async def test_provider_failure_is_recorded_and_closes_the_run() -> None:
    gov, core = build_governance()

    class Failing:
        name = "failing"

        async def complete(self, request):
            raise ConnectionError("upstream down")

    with pytest.raises(ConnectionError):
        await gov.govern_loop(AgentLoop(Failing())).run(AGENT, [user("hi")])
    types = event_types(core)
    done = next(p for p in core.payloads if p["event_type"] == "ActivityCompleted")
    assert done["error"]["type"] == "ConnectionError"
    assert types[-1] == "WorkflowFailed"
