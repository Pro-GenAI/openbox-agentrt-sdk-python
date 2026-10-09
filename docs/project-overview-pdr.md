# Project Overview & PDR — OpenBox Agent-RT SDK (Python)

## Project Summary

OpenBox governance and observability adapter for [Agent-RT](https://github.com/Pro-GenAI/Agent-RT) agents. It enforces policy, guardrail redaction, and human-in-the-loop (HITL) approvals through governed `AgentLoop`, `ModelProvider`, `ToolRegistry` and `ToolExecutor` wrappers. HTTP/DB/file governance comes from the base OpenBox SDK.

**Package:** `openbox-agentrt-sdk-python`  
**Primary API:** `create_openbox_agentrt()`  
**Language:** Python 3.11–3.14  
**Compatibility:** Exactly `agent-rt==0.0.3`

## Goals & Vision

Enable developers to:

1. Enforce governance before an Agent-RT model or tool executes.
2. Apply guardrail redaction without altering original conversation messages.
3. Wait asynchronously for human approvals and reject timed-out requests.
4. Audit nested HTTP/DB/file operations under the correct workflow activity.
5. Adopt governance without modifying Agent-RT itself or copying the base SDK.

## Functional Requirements

### FR1: Governed Agent Loop

- **Requirement:** Wrap `AgentLoop.run` and `run_streaming` without modifying the original loop.
- **Implementation:** `govern_loop` clones the loop and wraps provider, registry, and executor seams.
- **Acceptance:** Governed calls retain Agent-RT method signatures and a blocked input never reaches the underlying loop.
- **Status:** Implemented; covered by offline tests.

### FR2: Lifecycle Interception

- **Requirement:** Emit workflow, prompt, model, and tool lifecycle events.
- **Enforcement points:** `SignalReceived`, pre-screen `ActivityStarted`, model starts, and tool starts.
- **Observation points:** `WorkflowStarted`, `ActivityCompleted`, `WorkflowCompleted`, `WorkflowFailed`.
- **Acceptance:** Completion telemetry never overrides a successful result or masks an original exception.
- **Status:** Implemented.

### FR3: Governance Verdict System

- **Requirement:** Handle ALLOW, CONSTRAIN, REQUIRE_APPROVAL, BLOCK and HALT, plus guardrail failures.
- **Acceptance:** Blocking verdicts and failed guardrails stop work before execution, and failed gates close their orphaned activities and workflow.
- **Status:** Implemented.

### FR4: HITL Approval Flows

- **Requirement:** Asynchronously await OpenBox approval decisions using the base SDK's `ApprovalPoller`.
- **Configuration:** `approval_poll_interval_seconds` and `approval_max_wait_seconds` (default one hour; explicit `None` means no client-side limit).
- **Acceptance:** Approved work continues; rejected, expired or timed-out work does not execute.
- **Status:** Implemented via `openbox_core`.

### FR5: Layer 2 Hook Governance

- **Requirement:** Use base-SDK HTTP/DB/file instrumentation, not framework-specific copied hooks.
- **Implementation:** One `OpenBoxRuntime` with installed instrumentation and private context store.
- **Acceptance:** Hook evaluation and audit remain owned by `openbox_core`.
- **Status:** Integrated; supported I/O depends on the installed base SDK.

### FR6: Layer 3 Activity Context Mapping

- **Requirement:** Associate nested I/O with the initiating Agent-RT model/tool call.
- **Implementation:** Activate `activity_scope` around call bodies; bind streaming context per iterator step.
- **Acceptance:** A model/tool activity ID and workflow ID are available to instrumented nested work.
- **Status:** Implemented.

### FR7: Async Execution & Safe Concurrency

- **Requirement:** Support async-only execution and independent concurrent runs.
- **Implementation:** `ContextVar`-backed `RunState`; no global run identity; `BaseException`-safe cleanup.
- **Acceptance:** Concurrent workflows do not mix run IDs, pre-screen results, or close flags.
- **Status:** Implemented and exercised by offline lifecycle tests.

### FR8: Pre-Screen Guardrails & Redaction

- **Requirement:** Evaluate the prompt before model execution and reuse the verdict for the first matching model call.
- **Implementation:** Pre-screened prompt/activity held in `RunState`; immutable request copy for `redacted_input`.
- **Acceptance:** Redacted text reaches the model, raw user-owned messages remain unchanged.
- **Status:** Implemented.

### FR9: Configuration & Developer Experience

- **Requirement:** One async factory, documented options, CI/security configuration, and an example using memory, skills and a delegated subagent.
- **Acceptance:** SDK and example resolve exactly Agent-RT `0.0.3` and pass offline quality gates.
- **Status:** Local tests, lockfile resolution and package build verified; supported-version matrix and CI/security sign-off remain release gates.

## Non-Functional Requirements

### NFR1: Security

- Never log API keys or private signing/workload credentials.
- Preserve base-SDK identity and verdict enforcement.
- A BLOCK/HALT/guardrail rejection must prevent the wrapped operation.

### NFR2: Reliability

- Close each emitted activity start, including failed gates, cancellation and early stream shutdown.
- Make completion telemetry best-effort even under `fail_closed`.
- Close newly built runtimes on startup validation failure.

### NFR3: Maintainability

- Keep integration logic in focused Agent-RT modules; import all governance infrastructure from `openbox_core`.
- Require Ruff, strict mypy, and deterministic offline regression tests.
- Keep Python and TypeScript lifecycle behavior aligned.

### NFR4: Compatibility

- Python 3.11–3.14 and exactly `agent-rt==0.0.3`.
- Base SDK dependency `openbox-sdk-python>=1.4.0`.
- Do not import LangChain, LangGraph, or other framework-specific OpenBox adapters.

### NFR5: Observability

- Correlate nested hook activity with workflow/model/tool IDs.
- Carry tool classification and side-effect metadata when available.
- Record model/tool completions and workflow outcomes without post-execution enforcement.

## Architecture

**Layer 1:** Governed Agent-RT public seams enforce prompt, model, and tool starts.  
**Layer 2:** Base SDK instrumentation governs/audits nested HTTP/DB/file I/O.  
**Layer 3:** Activity context maps nested I/O to workflow and activity identities.

See [System Architecture](./system-architecture.md) for the detailed event matrix and lifecycle flow.

## Key Metrics & Success Criteria

| Metric | Target | Verified locally / remaining |
|---|---|---|
| Unit/integration tests | Passing offline suite | 43 passed in last local gate |
| Package coverage | At least 80% | 94.42% in last local gate |
| Ruff / strict mypy | No errors | Passed in last local gate |
| Agent-RT dependency | Exactly 0.0.3 | Root and example manifests/locks aligned |
| Package build | Wheel and sdist | Built locally |
| Python compatibility | 3.11–3.14 | Full matrix remains to be verified |
| CI/security | Passing release gates | Not established by local tests alone |

## Dependencies

**Runtime:** `agent-rt==0.0.3` and `openbox-sdk-python>=1.4.0`. Traceloop is optional in Agent-RT 0.0.3's `observability` extra; the earlier 0.0.2 OpenTelemetry conflict is no longer a blocker.

**Development:** pytest, pytest-asyncio, pytest-cov, Ruff, mypy. The content-builder example additionally requires `agent-rt[openai]==0.0.3` and PyYAML.

## Risks & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| Agent-RT public seam changes | Governance bypass | Recheck registry/executor/provider/loop wrapping on upgrades |
| OpenBox Core outage | Allow or block depending on posture | Document `on_api_error` and test `fail_closed` for destructive work |
| Streaming context leakage | Wrong activity attribution | Scope each stream step, not the yielding generator |
| Cancellation during a gate | Orphaned activities | Close activities and workflow on `BaseException` paths |
| Async approval never completes | Long-running agent | Configure a bounded HITL wait |

## References

- [System Architecture](./system-architecture.md)
- [Code Standards](./code-standards.md)
- [Codebase Summary](./codebase-summary.md)
- [Project Roadmap](./project-roadmap.md)
- [SDK README](./../README.md)
