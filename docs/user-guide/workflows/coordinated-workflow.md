# Coordinated Workflow

The Coordinated preset is Morphix's **parallel multi-agent** workflow for tasks that split into independent pieces.

**How it runs (DSL preset `coordinated`):**

1. **Decompose** — your request is broken into subtasks.
2. **Execute in parallel** — subtasks run in a dynamic parallel loop over the decomposed list (up to 10 iterations, **max 5 concurrent**), each handled by the `developer` agent with retry ≤ 2 and 300 s timeout.
3. **Verify and aggregate** — results are verified and aggregated with confidence scoring at the end.

Agents available: developer, analista, moderador, architect. A project is **required**.

For the shared mechanics (pauses/clarifications, resume, running from GUI or CLI, the other 8 presets), see [Workflows Overview](../workflows.md).
