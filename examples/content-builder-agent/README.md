# Content Builder Agent

A content writing agent for writing blog posts, LinkedIn posts, and tweets with cover images included.

**This example demonstrates how to use Agent-RT with OpenBox governance through three filesystem primitives:**
- **Memory** (`AGENTS.md`) – persistent context like brand voice and style guidelines
- **Skills** (`skills/*/SKILL.md`) – workflows for specific tasks, loaded on demand
- **Subagents** (`subagents.yaml`) – specialized agents for delegated tasks like research

The `content_writer.py` script shows how to combine these into a working agent using Agent-RT with OpenBox governance.

## Quick Start

```bash
# Set API keys
export OPENAI_API_KEY="..."
export OPENAI_MODEL="gpt-4o-mini"
export OPENBOX_API_URL="https://core.openbox.ai"
export OPENBOX_API_KEY="obx_live_..."
export OPENBOX_AGENT_DID="did:aip:..."          # Required by default for newly registered agents
export OPENBOX_AGENT_PRIVATE_KEY="..."          # Required by default for newly registered agents
export GOOGLE_API_KEY="..."      # For image generation
export TAVILY_API_KEY="..."      # For web search (optional)

# Run (uv automatically installs dependencies on first run)
cd examples/content-builder-agent
uv run python content_writer.py "Write a blog post about prompt engineering"
```

**More examples:**
```bash
uv run python content_writer.py "Create a LinkedIn post about AI agents"
uv run python content_writer.py "Write a Twitter thread about the future of coding"
```

OpenBox enables DID signing by default for newly registered agents. If signing
has been explicitly disabled for this agent in OpenBox, you can omit
`OPENBOX_AGENT_DID` and `OPENBOX_AGENT_PRIVATE_KEY`.

To use Anthropic instead of OpenAI, set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`
and install the matching Agent-RT provider extra. The example installs the local
SDK in editable mode through `[tool.uv.sources]`.

## How It Works

The agent is configured by files on disk, not code:

```
content-builder-agent/
├── AGENTS.md                    # Brand voice & style guide
├── subagents.yaml               # Subagent definitions
├── skills/
│   ├── blog-post/
│   │   └── SKILL.md             # Blog writing workflow
│   └── social-media/
│       └── SKILL.md             # Social media workflow
└── content_writer.py            # Wires it together (includes tools)
```

| File | Purpose | When Loaded |
|------|---------|-------------|
| `AGENTS.md` | Brand voice, tone, writing standards | Always (system prompt) |
| `subagents.yaml` | Research subagent config (prompt, model, tools) | When research tool runs |
| `skills/*/SKILL.md` | Content-specific workflows | Always (appended to system prompt) |

## Architecture

```python
# Load memory + skills into system prompt
agents_md = (EXAMPLE_DIR / "AGENTS.md").read_text()
skills_text = load_skills(EXAMPLE_DIR / "skills")

# Create OpenBox governance
governance = await create_openbox_agentrt(
    api_url=os.environ["OPENBOX_API_URL"],
    api_key=os.environ["OPENBOX_API_KEY"],
    agent_did=os.environ.get("OPENBOX_AGENT_DID"),
    agent_private_key=os.environ.get("OPENBOX_AGENT_PRIVATE_KEY"),
    agent_name="ContentWriter",
)

# Create a governed agent loop
loop = governance.govern_loop(AgentLoop(load_model(), tool_registry=registry))

# Run with governance applied automatically
result = await loop.run_streaming(agent, [user_message(task)], print_progress)
```

**Flow:**
1. Agent receives task → loads relevant skill (blog-post or social-media)
2. Calls `research` tool → runs the researcher subagent as its own governed run → saves to `research/`
3. Reads research findings → writes content → saves to `blogs/`, `linkedin/`, or `tweets/`
4. Generates cover image with Gemini → saves alongside content

File tools refuse paths that would escape the example directory.

## Output

```
blogs/
└── prompt-engineering/
    ├── post.md       # Blog content
    └── hero.png      # Generated cover image

linkedin/
└── ai-agents/
    ├── post.md       # Post content
    └── image.png     # Generated image

research/
└── prompt-engineering.md   # Research notes
```

## Customizing

**Change the voice:** Edit `AGENTS.md` to modify brand tone and style.

**Add a content type:** Create `skills/<name>/SKILL.md` with YAML frontmatter:
```yaml
---
name: newsletter
description: Use this skill when writing email newsletters
---
# Newsletter Skill
...
```

**Add a subagent:** Add to `subagents.yaml`:
```yaml
editor:
  description: Review and improve drafted content
  model: inherit
  system_prompt: |
    You are an editor. Review the content and suggest improvements...
  tools: []
```

**Add a tool:** Define an async handler and `ToolDefinition` in `content_writer.py`, add it to `TOOLS`, and include its name in the `build_registry([...])` list. Classify it for policies through `tool_type_map`.

## Key Differences from LangChain Version

| Aspect | LangChain | Agent-RT |
|--------|-----------|----------|
| Agent creation | `create_agent()` from langchain | `AgentConfig` + `AgentLoop` |
| Memory/Skills | Loaded manually into system prompt | Loaded manually into system prompt |
| Subagents | Custom `research` tool spawning sub-agent | Custom `research` tool running a nested governed `AgentLoop` |
| Governance | `create_openbox_langchain_middleware()` via AgentMiddleware | `create_openbox_agentrt()` + `governance.govern_loop(...)` |
| Execution | Sync (`stream`) | Async (`run_streaming`) |
| Tools | `@tool` decorator | `ToolDefinition` + async `ToolRegistry` handler |

## Requirements

- Python 3.11–3.14
- `OPENAI_API_KEY` + `OPENAI_MODEL` - For the main agent and researcher
- `OPENBOX_API_URL` + `OPENBOX_API_KEY` - For OpenBox governance
- `GOOGLE_API_KEY` - For image generation (Gemini)
- `TAVILY_API_KEY` - For web search (optional, research still works without it)
