"""``GovernedAgentLoop`` — an ``AgentLoop`` whose provider, registry, and executor are governed."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from agent_rt import AgentConfig, AgentRunResult, ModelMessage

from openbox_agentrt.message_extraction import extract_last_user_text, message_text

if TYPE_CHECKING:
    from openbox_agentrt.governance import OpenBoxAgentRTGovernance

__all__ = ["GovernedAgentLoop"]


class GovernedAgentLoop:
    """Runs an ``AgentLoop`` inside ``run_scope`` with every seam wrapped.

    The original loop is not mutated: a shallow copy carries the wrapped
    provider/registry/executor. Unknown attributes delegate to that copy.
    """

    def __init__(self, gov: OpenBoxAgentRTGovernance, loop: Any) -> None:
        self._gov = gov
        governed = copy.copy(loop)
        governed.provider = gov.wrap_provider(loop.provider)
        if getattr(loop, "tool_registry", None) is not None:
            governed.tool_registry = gov.wrap_tool_registry(loop.tool_registry)
        if getattr(loop, "tool_executor", None) is not None:
            governed.tool_executor = gov.wrap_tool_executor(loop.tool_executor)
        self._loop = governed

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._loop, attr)

    @staticmethod
    def _output(result: AgentRunResult) -> dict[str, Any]:
        final = result.final_response
        return {
            "result": message_text(final.message) if final is not None else None,
            "termination_reason": result.termination_reason,
            "turns": result.turns,
            "tool_calls": result.tool_calls,
            "total_tokens": result.total_tokens,
        }

    async def run(
        self,
        agent: AgentConfig,
        messages: list[ModelMessage] | tuple[ModelMessage, ...],
        **kwargs: Any,
    ) -> AgentRunResult:
        async with self._gov.run_scope(extract_last_user_text(messages)) as run:
            result: AgentRunResult = await self._loop.run(agent, messages, **kwargs)
            run.output = self._output(result)
            return result

    async def run_streaming(
        self,
        agent: AgentConfig,
        messages: list[ModelMessage] | tuple[ModelMessage, ...],
        on_event: Any,
        **kwargs: Any,
    ) -> AgentRunResult:
        async with self._gov.run_scope(extract_last_user_text(messages)) as run:
            result: AgentRunResult = await self._loop.run_streaming(
                agent, messages, on_event, **kwargs
            )
            run.output = self._output(result)
            return result
