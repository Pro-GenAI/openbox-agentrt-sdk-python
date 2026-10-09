"""Governed ``ModelProvider`` wrapper: LLMStarted → redaction → model → LLMCompleted.

The first model call of a run reuses the pre-screen verdict from ``run_scope``
instead of re-evaluating the same prompt. Streaming providers keep streaming:
LLMCompleted is sent once the stream yields its ``completed`` event (or fails).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from agent_rt import ModelRequest, ModelResponse, ModelStreamEvent

from openbox_agentrt.lifecycle_events import build_activity_completed, build_activity_started
from openbox_agentrt.message_extraction import (
    apply_redaction,
    extract_last_user_text,
    extract_response_metadata,
)
from openbox_agentrt.run_state import RunState, require_run

if TYPE_CHECKING:
    from openbox_agentrt.governance import OpenBoxAgentRTGovernance

__all__ = ["GovernedModelProvider", "GovernedStreamingModelProvider", "wrap_model_provider"]

# Recorded when the consumer stops iterating before the stream completed.
STREAM_CLOSED = "model stream closed before completion"


async def _start(
    gov: OpenBoxAgentRTGovernance, request: ModelRequest
) -> tuple[ModelRequest, RunState, str | None]:
    """Enforce LLMStarted; returns (possibly redacted request, run, activity_id|None)."""
    run = require_run()
    # No user text (e.g. a tool-driven continuation) must not bypass governance.
    prompt = extract_last_user_text(request.messages)

    # The run's first model call for the pre-screened prompt reuses that verdict
    # (a stateful check, so callers passing prior chat history still reuse it).
    if run.pre_screen is not None and not run.pre_screen_used and prompt == run.pre_screen_prompt:
        run.pre_screen_used = True
        activity_id, result = f"{run.run_id}-pre", run.pre_screen
    elif gov.options.send_llm_start_event:
        activity_id = str(uuid.uuid4())

        async def close_orphan(exc: BaseException) -> None:
            await gov.close_llm_activity(run, activity_id, exc)

        result = await gov.gate(
            run,
            build_activity_started(
                gov.identity(run),
                activity_id=activity_id,
                activity_type="llm_call",
                activity_input=[{"prompt": prompt}],
            ),
            orphan_close=close_orphan,
        )
    else:
        return request, run, str(uuid.uuid4())

    if result.guardrails and result.guardrails.redacted_input:
        request = replace(
            request, messages=apply_redaction(request.messages, result.guardrails.redacted_input)
        )
    return request, run, activity_id


async def _complete(
    gov: OpenBoxAgentRTGovernance,
    run: RunState,
    activity_id: str | None,
    *,
    response: ModelResponse | None = None,
    error: BaseException | str | None = None,
) -> None:
    if activity_id is None or not gov.options.send_llm_end_event:
        return
    await gov.telemetry(
        build_activity_completed(
            gov.identity(run),
            activity_id=activity_id,
            activity_type="llm_call",
            result=extract_response_metadata(response) if response is not None else None,
            error=error,
        )
    )


class GovernedModelProvider:
    """Wraps any Agent-RT ``ModelProvider`` with OpenBox governance."""

    def __init__(self, gov: OpenBoxAgentRTGovernance, inner: Any) -> None:
        self._gov = gov
        self._inner = inner

    @property
    def name(self) -> str:
        return str(self._inner.name)

    def __getattr__(self, attr: str) -> Any:
        # ``stream`` must never fall through to the UNGOVERNED inner provider:
        # ``AgentLoop`` detects streaming support structurally, so delegating it
        # would bypass governance. Streaming is :class:`GovernedStreamingModelProvider`.
        if attr == "stream":
            raise AttributeError(attr)
        # Everything else (count_tokens, settings, ...) is the inner provider's.
        return getattr(self._inner, attr)

    async def complete(self, request: ModelRequest) -> ModelResponse:
        request, run, activity_id = await _start(self._gov, request)
        try:
            if activity_id is None:
                return await self._inner.complete(request)
            with self._gov.activity_scope(run, activity_id, "llm_call"):
                response: ModelResponse = await self._inner.complete(request)
        except BaseException as exc:  # incl. cancellation by a run deadline
            await _complete(self._gov, run, activity_id, error=exc)
            raise
        await _complete(self._gov, run, activity_id, response=response)
        return response


class GovernedStreamingModelProvider(GovernedModelProvider):
    """Same as :class:`GovernedModelProvider`, for providers that implement ``stream``."""

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        request, run, activity_id = await _start(self._gov, request)
        iterator: AsyncIterator[ModelStreamEvent] | None = None
        completed: ModelResponse | None = None
        try:
            # Iterator construction can fail before the first step. Still close
            # the activity opened by the start-stage gate.
            iterator = self._inner.stream(request).__aiter__()
            while True:
                # Bind the activity per step, never across ``yield``: each step may
                # run in a different task context (``AgentLoop`` wraps every
                # ``__anext__`` in a task under a deadline/cancellation token), and the
                # binding must not leak into the consumer's event handler.
                try:
                    if activity_id is None:
                        event = await iterator.__anext__()
                    else:
                        with self._gov.activity_scope(run, activity_id, "llm_call"):
                            event = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                if event.type == "completed" and event.response is not None:
                    completed = event.response
                yield event
        except BaseException as exc:  # incl. cancellation and early close
            closed_early = isinstance(exc, GeneratorExit)
            await _complete(
                self._gov, run, activity_id, error=STREAM_CLOSED if closed_early else exc
            )
            aclose = getattr(iterator, "aclose", None) if iterator is not None else None
            if aclose is not None:
                with contextlib.suppress(Exception):  # never mask the original error
                    await aclose()
            raise
        await _complete(self._gov, run, activity_id, response=completed)


def wrap_model_provider(gov: OpenBoxAgentRTGovernance, provider: Any) -> Any:
    cls = GovernedStreamingModelProvider if hasattr(provider, "stream") else GovernedModelProvider
    return cls(gov, provider)
