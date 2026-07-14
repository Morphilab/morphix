# Contributing to Morphix

## Setup

```bash
poetry install --with dev
cp example.env .env
# Fill in DATABASE_URL + at least one API key
poetry run alembic upgrade head
```

## Development

- **Tests:** `poetry run pytest`
- **Lint:** `poetry run ruff check .`
- **Format:** `poetry run black .`
- **Typecheck:** `poetry run mypy core/ llm/ agents/ tools/ orchestration/ desktop/`
- **Pre-commit:** `poetry run pre-commit run --all-files`

## Pull Requests

1. Create a feature branch from `main`
2. Make changes, write tests
3. Run pre-commit, mypy, and pytest before pushing
4. Open a PR against `main`
