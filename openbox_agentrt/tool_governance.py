"""Governed tool execution: ToolStarted (enforce) → tool (activity_scope) → ToolCompleted.

``AgentLoop`` executes registered tools through ``tool_registry.execute`` and
only falls back to ``tool_executor.execute`` for unregistered ones, so BOTH are
wrapped. Wrapping the execution seam (rather than ``ToolLifecycleHooks``) lets
the tool body run inside an ``activity_scope`` so instrumented HTTP/DB/file I/O
is attributed to the right activity.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from agent_rt import CancellationToken, ToolCall
from openbox_core.serialization import to_json_safe

from openbox_agentrt.lifecycle_events import (
    build_activity_completed,
    build_activity_started,
    enrich_activity_input,
)
from openbox_agentrt.run_state import RunState, require_run

if TYPE_CHECKING:
    from openbox_agentrt.governance import OpenBoxAgentRTGovernance

__all__ = ["GovernedToolExecutor", "GovernedToolRegistry", "govern_tool_call"]


async def _complete(
    gov: OpenBoxAgentRTGovernance,
    run: RunState,
    activity_id: str,
    tool: str,
    *,
    result: Any = None,
    error: BaseException | None = None,
) -> None:
    if not gov.options.send_tool_end_event:
        return
    await gov.telemetry(
        build_activity_completed(
            gov.identity(run),
            activity_id=activity_id,
            activity_type=tool,
            result=to_json_safe(result) if result is not None else None,
            error=error,
        )
    )


async def govern_tool_call(
    gov: OpenBoxAgentRTGovernance,
    call: ToolCall,
    invoke: Callable[[], Awaitable[Any]],
    *,
    side_effect: str | None = None,
) -> Any:
    """Run ``invoke`` under OpenBox governance for ``call``."""
    run = require_run()
    if call.name in gov.options.skip_tool_types:
        return await invoke()

    activity_id = str(uuid.uuid4())

    if gov.options.send_tool_start_event:
        activity_input = enrich_activity_input(
            [to_json_safe(dict(call.arguments))],
            tool_type=gov.options.tool_type_map.get(call.name),
            side_effect=side_effect,
        )

        async def close_orphan(exc: BaseException) -> None:
            await _complete(gov, run, activity_id, call.name, error=exc)

        await gov.gate(
            run,
            build_activity_started(
                gov.identity(run),
                activity_id=activity_id,
                activity_type=call.name,
                activity_input=activity_input,
            ),
            orphan_close=close_orphan,
        )

    try:
        with gov.activity_scope(run, activity_id, call.name):
            value = await invoke()
    except BaseException as exc:  # incl. cancellation by a run deadline
        # A tool BODY failure, not a governance block: record it, do not close the run.
        await _complete(gov, run, activity_id, call.name, error=exc)
        raise
    await _complete(gov, run, activity_id, call.name, result=value)
    return value


class GovernedToolExecutor:
    """Wraps a plain Agent-RT ``ToolExecutor``."""

    def __init__(self, gov: OpenBoxAgentRTGovernance, inner: Any) -> None:
        self._gov = gov
        self._inner = inner

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)

    async def execute(
        self, call: ToolCall, cancellation_token: CancellationToken | None = None
    ) -> Any:
        if cancellation_token is None:
            return await govern_tool_call(self._gov, call, lambda: self._inner.execute(call))
        return await govern_tool_call(
            self._gov, call, lambda: self._inner.execute(call, cancellation_token)
        )


class GovernedToolRegistry:
    """Proxy over a ``ToolRegistry``: every attribute is the registry's except ``execute``."""

    def __init__(self, gov: OpenBoxAgentRTGovernance, inner: Any) -> None:
        self._gov = gov
        self._inner = inner

    def __getattr__(self, attr: str) -> Any:
        return getattr(self._inner, attr)

    def _side_effect(self, name: str) -> str | None:
        try:
            return str(self._inner.get(name).definition.side_effect)
        except Exception:  # unknown/disabled tool: the registry raises its own error on execute
            return None

    async def execute(
        self,
        call: ToolCall,
        cancellation_token: CancellationToken | None = None,
        request_context: Mapping[str, Any] | None = None,
    ) -> Any:
        return await govern_tool_call(
            self._gov,
            call,
            lambda: self._inner.execute(call, cancellation_token, request_context),
            side_effect=self._side_effect(call.name),
        )
