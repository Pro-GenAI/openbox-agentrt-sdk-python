"""Envelope builders for every lifecycle event this SDK emits.

Pure helpers — no gate calls, no run state. ``session_id``/``agent_name`` are not
first-class fields on the base factories, so they travel via ``extra`` (merged
into the wire payload) without widening the base SDK's contract surface.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from openbox_core.contracts.events import (
    EventEnvelope,
    activity_completed,
    activity_started,
    signal_received,
    workflow_completed,
    workflow_failed,
    workflow_started,
)

__all__ = [
    "Identity",
    "build_activity_completed",
    "build_activity_started",
    "build_signal_received",
    "build_workflow_completed",
    "build_workflow_failed",
    "build_workflow_started",
    "enrich_activity_input",
    "error_info",
]

# (workflow_id, run_id, workflow_type, task_queue, session_id, agent_name)
Identity = tuple[str, str, str, str | None, str | None, str | None]


def error_info(error: BaseException | str) -> dict[str, str]:
    """Structured ``ErrorInfo`` Core requires (a bare string is rejected)."""
    if isinstance(error, BaseException):
        return {"type": type(error).__name__, "message": str(error)}
    return {"type": "Error", "message": error}


def _extra(identity: Identity, extra: Mapping[str, Any] | None) -> dict[str, Any] | None:
    merged: dict[str, Any] = dict(extra) if extra else {}
    if identity[4] is not None:
        merged.setdefault("session_id", identity[4])
    if identity[5] is not None:
        merged.setdefault("agent_name", identity[5])
    return merged or None


def _base(identity: Identity) -> dict[str, Any]:
    return {
        "workflow_id": identity[0],
        "run_id": identity[1],
        "workflow_type": identity[2],
        "task_queue": identity[3],
    }


def build_workflow_started(
    identity: Identity, extra: Mapping[str, Any] | None = None
) -> EventEnvelope:
    return workflow_started(**_base(identity), extra=_extra(identity, extra))


def build_workflow_completed(identity: Identity, output: Any = None) -> EventEnvelope:
    extra: dict[str, Any] = {"status": "completed"}
    if output is not None:
        extra["workflow_output"] = output
    return workflow_completed(**_base(identity), extra=_extra(identity, extra))


def build_workflow_failed(identity: Identity, error: BaseException | str) -> EventEnvelope:
    return workflow_failed(
        **_base(identity), error=error_info(error), extra=_extra(identity, {"status": "failed"})
    )


def build_signal_received(identity: Identity, signal_name: str, args: list[Any]) -> EventEnvelope:
    return signal_received(
        **_base(identity),
        signal_name=signal_name,
        extra=_extra(identity, {"signal_args": args}),
    )


def build_activity_started(
    identity: Identity, *, activity_id: str, activity_type: str, activity_input: Any = None
) -> EventEnvelope:
    return activity_started(
        **_base(identity),
        activity_id=activity_id,
        activity_type=activity_type,
        activity_input=activity_input,
        extra=_extra(identity, None),
    )


def build_activity_completed(
    identity: Identity,
    *,
    activity_id: str,
    activity_type: str,
    result: Any = None,
    error: BaseException | str | None = None,
) -> EventEnvelope:
    return activity_completed(
        **_base(identity),
        activity_id=activity_id,
        activity_type=activity_type,
        result=result,
        error=error_info(error) if error is not None else None,
        extra=_extra(identity, None),
    )


def enrich_activity_input(
    base_input: list[Any], *, tool_type: str | None, side_effect: str | None
) -> list[Any]:
    """Append an ``__openbox`` sentinel so Rego policies can classify tools.

    Only appended when a classification exists. NOT sanitized: forgery of this
    sentinel by a malicious tool payload is a documented non-goal — callers
    append it after building the input from trusted fields.
    """
    meta: dict[str, Any] = {}
    if tool_type is not None:
        meta["tool_type"] = tool_type
    if side_effect is not None:
        meta["side_effect"] = side_effect
    return [*base_input, {"__openbox": meta}] if meta else base_input
