"""Builds the ``OpenBoxRuntime`` for one governance instance.

Each instance gets its OWN runtime with a private ``ContextStore`` (never a
module-level singleton): concurrent governed runtimes must not share
correlation state or instrumentation installs.
"""

from __future__ import annotations

import os

from openbox_core.adapters.base import CoreAdapter
from openbox_core.approvals import ApprovalPoller
from openbox_core.client import EvaluationClient
from openbox_core.config import OpenBoxConfig
from openbox_core.context import ContextStore
from openbox_core.runtime import OpenBoxRuntime

from openbox_agentrt.options import OpenBoxAgentRTOptions
from openbox_agentrt.sdk_metadata import SDK_ENGINE, SDK_LANGUAGE, SDK_PACKAGE_VERSION

__all__ = ["build_client", "build_runtime", "resolve_config"]


def resolve_config(options: OpenBoxAgentRTOptions) -> OpenBoxConfig:
    """Config precedence: explicit options > ``OPENBOX_AGENTRT_*`` > ``OPENBOX_*`` > defaults."""
    return OpenBoxConfig.resolve(
        env_prefix="OPENBOX_AGENTRT",
        api_url=options.api_url or os.environ.get("OPENBOX_URL") or "https://core.openbox.ai",
        api_key=options.api_key,
        on_api_error=options.on_api_error,
        timeout_seconds=options.governance_timeout,
        agent_name=options.agent_name,
        agent_did=options.agent_did,
        agent_private_key=options.agent_private_key,
        sdk_version=SDK_PACKAGE_VERSION,
        sdk_engine=SDK_ENGINE,
        sdk_language=SDK_LANGUAGE,
    )


def build_client(config: OpenBoxConfig) -> EvaluationClient:
    """The ONE client for this runtime — same config → client mapping ``OpenBoxRuntime``
    uses, so approval polling carries every identity mode (DID, Okta, Okta bootstrap,
    Keycloak workload) and is closed with the runtime."""
    return EvaluationClient(
        config.api_url,
        config.api_key,
        timeout_seconds=config.timeout_seconds,
        on_api_error=config.on_api_error,
        identity=config.load_okta_identity() or config.load_identity(),
        okta_bootstrap_private_key=config.okta_bootstrap_private_key(),
        workload_private_key=config.keycloak_workload_private_key(),
        sdk_version=config.sdk_version,
        sdk_engine=config.sdk_engine,
        sdk_language=config.sdk_language,
    )


def build_runtime(options: OpenBoxAgentRTOptions) -> OpenBoxRuntime:
    """Resolve config, build the optional HITL approval poller, and construct the runtime."""
    config = resolve_config(options)
    client = build_client(config)

    approval_poller: ApprovalPoller | None = None
    if config.hitl.enabled:
        interval = options.approval_poll_interval_seconds
        approval_poller = ApprovalPoller(
            client,
            poll_interval_seconds=(
                interval if interval is not None else config.hitl.poll_interval_ms / 1000
            ),
            max_wait_seconds=options.approval_max_wait_seconds,
        )

    runtime = OpenBoxRuntime(
        config,
        CoreAdapter(approval_poller=approval_poller),
        client=client,
        context_store=ContextStore(),
    )
    try:
        runtime.install_instrumentation()
    except BaseException:
        # An install failure must release the runtime and its HTTP client.
        runtime.close()
        raise
    return runtime
