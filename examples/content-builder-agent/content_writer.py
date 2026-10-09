#!/usr/bin/env python3
"""
Content Builder Agent (Agent-RT + OpenBox Governance)

A content writer agent configured entirely through files on disk:
- AGENTS.md defines brand voice and style guide
- skills/ provides specialized workflows (blog posts, social media)
- subagents.yaml defines the researcher subagent configuration

Usage:
    uv run python content_writer.py "Write a blog post about AI agents"
    uv run python content_writer.py "Create a LinkedIn post about prompt engineering"

This is a port of the LangChain SDK's content_writer.py onto Agent-RT's public
APIs: tools are ``ToolRegistry`` handlers instead of ``@tool`` functions, and
governance wraps the ``AgentLoop`` (``governance.govern_loop``) instead of
being injected as middleware.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from agent_rt import (
    AgentConfig,
    AgentLoop,
    AgentRunResult,
    CancellationToken,
    ContentPart,
    ModelMessage,
    ModelSettings,
    ModelStreamEvent,
    ToolDefinition,
    ToolHandler,
    ToolRegistry,
    load_model,
)
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from openbox_agentrt import OpenBoxAgentRTGovernance, create_openbox_agentrt

load_dotenv()

logging.basicConfig(level=logging.WARNING)
logging.getLogger("openbox_agentrt").setLevel(logging.DEBUG)

EXAMPLE_DIR = Path(__file__).resolve().parent
console = Console()


def configured_model() -> str:
    """Model name for Agent-RT's ``load_model()`` provider (OPENAI_MODEL or ANTHROPIC_MODEL)."""
    model = os.environ.get("OPENAI_MODEL") or os.environ.get("ANTHROPIC_MODEL")
    if not model:
        raise RuntimeError("Set OPENAI_MODEL or ANTHROPIC_MODEL before running this example.")
    return model


def load_example_model() -> Any:
    """Prefer HTTP for OpenAI-compatible endpoints; allow WebSockets explicitly.

    Custom OpenAI endpoints may accept aliases not listed in /models, so
    skip model-list preflight only for those endpoints.
    """
    environ = {**os.environ, "OPENAI_WEBSOCKET": os.environ.get("OPENAI_WEBSOCKET", "false")}
    return load_model(environ=environ, validate=not bool(environ.get("OPENAI_BASE_URL")))


def example_path(file_path: str) -> Path:
    """Resolve a model-supplied relative path, refusing to escape the example directory."""
    path = (EXAMPLE_DIR / file_path).resolve()
    if EXAMPLE_DIR not in path.parents:
        raise ValueError(f"path must stay inside the example directory: {file_path}")
    return path


def string_schema(**properties: str) -> dict[str, Any]:
    """JSON schema for an object whose properties are all required strings."""
    return {
        "type": "object",
        "required": list(properties),
        "properties": {
            name: {"type": "string", "description": description}
            for name, description in properties.items()
        },
        "additionalProperties": False,
    }


# ═══════════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════════


async def web_search(arguments: Mapping[str, Any], _token: CancellationToken | None) -> Any:
    """Search the web for current information."""
    try:
        from tavily import TavilyClient

        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return {"error": "TAVILY_API_KEY not set"}

        client = TavilyClient(api_key=api_key)
        return await asyncio.to_thread(
            client.search,
            arguments["query"],
            max_results=int(arguments.get("max_results", 5)),
            topic=arguments.get("topic", "general"),
        )
    except Exception as e:
        return {"error": f"Search failed: {e}"}


async def write_file(arguments: Mapping[str, Any], _token: CancellationToken | None) -> str:
    """Write content to a file. Creates parent directories as needed."""
    try:
        path = example_path(arguments["file_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"])
        return f"File written to {path}"
    except Exception as e:
        return f"Error: {e}"


async def read_file(arguments: Mapping[str, Any], _token: CancellationToken | None) -> str:
    """Read content from a file."""
    try:
        return example_path(arguments["file_path"]).read_text()
    except Exception as e:
        return f"Error: {e}"


async def generate_image(prompt: str, output_path: Path) -> str:
    """Generate one image with Gemini and save it to ``output_path``."""
    if not (os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")):
        return "Image generation skipped: set GOOGLE_API_KEY or GEMINI_API_KEY."

    from google import genai

    client = genai.Client()
    response = await client.aio.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=[prompt],
    )

    for part in response.parts or []:
        if part.inline_data is not None and part.inline_data.data:
            data = part.inline_data.data
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(data if isinstance(data, bytes) else base64.b64decode(data))
            return f"Image saved to {output_path}"

    return "No image generated"


async def generate_cover(arguments: Mapping[str, Any], _token: CancellationToken | None) -> str:
    """Generate a cover image for a blog post."""
    try:
        output_path = example_path(f"blogs/{arguments['slug']}/hero.png")
        return await generate_image(arguments["prompt"], output_path)
    except Exception as e:
        return f"Error: {e}"


async def generate_social_image(
    arguments: Mapping[str, Any], _token: CancellationToken | None
) -> str:
    """Generate an image for a social media post."""
    try:
        output_path = example_path(f"{arguments['platform']}/{arguments['slug']}/image.png")
        return await generate_image(arguments["prompt"], output_path)
    except Exception as e:
        return f"Error: {e}"


TOOLS: dict[str, tuple[ToolDefinition, ToolHandler]] = {
    "web_search": (
        ToolDefinition(
            name="web_search",
            description=(
                "Search the web for current information. Returns search results with "
                "titles, URLs, and content excerpts."
            ),
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (be specific and detailed)",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)",
                    },
                    "topic": {
                        "type": "string",
                        "enum": ["general", "news"],
                        "description": '"general" for most queries, "news" for current events',
                    },
                },
                "additionalProperties": False,
            },
            side_effect="read",
        ),
        web_search,
    ),
    "write_file": (
        ToolDefinition(
            name="write_file",
            description="Write content to a file. Creates parent directories as needed.",
            input_schema=string_schema(
                file_path="Relative path from the project root (e.g., 'blogs/my-post/post.md')",
                content="The content to write",
            ),
            side_effect="write",
        ),
        write_file,
    ),
    "read_file": (
        ToolDefinition(
            name="read_file",
            description="Read content from a file.",
            input_schema=string_schema(
                file_path="Relative path from the project root (e.g., 'research/topic.md')",
            ),
            side_effect="read",
        ),
        read_file,
    ),
    "generate_cover": (
        ToolDefinition(
            name="generate_cover",
            description="Generate a cover image for a blog post.",
            input_schema=string_schema(
                prompt="Detailed description of the image to generate.",
                slug="Blog post slug. Image saves to blogs/<slug>/hero.png",
            ),
            side_effect="write",
        ),
        generate_cover,
    ),
    "generate_social_image": (
        ToolDefinition(
            name="generate_social_image",
            description="Generate an image for a social media post.",
            input_schema=string_schema(
                prompt="Detailed description of the image to generate.",
                platform='Either "linkedin" or "tweets"',
                slug="Post slug. Image saves to <platform>/<slug>/image.png",
            ),
            side_effect="write",
        ),
        generate_social_image,
    ),
}


def build_registry(names: list[str]) -> ToolRegistry:
    """Register the named tools from ``TOOLS`` in a fresh registry."""
    registry = ToolRegistry()
    for name in names:
        if name not in TOOLS:
            raise ValueError(f"Unknown tool in subagents.yaml: {name}")
        definition, handler = TOOLS[name]
        registry.register(definition, handler=handler)
    return registry


# ═══════════════════════════════════════════════════════════════════
# Skill & subagent loading
# ═══════════════════════════════════════════════════════════════════


def load_skills(skills_dir: Path) -> str:
    """Load all SKILL.md files and return their combined content."""
    skills_text = []
    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        skills_text.append(skill_file.read_text())
    return "\n\n---\n\n".join(skills_text)


def load_subagent_config(config_path: Path) -> dict[str, Any]:
    """Load subagent definitions from YAML."""
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


# ═══════════════════════════════════════════════════════════════════
# Researcher subagent
# ═══════════════════════════════════════════════════════════════════


def user_message(text: str) -> ModelMessage:
    return ModelMessage(role="user", content=(ContentPart(type="text", text=text),))


def last_assistant_text(result: AgentRunResult) -> str:
    """Text of the run's final assistant response."""
    response = result.final_response
    if response is None:
        return ""
    return "".join(part.text or "" for part in response.message.content if part.type == "text")


async def run_researcher(
    governance: OpenBoxAgentRTGovernance,
    topic: str,
    save_to: str,
) -> str:
    """Create and run the researcher subagent as its own governed Agent-RT run."""
    config = load_subagent_config(EXAMPLE_DIR / "subagents.yaml")
    researcher_spec = config.get("researcher", {})
    system_prompt = researcher_spec.get("system_prompt", "You are a research assistant.")
    model_name = researcher_spec.get("model", "inherit")
    if model_name == "inherit":
        model_name = configured_model()

    researcher = AgentConfig(
        name="researcher",
        instructions=system_prompt,
        model=ModelSettings(model=model_name, temperature=0),
    )
    registry = build_registry(researcher_spec.get("tools", ["web_search", "write_file"]))
    # Agent-RT's OpenAI WebSocket provider retains response-continuation state.
    # Nested runs must not share that state with the parent agent.
    researcher_provider = load_example_model()
    loop = governance.govern_loop(AgentLoop(researcher_provider, tool_registry=registry))

    task_description = f"Research {topic} and save findings to {save_to}"
    try:
        result = await loop.run(researcher, [user_message(task_description)])
    finally:
        close = getattr(researcher_provider, "close", None)
        if close is not None:
            await close()

    # Extract last AI message as summary
    content = last_assistant_text(result)
    if content:
        return f"Research complete. Summary: {content[:300]}..."
    return "Research complete."


def research_tool(governance: OpenBoxAgentRTGovernance) -> tuple[ToolDefinition, ToolHandler]:
    """Delegate research to the researcher subagent."""

    async def research(arguments: Mapping[str, Any], _token: CancellationToken | None) -> str:
        topic = str(arguments["topic"])
        console.print(f"  [bold magenta]>> Researching:[/] {topic[:60]}...")
        result = await run_researcher(governance, topic, str(arguments["save_to"]))
        console.print("  [green]✓ Research complete[/]")
        return result

    definition = ToolDefinition(
        name="research",
        description=(
            "Delegate research to the researcher subagent. "
            "ALWAYS use this first before writing any content."
        ),
        input_schema=string_schema(
            topic="The topic to research (be specific)",
            save_to="File path to save research results (e.g., 'research/ai-agents.md')",
        ),
        side_effect="write",
    )
    return definition, research


# ═══════════════════════════════════════════════════════════════════
# Main agent
# ═══════════════════════════════════════════════════════════════════


async def create_content_writer() -> tuple[Any, AgentConfig, OpenBoxAgentRTGovernance]:
    """Create a content writer agent configured by filesystem files.

    Returns:
        Tuple of (governed loop, agent config, governance). The caller must
        ``await governance.aclose()`` when done.
    """
    # Load memory (brand voice & style guide)
    agents_md = (EXAMPLE_DIR / "AGENTS.md").read_text()

    # Load skills (blog-post, social-media workflows)
    skills_text = load_skills(EXAMPLE_DIR / "skills")

    # Build system prompt combining memory + skills
    system_prompt = f"""{agents_md}

## Available Skills (loaded from skills/)

{skills_text}

## Tool Usage Instructions

- Use the `research` tool FIRST before writing any content
- Use `write_file` to save content to the appropriate directory
- Use `read_file` to read research results before writing
- Use `generate_cover` for blog post cover images (saves to blogs/<slug>/hero.png)
- Use `generate_social_image` for social media images (saves to <platform>/<slug>/image.png)
"""

    # Create OpenBox governance (async: validates the API key on startup)
    governance = await create_openbox_agentrt(
        api_key=os.environ["OPENBOX_API_KEY"],
        agent_name=os.environ.get("OPENBOX_AGENT_NAME", "ContentWriter"),
        agent_did=os.environ.get("OPENBOX_AGENT_DID"),
        agent_private_key=os.environ.get("OPENBOX_AGENT_PRIVATE_KEY"),
        tool_type_map={"web_search": "http", "research": "agent"},
    )

    provider = load_example_model()

    registry = build_registry(
        ["write_file", "read_file", "generate_cover", "generate_social_image"]
    )
    definition, handler = research_tool(governance)
    registry.register(definition, handler=handler)

    agent = AgentConfig(
        name="content-writer",
        instructions=system_prompt,
        model=ModelSettings(model=configured_model(), temperature=0),
    )
    loop = governance.govern_loop(AgentLoop(provider, tool_registry=registry))

    return loop, agent, governance


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════


async def print_progress(event: ModelStreamEvent) -> None:
    """Render each completed model turn: assistant text and notable tool calls."""
    if event.type != "completed" or event.response is None:
        return
    message = event.response.message
    content = "".join(part.text or "" for part in message.content if part.type == "text")
    if content.strip():
        console.print(Panel(Markdown(content), title="Agent", border_style="green"))
    for tc in message.tool_calls:
        if tc.name == "research":
            console.print(f"  >> Research: {str(tc.arguments.get('topic', ''))[:60]}...")
        elif tc.name == "write_file":
            console.print(f"  >> Writing: {tc.arguments.get('file_path', '')}")


async def main() -> None:
    """Run the content writer agent with progress output."""
    if len(sys.argv) > 1:
        task = " ".join(sys.argv[1:])
    else:
        task = "Write a blog post about how AI agents are transforming software development"

    console.print()
    console.print("[bold blue]Content Builder Agent[/] [dim](Agent-RT + OpenBox)[/]")
    console.print(f"[dim]Task: {task}[/]")
    console.print()

    loop, agent, governance = await create_content_writer()

    try:
        # Stream with governance applied by the governed loop
        await loop.run_streaming(agent, [user_message(task)], print_progress)
    finally:
        await governance.aclose()

    console.print()
    console.print("[bold green]✓ Done![/]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/]")
