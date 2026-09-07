# Development Workflow

The Development preset is Morphix's general-purpose workflow for everyday software engineering — building features, fixing bugs, refactoring, adding tests.

**How it runs (DSL preset `development`):**

1. **Decompose** — your request is broken into flat subtasks.
2. **Execute** — subtasks run sequentially on the `developer` agent (loop of up to 10 iterations, per-subtask retry ≤ 2 and 300 s timeout) with the full coding toolset: `file_manager`, `git_manager`, `bash_manager`, `lsp_manager`, `code_exec`, `test_runner`, `diff_editor`, `file_view`, goals/todos, and project docs.
3. **Aggregate** — results are aggregated deterministically from the subtask statuses and the files actually written.

A project is **required** — select or create one in the Maestro top bar before launching.

For the shared mechanics (pauses/clarifications, resume, running from GUI or CLI, the other 8 presets), see [Workflows Overview](../workflows.md).
