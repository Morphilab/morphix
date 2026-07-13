# Adding Workflows

This guide walks you through creating a new workflow strategy. We'll create a `code_review` workflow that orchestrates a reviewer agent followed by a developer to fix issues.

## Step 1: Create the workflow YAML template

Create `templates/workflows/code_review.yaml`:

```yaml
name: code_review
type: development
description: "Focused code review workflow — scan, report issues, apply fixes"
decomposition: flat
execution: sequential
max_parallel: 1
retry_on_failure: true
retry_max: 1
max_agent_iterations: 6
agents:
  allowed:
    - reviewer
    - developer
  default_simple: reviewer
stages:
  task_analysis: true
  task_decomposition: true
  supervisor_review: true
  result_aggregation: true
  finalization: true
tools:
  allowed:
    - file_manager
    - lsp_manager
    - code_search
    - web_search
    - bash_manager
    - test_runner
    - diff_editor
project:
  required: true
```

### YAML field reference

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Unique workflow identifier |
| `type` | `str` | Workflow type: `development`, `coordinated`, `collaborative`, `tdd` |
| `description` | `str` | Human-readable description for the Dashboard |
| `decomposition` | `str` | How tasks are split: `flat` (sequential list), `dag` (directed acyclic graph) |
| `execution` | `str` | How subtasks run: `sequential`, `parallel_levels`, `parallel_rounds` |
| `max_parallel` | `int` | Maximum concurrent subtasks for parallel execution |
| `retry_on_failure` | `bool` | Whether to retry failed subtasks |
| `retry_max` | `int` | Maximum retry attempts per subtask |
| `max_agent_iterations` | `int` | Maximum tool-calling iterations per agent loop |
| `agents.allowed` | `list[str]` | Agent names allowed in this workflow |
| `agents.default_simple` | `str` | Default agent for simple conversation mode |
| `stages` | `dict` | Which pipeline stages are enabled |
| `tools.allowed` | `list[str]` | Tool names allowed for all agents in this workflow |
| `project.required` | `bool` | Whether a project must be selected |

### Workflow types and their routing

| Type | Router behavior |
|------|----------------|
| `development` | Decompose into sequential subtasks, agent routing, full orchestration |
| `coordinated` | Multi-agent DAG with parallel execution, shared blackboard, confidence aggregation |
| `collaborative` | Debate-style multi-agent rounds with panel evaluation |
| `tdd` | Autonomous test-driven development loop |

## Step 2: Implement the Python execution class (if needed)

The `development` type uses the built-in `_run_full_orchestration()` path — no custom Python class needed. For workflow types that need custom logic, create a module in `orchestration/workflows/`.

For example, `collaborative` has `orchestration/workflows/collaborative.py` with a `CollaborativeOrchestrator` class:

```python
class CollaborativeOrchestrator:
    @staticmethod
    async def run(
        query: str,
        template: dict,
        events: WorkflowEvents,
        history: list,
        project_root: str | None,
        workspace: str,
        force_agent: str | None,
        workflow_allowed_tools: list | None,
        start_time: float,
    ) -> str:
        # Custom collaborative execution logic
        ...
```

## Step 3: Register the route in WorkflowOrchestrator

Open `orchestration/workflows/orchestrator.py` and add your routing in `_dispatch_route()`. The routing follows this precedence:

1. **TDD loop** — when `active_wf == "tdd"`
2. **Collaborative** — when `template.get("type") == "collaborative"`
3. **Coordinated** — when `template.get("type") == "coordinated"`
4. **Development** — when `template.get("type") == "development"`
5. **Default** — analyze task and decide simple conversation vs full orchestration

For a new workflow type like `code_review` that uses the existing development executor:

```python
# In _dispatch_route() — add before the default fallback:
if template.get("type") == "code_review":
    # Treat like development but with reviewer-specific task analysis
    return await WorkflowOrchestrator._run_full_orchestration(
        query,
        conversation_history,
        await TaskAnalyzer.analyze_task(query, is_follow_up=ctx.is_follow_up),
        ctx,
        events,
        project_root,
        workspaces.current,
        allowed_agents,
        workflow_allowed_tools,
        start_time,
    )
```

!!! tip
    Most custom workflows can reuse the `development` executor path. The template YAML controls agent selection, tool filtering, and decomposition behavior. Only create a custom Python orchestrator for fundamentally different execution models (like the debate rounds in collaborative).

## Step 4: Copy templates to workspace

```bash
cp templates/workflows/code_review.yaml workspaces/main/workflows/code_review.yaml
cp templates/agents/reviewer.yaml workspaces/main/agents/reviewer.yaml
```

## Step 5: Add template consistency test

Create or update `tests/test_template_consistency.py`:

```python
def test_code_review_workflow_agents_exist():
    """Every agent referenced by code_review workflow must exist."""
    from core.path_resolver import paths
    import yaml

    wf_path = paths.templates_dir() / "workflows" / "code_review.yaml"
    workflow = yaml.safe_load(wf_path.read_text())

    agents_allowed = workflow.get("agents", {}).get("allowed", [])
    agents_dir = paths.templates_dir() / "agents"

    for agent_name in agents_allowed:
        agent_file = agents_dir / f"{agent_name}.yaml"
        assert agent_file.exists(), (
            f"Agent '{agent_name}' referenced in code_review.yaml not found at {agent_file}"
        )
```

## Step 6: Verify in the GUI

1. Launch the GUI: `poetry run python run.py`
2. In the Dashboard tab, select the `code_review` workflow from the dropdown.
3. Pick a project and type a review task (e.g., "Review the security of my authentication module").
4. The workflow should decompose the task, route subtasks to reviewer/developer agents, and aggregate results.

## Complete Workflow Execution Flow

```
User input
    │
    ▼
WorkflowOrchestrator.run_full_workflow()
    │
    ├─ Direct tool command? ───► _parse_direct_tool_command() ─► execute tool
    │
    ▼
_dispatch_route()
    │
    ├─ active_wf == "tdd" ───► _run_tdd_loop()
    ├─ type == "collaborative" ─► CollaborativeOrchestrator.run()
    ├─ type == "coordinated" ─► _run_coordinated() (DAG + phases)
    ├─ type == "development" ─► _run_full_orchestration()
    └─ default ─► TaskAnalyzer.analyze_task()
                    │
                    ├─ simple? ─► _run_simple_conversation()
                    └─ complex? ─► _run_full_orchestration()
```

## Checklist

- [ ] YAML template has `name`, `type`, `agents.allowed`, `tools.allowed`, `stages`
- [ ] All referenced agents exist in `templates/agents/` or `workspaces/<name>/agents/`
- [ ] All referenced tools exist in `tools/specs.py` `TOOL_DEFINITIONS`
- [ ] Custom Python orchestrator (if needed) implements `async run(...) -> str`
- [ ] Route added in `_dispatch_route()` if using a new workflow type
- [ ] Template copied to `workspaces/<name>/workflows/`
- [ ] Template consistency test added
- [ ] Workflow appears in Dashboard dropdown
- [ ] Run `ruff check . && black --check . && mypy && pytest`
