"""Shared fakes: scripted Agent-RT providers and a governance wired to a FakeCore."""

from __future__ import annotations

from typing import Any

from agent_rt import (
    ContentPart,
    ModelMessage,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
    ToolCall,
    ToolDefinition,
    ToolRegistry,
)
from openbox_core.adapters.base import CoreAdapter
from openbox_core.config import OpenBoxConfig
from openbox_core.conformance.fake_core import FakeCore, fake_client
from openbox_core.context import ContextStore
from openbox_core.runtime import OpenBoxRuntime

from openbox_agentrt import OpenBoxAgentRTGovernance, OpenBoxAgentRTOptions


def user(text: str) -> ModelMessage:
    return ModelMessage(role="user", content=(ContentPart(type="text", text=text),))


def final(text: str) -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(role="assistant", content=(ContentPart(type="text", text=text),)),
        model="fake-model",
        usage=ModelUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        finish_reason="stop",
    )


def tool_turn(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> ModelResponse:
    return ModelResponse(
        message=ModelMessage(
            role="assistant",
            content=(),
            tool_calls=(ToolCall(id=call_id, name=name, arguments=arguments),),
        ),
        model="fake-model",
        finish_reason="tool_calls",
    )


class FakeProvider:
    name = "fake"

    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)


class FakeStreamingProvider(FakeProvider):
    async def stream(self, request: Any):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        response = self.responses.pop(0)
        yield ModelStreamEvent(type="text_delta", text="hi")
        yield ModelStreamEvent(type="completed", response=response)


def make_registry(calls: list[dict]) -> ToolRegistry:
    registry = ToolRegistry()

    async def echo(arguments, token):
        calls.append(dict(arguments))
        return {"echo": arguments["text"]}

    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            side_effect="read",
        ),
        handler=echo,
    )
    return registry


def build_governance(
    *responses: dict[str, Any], adapter: Any = None, **option_overrides: Any
) -> tuple[OpenBoxAgentRTGovernance, FakeCore]:
    """Governance whose Core is an in-process FakeCore (queued verdicts, payload capture)."""
    core = FakeCore(*responses)
    config = OpenBoxConfig.resolve(
        env_prefix="OPENBOX_AGENTRT",
        environ={},
        api_url="https://core.test",
        api_key="obx_test_agentrt",
        agent_name="TestAgent",
    )
    runtime = OpenBoxRuntime(
        config, adapter or CoreAdapter(), client=fake_client(core), context_store=ContextStore()
    )
    options = OpenBoxAgentRTOptions(agent_name="TestAgent", **option_overrides)
    return OpenBoxAgentRTGovernance(options, runtime=runtime), core


def event_types(core: FakeCore) -> list[str]:
    return [str(p["event_type"]) for p in core.payloads]
