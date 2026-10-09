# System Architecture — OpenBox Agent-RT SDK (Python)

## Overview

Three-layer governance architecture built around Agent-RT's public model, tool, and loop interfaces:

1. **Layer 1:** Governed `AgentLoop`, `ModelProvider`, and tool execution wrappers enforce decisions before work runs.
2. **Layer 2:** HTTP/DB/file instrumentation is supplied by `openbox_core`, not reimplemented here.
3. **Layer 3:** Runtime-scoped activity context attributes nested instrumentation events to the active model or tool call.

An `OpenBoxAgentRTGovernance` instance holds a base-SDK runtime and coordinates all three layers. `RunState` is isolated per asynchronous task by `ContextVar`.

## Layer 1: Agent-RT Governance

| Integration point | Lifecycle event | Enforced? | Purpose |
|---|---|---|---|
| `run_scope` startup | `WorkflowStarted` | No — telemetry | Open workflow/session |
| `run_scope` prompt | `SignalReceived` (`user_prompt`) | **Yes** | Block disallowed user input |
| `run_scope` pre-screen | `ActivityStarted` (`llm_call`) | **Yes** | Screen prompt before model execution |
| `ModelProvider.complete` / `stream` | `ActivityStarted` (`llm_call`) | **Yes**, unless the matching pre-screen is reused | Gate each model call; apply input redaction |
| `ToolRegistry.execute` / `ToolExecutor.execute` | `ActivityStarted` (tool name) | **Yes** | Gate both tool execution paths |
| Model/tool completion | `ActivityCompleted` | No — telemetry | Record result or failure |
| Run completion/failure | `WorkflowCompleted` / `WorkflowFailed` | No — telemetry | Close the workflow exactly once |

**Implementation:** `GovernedAgentLoop` clones the original loop and wraps its provider, registry, and executor. The original loop remains available unchanged.

### Lifecycle Flow

```text
governance.govern_loop(loop).run(agent, messages)
  |
  +-- run_scope(prompt) --> RunState (ContextVar)
  |     +-- WorkflowStarted ----------------------- telemetry
  |     +-- SignalReceived("user_prompt") --------- enforce
  |     +-- ActivityStarted(llm_call, "<run>-pre") - enforce
  |
  +-- AgentLoop.run(...) with governed seams
  |     +-- provider.complete / stream
  |     |     +-- reuse matching pre-screen or gate new LLM start
  |     |     +-- build redacted request, if directed by Core
  |     |     +-- activity_scope --> inner provider
  |     |     +-- ActivityCompleted -------------- telemetry
  |     |
  |     +-- registry.execute (or executor.execute)
  |           +-- gate ActivityStarted(tool) ---- enforce
  |           +-- activity_scope --> inner tool
  |           +-- ActivityCompleted -------------- telemetry
  |
  +-- close unused pre-screen (if any) ----------- telemetry
  +-- WorkflowCompleted / WorkflowFailed -------- telemetry
```

The first model call **for the pre-screened prompt** reuses that pre-screen verdict and activity ID, including when earlier chat messages are supplied. Other model calls are evaluated independently.

Model calls with no non-empty user text still enforce a model-start activity (with an empty `prompt` field); missing text is not an authorization to bypass policy.

On a failed enforcing gate, the orphaned start is closed using its original activity ID, and an allowed pre-screen that no model call consumed is closed before the workflow. A run-level `WorkflowFailed` is emitted only once. Cancellation and run deadlines are handled through `BaseException` paths to avoid abandoned rows. A stream closed by its consumer records an early-close error; streaming correlation is bound separately around each iterator step.

## Layer 2: Hook Governance

`openbox_core` installs instrumentation for supported I/O operations. It can govern or audit HTTP, database, and file activity according to base-SDK configuration and the operation's capabilities.

| I/O category | Owner | Attribution |
|---|---|---|
| HTTP requests | Base SDK instrumentation | Current run/model/tool activity |
| Database operations | Base SDK instrumentation | Current run/model/tool activity |
| File operations | Base SDK instrumentation | Current run/model/tool activity |

The adapter must not copy monkey patches, transport clients, or policy logic. The precise supported libraries and synchronous/async interception behavior are defined by the installed base SDK.

## Layer 3: Activity Context Mapping

**Problem:** A nested HTTP/DB/file hook must know which Agent-RT activity initiated the I/O.

**Solution:** The governed wrapper constructs an `ActivityContext` (workflow ID, run ID, activity ID, activity type, agent/session metadata) and activates it with the runtime's private `ContextStore`.

```python
with governance.activity_scope(run, activity_id, "llm_call"):
    response = await inner_provider.complete(request)
```

For model streams, the wrapper activates this scope around each awaited `__anext__` call rather than keeping it active while control is yielded to a consumer. This prevents context from leaking to unrelated consumer work or crossing task boundaries.

## Verdict System

Enforcing evaluations use `OpenBoxRuntime.aevaluate_lifecycle`. Effective priority is **HALT → BLOCK → guardrail failure → REQUIRE_APPROVAL → CONSTRAIN → ALLOW**.

| Verdict or condition | Effect |
|---|---|
| `ALLOW` | Proceed |
| `CONSTRAIN` | Proceed subject to applicable returned constraints |
| `REQUIRE_APPROVAL` | Await the base `ApprovalPoller`; reject/expire/timeout prevents execution |
| `BLOCK` | Raise `GovernanceBlockedError` before the body |
| `HALT` | Raise `GovernanceHaltError` before the body |
| Guardrail validation failure | Raise `GuardrailsValidationError` before the body |

Only **start-stage** lifecycle events enforce. A completion event already follows executed work and must never turn a successful response into an enforcement failure.

## Pre-Screen Guardrails

1. `run_scope` sends a prompt signal (when non-empty).
2. It gates an `llm_call` pre-screen start (when start events are enabled).
3. A BLOCK/HALT/guardrail failure aborts before invoking the loop.
4. A matching first model call reuses the verdict, including redacted input.
5. An unused allowed pre-screen receives a matching completion at run end.

`send_llm_start_event=False` disables the corresponding pre-screen/model-start enforcement; configuration flags should not be described as observability-only switches.

## PII Redaction

A returned `guardrails.redacted_input` substitutes the latest user text in **new immutable** `ModelMessage` values. The caller's original message objects are not changed and non-text content is preserved.

```python
from dataclasses import replace

redacted_request = replace(
    request,
    messages=apply_redaction(request.messages, redacted_text),
)
```

## Configuration & Initialization

```python
governance = await create_openbox_agentrt(
    api_url=os.environ["OPENBOX_API_URL"],
    api_key=os.environ["OPENBOX_API_KEY"],
    agent_name="MyAgent",
    on_api_error="fail_closed",
)
```

Initialization resolves `OpenBoxConfig` (explicit args → `OPENBOX_AGENTRT_*` → `OPENBOX_*` → defaults), builds one `EvaluationClient` and `OpenBoxRuntime` with its own context store, configures an optional shared-client approval poller, installs base instrumentation, and validates the API key by default. Always close via `await governance.aclose()`.

## Error Handling

- **Gate error:** Close orphan activity, close workflow once, then rethrow.
- **Tool/model body error:** Send errored completion telemetry and propagate or handle according to Agent-RT.
- **Cancellation:** Close started activities and workflow; propagate cancellation.
- **Completion telemetry error:** Log and suppress, even when `on_api_error="fail_closed"`.
- **Core connectivity failure:** `on_api_error` determines fail-open or fail-closed posture; do not treat auth/signing failures as ordinary connectivity fallback.

Agent-RT's own permission engine, guardrails, rate limiting and `ApprovalManager` remain active inside the inner tool execution path. Agent-RT approval pauses are distinct from OpenBox `REQUIRE_APPROVAL`.

## Data Serialization

`lifecycle_events.py` builds OpenBox event envelopes and records identity metadata. Tool starts can carry an `__openbox` sentinel with the configured `tool_type_map` classification and Agent-RT `side_effect`. `message_extraction.py` extracts text and response metadata for events without mutating user-owned requests.

## Performance Characteristics

- **Async enforcement:** Model, tool and HITL evaluations do not require sync/async bridging.
- **Pre-screen reuse:** Avoids evaluating the first matching model prompt twice.
- **Run isolation:** `ContextVar` prevents mutable per-run state from crossing concurrent tasks.
- **Network cost:** Depends on OpenBox Core latency, policy configuration, and approval duration; no unmeasured latency or overhead guarantee is asserted.

## References

- [Project Overview & PDR](./project-overview-pdr.md)
- [Code Standards](./code-standards.md)
- [Codebase Summary](./codebase-summary.md)
- [Project Roadmap](./project-roadmap.md)
