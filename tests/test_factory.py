"""Factory startup options and standalone wrapper behavior (mirrors TS ``test/factory.test.ts``)."""

from __future__ import annotations

from typing import Any

import pytest
from agent_rt import AgentConfig, AgentLoop, ModelRequest, ModelSettings, ToolCall, ToolRegistry
from openbox_core.client import EvaluationClient
from openbox_core.runtime import OpenBoxRuntime

from openbox_agentrt import OpenBoxAgentRTOptions, create_openbox_agentrt
from openbox_agentrt.sdk_metadata import SDK_ENGINE, SDK_LANGUAGE
from tests.fakes import (
    FakeProvider,
    build_governance,
    event_types,
    final,
    make_registry,
    tool_turn,
    user,
)

AGENT = AgentConfig(name="a", instructions="i", model=ModelSettings(model="m"))


async def test_unknown_option_is_rejected() -> None:
    with pytest.raises(TypeError, match="unknown option"):
        await create_openbox_agentrt(
            api_url="https://core.test", api_key="obx_test_x", validate=False, bogus=True
        )


@pytest.mark.parametrize("validate", [True, False])
async def test_validate_controls_the_startup_api_key_check(
    monkeypatch: pytest.MonkeyPatch, validate: bool
) -> None:
    # Never patch process-global HTTP/DB/file hooks from a unit test.
    monkeypatch.setattr(OpenBoxRuntime, "install_instrumentation", lambda self: None)
    checks: list[str] = []

    async def record(self: Any) -> bool:
        checks.append("validated")
        return True

    monkeypatch.setattr(EvaluationClient, "avalidate_api_key", record)
    governance = await create_openbox_agentrt(
        api_url="https://core.test", api_key="obx_test_x", validate=validate
    )
    await governance.aclose()
    assert checks == (["validated"] if validate else [])


def test_sdk_identity() -> None:
    assert (SDK_ENGINE, SDK_LANGUAGE) == ("agentrt", "python")


def test_option_defaults() -> None:
    o = OpenBoxAgentRTOptions()
    assert o.task_queue == "agentrt" and o.on_api_error == "fail_open"
    assert o.send_tool_start_event and o.send_llm_end_event


async def test_wrappers_are_usable_standalone_inside_run_scope() -> None:
    gov, core = build_governance()
    calls: list[dict] = []
    registry = gov.wrap_tool_registry(make_registry(calls))

    class Executor:
        async def execute(self, call: ToolCall) -> int:
            return 42

    executor = gov.wrap_tool_executor(Executor())  # type: ignore[arg-type]
    provider = gov.wrap_provider(FakeProvider(final("x")))

    async with gov.run_scope("hello") as run:
        await registry.execute(ToolCall(id="1", name="echo", arguments={"text": "a"}))
        assert await executor.execute(ToolCall(id="2", name="other", arguments={})) == 42
        await provider.complete(ModelRequest(messages=[user("hello")]))
        run.output = {"done": True}

    assert calls == [{"text": "a"}]
    done = core.payloads[-1]
    assert done["event_type"] == "WorkflowCompleted"
    assert done["workflow_output"] == {"done": True}


async def test_skip_tool_types_bypasses_tool_governance() -> None:
    gov, core = build_governance(skip_tool_types={"echo"})
    calls: list[dict] = []
    provider = FakeProvider(tool_turn("echo", {"text": "a"}), final("d"))
    await gov.govern_loop(AgentLoop(provider, tool_registry=make_registry(calls))).run(
        AGENT, [user("go")]
    )
    assert len(calls) == 1
    assert not any(p.get("activity_type") == "echo" for p in core.payloads)


async def test_send_flags_disable_their_events() -> None:
    gov, core = build_governance(
        send_run_start_event=False, send_run_end_event=False, send_llm_end_event=False
    )
    await gov.govern_loop(AgentLoop(FakeProvider(final("d")))).run(AGENT, [user("go")])
    types = event_types(core)
    assert "WorkflowStarted" not in types
    assert "WorkflowCompleted" not in types
    assert "ActivityCompleted" not in types


async def test_proxied_registry_keeps_the_rest_of_the_tool_registry_api() -> None:
    gov, _ = build_governance()
    raw: ToolRegistry = make_registry([])
    wrapped = gov.wrap_tool_registry(raw)
    assert [d.name for d in wrapped.definitions()] == ["echo"]
    assert wrapped.version == raw.version
