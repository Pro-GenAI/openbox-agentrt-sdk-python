# Code Standards — OpenBox Agent-RT SDK (Python)

## Architecture & Design Patterns

### Public Seam Wrappers (Agent-RT)

All agent interception goes through Agent-RT's public, async-first seams — never private runtime internals:

| Agent-RT seam | Adapter | Responsibility |
|---|---|---|
| `AgentLoop.run` / `run_streaming` | `GovernedAgentLoop` | Open and close a governed run |
| `ModelProvider.complete` / `stream` | `GovernedModelProvider` / `GovernedStreamingModelProvider` | Gate model calls, apply input redaction, correlate spans |
| `ToolRegistry.execute` | `GovernedToolRegistry` | Gate registered tool calls |
| `ToolExecutor.execute` | `GovernedToolExecutor` | Gate executor-based tool calls |

```python
class GovernedAgentLoop:
    def __init__(self, gov: OpenBoxAgentRTGovernance, loop: Any) -> None: ...

    async def run(self, agent: AgentConfig, messages, **kwargs) -> AgentRunResult: ...
    async def run_streaming(self, agent: AgentConfig, messages, on_event, **kwargs) -> AgentRunResult: ...
```

**Why wrappers over loop hooks:**
- `ModelProvider`, `ToolRegistry`, `ToolExecutor`, and `AgentLoop` are Agent-RT's stable public seams
- `AgentLoop` prefers registered tools through `ToolRegistry.execute` and otherwise uses `ToolExecutor.execute`; intercepting just one path silently misses governed work, so `govern_loop` wraps both
- The supplied loop is shallow-copied, never mutated
- The same wrappers work standalone inside `run_scope` for custom loops

### Async-Only Execution

Agent-RT model and tool interfaces are asynchronous. There is no sync/async bridge (unlike the LangChain reference):

```python
async with governance.run_scope("Summarize this document") as run:
    response = await governance.wrap_provider(provider).complete(request)
    run.output = {"status": "completed"}
```

**Implementation detail:** every start-stage gate uses `OpenBoxRuntime.aevaluate_lifecycle`, including HITL approval waits. Never invoke a synchronous gateway that cannot wait for a human decision.

### Factory Pattern

Single entry point for governance creation:

```python
async def create_openbox_agentrt(
    *,
    api_url: str | None = None,
    api_key: str | None = None,
    agent_name: str | None = None,
    validate: bool = True,
    **kwargs: Any,
) -> OpenBoxAgentRTGovernance:
    """Create configured governance.

    1. Reject unknown option names (TypeError)
    2. Build a private runtime via runtime_builder.build_runtime()
    3. Validate the API key (if validate=True, default); close the runtime on failure
    4. Return OpenBoxAgentRTGovernance
    """
```

**Purpose:**
- Centralize configuration logic in `factory.py` and `runtime_builder.py`
- Validate the API key on startup (fail fast)
- Never use process-wide global configuration or copied OpenBox infrastructure
- An injected runtime (tests, advanced callers) keeps its supplied identity/configuration

### Deferred Imports

`governance.py` imports the wrapper modules lazily inside `wrap_provider` / `wrap_tool_registry` / `wrap_tool_executor` / `govern_loop`:

```python
def wrap_provider(self, provider: ModelProvider) -> ModelProvider:
    from openbox_agentrt.model_provider import wrap_model_provider  # Deferred
    return wrap_model_provider(self, provider)
```

**Why:** the wrapper modules depend on `OpenBoxAgentRTGovernance` for typing; deferring keeps the dependency graph acyclic.

## File Organization

### `factory.py` (81 lines)

Single function: `create_openbox_agentrt()`
- Primary entry point for users
- Validates option names and the API key
- Returns configured `OpenBoxAgentRTGovernance`

### `options.py` (41 lines)

**`OpenBoxAgentRTOptions`** (dataclass):
- Connection, identity, `task_queue`, `on_api_error`, `tool_type_map`, `skip_tool_types`, `send_*_event` flags, approval polling
- No business logic, purely data structure

### `runtime_builder.py` (86 lines)

- `resolve_config()` — `OpenBoxConfig.resolve(env_prefix="OPENBOX_AGENTRT", ...)`
- `build_client()` — the ONE `EvaluationClient`, shared with the `ApprovalPoller`
- `build_runtime()` — private `ContextStore`, `CoreAdapter`, instrumentation install

### `governance.py` (228 lines)

**`OpenBoxAgentRTGovernance`:**
- `run_scope()` — `WorkflowStarted`, enforced `SignalReceived` + pre-screen, run close
- `gate()` — enforced start-stage evaluation; closes orphan rows and the workflow on failure
- `telemetry()` — best-effort completion send; never raises
- `wrap_provider()` / `wrap_tool_registry()` / `wrap_tool_executor()` / `govern_loop()`
- `activity_scope()` — binds Layer-3 activity context on the runtime's own `ContextStore`

### `run_state.py` (62 lines)

- `RunState` — workflow/run IDs, pre-screen verdict and prompt, `closed`, `output`
- `current_run()` / `require_run()` — `ContextVar` access; `require_run` fails closed outside a run

### `loop.py` (72 lines)

**`GovernedAgentLoop`:**
- Shallow-copies the loop and wraps provider, registry, and executor
- Runs `run` / `run_streaming` inside `run_scope` and reports the run result

### `model_provider.py` (171 lines)

**`GovernedModelProvider` / `GovernedStreamingModelProvider`:**
- Reuse the pre-screen verdict for the first matching model call
- Build + enforce `ActivityStarted(llm_call)`, apply redaction
- Call the model inside `activity_scope`
- Send `ActivityCompleted` with response metadata (or the error)

### `tool_governance.py` (147 lines)

**`govern_tool_call()`** (shared by both wrappers):
- Skip tools named in `skip_tool_types` (no events)
- Build + enforce `ActivityStarted(<tool>)` with the `__openbox` sentinel
- Execute the tool inside `activity_scope`
- Send `ActivityCompleted`

### `lifecycle_events.py` (135 lines) / `message_extraction.py` (75 lines)

Pure helpers: envelope builders, `error_info`, `__openbox` enrichment, text extraction, redaction, response metadata.

### `sdk_metadata.py` (12 lines) / `__init__.py` (36 lines)

Static SDK identity (engine `agentrt`) and public exports.

## Coding Conventions

### Type Hints (Required)

All functions must have complete type hints:

```python
async def govern_tool_call(
    gov: OpenBoxAgentRTGovernance,
    call: ToolCall,
    invoke: Callable[[], Awaitable[Any]],
    *,
    side_effect: str | None = None,
) -> Any:
    """Run ``invoke`` under OpenBox governance for ``call``."""
```

**Tools used:** `mypy --strict` on `openbox_agentrt/`. Agent-RT ships no `py.typed`, so its imports are the only `ignore_missing_imports` override. This package ships `openbox_agentrt/py.typed`, so consumers' type checkers use its annotations.

### Docstrings (Minimal)

Use minimal docstrings (one-liner + args/returns if needed) that document lifecycle guarantees, ownership, error behavior, and non-obvious constraints:

```python
async def telemetry(self, event: EventEnvelope) -> EvaluationResult | None:
    """Best-effort, non-enforcing send. Never raises: completions run AFTER the work,
    so a transport failure (``fail_closed``/auth) must not discard a result or mask
    the original error. Enforcement still fails closed through :meth:`gate`."""
```

**Style:** Google-style docstrings, focus on **why** not **what**.

### Error Handling

Use the base SDK's exception hierarchy (`openbox_core.errors`) for BLOCK, HALT, guardrail failure, and approval rejection/timeout:

| Event | Policy behavior |
|---|---|
| `WorkflowStarted` | Best-effort telemetry |
| `SignalReceived` and pre-screen `ActivityStarted` | Enforce before the run |
| Model/tool `ActivityStarted` | Enforce before the body (first matching model call reuses the pre-screen verdict) |
| `ActivityCompleted` and `WorkflowCompleted` / `WorkflowFailed` | Telemetry only |

```python
try:
    return await self._runtime.aevaluate_lifecycle(event)
except BaseException as exc:  # incl. cancellation during an approval wait
    if orphan_close is not None:
        await orphan_close(exc)      # same activity ID as the start row
    await self.close_run(run, exc)   # WorkflowFailed exactly once
    raise
```

**Never enforce a completion verdict.** Completion telemetry cannot undo a finished model/tool operation; a failed send must not mask the result or original error, even under `fail_closed`.

### Logging

Use the package logger:

```python
_logger = logging.getLogger("openbox_agentrt")

_logger.warning("OpenBox telemetry send failed (suppressed)", exc_info=True)
```

**Convention:** never log API keys, signing keys, workload identity material, or raw sensitive prompts. A suppressed post-work telemetry failure is logged at WARNING without interrupting agent execution.

### Testing Strategy

**Test organization:**
- `tests/test_governed_loop.py` — real `AgentLoop` end-to-end against `FakeCore`
- `tests/test_lifecycle_hardening.py` — orphan rows, cancellation, streaming deadlines, telemetry failures
- `tests/test_stream_lifecycle.py` — stream setup/iteration failures and inner iterator cleanup
- `tests/test_factory.py` / `tests/test_message_extraction.py` — focused unit tests
- `tests/test_package_boundaries.py` — import boundaries and the Agent-RT pin
- `tests/fakes.py` — scripted providers and `build_governance()` wired to `FakeCore`

**Coverage:** 43 tests, 94% package coverage; CI fails under 80%.

**Patterns:**
```python
async def test_blocked_tool_never_executes_and_orphan_start_is_closed() -> None:
    # Queue: WorkflowStarted, Signal, pre-screen, LLMCompleted (all allow); tool start -> block.
    allow = {"verdict": "allow"}
    gov, core = build_governance(
        allow, allow, allow, allow, {"verdict": "block", "reason": "tool denied"},
    )
    provider = FakeProvider(tool_turn("echo", {"text": "hi"}), final("never"))
    loop = gov.govern_loop(AgentLoop(provider, tool_registry=make_registry(calls)))

    with pytest.raises(GovernanceBlockedError):
        await loop.run(AGENT, [user("go")])

    assert calls == []
    assert event_types(core)[-1] == "WorkflowFailed"
```

## Module Dependencies

**Dependency graph (acyclic):**

```
factory.py
  ├─ runtime_builder.py ──> openbox_core
  └─ governance.py
       ├─ run_state.py
       ├─ lifecycle_events.py
       ├─ model_provider.py (deferred)
       │   └─ message_extraction.py
       ├─ tool_governance.py (deferred)
       └─ loop.py (deferred)

Agent-RT public types ──> model_provider.py / tool_governance.py / loop.py
```

**Rule:** Production code may import `agent_rt` and `openbox_core`, but never LangChain, LangGraph, or other OpenBox framework adapters. Deferred imports only to break typing cycles.

## Code Quality Standards

### Linting & Formatting

**Tool:** ruff (configured in `pyproject.toml`)

```bash
ruff check openbox_agentrt/ tests/ examples/
ruff format openbox_agentrt/ tests/ examples/
```

Formatting is ruff's Black-compatible style, matching the LangChain reference: once a call
wraps, each argument goes on its own line.

**Rules enforced:**
- E (PEP 8 errors)
- F (undefined names)
- I (import sorting)
- UP (Python syntax upgrade)
- B (bugbear)
- C4 (comprehensions)
- PIE (pie)
- RUF (ruff-specific)

### Type Checking

**Tool:** mypy (strict mode)

```bash
mypy openbox_agentrt/
```

**Rule:** All functions must pass strict type checking (no `Any` without justification — Agent-RT seam objects are the documented exception).

### Line Length

**Limit:** 100 characters (per ruff config).

### File Size

**Limit:** < 240 lines per file (largest: `governance.py`, 225 lines).

**Rationale:** Optimal for context window, encourages modularization.

## Async/Await Patterns

### Rule 1: Async Only

Every governed seam is a coroutine or async iterator; do not add sync variants:

```python
async def complete(self, request: ModelRequest) -> ModelResponse: ...
async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
```

### Rule 2: Context Propagation

Bind `activity_scope` around the **model/tool body**. For streams, bind around each `__anext__` step, never across an outgoing `yield`:

```python
while True:
    try:
        with self._gov.activity_scope(run, activity_id, "llm_call"):
            event = await iterator.__anext__()  # bound only for this step
    except StopAsyncIteration:
        break
    yield event  # consumer may resume in another task context
```

**Why:** `AgentLoop` runs each stream step in its own task under a deadline; a `ContextVar` token reset from another context raises.

### Rule 3: No Blocking I/O in Async Code

Async functions must not use blocking I/O; all governance sends go through the base SDK's async client (`aevaluate_lifecycle`, `gate.aevaluate`).

## Event Building & Serialization

### Pattern: Immutable Events

Events are base-SDK `EventEnvelope`s built by `openbox_core.contracts.events` factories. `session_id` and `agent_name` travel via `extra` so the base contract surface is not widened.

### Building Events

Use helper functions in `lifecycle_events.py`:

```python
# ✓ Good: Use builder
event = build_activity_started(
    gov.identity(run),
    activity_id=activity_id,
    activity_type="llm_call",
    activity_input=[{"prompt": prompt}],
)
```

Errors are always structured (`error_info()` → `{"type", "message"}`); Core rejects a bare string.

### PII Redaction

Apply the verdict's `guardrails.redacted_input` to the request before the model call:

```python
if result.guardrails and result.guardrails.redacted_input:
    request = replace(
        request, messages=apply_redaction(request.messages, result.guardrails.redacted_input)
    )
```

Redaction replaces the most recent user text by creating **new** frozen `ModelMessage` instances; the remaining history and non-text parts are preserved and the caller's conversation is never mutated.

## Configuration Management

### Pattern: Per-Instance Config (No Global State)

Configuration is resolved per governance instance:

```python
config = OpenBoxConfig.resolve(
    env_prefix="OPENBOX_AGENTRT",
    api_url=options.api_url,
    api_key=options.api_key,
    ...
)
```

**Precedence:** explicit arguments → `OPENBOX_AGENTRT_*` → `OPENBOX_*` → base-SDK defaults. `on_api_error` defaults to `fail_open`; select `fail_closed` for destructive tools. Identity selection and signing belong to the base SDK.

### Pattern: Options Dataclass

```python
@dataclass
class OpenBoxAgentRTOptions:
    agent_name: str | None = None
    session_id: str | None = None
    task_queue: str = "agentrt"
    on_api_error: str = "fail_open"
    governance_timeout: float = 30.0
    tool_type_map: dict[str, str] = field(default_factory=dict)
    approval_max_wait_seconds: float | None = 3600.0
```

Agent-RT `0.0.3` is required **exactly**, including the content-builder example. Its optional `observability` extra avoids the Traceloop/OpenTelemetry conflict present in `0.0.2`. Update both `uv.lock` files only alongside a deliberate version change.

## Performance Considerations

### Async I/O (Non-Blocking)

All governance HTTP calls use the base SDK's async `EvaluationClient`; one client per runtime is shared with the `ApprovalPoller` and closed by `governance.aclose()`.

### Parallel Execution

Agent-RT may execute tool calls concurrently (`execution_mode="parallel"`). Each tool call gets its own activity ID and `activity_scope`, and run identity lives in a `ContextVar`, so concurrent tools and concurrent runs never share correlation state.

### Caching (Pre-Screen)

The pre-screen verdict from `run_scope` is reused by the first model call for the same prompt instead of re-evaluating it:

```python
if run.pre_screen is not None and not run.pre_screen_used and prompt == run.pre_screen_prompt:
    run.pre_screen_used = True
    activity_id, result = f"{run.run_id}-pre", run.pre_screen
```

## References

- **System Architecture:** `./system-architecture.md` — Data flow, layer descriptions
- **Codebase Summary:** `./codebase-summary.md` — Module organization, class/function lists
- **Project Overview & PDR:** `./project-overview-pdr.md` — Requirements and acceptance
- **Project Roadmap:** `./project-roadmap.md` — Release gates and future work
- **Agent-RT:** `https://github.com/Pro-GenAI/Agent-RT` — Runtime seams (`ModelProvider`, `ToolRegistry`, `AgentLoop`)
- **OpenBox base SDK:** `openbox-sdk-python` (`openbox_core`) — Governance infrastructure
