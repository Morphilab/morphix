# TDD Workflow

The TDD preset automates the **red-green-refactor** cycle: Morphix writes tests, runs them, implements code to make them pass, and iterates until all tests succeed.

**How it runs (DSL preset `tdd`):**

1. **Loop (max 5 iterations)** — the `developer` agent writes or corrects code *and* tests (`file_manager`, `diff_editor`), informed by the previous iteration's output (`$last_output`, `$iter`).
2. **Deterministic exit** — after each iteration the engine runs `test_runner` on the project; the loop only ends when **all tests pass** (parsed pytest counts — `tests_all_pass` — never the model's own judgment).
3. Tools available inside the cycle: `file_manager`, `diff_editor`, `test_runner`, `git_manager`. Per-step timeout: 300 s.

A project is **required** — select or create one in the Maestro top bar before launching the preset.

For the shared mechanics (pauses/clarifications, resume, running from GUI or CLI, the other 8 presets), see [Workflows Overview](../workflows.md).
