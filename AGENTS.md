# AGENTS.md — OpenBox Agent-RT SDK (Python)

## Project Overview

OpenBox governance SDK for [Agent-RT](https://github.com/Pro-GenAI/Agent-RT) agents. Wraps Agent-RT's
public seams (`ModelProvider`, `ToolRegistry` / `ToolExecutor`, `AgentLoop`) and enforces OpenBox
policies, guardrails, and HITL approvals inline. Mirrors the layout and conventions of
`openbox-langchain-sdk-python`; the TypeScript twin is `openbox-agentrt-sdk-ts`.

**Key principle:** only the Agent-RT integration layer lives here. All governance infrastructure
(client, gate, runtime, approvals, instrumentation, contracts) is imported from `openbox-sdk-python`
(`openbox_core`) — never copied.

## Architecture

- **Layer 1:** `OpenBoxAgentRTGovernance` — run scope + governed provider/tool/loop wrappers.
- **Layer 2:** HTTP/DB/file hook governance — `openbox_core` instrumentation, installed per runtime.
- **Layer 3:** activity context mapping — wrappers bind `activity_scope(...)` to the runtime's own
  `ContextStore` around every model and tool body so Layer-2 spans resolve to the right activity.

Run identity lives in a `ContextVar` (`run_state.py`) bound by `run_scope`, never on the
governance instance, so concurrent runs never cross-contaminate.

**Invariants:**

- Agent-RT is async-only: every gate uses `runtime.aevaluate_lifecycle`; do not add a sync path.
- Start-stage events are **enforced** (`gov.gate`); completion events are **telemetry only**
  (`gov.telemetry`), best-effort, and never raise — not for a verdict, and not for a transport or
  auth failure under `fail_closed` (the work already ran; a failed send must not discard its result).
- On any enforcement failure: close the orphan start row (same activity id — model, tool, and
  pre-screen rows alike), send `WorkflowFailed` exactly once (`RunState.closed`), then re-raise.
- No orphan rows: model/tool wrappers and `run_scope` handle `BaseException`, so cancellation by an
  `AgentLoop` run deadline or cancellation token still records `ActivityCompleted` / `WorkflowFailed`.
  An allowed pre-screen that no model call consumed is closed at run end, before `WorkflowFailed`
  when a later gate blocks (`close_run`).
- The run's first model call **for the pre-screened prompt** (`RunState.pre_screen_prompt`) reuses the
  pre-screen verdict (`RunState.pre_screen_used`) — even when the caller passes prior chat history.
- Streaming: bind `activity_scope` around each `__anext__` step, never across a `yield` (`AgentLoop`
  runs each step in its own task under a deadline; a `ContextVar` reset from another context raises).
  `GovernedModelProvider` must never delegate `stream` to the ungoverned inner provider.
- One `EvaluationClient` per runtime, shared with the `ApprovalPoller` (`runtime_builder.build_client`),
  so approval polls carry every identity mode and close with the runtime.
- `AgentLoop` prefers `tool_registry.execute` over `tool_executor.execute`; wrap both.
- Redaction builds new frozen `ModelMessage`s; never mutate conversation history.
- Keep every source file under 240 lines (the LangChain reference limit); format with `ruff format`
  (one argument per line, as in the reference).

## Package Structure

```
openbox_agentrt/
├── __init__.py            # Public API
├── factory.py             # create_openbox_agentrt() (async; validates the API key)
├── governance.py          # OpenBoxAgentRTGovernance: run_scope, gate, telemetry, wrappers
├── options.py             # OpenBoxAgentRTOptions
├── runtime_builder.py     # config resolution + one client → OpenBoxRuntime (+ ApprovalPoller)
├── run_state.py           # RunState + ContextVar
├── model_provider.py      # GovernedModelProvider (+ streaming variant)
├── tool_governance.py     # GovernedToolRegistry / GovernedToolExecutor
├── loop.py                # GovernedAgentLoop
├── lifecycle_events.py    # envelope builders, error_info, __openbox enrichment
├── message_extraction.py  # ModelMessage helpers, redaction, response metadata
├── sdk_metadata.py        # static SDK identity (engine "agentrt")
└── py.typed               # PEP 561 marker: ship inline types to consumers
```

## Key Classes

- `OpenBoxAgentRTGovernance` — Owns one `OpenBoxRuntime`; run scope, gate, telemetry, seam wrappers
- `OpenBoxAgentRTOptions` — Configuration dataclass
- `GovernedAgentLoop` — Governed `AgentLoop` (`run`, `run_streaming`)
- `GovernedModelProvider` / `GovernedStreamingModelProvider` — Governed `ModelProvider`
- `GovernedToolRegistry` / `GovernedToolExecutor` — Governed tool seams
- `RunState` — Per-run identity, pre-screen state, and close flag (bound in a `ContextVar`)

## Key Functions

- `create_openbox_agentrt()` — Async factory (primary entry point); validates the API key
- `OpenBoxAgentRTGovernance.run_scope()` — `WorkflowStarted`, `SignalReceived`, pre-screen, run close
- `OpenBoxAgentRTGovernance.govern_loop()` — Wraps an `AgentLoop`'s provider, registry, and executor
- `OpenBoxAgentRTGovernance.gate()` — Enforced start-stage evaluation; closes orphans on failure
- `OpenBoxAgentRTGovernance.telemetry()` — Best-effort completion send; never raises
- `govern_tool_call()` — Tool gate, `activity_scope`, and completion for registry/executor paths
- `build_runtime()` — Config resolution, one shared client, optional `ApprovalPoller`

## Quick Start

```python
from agent_rt import AgentLoop, ToolRegistry, load_model
from openbox_agentrt import create_openbox_agentrt

# Create governance
governance = await create_openbox_agentrt(
    api_url="https://core.openbox.ai",
    api_key="obx_live_...",
    agent_name="MyAgent",
)

# Wrap an Agent-RT loop
loop = governance.govern_loop(AgentLoop(load_model(), tool_registry=ToolRegistry()))

# Run — governance applied automatically
result = await loop.run(agent, messages)
await governance.aclose()
```

## Commands

```bash
# Install
pip install -e ".[dev]"

# Test
pytest tests/ -v

# Lint + format
ruff check openbox_agentrt/ tests/ examples/
ruff format openbox_agentrt/ tests/ examples/

# Type check
mypy openbox_agentrt/
```

## Testing

**Current:** 47 tests, 100% pass rate, 94.67% package coverage

**Coverage:**
- Full governed `AgentLoop` lifecycle against a real Agent-RT loop
- Verdict enforcement (BLOCK at signal/pre-screen/model/tool, REQUIRE_APPROVAL fail-safe, guardrail redaction)
- Orphan-row closure, cancellation, streaming deadlines, telemetry failures
- Pre-screen reuse, factory option validation (`validate`, `skip_tool_types`, `send_*_event`), message extraction
- Package boundaries (imports limited to Agent-RT + base SDK) and the Agent-RT 0.0.3 pin

Offline only: tests wire `OpenBoxRuntime` to `openbox_core.conformance.fake_core.FakeCore`
(queued verdicts + payload capture) — no network, no real LLM. Queue order matters: every
telemetry send (incl. `WorkflowStarted` and `LLMCompleted`) consumes one queued response.
`tests/test_lifecycle_hardening.py` covers the orphan-row, cancellation, streaming-deadline,
telemetry-failure, and pre-screen-reuse invariants above; keep it in lockstep with the TS
`test/lifecycle-hardening.test.ts`.

Require exactly Agent-RT 0.0.3 for optional Traceloop packaging and the verified public
integration seams. Pin the content-builder example to the same exact version and maintain
both SDK and example `uv.lock` files. Keep the supported Agent-RT version in sync with the
TypeScript SDK and re-run offline tests when updating it.

## Documentation

- **README.md** — install, quick start, configuration
- **docs/system-architecture.md** — data flow and verdict handling
- **docs/code-standards.md** — implementation/test conventions
- **docs/codebase-summary.md** — module ownership and boundaries
- **docs/project-overview-pdr.md** — requirements and acceptance criteria
- **docs/project-roadmap.md** — release verification, risks, and follow-up work
- **examples/content-builder-agent/** — governed port of the LangChain reference example (Tavily search, Gemini images), aligned with the TS twin

Preserve the exact five-document `docs/` layout of the LangChain Python reference.
Match its Markdown structure (title-cased headings, detailed patterns/requirements,
architecture flows, tables, release checks, and cross-links) while describing only
Agent-RT's verified behavior. Keep file paths and measured status claims current.

## References

- Agent-RT: `../python` (runtime) and `../docs/Introduction-to-Harness.md`
- LangChain reference SDK: `../openbox-examples/openbox-langchain-sdk-python`
