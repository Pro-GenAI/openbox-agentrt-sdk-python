"""OpenBox Agent-RT SDK — governance for Agent-RT agents.

Built on the framework-neutral base SDK (``openbox_core``) and Agent-RT's public
seams (``ModelProvider``, ``ToolRegistry``/``ToolExecutor``, ``AgentLoop``). Policy
verdicts (ALLOW / BLOCK / HALT / REQUIRE_APPROVAL), guardrail redaction, and HITL
approvals are enforced INLINE, before the governed call runs.
"""

from importlib.metadata import PackageNotFoundError, version

from openbox_agentrt.factory import create_openbox_agentrt
from openbox_agentrt.governance import OpenBoxAgentRTGovernance
from openbox_agentrt.loop import GovernedAgentLoop
from openbox_agentrt.model_provider import GovernedModelProvider, GovernedStreamingModelProvider
from openbox_agentrt.options import OpenBoxAgentRTOptions
from openbox_agentrt.run_state import RunState, current_run
from openbox_agentrt.tool_governance import GovernedToolExecutor, GovernedToolRegistry

try:
    __version__ = version("openbox-agentrt-sdk-python")
except PackageNotFoundError:
    __version__ = "unknown"

__all__ = [
    "GovernedAgentLoop",
    "GovernedModelProvider",
    "GovernedStreamingModelProvider",
    "GovernedToolExecutor",
    "GovernedToolRegistry",
    "OpenBoxAgentRTGovernance",
    "OpenBoxAgentRTOptions",
    "RunState",
    "__version__",
    "create_openbox_agentrt",
    "current_run",
]
