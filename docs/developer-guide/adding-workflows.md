# Adding Workflows

A workflow in Morphix is a **DSL preset**: a YAML document with `version: 1` interpreted by the deterministic engine in `orchestration/dsl/`. There is no per-workflow Python code to write — the YAML declares agents, tools, and steps; the engine handles retries, gates, loops, parallel branches, pauses, and aggregation.

Design rule: **"el modelo elige, el motor acota"** — flow control NEVER depends on free-form LLM output. Model decisions are bounded by declared contracts with deterministic fallbacks (`decide` options + fallback, `until.agent` expect + fallback).

This guide walks through creating a `code_review` workflow: a reviewer agent scans the code, a developer fixes issues, and the cycle repeats until the reviewer approves.

## Step 1: Generate a skeleton

```bash
uv run python -m orchestration.dsl.cli new code_review --type=development
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
| `budget_tokens` | `int ≥ 1000` | Token ceiling for THIS run (guardrail, not a target) — the preset lowers the global limit; `None` = global only |
| `budget_reserve_final` | `int ≥ 0` | Tokens reserved for the closing steps (aggregate/traceability): the loop cannot start a cycle that would eat them |
| `budget_reserve_replan` | `int ≥ 0` | Tokens reserved for the replanner; together with `budget_reserve_final` it must be lower than `budget_tokens` |
| `max_interp_chars` | `int 200-50000` | Cap of each interpolated `$var` value in prompts (default 2000). Raise it when a preset passes a long artifact between steps (e.g. sdd's spec) |
| `cycle_token_estimate` | `int ≥ 1000` | Floor of the estimated cost of one loop cycle (default 8000). The engine uses the observed average of the loop's finished cycles when it is higher |
| `steps` | `list` | The program — at least one step |

### Step kinds

| Kind | Purpose |
|------|---------|
| `agent` | Run an agent with a `goal`; `retry_max` (0-3), `timeout` (≥10s), `max_iterations` (1-50, overrides `MAX_AGENT_ITERATIONS` for this step only), `tools` (narrow this step's toolset: a subset of the workflow allowlist; `[]` = text-only step), `model_role`, `outputs` (vars to export), `gate` (regex or `true` → sets `$last_gate`), `produces` (globs the step must write), `verified_by` (later tool step that measures the delivery), `on_blocker` (`continue` \| `stop`, only with `gate`), `min_output_chars` (text steps shorter than this are `unmeasured`), `use_reserve_final` (closing steps may spend the run's final reserve) |
| `tool` | Call a tool with `args`; `outputs` maps state vars to result keys; `verdict` names an exported boolean — a measured `False` fails the step and its cycle's delivery |
| `decompose` | Break the task into subtasks (`strategy: flat \| dag`, `output` var name); `query` interpolates (e.g. `"$spec"`) instead of `$query` + history |
| `parallel` | Run `branches` (each a step list with optional `depends_on`), `max_parallel` 1-8 |
| `loop` | `max_iter` (1-20), `over` a list var (exposes `$item`, `$iter`, `$max_iter`), `until` early exit, `init_vars`, `parallel` body per item with `parallel_max`, `budget_tokens` per round (sequential) or per batch (parallel), `max_tokens_per_item` (sequential only: caps one item so it cannot eat the round), `replan` (bounded replanning of a sequential `over` loop), `retry_on_verification` (0-2 local retries before replanning) |
| `decide` | Model picks from `options`; invalid/ambiguous answer → `fallback` (must be one of the options); `branches` per option |
| `checkpoint` | Pause for human approval — persisted as `PausedSession`, survives restarts |
| `workflow` | Include another preset as a subroutine (`inputs` map, `outputs`) |
| `fragment` | Include a shared body in-place: `include: <name>` — expanded at compile time, the engine never sees the fragment step |
| `aggregate` | Final synthesis: `strategy: result \| confidence \| moderator` (+ `agent` for moderator) |
| `plugin` | Registered plugin primitive (`params`, declarative `outputs`) |

Every step accepts `id` (unique, lowercase), `when` (conditional execution: `if_blockers`, `query_contains:X`, a truthy `$var`, or its negation `!$var`), and `on_error` (`abort` | `continue`).

#### Fragments (shared bodies)

A fragment is a `_`-prefixed file in the catalog (invisible in preset listings) with
`version: 1` and a `steps:` list. Referenced from any step list via
`kind: fragment / include: _name`; the compiler splices the steps in place, re-validates
the whole document (unique ids included) and fails loud on missing, cyclic or too-deep
includes. Compiling a preset with fragments requires a catalog (`FileSystemCatalog`).

#### Delivery contract, reserves and bounded replanning

The engine does not trust an agent's "done": a subtask is `delivered` only when its
evidence matches the declared contract.

- `produces`: globs relative to the project root (`$iter`-interpolable) that the agent step
  must write. A completed step that wrote no matching file leaves its item `unmeasured`.
- `verified_by`: id of a later `tool` step in the same body with `verdict`. The measured
  result is attached to the cycle item: green → `delivered`, red → `failed`, no measurement
  → `unmeasured`.
- `min_output_chars`: text steps (specs) shorter than this are `unmeasured`, not delivery.
- `on_blocker: stop`: only with `gate`; a blocking pattern halts the run as `stopped`
  (terminal, persisted with `calidad=Fallido`) instead of running the rest blindly.
- `budget_reserve_final` / `budget_reserve_replan`: tokens the loop may not consume; a cycle
  only starts when `limit - spent - reserves` covers its estimated cost.
- `cycle_token_estimate`: floor of a cycle's estimated cost. The engine estimates the next
  cycle with the observed average of the loop's finished cycles (max of both), so a loop
  with 100k cycles does not start the next one with 54k left.
- `agent.use_reserve_final`: a closing step (e.g. `reparar_agregado`) may spend the final
  reserve that loops cannot touch. The engine raises the agent-loop ceiling only for that
  step and restores it when the step returns.
- `loop.replan`: `{max, model_role, max_new_items, min_remaining_tokens, triggers}` on a
  sequential loop with `over`. After the local `retry_on_verification` retries fail, a
  tool-less planner receives the measured evidence and answers JSON (`retry | replace |
  skip | stop`); the engine validates and caps the decision.

The run report opens the final message with `calidad_entrega` (delivered/verified subtasks)
and `calidad_proceso` (degradations, unmeasured phases), so a green aggregate with an
unmeasured spec is `Parcial` with a reason instead of a false `Alta`.

#### What every agent step sees (run memory)

An agent step does not inherit the previous step's conversation: the only bridge is the
interpolated `$var`. The engine also injects a compact `<RUN_STATE>` block
into every `call_agent` (its own prompt layer, capped at 2k chars):

```
<RUN_STATE>
Goal: …
Plan:
1. ✅ previous subtask
2. 🔵 current subtask
Current: … (intento N)
Files: …
Last verification: test_runner ❌ (3 passed, 2 failed) — first tail line
Already tried: test_runner|tests/x.py ×3
Remaining budget: ~340k de 500k tokens
Rules: …
</RUN_STATE>
```

The block is generated from `RunState` (`plan`, `files_written`, `Verification`,
`RunMemory.tried`, `BudgetState`) and is fitted by **chars and tokens** (≤2k chars, ≤760
tokens) with its closing marker guaranteed: the prompt budget clips by tokens/lines, so a
token-dense block (CJK, emojis) could otherwise lose its close. LLM-produced text cannot
inject a closing marker (the exact markers are neutralized). The loop also injects a
`<BLACKBOARD>` snapshot of the active plan-mode plan (title + steps) and the workspace's
persistent goals/todos (`goal_*`/`todo_*`; ≤1.2k chars, ≤320 tokens, todo section budget
reserved) in its own layer — that storage finally has a consumer. The plan appears only
when the run actually applied plan-mode; the bot prompt branch does not receive either
layer.

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
uv run python -m orchestration.dsl.cli validate code_review

# Validation + executable conformance: runs the preset against the REAL engine
# with signature-identical fake handlers (an until/tool-step missing required
# args fails exactly as it would in production)
uv run python -m orchestration.dsl.cli validate code_review --conformance
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

1. Launch the GUI: `uv run python run.py`
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
