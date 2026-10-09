# Codebase Summary — OpenBox Agent-RT SDK (Python)

## Package Structure

```text
openbox_agentrt/
├── __init__.py             # Public imports and __version__
├── factory.py              # create_openbox_agentrt() factory
├── options.py              # OpenBoxAgentRTOptions
├── runtime_builder.py      # Base SDK config/client/runtime/approval wiring
├── governance.py           # Run scope, start gates, telemetry, activity scopes
├── run_state.py            # Per-run ContextVar state
├── loop.py                 # GovernedAgentLoop
├── model_provider.py       # Governed complete()/stream()
├── tool_governance.py      # Governed registry and executor
├── lifecycle_events.py     # OpenBox event-envelope builders
├── message_extraction.py  # Prompt/response extraction and redaction
├── sdk_metadata.py        # SDK engine/language/version
└── py.typed               # PEP 561 typed-package marker

tests/
├── fakes.py
├── test_factory.py
├── test_governed_loop.py
├── test_lifecycle_hardening.py
├── test_message_extraction.py
├── test_no_prompt_governance.py
├── test_package_boundaries.py
└── test_stream_lifecycle.py

examples/content-builder-agent/
├── AGENTS.md
├── content_writer.py
├── pyproject.toml
├── uv.lock
├── README.md
├── subagents.yaml
└── skills/
    ├── blog-post/SKILL.md
    └── social-media/SKILL.md
```

## Code Statistics

The package consists of twelve focused Python modules. Its release gate requires at least 80% coverage; the last local test gate reported **47 passing tests and 94.67% coverage** (Python 3.14). This is a local result, not evidence of the full supported-Python CI matrix.

| Area | Modules | Responsibility |
|---|---|---|
| Configuration | `factory.py`, `options.py`, `runtime_builder.py` | One configured and validated base-SDK runtime |
| Lifecycle | `governance.py`, `run_state.py`, `lifecycle_events.py` | Run identity, event construction, gate and telemetry paths |
| Agent-RT interception | `loop.py`, `model_provider.py`, `tool_governance.py` | Govern model and tool execution |
| Data and identity | `message_extraction.py`, `sdk_metadata.py`, `__init__.py` | Redaction helpers, package exports and SDK branding |

**Governance infrastructure:** All provided by `openbox-sdk-python` / `openbox_core` (not copied):

- `OpenBoxRuntime` and its lifecycle evaluator;
- `EvaluationClient` / `CoreAdapter` / `ApprovalPoller`;
- `OpenBoxConfig`, `ContextStore`, and `activity_scope`;
- HTTP/DB/file instrumentation, error types and event contracts.

## Key Classes

**Agent-RT integration:**

- `OpenBoxAgentRTGovernance` — exposes `run_scope`, `gate`, `telemetry` and individual wrapper methods; owns a runtime unless injected.
- `GovernedAgentLoop` — clones the supplied loop, wraps provider/registry/executor, and preserves `run` and `run_streaming`.
- `GovernedModelProvider` / `GovernedStreamingModelProvider` — enforce model starts, apply redaction, correlate nested I/O and close model activities.
- `GovernedToolRegistry` / `GovernedToolExecutor` — enforce tool starts on both Agent-RT execution paths.
- `RunState` — context-local workflow/run identifiers, pre-screen metadata and close flags.
- `OpenBoxAgentRTOptions` — configuration and behavioral flags.

## Key Functions

- `create_openbox_agentrt(...)` — asynchronous primary factory; validates credentials by default.
- `build_runtime(...)` / `build_client(...)` / `resolve_config(...)` — create one base-SDK runtime and shared approval client.
- `wrap_model_provider(...)` — selects the streaming-aware wrapper when the provider supports it.
- `extract_last_user_text(...)` / `apply_redaction(...)` — extract prompts and rebuild immutable messages.
- `build_activity_started(...)` / `build_activity_completed(...)` and workflow builders — construct lifecycle envelopes.

## Test Structure

The `tests/` package uses base-SDK fakes and scripted providers/tools. `test_factory.py` covers startup validation and the `skip_tool_types` / `send_*_event` options. `test_lifecycle_hardening.py` exercises blocked orphan starts, cancellation (task cancel, cancellation token, run deadline), pre-screen reuse, streaming boundaries, and non-enforcing completion sends. `test_no_prompt_governance.py` checks that empty/missing user text cannot bypass model-start policy enforcement and that a failed instrumentation install closes its runtime. `test_stream_lifecycle.py` additionally checks that stream-factory and iterator errors close the LLM activity and release the iterator. `test_package_boundaries.py` prevents imports of framework-specific OpenBox adapters and guards the exact Agent-RT dependency.

The example ports the LangChain reference's `content_writer.py`: a `research` tool runs the researcher subagent as its own governed `AgentLoop` (`web_search` via Tavily, `write_file`), and the writer uses `write_file`, `read_file`, `generate_cover`, and `generate_social_image` (Gemini). File tools refuse paths outside the example directory.

## Module Dependencies

```text
openbox_agentrt.__init__
    ├── factory -> runtime_builder -> openbox_core
    ├── governance -> run_state + lifecycle_events
    ├── loop -> governed model/tool wrappers
    ├── model_provider -> message_extraction
    ├── tool_governance -> lifecycle_events
    └── sdk_metadata

agent_rt public seams: ModelProvider, ToolRegistry, ToolExecutor, AgentLoop
```

## File Organization Rationale

**Split by responsibility:** Runtime construction, lifecycle event building, request mutation, execution interception, and run state live in distinct modules. The adapter never reimplements OpenBox policies, identity verification, HTTP/DB/file hooks, or approval polling.

**Behavioral invariants:** `AgentLoop` checks the tool registry before the executor. Both wrappers are required. Run-scoped `ContextVar` state and per-stream-step `activity_scope` make concurrent runs and cancellation cleanup safe. Completion events are telemetry only, not a post-hoc governance gate.

## Dependencies & Compatibility

| Kind | Requirement |
|---|---|
| Python | 3.11–3.14 |
| Agent runtime | `agent-rt==0.0.3` (exact) |
| Base governance | `openbox-sdk-python>=1.4.0` |
| Development | pytest, pytest-asyncio, pytest-cov, Ruff, strict mypy |
| Example | `agent-rt[openai]==0.0.3`, PyYAML, editable local SDK |

Published Agent-RT `0.0.3` makes Traceloop optional through the `observability` extra. The SDK and example each maintain a `uv.lock`. See [Project Roadmap](./project-roadmap.md) for remaining cross-version and release checks.

## References

- [Code Standards](./code-standards.md) — implementation and test conventions
- [System Architecture](./system-architecture.md) — lifecycle and governance layers
- [Project Overview & PDR](./project-overview-pdr.md) — functional requirements
- [SDK README](./../README.md) — public installation and usage
