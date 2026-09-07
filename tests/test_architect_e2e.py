# tests/test_architect_e2e.py — e2e de nivel-agente que verifica: carga REAL
# desde su YAML en el registro global, ejecución por _execute_specialized_agent
# con LLM mockeado, salida de análisis y NINGUNA acción destructiva de tools.

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def architect_loaded(tmp_path, monkeypatch):
    """Carga el agent architect real en el REGISTRO GLOBAL (workspace temporal)."""
    import shutil

    from agents.loader import load_workspace_agents
    from agents.registry import agents_registry
    from core.path_resolver import PathResolver

    ws_agents = tmp_path / "agents"
    ws_agents.mkdir()
    shutil.copy("templates/agents/architect.yaml", ws_agents / "architect.yaml")

    # Patrón del suite (nivel clase): evita dejar atributo de instancia en el
    # singleton `paths` que sombrearía stubs de tests posteriores.
    monkeypatch.setattr(
        PathResolver,
        "workspace_agents_dir",
        staticmethod(lambda ws: ws_agents),
    )
    load_workspace_agents("main")

    yield agents_registry

    agents_registry.clear_workspace_agents()


@pytest.mark.asyncio
async def test_architect_produces_analysis_without_writes(architect_loaded):
    fn = architect_loaded.get_agent("architect")
    assert fn is not None, "el agent 'architect' debió cargarse desde su YAML"
    profile = architect_loaded.get_profile("architect")
    assert profile and profile["name"] == "architect"

    design_text = "## Diseño\nComponentes: api, core.\n## Plan\n1. Módulo X. 2. Tests."
    executed: list[tuple[str, str]] = []

    async def fake_execute_tool(name, params, **kw):
        executed.append((str(name), str((params or {}).get("action", ""))))
        return {"success": True, "output": "(contenido)"}

    async def fake_call(messages=None, role="default", **kw):
        msg = MagicMock()
        msg.content = design_text
        msg.tool_calls = None
        resp = MagicMock()
        resp.choices = [MagicMock(message=msg)]
        return resp

    with (
        patch("agents.base.memory_manager.get_user_profile", return_value={}),
        patch("agents.base.memory_manager.get_user_summary", return_value=""),
        patch("agents.base.models.call", side_effect=fake_call),
        patch(
            "tools.wrapper.tool_orchestrator.execute_tool",
            side_effect=fake_execute_tool,
        ),
    ):
        out = await fn(task="Diseña el módulo de pagos", history=[])

    text = str(out)
    assert ("Diseño" in text) or ("Plan" in text), "debe entregar análisis estructurado"
    writes = [
        (n, a)
        for n, a in executed
        if ("file_manager" in n and a in {"write", "append", "delete"}) or "git" in n
    ]
    assert writes == [], f"architect ejecutó acciones destructivas: {writes}"


def test_coordinated_design_phase_includes_architect():
    import yaml

    with open("templates/workflows/coordinated.yaml", encoding="utf-8") as fh:
        tpl = yaml.safe_load(fh)
    # DSL v1: el rol de diseño vive en agents.allowed (verificar step incluye architect)
    allowed = (tpl.get("agents") or {}).get("allowed") or []
    assert "architect" in allowed, "architect debe estar en agents.allowed del DSL coordinated"
