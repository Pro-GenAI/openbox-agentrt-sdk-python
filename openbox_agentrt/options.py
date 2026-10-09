"""Configuration for :class:`OpenBoxAgentRTGovernance`."""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["OpenBoxAgentRTOptions"]


@dataclass
class OpenBoxAgentRTOptions:
    """Options for one governed Agent-RT runtime.

    ``api_url``/``api_key`` default to None, which falls through to
    ``OPENBOX_AGENTRT_*`` / ``OPENBOX_*`` env resolution (see ``runtime_builder``);
    the factory (``create_openbox_agentrt``) always threads its explicit values here.
    """

    api_url: str | None = None
    api_key: str | None = None
    agent_name: str | None = None
    agent_did: str | None = None
    agent_private_key: str | None = None
    session_id: str | None = None
    task_queue: str = "agentrt"
    on_api_error: str = "fail_open"
    governance_timeout: float = 30.0
    # tool name -> tool type, surfaced to policies as ``__openbox.tool_type``.
    tool_type_map: dict[str, str] = field(default_factory=dict)
    skip_tool_types: set[str] = field(default_factory=set)
    send_run_start_event: bool = True
    send_run_end_event: bool = True
    send_llm_start_event: bool = True
    send_llm_end_event: bool = True
    send_tool_start_event: bool = True
    send_tool_end_event: bool = True
    # HITL: Agent-RT is async-only, so every governed call can await a real
    # approval via ApprovalPoller. Poll interval ``None`` uses the resolved config
    # (``hitl.poll_interval_ms``, default 5 s); max wait ``None`` waits indefinitely.
    approval_poll_interval_seconds: float | None = None
    approval_max_wait_seconds: float | None = 3600.0
