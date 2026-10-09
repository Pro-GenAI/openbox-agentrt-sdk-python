"""Per-run state for governed Agent-RT runs.

Run identity (workflow_id/run_id/pre-screen verdict) lives in a ``ContextVar``
bound by ``OpenBoxAgentRTGovernance.run_scope`` — never on the governance
instance — so concurrent ``AgentLoop.run`` calls on one instance cannot
cross-contaminate. ``asyncio`` copies the context into every child task
(including concurrent tool calls), so the governed provider and tool wrappers
see the run that spawned them.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openbox_core.contracts.results import EvaluationResult

__all__ = ["RunState", "current_run", "require_run"]


@dataclass
class RunState:
    """Identity + mutable gate state of one governed run."""

    workflow_id: str
    run_id: str
    pre_screen: EvaluationResult | None = None
    # The prompt the pre-screen evaluated; only a model call for this same prompt reuses it.
    pre_screen_prompt: str | None = None
    # Set by whichever enforcing gate closed the workflow, so run_scope exit
    # does not double-close it.
    closed: bool = False
    # True once the pre-screen verdict has been reused by the first model call.
    pre_screen_used: bool = False
    # Workflow result reported on WorkflowCompleted (set by the caller in run_scope).
    output: Any = None

    @classmethod
    def new(cls, prefix: str = "agentrt") -> RunState:
        token = uuid.uuid4().hex
        return cls(workflow_id=f"{prefix}-{token[:16]}", run_id=f"{prefix}-run-{token[16:32]}")


_current_run: ContextVar[RunState | None] = ContextVar("openbox_agentrt_run", default=None)


def current_run() -> RunState | None:
    return _current_run.get()


def require_run() -> RunState:
    """Fail closed: governed calls outside a run scope are a wiring bug."""
    run = _current_run.get()
    if run is None:
        raise RuntimeError(
            "OpenBox governance: no active run — execute the agent through "
            "OpenBoxAgentRTGovernance.run_scope(...) or a GovernedAgentLoop"
        )
    return run
