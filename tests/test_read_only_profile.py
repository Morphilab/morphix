# tests/test_read_only_profile.py — enforcement tool-level del perfil read-only
"""El SOUL del analista prohíbe escribir en PROSA; el motor lo prohíbe de verdad.

Caso real: el analista escribió implementación
completa — archivos de código incluidos — desde el paso de spec de un run,
porque file_manager (con acción write) está en su perfil y nada a nivel de
tool lo impedía. El gate niega ANTES de ejecutar: la denegación viaja al
modelo como tool_result accionable, igual que la del allowlist.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml
from _agent_loop_support import mock_agent_deps  # noqa: F401,E402 — fixture compartida del split


def _viola(tool: str, params: dict):
    from orchestration.loop import _read_only_violation

    return _read_only_violation(tool, params)


class TestReadOnlyViolation:
    def test_file_manager_acciones_mutantes_bloqueadas(self):
        for action in ("write", "append", "delete"):
            assert _viola("file_manager", {"action": action, "path": "x.py"})

    def test_file_manager_content_sin_action_cuenta_como_write(self):
        # paritario con _is_modifying_action: el modelo omite 'action'
        assert _viola("file_manager", {"path": "x.py", "content": "print(1)"})

    def test_file_manager_lecturas_permitidas(self):
        assert _viola("file_manager", {"action": "read", "path": "x.py"}) is None
        assert _viola("file_manager", {}) is None

    def test_tools_de_escritura_directa_bloqueadas_enteras(self):
        # bash escribe por redirección; diff_editor solo existe para aplicar
        # ediciones; code_exec ejecuta código arbitrario.
        for tool in ("diff_editor", "bash_manager", "code_exec"):
            assert _viola(tool, {})

    def test_git_manager_mutante_bloqueado_consultas_permitidas(self):
        for action in ("init", "add", "commit"):
            assert _viola("git_manager", {"action": action})
        for action in ("log", "diff", "show"):
            assert _viola("git_manager", {"action": action}) is None

    def test_verificacion_y_busqueda_permitidas(self):
        assert _viola("test_runner", {"file_path": "tests/x.py"}) is None
        assert _viola("code_search", {"query": "x"}) is None
        assert _viola("lsp_manager", {"action": "hover"}) is None


@pytest.mark.asyncio
async def test_single_tool_call_niega_escritura_antes_de_ejecutar(monkeypatch):
    """El gate precede al dispatch: safe_tool_call jamás se invoca."""
    from orchestration import loop as loop_mod

    ejecutada = AsyncMock(return_value={"success": True, "output": "no debe llegar"})
    monkeypatch.setattr(loop_mod, "safe_tool_call", ejecutada)

    out, is_mod, _path, ok = await loop_mod._execute_single_tool_call(
        "file_manager",
        {"action": "write", "path": "x.py", "content": "x"},
        project_root=None,
        workspace="main",
        read_only=True,
    )
    assert "solo lectura" in out
    assert ok is False
    assert is_mod is False
    ejecutada.assert_not_awaited()


@pytest.mark.asyncio
async def test_single_tool_call_permite_lectura_read_only(monkeypatch):
    from orchestration import loop as loop_mod

    ejecutada = AsyncMock(return_value={"success": True, "output": "contenido"})
    monkeypatch.setattr(loop_mod, "safe_tool_call", ejecutada)

    out, _is_mod, _path, ok = await loop_mod._execute_single_tool_call(
        "file_manager",
        {"action": "read", "path": "x.py"},
        project_root=None,
        workspace="main",
        read_only=True,
    )
    assert ok is True
    assert out == "contenido"
    ejecutada.assert_awaited_once()


@pytest.mark.asyncio
async def test_single_tool_call_sin_read_only_no_getea(monkeypatch):
    """Agente normal: el write pasa al dispatch (regresión del default)."""
    from orchestration import loop as loop_mod

    ejecutada = AsyncMock(return_value={"success": True, "output": "escrito"})
    monkeypatch.setattr(loop_mod, "safe_tool_call", ejecutada)

    _out, is_mod, _path, ok = await loop_mod._execute_single_tool_call(
        "file_manager",
        {"action": "write", "path": "x.py", "content": "x"},
        project_root=None,
        workspace="main",
        read_only=False,
    )
    assert ok is True
    assert is_mod is True
    ejecutada.assert_awaited_once()


@pytest.mark.asyncio
async def test_loop_resuelve_read_only_del_perfil_y_deniega(mock_agent_deps, monkeypatch):
    """E2E del plumbing: perfil con read_only → el modelo EMITE un write →
    la denegación precede a safe_tool_call y el archivo jamás se escribe."""
    from agents.registry import agents_registry

    mock_llm, mock_tool, _mock_response = mock_agent_deps
    monkeypatch.setattr(
        agents_registry,
        "get_profile",
        lambda t: {"name": t, "read_only": True} if t == "analista" else None,
    )

    tool_response = MagicMock()
    tool_response.choices = [MagicMock()]
    tool_response.choices[0].message.content = None
    tool_call = MagicMock()
    tool_call.function.name = "file_manager"
    tool_call.function.arguments = '{"action": "write", "path": "x.py", "content": "x"}'
    tool_response.choices[0].message.tool_calls = [tool_call]

    final_response = MagicMock()
    final_response.choices = [MagicMock()]
    final_response.choices[0].message.content = "Análisis entregado (prosa)."
    final_response.choices[0].message.tool_calls = None

    mock_llm.side_effect = [tool_response, final_response]

    from orchestration.loop import execute_agent_loop

    result = await execute_agent_loop(
        task="Diseña el módulo",
        agent_type="analista",
        allowed_tools=["file_manager"],
        workspace="main",
    )
    assert result["status"] == "completed"
    # el write del modelo murió en el gate: la tool nunca se ejecutó
    mock_tool.assert_not_awaited()
    assert result["files_written"] == []


def test_perfil_analista_declara_read_only():
    """El enforcement vive del campo: el guard fija que analista lo declara.

    El template es la fuente de verdad; la copia runtime solo se valida si
    existe (un checkout limpio no tiene workspaces provisionados todavía).
    """
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    rels = ["templates/agents/analista.yaml"]
    runtime = repo / "workspaces/main/agents/analista.yaml"
    if runtime.exists():
        rels.append("workspaces/main/agents/analista.yaml")
    for rel in rels:
        data = yaml.safe_load((repo / rel).read_text(encoding="utf-8"))
        assert data.get("read_only") is True, f"{rel} debe declarar read_only: true"


def test_los_demas_agentes_no_declaran_read_only():
    """Solo el rol de análisis es read-only: un read_only accidental en
    developer/moderador/conversacional mataría su propósito."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    for y in sorted((repo / "templates" / "agents").glob("*.yaml")):
        if y.name.startswith("_") or y.stem == "analista":
            continue
        data = yaml.safe_load(y.read_text(encoding="utf-8")) or {}
        assert not data.get("read_only"), f"{y.name} declara read_only sin ser rol de análisis"
