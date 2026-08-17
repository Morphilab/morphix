"""Guard tests para defaults de configuración sensibles.

El rol `fast` bajó a max_tokens=512 para contener el reasoning, pero la
evidencia de validación (2026-08-14) mostró starvation: "Respuesta vacía
por agotamiento de tokens (reasoning)". Mínimo aceptado: 1024.
"""

from core.config import Settings


def test_fast_role_max_tokens_at_least_1024():
    roles = Settings.model_fields["model_roles"].default_factory()
    assert roles["fast"]["max_tokens"] >= 1024


def test_fast_role_has_tool_calling():
    roles = Settings.model_fields["model_roles"].default_factory()
    assert roles["fast"].get("tool_calling") is True


def test_token_budget_single_canonical_value(monkeypatch):
    """El default de Settings y example.env deben coincidir (80000).

    Drift histórico: 50000 (default), 8000 (example.env), 500000 (.env),
    80000 (AGENTS.md). Canonical: 80000.
    """
    import re
    from pathlib import Path

    from core.config import Settings

    monkeypatch.delenv("TOOL_MAX_TOKENS_PER_WORKFLOW", raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.tool_max_tokens_per_workflow == 80000

    root = Path(__file__).parent.parent
    example_env = (root / "example.env").read_text()
    m = re.search(r"^TOOL_MAX_TOKENS_PER_WORKFLOW=(\d+)$", example_env, re.M)
    assert m, "example.env no define TOOL_MAX_TOKENS_PER_WORKFLOW"
    assert int(m.group(1)) == 80000
