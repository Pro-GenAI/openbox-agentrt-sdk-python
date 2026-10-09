"""Regression tests: no orphan activity rows, cancellation, streaming deadlines,
best-effort telemetry, and prompt-matched pre-screen reuse. Keep in lockstep
with the TypeScript ``test/lifecycle-hardening.test.ts``."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from agent_rt import (
    AgentConfig,
    AgentLoop,
    AgentRunLimits,
    CancellationToken,
    ModelRequest,
    ModelSettings,
    ToolDefinition,
    ToolRegistry,
)
from openbox_core.errors import GovernanceAPIError, GovernanceBlockedError
from openbox_core.runtime import OpenBoxRuntime

from openbox_agentrt import GovernedModelProvider, OpenBoxAgentRTOptions
from openbox_agentrt.runtime_builder import build_runtime
from tests.fakes import (
    FakeProvider,
    FakeStreamingProvider,
    build_governance,
    event_types,
    final,
    tool_turn,
    user,
)

AGENT = AgentConfig(name="a", instructions="be brief", model=ModelSettings(model="fake-model"))
ALLOW = {"verdict": "allow"}


def rows(core: Any, activity_type: str) -> list[tuple[str, str]]:
    return [
        (p["event_type"], p["activity_id"])
        for p in core.payloads
        if p.get("activity_type") == activity_type
    ]


def assert_every_start_closed(core: Any) -> None:
    started = {p["activity_id"] for p in core.payloads if p["event_type"] == "ActivityStarted"}
    done = {p["activity_id"] for p in core.payloads if p["event_type"] == "ActivityCompleted"}
    assert started <= done, f"orphan start rows: {started - done}"


async def _noop(event: Any) -> None:
    return None


async def test_streaming_run_with_deadline_is_governed() -> None:
    # AgentLoop wraps each stream step in its own task under a deadline; the
    # activity binding must not span a ``yield`` (it used to raise ValueError).
    gov, core = build_governance()
    loop = gov.govern_loop(AgentLoop(FakeStreamingProvider(final("s"))))
    result = await loop.run_streaming(
        AGENT, [user("hi")], _noop, limits=AgentRunLimits(timeout_seconds=30)
    )
    assert result.termination_reason == "completed"
    assert event_types(core)[-1] == "WorkflowCompleted"
    assert_every_start_closed(core)


async def test_tool_cancelled_by_run_deadline_closes_its_row() -> None:
    gov, core = build_governance()
    registry = ToolRegistry()

    async def slow(arguments: Any, token: Any) -> int:
        await asyncio.sleep(5)
        return 1

    registry.register(
        ToolDefinition(name="slow", description="d", input_schema={"type": "object"}),
        handler=slow,
    )
    loop = gov.govern_loop(
        AgentLoop(FakeProvider(tool_turn("slow", {}), final("x")), tool_registry=registry)
    )
    result = await loop.run(AGENT, [user("go")], limits=AgentRunLimits(timeout_seconds=0.2))
    assert result.termination_reason == "timeout"
    slow_rows = [p for p in core.payloads if p.get("activity_type") == "slow"]
    assert [p["event_type"] for p in slow_rows] == ["ActivityStarted", "ActivityCompleted"]
    assert slow_rows[1]["error"]["type"] == "CancelledError"
    assert_every_start_closed(core)


async def test_cancelled_run_sends_workflow_failed() -> None:
    gov, core = build_governance()
    entered = asyncio.Event()

    class Hanging:
        name = "hanging"

        async def complete(self, request: Any) -> Any:
            entered.set()
            await asyncio.sleep(5)

    task = asyncio.create_task(gov.govern_loop(AgentLoop(Hanging())).run(AGENT, [user("hi")]))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert event_types(core)[-1] == "WorkflowFailed"
    assert_every_start_closed(core)


async def test_cancellation_token_closes_the_pending_model_row() -> None:
    gov, core = build_governance()
    token = CancellationToken()

    class Hanging:
        name = "hanging"

        async def complete(self, request: Any) -> Any:
            asyncio.get_running_loop().call_later(0.01, token.cancel)
            await asyncio.sleep(5)

    result = await gov.govern_loop(AgentLoop(Hanging())).run(
        AGENT, [user("hi")], cancellation_token=token
    )
    assert result.termination_reason == "cancelled"
    assert [t for t, _ in rows(core, "llm_call")] == ["ActivityStarted", "ActivityCompleted"]
    done = next(p for p in core.payloads if p["event_type"] == "ActivityCompleted")
    assert done["error"]["type"] == "CancelledError"
    assert event_types(core)[-1] == "WorkflowCompleted"


async def test_pre_screen_is_reused_with_prior_chat_history() -> None:
    gov, core = build_governance()
    history = [user("earlier"), final("earlier answer").message, user("now")]
    await gov.govern_loop(AgentLoop(FakeProvider(final("ok")))).run(AGENT, history)
    llm = rows(core, "llm_call")
    assert [t for t, _ in llm] == ["ActivityStarted", "ActivityCompleted"]
    assert llm[0][1] == llm[1][1]  # the pre-screen row is the one completed


async def test_blocked_model_call_closes_its_orphan_row() -> None:
    # Queue: WorkflowStarted, Signal, pre-screen, LLMCompleted, tool start,
    # tool completed (all allow); the second LLM start is blocked.
    gov, core = build_governance(
        ALLOW, ALLOW, ALLOW, ALLOW, ALLOW, ALLOW, {"verdict": "block", "reason": "no"}
    )
    registry = ToolRegistry()

    async def echo(arguments: Any, token: Any) -> str:
        return "e"

    registry.register(
        ToolDefinition(name="echo", description="d", input_schema={"type": "object"}),
        handler=echo,
    )
    provider = FakeProvider(tool_turn("echo", {}), final("never"))
    with pytest.raises(GovernanceBlockedError):
        await gov.govern_loop(AgentLoop(provider, tool_registry=registry)).run(AGENT, [user("go")])
    assert len(provider.requests) == 1
    second = rows(core, "llm_call")[-2:]
    assert [t for t, _ in second] == ["ActivityStarted", "ActivityCompleted"]
    assert second[0][1] == second[1][1]
    assert event_types(core).count("WorkflowFailed") == 1
    assert_every_start_closed(core)


async def test_blocked_pre_screen_closes_its_row() -> None:
    gov, core = build_governance(ALLOW, ALLOW, {"verdict": "block", "reason": "pre"})
    with pytest.raises(GovernanceBlockedError):
        await gov.govern_loop(AgentLoop(FakeProvider(final("x")))).run(AGENT, [user("x")])
    assert [t for t, _ in rows(core, "llm_call")] == ["ActivityStarted", "ActivityCompleted"]
    assert_every_start_closed(core)


async def test_unconsumed_pre_screen_is_closed_at_run_end() -> None:
    gov, core = build_governance()
    async with gov.run_scope("hello"):
        pass  # no model call consumes the pre-screen
    assert [t for t, _ in rows(core, "llm_call")] == ["ActivityStarted", "ActivityCompleted"]
    assert event_types(core)[-1] == "WorkflowCompleted"


async def test_unconsumed_pre_screen_closes_before_workflow_failed() -> None:
    # Queue: WorkflowStarted, Signal, pre-screen (allow); the model start for a
    # DIFFERENT prompt is blocked, so the pre-screen row is never consumed.
    gov, core = build_governance(ALLOW, ALLOW, ALLOW, {"verdict": "block", "reason": "no"})
    provider = gov.wrap_provider(FakeProvider(final("never")))
    with pytest.raises(GovernanceBlockedError):
        async with gov.run_scope("hello"):
            await provider.complete(ModelRequest(messages=[user("other")]))
    assert event_types(core)[-1] == "WorkflowFailed"
    assert event_types(core).count("WorkflowFailed") == 1
    assert_every_start_closed(core)


async def test_failed_completion_telemetry_never_discards_a_tool_result() -> None:
    gov, core = build_governance()
    calls: list[str] = []
    registry = ToolRegistry()

    async def write(arguments: Any, token: Any) -> str:
        calls.append("ran")
        return "written"

    registry.register(
        ToolDefinition(name="write", description="d", input_schema={"type": "object"}),
        handler=write,
    )
    real = gov.runtime.gate.aevaluate

    async def flaky(event: Any) -> Any:  # Core unreachable under fail_closed
        if event.payload.get("event_type") == "ActivityCompleted":
            raise GovernanceAPIError("unreachable")
        return await real(event)

    gov.runtime.gate.aevaluate = flaky  # type: ignore[method-assign]
    loop = gov.govern_loop(
        AgentLoop(FakeProvider(tool_turn("write", {}), final("done")), tool_registry=registry)
    )
    result = await loop.run(AGENT, [user("go")])
    assert result.termination_reason == "completed"
    assert calls == ["ran"]
    assert event_types(core)[-1] == "WorkflowCompleted"


async def test_plain_governed_provider_never_exposes_an_ungoverned_stream() -> None:
    gov, _ = build_governance()
    wrapped = GovernedModelProvider(gov, FakeStreamingProvider(final("x")))
    assert not hasattr(wrapped, "stream")
    assert gov.wrap_provider(FakeStreamingProvider(final("x"))).stream is not None


def test_approval_poller_shares_the_runtime_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # Never patch process-global HTTP/DB/file hooks from a unit test.
    monkeypatch.setattr(OpenBoxRuntime, "install_instrumentation", lambda self: None)
    runtime = build_runtime(
        OpenBoxAgentRTOptions(api_url="https://core.test", api_key="obx_test_agentrt")
    )
    try:
        poller = runtime.adapter._poller  # type: ignore[attr-defined]
        assert poller is not None
        assert poller._client is runtime.client  # one client: identity + close() shared
        assert poller._interval == runtime.config.hitl.poll_interval_ms / 1000
    finally:
        runtime.close()


async def test_stream_closed_early_closes_its_row() -> None:
    gov, core = build_governance()
    provider = gov.wrap_provider(FakeStreamingProvider(final("s")))
    async with gov.run_scope("hi"):
        stream = provider.stream(ModelRequest(messages=[user("hi")]))
        first = await stream.__anext__()
        assert first.type == "text_delta"
        await stream.aclose()  # consumer stops before ``completed``
    done = [p for p in core.payloads if p["event_type"] == "ActivityCompleted"]
    assert done[0]["error"] == {
        "type": "Error",
        "message": "model stream closed before completion",
    }
    assert_every_start_closed(core)
