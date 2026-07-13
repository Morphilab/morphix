# Adding Agents

This guide walks you through creating a new agent profile. We'll create a `reviewer` agent that validates code without modifying it.

## Step 1: Create the YAML profile

Create `templates/agents/reviewer.yaml`:

```yaml
name: reviewer
type: analysis
system_prompt: >
  You are a code reviewer. Your task is to read code, identify issues,
  and provide constructive feedback. You do NOT write or modify code —
  you only review and report.

  HOW TO WORK:
  1. Read files using file_manager.read with relative paths (e.g., "src/main.py").
  2. Use lsp_manager.diagnostics to find linting issues.
  3. Use code_search to find patterns or anti-patterns across the codebase.
  4. Use web_search if you need documentation references.

  WHAT TO DELIVER:
  - A list of issues ordered by severity (critical, major, minor, suggestion).
  - Each issue: file path, line range, description, and suggested fix.
  - A summary: overall code health, what's done well, what needs attention.

  LIMITATIONS:
  - Do NOT use file_manager.write or any modifying tool.
  - Do NOT execute code or run tests.

  PATHS: The project root is already configured. Use relative paths.
    Never use absolute paths like "/home/user/code_projects/...".

  FINISHING: After delivering the review, stop using tools.
length_guidance: "Thorough but focused (max 1000 words)."
temperature: 0.3
tools: ["file_manager", "lsp_manager", "code_search", "web_search"]
keywords:
  - review
  - code review
  - audit
  - inspection
  - quality
  - critique
  - feedback
  - best practices
  - security
  - performance
priority: 60
model_role: analysis
last_memory_key: null
```

### YAML field reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique agent identifier (lowercase, no spaces) |
| `type` | `str` | Agent category: `development`, `analysis`, `agent` |
| `system_prompt` | `str` | Instructions injected into the LLM system message |
| `length_guidance` | `str` | Target response length hint for the LLM |
| `temperature` | `float` | LLM temperature (0.0-1.0). Lower = more deterministic |
| `tools` | `list[str]` | Tool names this agent can call |
| `keywords` | `list[str]` | Words that hint this agent is suitable for a task |
| `priority` | `int` | Priority for agent selection (higher = more likely) |
| `model_role` | `str` | Maps to `settings.model_roles` for selecting the LLM model |
| `last_memory_key` | `str` or `null` | Memory namespace for this agent |

### Agent types

- **`development`** — Can read and write files, run commands, execute code. Example: `developer`.
- **`analysis`** — Read-only: can read files, search, analyze. Cannot modify. Example: `architect`, `analista`.
- **`agent`** — General-purpose conversational agent. Example: `moderador`.

## Step 2: Copy to the workspace

Agents are loaded from `workspaces/<name>/agents/`. Copy your template:

```bash
cp templates/agents/reviewer.yaml workspaces/main/agents/reviewer.yaml
```

The loader in `agents/loader.py` (`load_workspace_agents`) scans `workspaces/<name>/agents/*.yaml` and registers each valid YAML file. Files starting with `_` (like `_FULL_TEMPLATE.yaml`) are ignored.

## Step 3: Add to workflow allowed agents

Open the workflow template where you want to use this agent (e.g., `templates/workflows/development.yaml`) and add `"reviewer"` to `agents.allowed`:

```yaml
agents:
  allowed:
    - developer
    - analista
    - reviewer
```

Copy the updated template to the workspace:

```bash
cp templates/workflows/development.yaml workspaces/main/workflows/development.yaml
```

## Step 4: Verify registration

The agent is registered globally via `agents_registry` and will appear:

1. In the GUI agent picker dropdown (Maestro tab top bar).
2. In the supervisor's agent selection logic.
3. In the router (`orchestration/router.py`) for task-to-agent matching.

To check registration programmatically:

```python
from agents.registry import agents_registry

profile = agents_registry.get_profile("reviewer")
print(profile["name"])           # reviewer
print(profile["type"])           # analysis
print(profile["tools"])          # ['file_manager', 'lsp_manager', ...]
```

## Step 5: Reload workspace

In the GUI, switch to a different workspace and back, or restart the application. The loader runs on workspace switch and picks up new agents.

## Agent Registration Flow

```
templates/agents/reviewer.yaml
        │
        ▼
  (on workspace switch)
        │
        ▼
workspaces/main/agents/reviewer.yaml   ← copied from template if missing
        │
        ▼
agents/loader.py: load_workspace_agents()
        │
        ▼
agents_registry.register_workspace_agent("reviewer", func, profile)
        │
        ▼
GUI agent picker + router + supervisor
```

## Testing Your Agent

Create `tests/test_agent_reviewer.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch

from agents.registry import AgentsRegistry


@pytest.mark.asyncio
async def test_reviewer_profile_loads():
    """The reviewer profile should have correct metadata."""
    import yaml
    from pathlib import Path

    template_path = Path("templates/agents/reviewer.yaml")
    assert template_path.exists(), "reviewer.yaml template not found"

    profile = yaml.safe_load(template_path.read_text())
    assert profile["name"] == "reviewer"
    assert profile["type"] == "analysis"
    assert "file_manager" in profile["tools"]
    assert "web_search" in profile["tools"]
    # Reviewer should NOT have write tools
    assert "file_manager" in profile["tools"]  # read-only usage
    assert "git_manager" not in profile["tools"]


@pytest.mark.asyncio
async def test_reviewer_registered_in_registry():
    """After workspace load, reviewer should be in the registry."""
    reg = AgentsRegistry()
    reg.register_workspace_agent(
        "reviewer",
        AsyncMock(return_value="review output"),
        {"name": "reviewer", "type": "analysis", "tools": ["file_manager"]},
    )

    agent = reg.get_agent("reviewer")
    assert agent is not None

    profile = reg.get_profile("reviewer")
    assert profile["type"] == "analysis"
```

## Checklist

- [ ] YAML file has `name`, `type`, `system_prompt`, `tools`, `keywords`
- [ ] `type` matches intended capabilities (`development` vs `analysis` vs `agent`)
- [ ] Tool list only includes tools the agent should access
- [ ] `model_role` matches a key in `settings.model_roles`
- [ ] Copied to `workspaces/<name>/agents/`
- [ ] Added to `agents.allowed` in relevant workflow templates
- [ ] Agent appears in GUI after workspace reload
- [ ] Keywords match the types of tasks the agent should handle
