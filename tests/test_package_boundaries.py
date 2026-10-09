"""Package-boundary guards for the Agent-RT adapter."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PKG_ROOT = _REPO_ROOT / "openbox_agentrt"

# Framework-specific governance packages must never leak into this adapter.
_FORBIDDEN = (
    "langchain",
    "langgraph",
    "openbox_langchain",
    "openbox_langgraph",
)
_DYNAMIC_IMPORT_FUNCS = ("import_module", "__import__")


def _root(module: str) -> str:
    return module.split(".", 1)[0]


def _forbidden_imports(source: str) -> list[str]:
    """Return statically discoverable imports from forbidden package roots."""
    hits: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(alias.name for alias in node.names if _root(alias.name) in _FORBIDDEN)
        elif isinstance(node, ast.ImportFrom):
            if node.module and _root(node.module) in _FORBIDDEN:
                hits.append(node.module)
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            first = node.args[0]
            if (
                name in _DYNAMIC_IMPORT_FUNCS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and _root(first.value) in _FORBIDDEN
            ):
                hits.append(first.value)
    return hits


def test_production_modules_import_only_agentrt_and_base_sdk_boundaries() -> None:
    for path in sorted(_PKG_ROOT.rglob("*.py")):
        hits = _forbidden_imports(path.read_text())
        assert not hits, f"{path.relative_to(_REPO_ROOT)} imports forbidden package(s): {hits}"


def test_scanner_catches_static_and_dynamic_framework_imports() -> None:
    bad = [
        "import langchain",
        "import os, openbox_langgraph",
        "from langgraph.prebuilt import ToolNode",
        "import importlib\nimportlib.import_module('openbox_langchain.middleware')",
        "__import__('langchain.agents')",
    ]
    assert all(_forbidden_imports(source) for source in bad)

    good = [
        "import agent_rt",
        "from openbox_core.runtime import OpenBoxRuntime",
        "from . import governance",
    ]
    assert all(not _forbidden_imports(source) for source in good)


def test_agentrt_is_pinned_in_sdk_example_and_lockfiles() -> None:
    example = _REPO_ROOT / "examples" / "content-builder-agent"
    sdk_manifest = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text())
    example_manifest = tomllib.loads((example / "pyproject.toml").read_text())
    assert "agent-rt==0.0.3" in sdk_manifest["project"]["dependencies"]
    assert "agent-rt[openai]==0.0.3" in example_manifest["project"]["dependencies"]

    for root in (_REPO_ROOT, example):
        lock = tomllib.loads((root / "uv.lock").read_text())
        agent_rt = next(pkg for pkg in lock["package"] if pkg["name"] == "agent-rt")
        assert agent_rt["version"] == "0.0.3"
