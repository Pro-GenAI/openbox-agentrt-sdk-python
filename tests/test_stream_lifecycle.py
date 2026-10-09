"""Regression tests for streaming iterator startup, errors, and cleanup."""

from __future__ import annotations

from typing import Any

import pytest
from agent_rt import ModelRequest

from tests.fakes import build_governance, user


async def test_stream_factory_failure_closes_its_start_row() -> None:
    gov, core = build_governance()

    class BrokenFactory:
        name = "broken"

        def stream(self, request: Any) -> Any:
            raise RuntimeError("stream factory failed")

    provider = gov.wrap_provider(BrokenFactory())
    async with gov.run_scope("hi"):
        with pytest.raises(RuntimeError, match="stream factory failed"):
            await anext(provider.stream(ModelRequest(messages=[user("hi")])))
    started = {p["activity_id"] for p in core.payloads if p["event_type"] == "ActivityStarted"}
    done = {p["activity_id"] for p in core.payloads if p["event_type"] == "ActivityCompleted"}
    assert started <= done


async def test_stream_iteration_failure_closes_inner_iterator() -> None:
    gov, core = build_governance()
    closed: list[bool] = []

    class BrokenIterator:
        def __aiter__(self) -> BrokenIterator:
            return self

        async def __anext__(self) -> Any:
            raise RuntimeError("next failed")

        async def aclose(self) -> None:
            closed.append(True)

    class BrokenProvider:
        name = "broken"

        def stream(self, request: Any) -> BrokenIterator:
            return BrokenIterator()

    provider = gov.wrap_provider(BrokenProvider())
    async with gov.run_scope("hi"):
        with pytest.raises(RuntimeError, match="next failed"):
            await anext(provider.stream(ModelRequest(messages=[user("hi")])))
    assert closed == [True]
    started = {p["activity_id"] for p in core.payloads if p["event_type"] == "ActivityStarted"}
    done = {p["activity_id"] for p in core.payloads if p["event_type"] == "ActivityCompleted"}
    assert started <= done
