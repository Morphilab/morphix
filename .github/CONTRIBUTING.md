# Contributing to Morphix

## Setup

```bash
uv sync --all-groups
cp example.env .env
# Fill in DATABASE_URL + at least one API key
uv run alembic upgrade head
```

## Development

- **Tests:** `uv run pytest`
- **Lint:** `uv run ruff check .`
- **Format:** `uv run black .`
- **Typecheck:** `uv run mypy core/ llm/ agents/ tools/ orchestration/ desktop/ viewer/`
- **Pre-commit:** `uv run pre-commit run --all-files`

## Pull Requests

1. Create a feature branch from `main`
2. Make changes, write tests
3. Run pre-commit, mypy, and pytest before pushing
4. Open a PR against `main`
