# Adding Workflows

A workflow in Morphix is a **DSL preset**: a YAML document with `version: 1` interpreted by the deterministic engine in `orchestration/dsl/`. There is no per-workflow Python code to write — the YAML declares agents, tools, and steps; the engine handles retries, gates, loops, parallel branches, pauses, and aggregation.

Design rule: **"el modelo elige, el motor acota"** — flow control NEVER depends on free-form LLM output. Model decisions are bounded by declared contracts with deterministic fallbacks (`decide` options + fallback, `until.agent` expect + fallback).

This guide walks through creating a `code_review` workflow: a reviewer agent scans the code, a developer fixes issues, and the cycle repeats until the reviewer approves.

## Step 1: Generate a skeleton

```bash
poetry run python -m orchestration.dsl.cli new code_review --type=development
```

Skeleton types: `development`, `tdd`, `collaborative`, `coordinated`. The command writes a valid, ready-to-edit YAML. You can also start from any product preset in `templates/workflows/` (development, tdd, collaborative, coordinated, bdd, sdd, reflexion, domain_tdd, edd) as a reference.

## Step 2: Edit the preset

A `code_review` preset (simplified from the `tdd` preset):

```yaml
version: 1
name: code_review
description: "Review → fix loop until the reviewer approves"
agents:
  allowed: ["reviewer", "developer"]
tools:
  allowed: ["file_manager", "lsp_manager", "code_search", "diff_editor", "test_runner"]
project:
  required: true
skills: true
steps:
  - id: ciclo
    kind: loop
    max_iter: 4
    until:
      type: agent
      question: >-
        ¿El review de $last_output aprueba el código? Responde EXACTAMENTE
        una opción: approved | changes_needed
      expect: "approved|changes_needed"
      fallback: fail
    body:
      - id: revisar
        kind: agent
        agent: reviewer
        goal: >-
          Revisa el código del proyecto para: $query. Resultado de la
          iteración previa: $last_output. Lista issues por severidad.
        gate: true
      - id: corregir
        kind: agent
        agent: developer
        goal: >-
          Corrige los issues del último review: $last_gate. Continúa desde
          $last_output — no repitas trabajo ya hecho.
        retry_max: 1
        timeout: 300
  - id: resumen
    kind: aggregate
    strategy: result
```

### Root field reference

| Field | Type | Description |
|-------|------|-------------|
| `version` | `1` | **Required.** Documents without it are rejected (legacy format is retired) |
| `name` | `str` | Preset identifier |
| `description` | `str` | Human-readable description |
| `inputs` / `outputs` | `list[str]` | I/O contract — required for the preset to be included by others via `kind: workflow` |
| `defaults` | `dict` | Default values for `inputs` |
| `agents.allowed` | `list[str]` | Agent names allowed (deny-by-default) |
| `tools.allowed` | `list[str]` | Tool names allowed for all agent calls in this workflow |
| `project.required` | `bool` | Whether a project must be selected before running |
| `skills` | `bool` | Enable procedural skills injection for agent calls |
| `commit_after` | `list[str]` | Step ids after which the engine commits (only with completed subtasks + files written) |
| `steps` | `list` | The program — at least one step |

### Step kinds

| Kind | Purpose |
|------|---------|
| `agent` | Run an agent with a `goal`; `retry_max` (0-3), `timeout` (≥10s), `model_role`, `outputs` (vars to export), `gate` (regex or `true` → sets `$last_gate`) |
| `tool` | Call a tool with `args`; `outputs` maps state vars to result keys |
| `decompose` | Break `$query` into subtasks (`strategy: flat \| dag`, `output` var name) |
| `parallel` | Run `branches` (each a step list with optional `depends_on`), `max_parallel` 1-8 |
| `loop` | `max_iter` (1-20), `over` a list var (exposes `$item`, `$iter`, `$max_iter`), `until` early exit, `init_vars`, `parallel` body per item |
| `decide` | Model picks from `options`; invalid/ambiguous answer → `fallback` (must be one of the options); `branches` per option |
| `checkpoint` | Pause for human approval — persisted as `PausedSession`, survives restarts |
| `workflow` | Include another preset as a subroutine (`inputs` map, `outputs`) |
| `aggregate` | Final synthesis: `strategy: result \| confidence \| moderator` (+ `agent` for moderator) |
| `plugin` | Registered plugin primitive (`params`, declarative `outputs`) |

Every step accepts `id` (unique, lowercase), `when` (conditional execution: `if_blockers`, `query_contains:X`, or a `$var`), and `on_error` (`abort` | `continue`).

### Variables

Steps interpolate `$var` from the engine state: `$query`, `$item`, `$iter`, `$max_iter`, `$last_gate`, `$last_output`, plus anything exported via `outputs` or `init_vars`. An undefined variable fails loud at runtime — the engine never silently interpolates empty strings.

## Step 3: Place the preset

Preset resolution order (`FileSystemCatalog`):

1. `workspaces/<ws>/workflows/code_review.yaml` — workspace override
2. `templates/workspaces/<ws>/workflows/` — product workspaces
3. `templates/workflows/` — global catalog

For a personal preset, put it in your workspace directory. For a shipped preset, put it in `templates/workflows/`.

## Step 4: Validate

```bash
# Structural + semantic validation (no LLM, no DB)
poetry run python -m orchestration.dsl.cli validate code_review

# Validation + executable conformance: runs the preset against the REAL engine
# with signature-identical fake handlers (an until/tool-step missing required
# args fails exactly as it would in production)
poetry run python -m orchestration.dsl.cli validate code_review --conformance
```

The validator catches: unknown agents/tools, undefined `$var` references, duplicate step ids (recursively), `decide` branches not covering options, invalid `when` expressions, and I/O contract violations of `workflow` includes.

!!! warning "Structure validation alone is not enough"
    A preset can compile, validate, and still be broken at runtime (e.g., an `until.tool` without the required `args` of the target tool dies in TypeError after max retries). Always run `--conformance` for presets you intend to ship.

## Step 5: Add a guard test

Product presets are exercised by `tests/test_dsl_conformance.py` — add yours to its preset list (or add a template-consistency check in `tests/test_template_consistency.py`):

```python
def test_code_review_agents_exist():
    """Every agent referenced by code_review must exist."""
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

## Step 6: Verify

1. Launch the GUI: `poetry run python run.py`
2. Select `code_review` as the active workflow (top bar of the Maestro tab / Dashboard).
3. Pick a project and type a review task.
4. The run log shows each DSL step (`agente ▸ step` names); checkpoint steps surface an approval prompt; `⏹` stops and persists the partial conversation.

## Execution Flow

```
User input
    │
    ▼
WorkflowOrchestrator.run_full_workflow()
    │
    ├─ Direct tool command? ───► _parse_direct_tool_command() ─► execute tool
    ├─ Bot-owned conversation? ─► bots_dispatch.dispatch_canonical_turn()
    │
    ▼
_dispatch_route() — loads the raw document
    │
    ├─ No document ────────────► actionable error (hint: cli new)
    ├─ Legacy doc (no version) ─► actionable rejection
    └─ version: 1 ─────────────► _run_dsl_workflow()
                                    │
                                    ▼
                        compile_workflow() → validate_workflow()
                                    │
                                    ▼
                        WorkflowEngine.run() + ProductionRuntime
                                    │
                        ├─ paused ──► PausedSession origin="dsl" ──► resume at exact step
                        └─ completed ─► aggregate + finalize_workflow()
```

## Checklist

- [ ] YAML has `version: 1`
- [ ] All referenced agents exist in `templates/agents/` or `workspaces/<name>/agents/` and are in `agents.allowed`
- [ ] All referenced tools exist in `tools/specs.py` `TOOL_DEFINITIONS` and are in `tools.allowed`
- [ ] Loops without `over` consume loop feedback in the goal (`$last_output`/`$iter`) — the structural guard `structural_feedback_errors` enforces this
- [ ] `until.tool`/`tool` steps declare all required `args` of the target tool
- [ ] `cli validate` passes, and `cli validate --conformance` runs the preset against the real engine
- [ ] Preset placed in the right catalog level (workspace vs `templates/workflows/`)
- [ ] Guard/conformance test added
- [ ] Run `ruff check . && black --check . && mypy && pytest`
