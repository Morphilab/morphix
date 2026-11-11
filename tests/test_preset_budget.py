# tests/test_preset_budget.py — budget_tokens por preset DSL
"""El preset acota SU run (budget_tokens) sin tocar el techo global.

El global (TOOL_MAX_TOKENS_PER_WORKFLOW) es la política de la plataforma;
el preset solo puede BAJARLO para su propio run — nunca ampliarlo.
"""

import pytest


def _doc(**over):
    base = {
        "version": 1,
        "name": "demo",
        "agents": {"allowed": ["developer"]},
        "tools": {"allowed": ["file_manager"]},
        "steps": [{"id": "a", "kind": "agent", "agent": "developer", "goal": "g"}],
    }
    base.update(over)
    return base


@pytest.fixture()
def _budget_activo(monkeypatch):
    """Estado de budget activo fijo (500k) con limpieza del ContextVar."""
    import tools.orchestrator as tools_orch
    from core.config import settings
    from tools.orchestrator import ToolOrchestrator

    monkeypatch.setattr(settings, "tool_max_tokens_per_workflow", 500_000, raising=False)
    monkeypatch.setattr(settings, "tool_enable_token_budget", True, raising=False)
    ToolOrchestrator.reset_token_budget()
    yield
    tools_orch._token_budget_ctx.set(None)  # no filtrar estado a otros tests


def test_schema_acepta_budget_tokens():
    from orchestration.dsl.compiler import compile_workflow

    cw = compile_workflow(_doc(budget_tokens=150_000))
    assert cw.dsl.budget_tokens == 150_000
    assert compile_workflow(_doc()).dsl.budget_tokens is None


def test_set_token_budget_max_respeta_el_hard_cap(_budget_activo, monkeypatch):
    from core.config import settings
    from tools.orchestrator import get_token_budget_max, set_token_budget_max

    monkeypatch.setattr(settings, "tool_max_tokens_hard_cap", 2_000_000, raising=False)
    assert get_token_budget_max() == 500_000
    set_token_budget_max(150_000)
    assert get_token_budget_max() == 150_000

    # long-horizon: un preset puede SUBIR el techo hasta el hard cap
    set_token_budget_max(1_500_000)
    assert get_token_budget_max() == 1_500_000

    # jamás por encima del hard cap de la plataforma
    set_token_budget_max(3_000_000)
    assert get_token_budget_max() == 2_000_000


def test_apply_preset_budget_cablea_el_techo(_budget_activo):
    from orchestration.dsl.compiler import compile_workflow
    from orchestration.workflows.orchestrator import _apply_preset_budget
    from tools.orchestrator import get_token_budget_max

    cw = compile_workflow(_doc(budget_tokens=120_000))
    assert _apply_preset_budget(cw) == 120_000
    assert get_token_budget_max() == 120_000

    # sin budget declarado no toca nada
    assert _apply_preset_budget(compile_workflow(_doc())) is None
    assert get_token_budget_max() == 120_000
