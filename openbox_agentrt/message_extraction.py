"""Extraction/redaction helpers over Agent-RT ``ModelMessage`` sequences."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from agent_rt import ContentPart, ModelMessage, ModelResponse

__all__ = [
    "apply_redaction",
    "extract_last_user_text",
    "extract_response_metadata",
    "message_text",
]


def message_text(message: ModelMessage) -> str:
    return "".join(p.text or "" for p in message.content if p.type == "text")


def extract_last_user_text(messages: Sequence[ModelMessage]) -> str:
    """Text of the most recent user turn ("" when there is none)."""
    for message in reversed(messages):
        if message.role == "user":
            return message_text(message)
    return ""


def coerce_redacted_text(redacted_input: Any) -> str | None:
    """Normalize ``GuardrailsResult.redacted_input`` to plain text."""
    if isinstance(redacted_input, str):
        return redacted_input or None
    if isinstance(redacted_input, list) and redacted_input:
        first = redacted_input[0]
        if isinstance(first, dict):
            return first.get("prompt") or first.get("text") or None
        if isinstance(first, str):
            return first or None
    return None


def apply_redaction(
    messages: Sequence[ModelMessage], redacted_input: Any
) -> tuple[ModelMessage, ...]:
    """Return ``messages`` with the last user turn replaced by the redacted text.

    Agent-RT messages are frozen, so this builds a new tuple; the original
    conversation history is never mutated.
    """
    text = coerce_redacted_text(redacted_input)
    if not text:
        return tuple(messages)
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        if out[i].role == "user":
            extras = tuple(p for p in out[i].content if p.type != "text")
            out[i] = replace(out[i], content=(ContentPart(type="text", text=text), *extras))
            break
    return tuple(out)


def extract_response_metadata(response: ModelResponse) -> dict[str, Any]:
    """``{llm_model, input_tokens, output_tokens, total_tokens, completion, has_tool_calls}``."""
    usage = response.usage
    return {
        "llm_model": response.model,
        "input_tokens": usage.input_tokens if usage else None,
        "output_tokens": usage.output_tokens if usage else None,
        "total_tokens": usage.total_tokens if usage else None,
        "completion": message_text(response.message) or None,
        "has_tool_calls": bool(response.message.tool_calls),
        "finish_reason": response.finish_reason,
    }
