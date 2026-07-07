# tests/test_template_consistency.py
"""Guard tests: every agent/tool referenced by a workflow template must exist.

Prevents regressions like the `architect` agent or `browser` tool being
referenced by a workflow template without a backing profile/registration.
"""

import yaml

from core.path_resolver import (
    paths,
)
from tools.specs import TOOL_DEFINITIONS

# Tools that are registered but live outside TOOL_DEFINITIONS (interception-only).
_EXTRA_TOOLS = {"ask_clarification"}


def _agent_names() -> set[str]:
    names: set[str] = set()
    dirs = [paths.templates_agents_dir(), paths.workspace_agents_dir("main")]
    for d in dirs:
        if not d.exists():
            continue
        for f in d.glob("*.yaml"):
            if f.name.startswith("_"):
                continue
            data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict) and data.get("name"):
                names.add(data["name"])
    return names


def _valid_tools() -> set[str]:
    return set(TOOL_DEFINITIONS) | _EXTRA_TOOLS


def _workflow_files() -> list:
    files = []
    for d in [paths.templates_workflows_dir(), paths.workspace_workflows_dir("main")]:
        if d.exists():
            files.extend(f for f in d.glob("*.yaml") if not f.name.startswith("_"))
    return files


def _template_agents(template: dict) -> set[str]:
    refs: set[str] = set()
    allowed = template.get("agents", {}).get("allowed")
    if isinstance(allowed, list):
        refs.update(a for a in allowed if isinstance(a, str))
    for phase in template.get("default_phases", []) or []:
        if isinstance(phase, dict):
            refs.update(a for a in (phase.get("agents") or []) if isinstance(a, str))
    return refs


def test_workflow_templates_reference_existing_agents():
    valid = _agent_names()
    assert valid, "No agent templates discovered"

    violations: list[str] = []
    for f in _workflow_files():
        template = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for agent in sorted(_template_agents(template)):
            if agent not in valid:
                violations.append(
                    f"{f.relative_to(paths.templates_dir().parent)} -> agent '{agent}'"
                )

    assert not violations, "Workflow templates reference non-existent agents:\n" + "\n".join(
        violations
    )


def test_architect_profile_loads_correctly():
    """Architect agent must be loadable with type=analysis and model_role=reasoning."""
    agent_dirs = [paths.templates_agents_dir(), paths.workspace_agents_dir("main")]
    architect_yaml = None
    for d in agent_dirs:
        candidate = d / "architect.yaml"
        if candidate.exists():
            architect_yaml = candidate
            break

    assert architect_yaml is not None, "architect.yaml not found in templates or workspaces"

    data = yaml.safe_load(architect_yaml.read_text(encoding="utf-8"))
    assert data["type"] == "analysis", f"Expected type=analysis, got {data.get('type')}"
    assert (
        data["model_role"] == "reasoning"
    ), f"Expected model_role=reasoning, got {data.get('model_role')}"
    assert "file_manager" in (
        data.get("tools") or []
    ), "architect must have file_manager (read-only)"
    assert data.get("name") == "architect", f"Expected name=architect, got {data.get('name')}"


def test_architect_is_allowed_in_coordinated_workflow():
    """Coordinated workflow must list architect in agents.allowed."""
    wf_file = paths.workspace_workflows_dir("main") / "coordinated.yaml"
    if not wf_file.exists():
        wf_file = paths.templates_workflows_dir() / "coordinated.yaml"
    assert wf_file.exists(), "coordinated.yaml not found"

    template = yaml.safe_load(wf_file.read_text(encoding="utf-8"))
    allowed = template.get("agents", {}).get("allowed") or []
    assert "architect" in allowed, f"architect not in coordinated.yaml agents.allowed: {allowed}"


def test_workflow_templates_reference_registered_tools():
    valid = _valid_tools()

    violations: list[str] = []
    for f in _workflow_files():
        template = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        allowed = template.get("tools", {}).get("allowed") or []
        for tool in allowed:
            if not isinstance(tool, str):
                continue
            if tool.startswith("mcp:") or tool.startswith("mcp_"):
                continue
            if tool not in valid:
                violations.append(f"{f.relative_to(paths.templates_dir().parent)} -> tool '{tool}'")

    assert not violations, "Workflow templates reference unregistered tools:\n" + "\n".join(
        violations
    )
