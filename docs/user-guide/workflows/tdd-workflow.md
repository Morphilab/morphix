# TDD Workflow

The TDD (Test-Driven Development) workflow automates the **red-green-refactor** cycle. Morphix writes tests, runs them, implements code to make them pass, and iterates until all tests succeed — up to 5 iterations. It's designed for building features with guaranteed test coverage from the start.

## How the TDD Loop Works

```
                 ┌─────────────┐
                 │ Run tests    │
                 └──────┬──────┘
                        │
                   tests pass? ──yes──→ ✅ Done
                        │
                        no
                        │
                 ┌──────▼──────┐
                 │ Agent fixes  │
                 │ or writes    │
                 │ code + tests │
                 └──────┬──────┘
                        │
                 (repeat up to 5 times)
```

### Iteration 1: First Test Run

```python
# Morphix calls test_runner with:
test_runner(file_path=".", workspace="main", project_root="myproject")
```

The output determines the next action:

- **All tests pass, no failures, no errors** → Loop exits immediately with success
- **Tests exist but fail** → Agent analyzes failures and corrects code
- **No tests found in project** → Green-field detection kicks in

### Green-Field Detection

If the project directory contains no files matching `test_*.py` or `*_test.py` (excluding `.git`, `node_modules`, `__pycache__`, `.venv`, `.undo`, `.redo`), Morphix enters **green-field mode**:

```
Aún NO existen tests en el proyecto. Implementa con TDD:
1. PRIMERO escribe los tests con pytest usando file_manager (action=write);
   nómbralos 'test_*.py' (p.ej. 'test_es_primo.py').
2. LUEGO escribe la implementación para que los tests pasen.
```

This ensures tests are written before implementation code — the core TDD principle.

### Correction Mode

When tests already exist but fail, the agent receives the full test output (up to 3000 characters) and is instructed:

```
Analiza los fallos y CORRIGE el código para que los tests pasen.
Usa file_manager (action=read) para leer archivos existentes,
y file_manager (action=write) o diff_editor (action=apply) para modificarlos.
```

The agent reads the failing files, identifies the issue, and applies fixes using `file_manager` or `diff_editor`.

## Configuration

| Parameter | Value | Description |
|-----------|:---:|-------------|
| `MAX_TDD_ITERATIONS` | 5 | Maximum red-green-refactor cycles |
| Per-iteration timeout | 300s | Agent has 5 minutes per correction attempt |
| Agent type | developer | The agent used for writing and fixing code |
| Allowed tools | file_manager, diff_editor, test_runner, git_manager | Tools available during TDD |
| pytest flags | `--rootdir=<project>`, `-p no:cacheprovider` | Applied automatically |
| Excluded dirs | `.git`, `node_modules`, `__pycache__`, `.venv`, `.undo`, `.redo` | Skipped during test file scan |

The developer agent uses `max_agent_iterations` (default 8) for its internal ReAct loop during each TDD iteration.

## Pytest Configuration

Morphix configures pytest for each test run:

```python
test_runner(
    file_path=".",
    workspace=workspace,
    project_root=project_root,
)
```

The `test_runner` tool internally sets:
- `--rootdir` to the project's root directory
- `-p no:cacheprovider` to disable pytest caching (ensures fresh results each iteration)
- Default timeout of 30 seconds per test run

## How the Agent Determines What Tests to Write

### Green-field (no existing tests)

The agent is told the task description and instructed to write tests first, then implementation. Example:

**Task:** "Create a function that checks if a number is prime"

**Agent writes:**
```python
# test_es_primo.py
import pytest
from es_primo import es_primo

def test_primo_con_numero_primo():
    assert es_primo(7) is True

def test_primo_con_numero_no_primo():
    assert es_primo(4) is False

def test_primo_con_uno():
    assert es_primo(1) is False

def test_primo_con_numero_negativo():
    assert es_primo(-5) is False
```

Then implements `es_primo.py`, then tests pass.

### With failing tests

The agent reads the failing test output, reads the relevant source files, and applies targeted fixes. It explains each change made and why.

## Exit Conditions

The TDD loop exits when:

| Condition | Status | Description |
|-----------|--------|-------------|
| All tests pass | `completed` | Success — tests pass with 0 failures and 0 errors |
| Timeout (300s) | `failed` | Agent took too long in an iteration |
| Agent stalled | `failed` | Agent returned `stalled` status (can't make progress) |
| Max iterations reached | `failed` | 5 iterations completed but tests still fail |

## Files Modified Tracking

Across all iterations, the `files_modified` list accumulates. Each iteration's `files_written` from the agent result is merged, and the final output includes all files that were created or modified:

```python
for f in agent_files:
    if f not in files_modified:
        files_modified.append(f)
```

## When to Use

**Good fits:**
- Building a new utility function or module from scratch
- Implementing an algorithm with known inputs/outputs
- Adding features to an existing pytest-covered project
- Learning TDD by watching an AI do it

**Not ideal for:**
- UI/frontend changes (pytest doesn't test React components)
- Infrastructure/config changes
- Design discussions (use Collaborative)
- Tasks where tests are hard to write (use Development)

## Example Session

```
Task: Create a Fibonacci function that returns the nth number in the sequence.
      fib(0) = 0, fib(1) = 1, fib(n) = fib(n-1) + fib(n-2)
```

**TDD Loop execution:**

1. **Run tests** → No test files found → Green-field mode
2. **Agent writes** `test_fibonacci.py` with test cases (0→0, 1→1, 5→5, 10→55)
3. **Agent writes** `fibonacci.py` with the recursive implementation
4. **Run tests** → All 4 tests pass → `✅ Done in iteration 1`

```
Task: This Fibonacci function is too slow for n=35. Optimize it.
```

**TDD Loop execution (same project, tests already exist):**

1. **Run tests** → All pass (tests from previous session)
2. **Agent reads** `fibonacci.py` and `test_fibonacci.py`
3. **Agent rewrites** `fibonacci.py` with memoization
4. **Agent adds** a performance test for n=35
5. **Run tests** → All tests pass, performance test passes under threshold → `✅ Done in iteration 2`

!!! note "Project required"
    TDD requires a project. If you haven't selected one, create a project first from the project dropdown in the Maestro top bar before clicking the TDD workflow card.
