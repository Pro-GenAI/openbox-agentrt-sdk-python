"""Factory for creating configured :class:`OpenBoxAgentRTGovernance` instances.

Config resolution goes through ``OpenBoxConfig.resolve`` (base SDK) directly — no
process-wide global config. Precedence: explicit args > ``OPENBOX_AGENTRT_*`` env
> global ``OPENBOX_*`` env > defaults. ``validate=True`` (default) checks the API
key against the server before returning.

Usage:
    from openbox_agentrt import create_openbox_agentrt
    governance = await create_openbox_agentrt(
        api_url=os.environ["OPENBOX_API_URL"],
        api_key=os.environ["OPENBOX_API_KEY"],
        agent_name="MyAgent",
    )
    loop = governance.govern_loop(AgentLoop(provider, tool_registry=registry))
    result = await loop.run(agent, messages)
"""

from __future__ import annotations

import dataclasses
from typing import Any

from openbox_agentrt.governance import OpenBoxAgentRTGovernance
from openbox_agentrt.options import OpenBoxAgentRTOptions
from openbox_agentrt.runtime_builder import build_runtime

__all__ = ["create_openbox_agentrt"]


async def create_openbox_agentrt(
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    agent_name: str | None = None,
    agent_did: str | None = None,
    agent_private_key: str | None = None,
    governance_timeout: float = 30.0,
    validate: bool = True,
    **kwargs: Any,
) -> OpenBoxAgentRTGovernance:
    """Create a configured ``OpenBoxAgentRTGovernance``.

    Validates the API key against the server (``validate=True``) before returning —
    a fast-failing config error beats a silent misconfiguration surfacing only on
    the first governed call.

    Args:
        api_url: Base URL of your OpenBox Core instance.
        api_key: API key in ``obx_live_*`` or ``obx_test_*`` format.
        agent_name: Agent name as configured in the dashboard.
        agent_did: Optional OpenBox agent DID (``OPENBOX_AGENT_DID`` fallback).
        agent_private_key: Optional raw Ed25519 private key seed
            (``OPENBOX_AGENT_PRIVATE_KEY`` fallback).
        governance_timeout: HTTP timeout in seconds (default 30.0).
        validate: If True, validates the API key on startup.
        **kwargs: Forwarded to :class:`OpenBoxAgentRTOptions` (``session_id``,
            ``task_queue``, ``on_api_error``, ``tool_type_map``, ``skip_tool_types``,
            ``send_*_event`` flags, approval polling).
    """
    valid = {f.name for f in dataclasses.fields(OpenBoxAgentRTOptions)}
    unknown = set(kwargs) - valid
    if unknown:
        raise TypeError(f"unknown option(s): {', '.join(sorted(unknown))}")
    options = OpenBoxAgentRTOptions(
        api_url=api_url,
        api_key=api_key,
        agent_name=agent_name,
        agent_did=agent_did,
        agent_private_key=agent_private_key,
        governance_timeout=governance_timeout,
        **kwargs,
    )
    runtime = build_runtime(options)
    if validate:
        try:
            await runtime.client.avalidate_api_key()
        except Exception:
            await runtime.aclose()
            raise
    return OpenBoxAgentRTGovernance(options, runtime=runtime)
