# Workflows Overview

Workflows in Morphix are **YAML documents** (docs with `version: 1`) executed by the built-in deterministic engine — the **Workflow DSL** (`orchestration/dsl/`). There is no hidden orchestration logic: what the YAML declares (steps, loops, gates, agents, allowed tools) is exactly what runs.

The guiding principle: **the model chooses, the engine bounds**. The LLM decides within limits the engine validates and enforces — model decisions are checked against declared expectations with explicit fallbacks, and loop exits can be **deterministic** (e.g., parsed pytest counts), never dependent on free-form model output.

## The 9 Presets

Morphix ships nine workflow presets in `templates/workflows/` (each workspace gets its own editable copy in `workspaces/<name>/workflows/`):

| Preset | Focus | Agents | Project |
|--------|-------|--------|:---:|
| **development** | General coding tasks | developer, analista | required |
| **coordinated** | Parallel multi-agent execution | developer, analista, moderador, architect | required |
| **collaborative** | Panel debate with moderator consensus | developer, analista, moderador | not required |
| **tdd** | Test-driven loop until green | developer, analista | required |
| **bdd** | Gherkin stories → tests → implementation | developer, analista | required |
| **sdd** | Spec-first with review gates | developer, analista | required |
| **edd** | Eval-driven — numeric metrics end the loop | developer, analista | required |
| **domain_tdd** | Domain model → per-scenario TDD cycles | developer, analista, architect | required |
| **reflexion** | Generator–critic refinement loop | developer, analista | required |

## Preset Details

### development

The general-purpose workflow. Decomposes your request into flat subtasks, executes them **sequentially** (loop of up to 10 iterations, per-subtask retry ≤2 and 300s timeout), then aggregates results deterministically. Each subtask runs on the `developer` agent with the full coding toolset (files, git, bash, tests, LSP, diffs, goals/todos).

### coordinated

For tasks that split into independent pieces. Decomposes, then executes subtasks in a **dynamic parallel loop** (up to 5 concurrent), verifies the results, and aggregates with confidence scoring.

### collaborative

A **panel debate**: 3 rounds where `developer` and `analista` give opinions (each round sees the previous output), then the `moderador` agent synthesizes the final consensus. It has a restricted, read-oriented toolset (file reads, code search, web) and — uniquely — **does not require a project**. Use it for design decisions and trade-off analysis, not for producing code.

### tdd

An autonomous **red-green loop** (max 5 iterations): the agent writes/fixes code and tests, the engine runs `test_runner`, and the loop only exits when **all tests pass** — the exit condition is the parsed pytest result (`tests_all_pass`), not the model's opinion. Ideal when you want guaranteed test coverage.

### bdd

**Behavior-Driven Development**: the `analista` extracts user stories with Gherkin criteria, each story becomes a failing test, then the `developer` implements the minimum needed — looping until the tests are green.

### sdd

**Spec-Driven Development**: a detailed spec is written first (scope, contracts, acceptance criteria), a review **gate** inspects it (blocker findings are fixed before continuing), then planning, implementation, and spec↔deliverable traceability with a final gate.

### edd

**Eval-Driven Development**: evaluation cases are defined up front as executable tests (at least 5), then the implementation loops until the numeric metric (`passed_count ≥ 5`) is met. The loop ends on measurement, not on vibes.

### domain_tdd

**Domain-Driven TDD**: the `architect` models the domain (entities, invariants, rules), the `analista` lists critical scenarios, and a mini TDD cycle runs **per scenario**.

### reflexion

**Generator–Critic loop** (max 3 iterations): the `developer` produces or improves the solution, the critic evaluates it against the original goal, and the loop continues until the critic's verdict matches the expected `APROBADO` (validated with a declared `exit` fallback). The previous critique is fed back into the next generation.

## Pauses, Clarifications and Resume

Workflows can pause for **human input** in two ways:

- An agent calls `ask_clarification` when your prompt is ambiguous — the run pauses and the question appears in the Maestro chat.
- A declared **gate step** (e.g. sdd's spec review) stops the run for your approval.

The pause is persisted as a `PausedSession` in PostgreSQL: it **survives app restarts**. Type your answer in the input field (or use the **⏸ Abandonar pausa** button to discard it) and the engine resumes at the exact paused step, injecting your answer where the workflow expects it.

## Running a Workflow

### From the GUI

1. Open the **Dashboard** tab and click a workflow card (this activates the preset and switches to Maestro in Orchestrate mode).
2. Select or create a **project** (required by every preset except collaborative — without one you get an actionable error).
3. Type your task and press **Ctrl+Enter**.
4. Watch progress in the activity panel (Ejecución / Subtareas / Archivos) and the Diagrama tab.

### From the CLI

```bash
poetry run python -m orchestration.dsl.cli new my-flow --type=development   # scaffold a workflow YAML
poetry run python -m orchestration.dsl.cli list                             # list available workflows
poetry run python -m orchestration.dsl.cli validate tdd --conformance       # validate + dry-run against the real engine
```

## Direct Tool Commands

Messages that match the `tool_name: action, key=value` format (e.g. `file_manager: read, path=src/main.py`) execute the tool directly — the DSL engine is not involved. See [Tools](tools.md).

## Writing Your Own Workflow

Copy a preset from `templates/workflows/` into `workspaces/<name>/workflows/` and edit it. Every document is compiled and **validated before running** (unique step ids, known agents/tools against the registry, allowed `$vars`, I/O contracts) — an invalid workflow fails fast with an actionable message instead of misbehaving at runtime. Start with `cli new` to get a correct skeleton.
