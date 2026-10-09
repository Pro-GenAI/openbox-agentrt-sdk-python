"""``OpenBoxAgentRTGovernance`` — the governance core for Agent-RT.

Owns exactly ONE ``OpenBoxRuntime`` (private ``ContextStore``) and exposes:

    run_scope(prompt)       → WorkflowStarted → SignalReceived (enforce)
                              → pre-screen (enforce) … WorkflowCompleted/Failed
    wrap_provider(p)        → LLMStarted (enforce + redaction) → model → LLMCompleted
    wrap_tool_registry(r)   → ToolStarted (enforce) → tool (activity_scope) → ToolCompleted
    wrap_tool_executor(e)   → same, for a plain ``ToolExecutor``
    govern_loop(loop)       → all of the above bundled around an ``AgentLoop``

Agent-RT is async-only, so every enforcement point can await a real HITL
approval (``ApprovalPoller``); there is no fail-shut sync path.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from openbox_core.context import activity_scope
from openbox_core.contracts.context import ActivityContext
from openbox_core.contracts.events import EventEnvelope
from openbox_core.contracts.results import EvaluationResult

from openbox_agentrt.lifecycle_events import (
    Identity,
    build_activity_completed,
    build_activity_started,
    build_signal_received,
    build_workflow_completed,
    build_workflow_failed,
    build_workflow_started,
)
from openbox_agentrt.options import OpenBoxAgentRTOptions
from openbox_agentrt.run_state import RunState, _current_run
from openbox_agentrt.runtime_builder import build_runtime

if TYPE_CHECKING:
    from agent_rt import ModelProvider, ToolExecutor, ToolRegistry
    from openbox_core.runtime import OpenBoxRuntime

    from openbox_agentrt.loop import GovernedAgentLoop

_logger = logging.getLogger("openbox_agentrt")

__all__ = ["OpenBoxAgentRTGovernance"]


class OpenBoxAgentRTGovernance:
    """OpenBox governance for Agent-RT providers, tools, and agent loops."""

    def __init__(
        self,
        options: OpenBoxAgentRTOptions | None = None,
        *,
        runtime: OpenBoxRuntime | None = None,
    ) -> None:
        self._options = options or OpenBoxAgentRTOptions()
        # An injected runtime WINS (it owns its client, identity, and adapter).
        self._runtime = runtime if runtime is not None else build_runtime(self._options)
        self._workflow_type = self._options.agent_name or "AgentRTRun"

    @property
    def options(self) -> OpenBoxAgentRTOptions:
        return self._options

    @property
    def runtime(self) -> OpenBoxRuntime:
        return self._runtime

    def close(self) -> None:
        """Release runtime resources (HTTP clients, instrumentation). Idempotent."""
        self._runtime.close()

    async def aclose(self) -> None:
        """Async :meth:`close`."""
        await self._runtime.aclose()

    # ─── Identity + gates ───────────────────────────────────────────

    def identity(self, run: RunState) -> Identity:
        o = self._options
        return (
            run.workflow_id,
            run.run_id,
            self._workflow_type,
            o.task_queue,
            o.session_id,
            o.agent_name,
        )

    async def telemetry(self, event: EventEnvelope) -> EvaluationResult | None:
        """Best-effort, non-enforcing send. Never raises: completions run AFTER the work,
        so a transport failure (``fail_closed``/auth) must not discard a result or mask
        the original error. Enforcement still fails closed through :meth:`gate`."""
        try:
            return await self._runtime.gate.aevaluate(event)
        except Exception:
            _logger.warning("OpenBox telemetry send failed (suppressed)", exc_info=True)
            return None

    async def gate(
        self, run: RunState, event: EventEnvelope, orphan_close: Any = None
    ) -> EvaluationResult:
        """Evaluate + ENFORCE a start-stage event (BLOCK/HALT/guardrails raise; approval
        awaits). On failure, close the orphan start row and the workflow, then re-raise."""
        try:
            return await self._runtime.aevaluate_lifecycle(event)
        except BaseException as exc:  # incl. cancellation during an approval wait
            if orphan_close is not None:
                await orphan_close(exc)
            await self.close_run(run, exc)
            raise

    async def close_run(self, run: RunState, error: BaseException) -> None:
        """Send WorkflowFailed once; later closers become no-ops."""
        if run.closed:
            return
        run.closed = True
        # Every row closes BEFORE the workflow: an allowed pre-screen that no model
        # call consumed (e.g. a later model gate blocked a different prompt).
        await self._close_unused_pre_screen(run, error)
        if self._options.send_run_end_event:
            await self.telemetry(build_workflow_failed(self.identity(run), error))

    async def close_llm_activity(
        self, run: RunState, activity_id: str, error: BaseException | None = None
    ) -> None:
        """Close an ``llm_call`` start row (telemetry; gated by ``send_llm_end_event``)."""
        if self._options.send_llm_end_event:
            await self.telemetry(
                build_activity_completed(
                    self.identity(run),
                    activity_id=activity_id,
                    activity_type="llm_call",
                    error=error,
                )
            )

    async def _close_unused_pre_screen(self, run: RunState, error: Any = None) -> None:
        """Close an allowed pre-screen row that no model call consumed."""
        if run.pre_screen is not None and not run.pre_screen_used:
            run.pre_screen_used = True
            await self.close_llm_activity(run, f"{run.run_id}-pre", error)

    # ─── Run scope ──────────────────────────────────────────────────

    @asynccontextmanager
    async def run_scope(self, prompt: str = "") -> AsyncIterator[RunState]:
        """Bind a governed run for the block; set ``run.output`` to report a result."""
        run = RunState.new(self._options.task_queue)
        ident = self.identity(run)
        token = _current_run.set(run)
        try:
            if self._options.send_run_start_event:
                await self.telemetry(build_workflow_started(ident))
            if prompt.strip():
                await self.gate(run, build_signal_received(ident, "user_prompt", [prompt]))
                if self._options.send_llm_start_event:
                    pre_id = f"{run.run_id}-pre"

                    async def close_pre(exc: BaseException) -> None:
                        await self.close_llm_activity(run, pre_id, exc)

                    run.pre_screen = await self.gate(
                        run,
                        build_activity_started(
                            ident,
                            activity_id=pre_id,
                            activity_type="llm_call",
                            activity_input=[{"prompt": prompt}],
                        ),
                        orphan_close=close_pre,
                    )
                    run.pre_screen_prompt = prompt
            yield run
        except BaseException as exc:  # incl. cancellation / run-deadline teardown
            await self._close_unused_pre_screen(run, exc)
            await self.close_run(run, exc)
            raise
        else:
            await self._close_unused_pre_screen(run)
            if not run.closed and self._options.send_run_end_event:
                run.closed = True
                await self.telemetry(build_workflow_completed(ident, run.output))
        finally:
            _current_run.reset(token)

    # ─── Wrappers ───────────────────────────────────────────────────

    def wrap_provider(self, provider: ModelProvider) -> ModelProvider:
        from openbox_agentrt.model_provider import wrap_model_provider

        return wrap_model_provider(self, provider)

    def wrap_tool_registry(self, registry: ToolRegistry) -> ToolRegistry:
        from openbox_agentrt.tool_governance import GovernedToolRegistry

        return GovernedToolRegistry(self, registry)

    def wrap_tool_executor(self, executor: ToolExecutor) -> ToolExecutor:
        from openbox_agentrt.tool_governance import GovernedToolExecutor

        return GovernedToolExecutor(self, executor)

    def govern_loop(self, loop: Any) -> GovernedAgentLoop:
        from openbox_agentrt.loop import GovernedAgentLoop

        return GovernedAgentLoop(self, loop)

    def activity_scope(self, run: RunState, activity_id: str, activity_type: str) -> Any:
        """Bind an ``ActivityContext`` to the runtime's own store for the call body, so
        Layer-2 HTTP/DB/file instrumentation resolves the right activity."""
        o = self._options
        ctx = ActivityContext(
            workflow_id=run.workflow_id,
            run_id=run.run_id,
            workflow_type=self._workflow_type,
            task_queue=o.task_queue,
            activity_id=activity_id,
            activity_type=activity_type,
            agent_name=o.agent_name,
            session_id=o.session_id,
        )
        return activity_scope(ctx, store=self._runtime.context_store)
