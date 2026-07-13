# Coordinated Workflow

The Coordinated workflow is Morphix's most powerful orchestration mode. It decomposes your task into a **Directed Acyclic Graph (DAG)**, executes independent subtasks in parallel, and shares context between agents via a **blackboard**. It's designed for complex multi-step tasks that benefit from structured decomposition and parallel execution.

## Architecture

The `MultiAgentCoordinator` drives the Coordinated workflow in four phases:

```
Phase 1: decompose_task_dag() → DAG with dependencies
Phase 2: assign_agents()        → Best agent per subtask
Phase 3: execute_dag()          → Parallel level-by-level execution
Phase 4: aggregate_with_confidence() → Confidence-weighted synthesis
```

## DAG-Based Parallel Execution

Unlike the Development workflow's sequential subtasks, Coordinated uses a DAG structure where each subtask declares its dependencies:

```json
{
  "subtasks": [
    {"id": "design_schema", "description": "Design the database schema", "depends_on": [], "agent_hint": "architect"},
    {"id": "create_models", "description": "Create SQLAlchemy models", "depends_on": ["design_schema"], "agent_hint": "developer"},
    {"id": "create_routes", "description": "Create API routes", "depends_on": ["design_schema"], "agent_hint": "developer"},
    {"id": "write_tests", "description": "Write pytest tests", "depends_on": ["create_models", "create_routes"], "agent_hint": "developer"}
  ]
}
```

**Parallelism rules:**
- Subtasks with **no dependencies** (`depends_on: []`) run immediately in parallel
- Subtasks whose **all dependencies are completed** run in the next parallel wave
- Up to **4 subtasks in parallel** (`max_parallel: 4`)

In the example above: `design_schema` runs first, then `create_models` and `create_routes` run in parallel (both depend on design), then `write_tests` runs after both complete.

### Fallback Behavior

If the DAG gets stuck (circular dependencies or all remaining subtasks have unmet dependencies), the coordinator falls back to sequential execution of remaining subtasks.

### Retry on Failure

If a subtask fails, the coordinator retries it **once** with a different fallback agent (cycles through `developer` → `analista` → `developer`). Maximum 2 retries per subtask (`retry_max: 2`).

## Blackboard: Cross-Phase Context Sharing

The `SharedBlackboard` is an async-safe, phase-scoped key-value store that enables agents to share information:

### Phase Namespaces

Blackboard entries are organized by phase name. The default phases are:

| Phase | Agents | Purpose |
|-------|--------|---------|
| `design` | architect, analista | Architecture planning and design |
| `implement` | developer | Code implementation |
| `verify` | analista, developer | Testing and verification |

Each phase's results are written to the blackboard under that phase namespace. Subsequent phases can read context from previous phases to avoid duplicated work.

### Writing and Reading

```python
# Write a result (done automatically after each subtask)
await blackboard.write(
    f"subtask_{sid}_result",
    {"agent": "developer", "task": "...", "status": "completed", "files_written": [...]},
    phase="implement"
)

# Read cross-phase context (injected as extra_context for agents)
ctx = await blackboard.get_cross_phase_context(exclude_phase="implement")
```

Agents receive a `SHARED CONTEXT` block in their prompt telling them what other agents have already done and which files exist — preventing duplicate work.

### Persistence

Blackboard entries are persisted to PostgreSQL via the `BlackboardEntry` model. After each phase completes, `sync_to_db()` writes all entries to the database. On workflow resume (after a pause for clarification), `sync_from_db()` restores the blackboard state.

### Snapshot and Restore

The blackboard supports `snapshot()` and `restore()` for pause/resume. When the workflow is paused for a user clarification, the blackboard state is serialized and saved with the `PausedSession`.

## Multi-Phase Execution

When the decomposer detects a task that naturally splits into phases, it uses `decompose_task_with_phases()` to produce a **phase-aware decomposition**:

```json
{
  "phases": [
    {"phase": "design", "subtasks": ["Design the user authentication system", "Plan the database schema"]},
    {"phase": "implement", "subtasks": ["Create the auth module", "Build the User model", "Add login endpoint"]},
    {"phase": "verify", "subtasks": ["Test the auth flow", "Verify security best practices"]}
  ]
}
```

Each phase is executed sequentially (phases are ordered), but subtasks **within** a phase run as a DAG with parallel execution.

Maximum 4 phases (`max_phases: 4`).

## Per-Subtask Timeout

Each subtask has a **180-second timeout**:

```python
result = await asyncio.wait_for(
    execute_agent_loop(...),
    timeout=180,
)
```

If a subtask times out, it's marked as `failed` with the error `"Subtask timed out after 180s"`.

## Agent Assignment

Agent assignment uses a two-tier strategy:

1. **AgentRouter** (primary) — LLM-based quality routing using the subtask description
2. **agent_hint fallback** — Each subtask's `agent_hint` field (set during decomposition) is used if the router fails
3. **Keyword fallback** — If both fail, the first allowed agent is used

The `allowed_agents` list from the template filters which agents can be assigned:
- `developer` — code writing, building, testing
- `analista` — analysis, review, verification
- `architect` — system design, architecture planning
- `moderador` — not typically used in coordinated (reserved for collaborative)

## Confidence-Weighted Aggregation

The `aggregate_with_confidence()` method asks the LLM to:
1. Score each agent's result for quality
2. Identify conflicts between agent outputs
3. Synthesize the best information into one coherent answer
4. State what's missing honestly if results are poor

This produces higher-quality final output than simple concatenation.

## Configuration

| Parameter | Default | Source |
|-----------|:---:|--------|
| `max_parallel` | 4 | `coordinated.yaml` → `max_parallel` |
| `max_agent_iterations` | 8 | `settings.max_agent_iterations` |
| `retry_on_failure` | true | `coordinated.yaml` |
| `retry_max` | 2 | `coordinated.yaml` |
| `phases_enabled` | true | `coordinated.yaml` |
| `max_phases` | 4 | `coordinated.yaml` |
| Per-subtask timeout | 180s | Hardcoded in `execute_dag()` |
| Max subtasks in DAG | 6 | DAG decompose prompt |

## When to Use

**Good fits:**
- Building a full-stack feature (backend + frontend + tests)
- Microservice with database, API, and deployment config
- Multi-module refactoring where modules are independent
- Data pipeline with extract, transform, validate stages

**Not ideal for:**
- Simple single-file changes (use Development)
- Design discussions (use Collaborative)
- TDD-first features (use TDD)
- Quick questions (use Chat mode)

## Example Prompt

```
Build a bookmark manager web app:

1. Design the React component tree and Flask API structure
2. Implement the Flask backend with SQLAlchemy models (Bookmark, Tag, Folder)
3. Create the React frontend with components for listing, adding, editing bookmarks
4. Add JWT authentication
5. Write integration tests for the API
6. Add a README with setup instructions
```

**Expected flow:**
- Decomposed into phases: `design` → `implement` → `verify`
- Within `implement`, backend and frontend subtasks run in parallel
- Both wait for `design` to complete
- `verify` runs after both complete
- Final output is a confidence-weighted synthesis of all results
