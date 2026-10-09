# Project Roadmap — OpenBox Agent-RT SDK (Python)

## Current Status

**SDK version:** 1.0.0  
**Agent-RT requirement:** Exactly `0.0.3`  
**Status:** SDK and example dependency locks resolve, local tests and package build pass; release sign-off remains separate.

### Completed

- [x] Governed `AgentLoop`, model provider, tool registry and tool executor wrappers.
- [x] Start-event verdict enforcement, pre-screen reuse, and immutable redaction.
- [x] Async HITL integration through the base SDK.
- [x] Per-run `ContextVar` identity and instrumented activity context.
- [x] Failure, cancellation, stream-closure and telemetry hardening tests.
- [x] Root and content-builder example pins/lockfiles for `agent-rt==0.0.3`.
- [x] Five-document architecture and engineering documentation structure, matching the Python LangChain reference.
- [x] Content-builder example ported from the LangChain reference (research subagent, file, search, and image tools).

### Outstanding Release Checks

- [x] Verify local Linux dependency resolution and tests across Python 3.11–3.14.
- [ ] Verify clean installs on release OS targets (Linux, macOS, Windows).
- [ ] Execute supported CI and security workflows.
- [ ] Verify packaging and installed-wheel behavior on release platforms.
- [ ] Obtain explicit publication approval.

## Phases

### Phase 1: Core Agent-RT Integration (IMPLEMENTED)

**Deliverables:**

- `create_openbox_agentrt` and `OpenBoxAgentRTOptions`.
- Workflow, signal, model and tool lifecycle handling.
- Registry and executor coverage; streaming-aware provider wrappers.
- Failure-safe activity closure and tool classification metadata.

**Key decisions:** Use Agent-RT public seams instead of LangChain middleware; keep all policy, identity, transport and instrumentation logic in the base SDK.

### Phase 2: Documentation & Example (IMPLEMENTED)

**Deliverables:**

- Root `README.md` and maintenance `AGENTS.md`.
- `project-overview-pdr.md`, `system-architecture.md`, `code-standards.md`, `codebase-summary.md`, and this roadmap.
- Content-builder agent using `AGENTS.md`, `skills/*/SKILL.md` and `subagents.yaml`, with both writer and researcher governed (port of the LangChain reference example).
- Exact 0.0.3 example resolution and reproducible `uv.lock`.

### Phase 3: Release Validation (PENDING)

**Tasks:**

- [x] Run locked dependency resolution and offline tests locally with Python 3.11–3.14 for the SDK.
- [ ] Run `uv sync --locked` for the example and verify release-platform installations.
- [ ] Exercise the tests, Ruff and mypy under supported Python versions.
- [ ] Build and install the wheel/sdist in an isolated environment.
- [ ] Review license, package metadata, README rendering, CI and security results.
- [ ] Publish only after explicit approval.

## Release Roadmap

### v1.0.0 (Current SDK Metadata)

The SDK implements core lifecycle enforcement, async HITL, context attribution, and a governed example. Package version `1.0.0` is a metadata value, not proof of a completed public release.

### Post-Release Candidates (Unscheduled)

- Offline demonstration of early streaming cancellation.
- Example of destructive tool approval with `fail_closed`.
- Example of concurrent governed runs sharing one governance instance.
- Cross-language contract parity tests for event payloads, redaction and error closure.

No dates or versions have been committed for these optional enhancements.

## Success Metrics

| Metric | Target | Current evidence |
|---|---|---|
| Tests | Offline suite passes | 47 passed in last local run |
| Coverage | ≥ 80% | 94.67% in last local run |
| Lint/typecheck | Ruff and strict mypy clean | Passed locally |
| Agent-RT compatibility | Exact 0.0.3 | Root + example manifests and locks |
| Packaging | Wheel/sdist build | Passed locally |
| Python version matrix | 3.11–3.14 | 47/47 tests pass on Linux with Python 3.11, 3.12, 3.13 and 3.14; multi-OS CI pending |
| Security/CI | Green release gates | Pending verification |

## Known Limitations

| Limitation | Current handling | Follow-up |
|---|---|---|
| Agent-RT is async-only | No synchronous governance bridge | Keep API async |
| Content-builder search/images need third-party keys | Tavily (`web_search`) and Gemini tools return an error result when unconfigured | Add offline doubles for an example smoke run |
| Agent-RT's own approvals differ from OpenBox HITL | Independent internal tool-approval state | Document and test combined workflows |
| Provider/loop internals can change | Recheck public seams on each compatibility bump | Run both SDK test suites |

## Dependencies & Compatibility

### Required
- Python 3.11–3.14
- agent-rt == 0.0.3
- openbox-sdk-python >= 1.4.0

### Optional
- Agent-RT provider extras (`agent-rt[openai]`, `agent-rt[anthropic]`)
- `agent-rt[observability]` (Traceloop; optional since 0.0.3)

### Development
- pytest, pytest-asyncio, pytest-cov
- ruff (linting)
- mypy (type checking)

### Example
- `agent-rt[openai]==0.0.3` with the local editable SDK
- google-genai (cover/social images), tavily-python (web search), rich, python-dotenv, pyyaml

Keep the Python and TypeScript SDKs aligned on **exactly 0.0.3** until an intentional, tested version change.

## Testing & QA

### Test Coverage

**Current:** 47 tests passing on each of Python 3.11–3.14 (Linux); 94.67% package coverage on Python 3.14 (94.88% on Python 3.11).

**Areas:**
- Full governed `AgentLoop` lifecycle (registry and executor tool paths, streaming)
- Verdict enforcement at signal, pre-screen, model, and tool gates
- Guardrail redaction and REQUIRE_APPROVAL fail-safe
- Orphan-row closure, cancellation, run deadlines, early stream close
- Telemetry failure suppression and concurrent-run isolation
- Package boundaries and the Agent-RT pin

### CI/CD

```bash
# Install
uv sync --extra dev --locked

# Test
pytest tests/ --cov=openbox_agentrt --cov-fail-under=80 -q

# Lint
ruff check openbox_agentrt/ tests/ examples/

# Type check
mypy openbox_agentrt/

# Build
uv build
```

These are release commands, not a claim that all platform-specific checks have already been completed.

### Manual Testing Checklist

- [ ] Create governance with `create_openbox_agentrt` against a real Core
- [ ] Run a governed loop with ALLOW verdicts
- [ ] Run with BLOCK verdict (expect `GovernanceBlockedError`)
- [ ] Run with REQUIRE_APPROVAL + approve in HITL
- [ ] Run with REQUIRE_APPROVAL + reject in HITL
- [ ] Verify pre-screen verdict reused by the first model call
- [ ] Verify guardrail redaction reaches the model request
- [ ] Verify HTTP/DB/file spans attribute to the right tool activity
- [ ] Run the content-builder example end to end

## Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| New Agent-RT release alters seams | High | Pin 0.0.3 and retest wrappers deliberately |
| Offline fake differs from deployed Core | High | Include a separate authenticated integration gate |
| Telemetry failure masks agent result | High | Suppress completion send failures and test |
| Concurrent context leakage | High | `ContextVar` run state and isolated context stores |

## Next Steps

1. Complete reproducible installs and the supported Python-version matrix.
2. Run repository CI and security release gates.
3. Verify installed artifacts, documentation links and published metadata.
4. Obtain explicit publication approval before releasing.

## References

- [Project Overview & PDR](./project-overview-pdr.md)
- [System Architecture](./system-architecture.md)
- [Code Standards](./code-standards.md)
- [Codebase Summary](./codebase-summary.md)
- [SDK README](./../README.md)
