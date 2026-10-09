"""Model-start governance must not depend on a non-empty user message."""

from __future__ import annotations

from typing import Any

import pytest
from agent_rt import ModelRequest
from openbox_core.errors import GovernanceBlockedError
from openbox_core.runtime import OpenBoxRuntime

from openbox_agentrt import OpenBoxAgentRTOptions
from openbox_agentrt.runtime_builder import build_runtime
from tests.fakes import FakeProvider, build_governance, event_types, final, user


@pytest.mark.parametrize("messages", [[], [user("")]])
async def test_blank_or_absent_user_text_still_gates_model(messages: list[Any]) -> None:
    gov, core = build_governance(
        {"verdict": "allow"}, {"verdict": "block", "reason": "no model access"}
    )
    provider = FakeProvider(final("should not run"))

    with pytest.raises(GovernanceBlockedError):
        async with gov.run_scope(""):
            await gov.wrap_provider(provider).complete(ModelRequest(messages=messages))

    assert provider.requests == []
    starts = [p for p in core.payloads if p["event_type"] == "ActivityStarted"]
    assert len(starts) == 1
    assert starts[0]["activity_input"] == [{"prompt": ""}]
    assert event_types(core).count("ActivityCompleted") == 1
    assert event_types(core)[-1] == "WorkflowFailed"


def test_failed_instrumentation_installation_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[OpenBoxRuntime] = []
    original_close = OpenBoxRuntime.close

    def record_close(runtime: OpenBoxRuntime) -> None:
        closed.append(runtime)
        original_close(runtime)

    def fail_install(_runtime: OpenBoxRuntime) -> None:
        raise RuntimeError("instrumentation unavailable")

    monkeypatch.setattr(OpenBoxRuntime, "close", record_close)
    monkeypatch.setattr(OpenBoxRuntime, "install_instrumentation", fail_install)
    with pytest.raises(RuntimeError, match="instrumentation unavailable"):
        build_runtime(OpenBoxAgentRTOptions(api_url="https://core.test", api_key="obx_test_x"))
    assert len(closed) == 1
