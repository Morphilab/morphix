# Contributing

This guide covers how to set up a development environment, follow code conventions, and submit pull requests to Morphix.

## Prerequisites

- **Python 3.12** (required; `<3.14`). Use [pyenv](https://github.com/pyenv/pyenv) or your system package manager.
- **PostgreSQL** — required. Install and create a database.
- **Poetry** — for dependency management. [Install via pipx](https://python-poetry.org/docs/#installation).
- **Redis** (optional) — for caching.
- **Ollama** (optional) — for offline mode.

## Setup

```bash
# Clone the repository
git clone https://github.com/morphilab/morphix.git
cd morphix

# Install dependencies including dev tools
poetry install --with dev

# Configure environment
cp example.env .env
```

Edit `.env` and fill in the required values:

```ini
# Required: PostgreSQL connection
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/morphix

# Required: at least one API key
DEEPSEEK_API_KEY=sk-xxxxxxxxxxx

# Optional but recommended for development
ENCRYPTION_KEY=     # auto-generates in dev; required in production
HF_TOKEN=           # speeds up HuggingFace model downloads
```

### Database setup

```bash
# Create the database (if not already created)
createdb morphix

# Run migrations
poetry run alembic upgrade head
```

!!! note "Migrations are manual"
    `startup_db()` creates tables directly but does not run Alembic. Always run `alembic upgrade head` after schema changes.

### Verify the setup

```bash
# All checks should pass
poetry run python -c "from core.config import settings; print(settings.database_url)"
poetry run ruff check .
poetry run black --check .
poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/
poetry run pytest
```

## Pre-commit hooks

Morphix uses [pre-commit](https://pre-commit.com/) to enforce code quality on every commit.

```bash
# Install the hooks
poetry run pre-commit install

# Run all hooks manually
poetry run pre-commit run --all-files
```

### What runs on commit

| Hook | What it does |
|------|-------------|
| `trailing-whitespace` | Removes trailing whitespace |
| `end-of-file-fixer` | Ensures files end with a newline |
| `check-yaml` | Validates YAML syntax |
| `check-added-large-files` | Prevents committing large files |
| `black` | Formats Python code |
| `ruff --fix` | Lints and auto-fixes Python code |
| `mypy` | Type-checks `core/ llm/ agents/ tools/ orchestration/ desktop/` |

Pre-commit auto-fixes formatting and linting issues. Mypy and structural checks must pass before the commit proceeds.

## Full local check order

Before submitting, run these in order:

```bash
# 1. Lint + auto-fix
poetry run ruff check .

# 2. Format check (must pass, no changes)
poetry run black --check .

# 3. Type check (0 errors required)
poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/

# 4. Run all tests
poetry run pytest
```

## Code conventions

### Paths

**Never hardcode paths.** Use `core.path_resolver.paths` for all filesystem paths:

```python
from core.path_resolver import paths

# Good
config_dir = paths.workspace_config_dir("main")
memory_base = paths.memory_base()

# Bad
config_dir = Path("workspaces/main/config")
memory_base = Path("memory")
```

### Environment

- `.env` is loaded from **project root** by `run.py` (which calls `load_dotenv` explicitly).
- For non-GUI contexts, ensure `.env` is on `PYTHONPATH` or loaded manually.
- `sys.path.insert(0, ...)` in `run.py` ensures imports work from project root regardless of CWD.

### Testing

- **Framework:** pytest with `pytest-asyncio`, `asyncio_mode = "auto"`.
- **Mark async tests** with `@pytest.mark.asyncio`.
- **Mocking:** `unittest.mock.AsyncMock` and `MagicMock` for async mocks.
- **No shared fixtures in `conftest.py`.** Define mocks inline in each test module.
- **Use `ToolsRegistry()` for tests**, not the global `tools_registry`.
- Coverage runs on `core/`, `llm/`, `agents/`, `tools/`, `orchestration/`.

See [Testing Guide](testing-guide.md) for detailed examples.

### Type checking

Mypy runs on all source directories with **0 errors allowed, 0 exclusions**:

```bash
poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/
```

### Imports

- `run.py` has `sys.path.insert(0, project_root)` so imports work from anywhere.
- Use absolute imports within the project: `from core.config import settings`, not `from ..config import settings`.
- No circular imports. If you hit one, extract the shared dependency into its own module.

### Layer boundaries

Do not import from layers above the one you're working in:

| Layer | Can import from |
|-------|----------------|
| `core/` | stdlib, third-party packages |
| `llm/` | `core/` |
| `agents/` | `core/`, `llm/` |
| `tools/` | `core/`, `llm/` |
| `orchestration/` | `core/`, `llm/`, `agents/`, `tools/` |
| `desktop/` | `core/`, `llm/`, `agents/`, `tools/`, `orchestration/` |

## Commit conventions

Commit messages follow this format:

```
type(scope): description
```

Where `type` is one of:

- `feat` — new feature
- `fix` — bug fix
- `refactor` — code restructuring without behavior change
- `test` — adding or updating tests
- `docs` — documentation changes
- `chore` — maintenance tasks

Examples from the project history:

```
fix: streaming tool-call argument accumulation
feat(gui): tab Editor con visualizador de archivos
docs(dev-guide): index, adding tools, agents, workflows, hooks, contributing, testing
refactor: DB engine loop-hardening
test: fix 3 stale tests + 1 isolation bug
```

## PR checklist

Before opening a pull request:

- [ ] `ruff check .` passes with no errors
- [ ] `black --check .` passes (no files would be reformatted)
- [ ] `mypy core/ llm/ agents/ tools/ orchestration/ desktop/` passes with 0 errors
- [ ] `pytest` passes all tests (675+ tests)
- [ ] New code has corresponding tests
- [ ] No hardcoded paths — use `core.path_resolver.paths`
- [ ] No shared fixtures in `conftest.py` — mocks are inline per module
- [ ] Workspace templates synced between `templates/` and `workspaces/main/`
- [ ] Pre-commit hooks pass (`pre-commit run --all-files`)
- [ ] Commit message follows `type(scope): description` format

## Common issues

### "Module not found" errors

Ensure you're running from the project root. `run.py` handles `sys.path`, but scripts and tests may need the Poetry environment:

```bash
poetry run python my_script.py
poetry run pytest tests/
```

### "Ruta no permitida" (path not allowed)

File and tool operations are sandboxed to `memory/<workspace>/`. Always use relative paths. The `project_root` parameter is for sub-project isolation, not absolute paths.

### Stale workspace templates

Workspace templates in `workspaces/<name>/` are copied from `templates/` once on first workspace creation. If you update templates, copy them manually:

```bash
cp templates/agents/*.yaml workspaces/main/agents/
cp templates/workflows/*.yaml workspaces/main/workflows/
```

### Database connection errors

Check that PostgreSQL is running and the DATABASE_URL in `.env` is correct. The URL format is:

```
postgresql://user:password@host:port/database
```

The code rewrites `postgresql://` to `postgresql+asyncpg://` for the async engine automatically.

## Getting help

- Review the [Architecture docs](../architecture/index.md) for design context
- Check the [API Reference](../api-reference/index.md) for module-level docs
- Read existing code in `tools/`, `agents/`, and `orchestration/workflows/` for patterns
- Run `poetry run pytest tests/ -x --lf` to focus on the most recent failing test
