# OpenBox Agent-RT SDK — Python

[![PyPI](https://img.shields.io/pypi/v/openbox-deepagent-sdk-python)](https://pypi.org/project/openbox-deepagent-sdk-python/)
[![Python](https://img.shields.io/pypi/pyversions/openbox-deepagent-sdk-python)](https://pypi.org/project/openbox-deepagent-sdk-python/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[Agent-RT](https://github.com/Pro-GenAI/Agent-RT) adapter for [OpenBox](https://openbox.ai) governance. Integrates with Agent-RT's model providers, tool execution, and agent loops (`ModelProvider`, `ToolRegistry` / `ToolExecutor`, `AgentLoop`) so every run, model call, and tool call emits lifecycle events to OpenBox Core's policy engine, enabling real-time governance, guardrails (redaction), HITL approval flows, and base-SDK hook governance (HTTP/DB/File I/O). Verdicts are enforced **inline** — before the governed model or tool call runs.

> **Status:** active development; APIs may still change.

## Installation

```bash
pip install openbox-agentrt-sdk-python "agent-rt[openai]==0.0.3"
```

The `openai` extra enables the model provider used in the example below.

## Quick Start

Set `OPENAI_MODEL`, `OPENAI_API_KEY`, and `OPENBOX_API_KEY` before running. The default Core endpoint is `https://core.openbox.ai`; set `OPENBOX_URL` to override it.

```python
import asyncio
import os

from agent_rt import (
    AgentConfig,
    AgentLoop,
    ContentPart,
    ModelMessage,
    ModelSettings,
    ToolRegistry,
    load_model,
)
from openbox_agentrt import create_openbox_agentrt


async def main() -> None:
    # 1. Create governance (validates the API key on startup)
    governance = await create_openbox_agentrt(
        api_key=os.environ["OPENBOX_API_KEY"],
        agent_name="MyAgent",
    )
    try:
        # 2. Wrap an ordinary Agent-RT loop
        provider = load_model()  # reads OPENAI_MODEL and OPENAI_API_KEY
        registry = ToolRegistry()  # register tools as usual
        loop = governance.govern_loop(AgentLoop(provider, tool_registry=registry))

        # 3. Run — governance applied automatically
        agent = AgentConfig(
            name="my-agent",
            instructions="Be brief.",
            model=ModelSettings(model=os.environ["OPENAI_MODEL"]),
        )
        messages = [ModelMessage(role="user", content=(ContentPart(type="text", text="Hello"),))]
        result = await loop.run(agent, messages)
        print(result.termination_reason)
    finally:
        await governance.aclose()


asyncio.run(main())
```

## How It Works

Three-layer governance architecture:

| Layer | Mechanism | Governs |
|-------|-----------|---------|
| 1 | Agent-RT model, tool, and agent loop wrappers | Run, model (`llm_call`), and tool lifecycle via `OpenBoxAgentRTGovernance` |
| 2 | Base SDK Hook Governance | HTTP requests, DB queries, file I/O at process boundary |
| 3 | Activity context mapping | Binds Layer-2 spans to the model/tool activity that caused them |

| Governed operation | Wrapper | Events |
|------|---------|--------|
| Run | `governance.run_scope(prompt)` | `WorkflowStarted` → `SignalReceived` (enforced) → pre-screen (enforced) → `WorkflowCompleted` / `WorkflowFailed` |
| Model | `governance.wrap_provider(provider)` | `ActivityStarted(llm_call)` (enforced, redaction applied) → `ActivityCompleted` |
| Tools | `governance.wrap_tool_registry(registry)` / `wrap_tool_executor(executor)` | `ActivityStarted(<tool>)` (enforced) → `ActivityCompleted` |

**Core components:**
- `OpenBoxAgentRTGovernance` — Owns one `OpenBoxRuntime`; opens run scopes, gates start events, sends completion telemetry
- `GovernedAgentLoop` — Returned by `govern_loop`; same `run(...)` / `run_streaming(...)` signatures as `AgentLoop`, original loop not mutated
- `GovernedModelProvider` / `GovernedStreamingModelProvider` — Gate each model call and apply guardrail redaction to the request
- `GovernedToolRegistry` / `GovernedToolExecutor` — Gate each tool call; `AgentLoop` prefers the registry and only falls back to the executor, so `govern_loop` wraps both

Every started activity is closed — including when a run deadline or cancellation token cancels the call, a stream is closed early, or a gate blocks — and completion telemetry is best-effort: a failed send is logged, never raised over a tool or model result.

Model calls without non-empty user text still pass through model-start governance (with an empty `prompt` field).

## Configuration

```python
governance = await create_openbox_agentrt(
    api_url="https://core.openbox.ai",  # OpenBox Core URL
    api_key="obx_live_...",              # API key (obx_live_* or obx_test_*)
    agent_did="did:aip:...",             # Optional; can use OPENBOX_AGENT_DID
    agent_private_key="...",             # Optional; can use OPENBOX_AGENT_PRIVATE_KEY
    agent_name="MyAgent",                # Agent name (from dashboard)
    governance_timeout=30.0,             # HTTP timeout in seconds
    validate=True,                       # Validate API key on startup
    session_id="session-123",            # Optional session tracking
    on_api_error="fail_open",            # Use "fail_closed" for destructive agents
    tool_type_map={                      # Optional tool classification
        "search_web": "http",
        "query_db": "database",
    },
    approval_max_wait_seconds=3600.0,    # HITL wait; None waits indefinitely
)
```

Precedence: explicit arguments > `OPENBOX_AGENTRT_*` env > global `OPENBOX_*` env > defaults. Extra keyword arguments are `OpenBoxAgentRTOptions` fields (`task_queue`, `skip_tool_types`, `send_*_event` flags — each also gates that point's enforcement — and `approval_poll_interval_seconds`, defaulting to the resolved `hitl` config). Unknown keyword arguments raise `TypeError`.

Tool activities carry an `__openbox` sentinel (`tool_type` from `tool_type_map`, plus the tool's Agent-RT `side_effect`) so Rego policies can classify calls.

## Agent Identity and DID Signing

OpenBox issues each registered agent a decentralized identifier (DID) and
private key. DID signing is enabled by default for newly registered agents. The
OpenBox UI returns both values when the agent is created. Pass them to the
factory so governance events are signed and attributable to that agent.

You can provide them directly:

```python
governance = await create_openbox_agentrt(
    api_url="https://core.openbox.ai",
    api_key="obx_live_...",
    agent_did="did:aip:...",
    agent_private_key="...",
)
```

Or set them through the environment:

```bash
export OPENBOX_AGENT_DID="did:aip:..."
export OPENBOX_AGENT_PRIVATE_KEY="..."
```

If DID signing is explicitly disabled for the agent in OpenBox, these values can
be omitted. Otherwise, provide both values together. Okta AI Agent and Keycloak
workload identity are resolved by the base SDK and forwarded unchanged.

## Supported Agent Types

- `governance.govern_loop(AgentLoop(...))` — recommended; governs runs, model calls, and tool calls
- Standalone wrappers (`wrap_provider`, `wrap_tool_registry`, `wrap_tool_executor`) inside `async with governance.run_scope(prompt):` — for custom loops

A wrapped provider or tool called outside a run scope fails closed (`RuntimeError`).

## Verdict Enforcement

5-tier verdict system:
- **ALLOW** — Request permitted
- **CONSTRAIN** — Request permitted with constraints (passed through like ALLOW; guardrail redaction is applied separately)
- **REQUIRE_APPROVAL** — Human approval required (async HITL polling via `ApprovalPoller`; fails safe as rejected when no approval flow is configured)
- **BLOCK** — Request blocked with a native `openbox_core` error before the call runs
- **HALT** — Entire workflow halted (`WorkflowFailed` sent, error re-raised)

Agent-RT is async-only, so there is no fail-shut sync approval path.

## Requirements

- Python 3.11–3.14
- openbox-sdk-python >= 1.4.0
- agent-rt == 0.0.3

## API Reference

**Entry point:**
- `create_openbox_agentrt()` — Async factory; resolves config, builds the runtime, validates the API key
- `OpenBoxAgentRTOptions` — Configuration dataclass

**Governance and wrappers:**
- `OpenBoxAgentRTGovernance` — `run_scope`, `govern_loop`, `wrap_provider`, `wrap_tool_registry`, `wrap_tool_executor`, `aclose`
- `GovernedAgentLoop` — Governed `AgentLoop` (`run`, `run_streaming`)
- `GovernedModelProvider` / `GovernedStreamingModelProvider` — Governed `ModelProvider`
- `GovernedToolRegistry` / `GovernedToolExecutor` — Governed tool execution
- `RunState` / `current_run()` — Per-run identity bound by `run_scope`

See `openbox_agentrt/__init__.py` for the full API export list, [`docs/`](docs/) for architecture and standards, and [`examples/content-builder-agent`](examples/content-builder-agent) for a governed example.

## Example Quick Start

From the Python SDK root, configure your API keys in
`examples/content-builder-agent/.env`, then run:

```bash
uv sync --all-extras --dev --locked
cd examples/content-builder-agent
uv sync --locked
uv run python content_writer.py "Write a blog post about prompt engineering"
```

`OPENBOX_URL` is optional and defaults to `https://core.openbox.ai`.

## Contributing

Contributions and bug reports are welcome. Install development dependencies with `uv sync --all-extras --dev --locked`, then run the checks from the SDK directory:

```bash
uv run --locked ruff check openbox_agentrt/ tests/ examples/
uv run --locked ruff format --check openbox_agentrt/ tests/ examples/
uv run --locked mypy openbox_agentrt/
uv run --locked pytest tests/ --cov=openbox_agentrt --cov-fail-under=80
uv build
```

Include tests for behavior changes and update documentation for public API changes.

## License

MIT
