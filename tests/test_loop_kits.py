"""El formatter de kits lee el schema YAML real y el filtro por
allowlist funciona; allowed_tools=None carga todos los kits."""

import pytest
import yaml

import orchestration.loop as loop_mod
from orchestration.loop import _format_kit, _load_tool_kits

KIT = {
    "tool_kit": "debug_cycle",
    "description": "Ciclo de depuración: diagnosticar, leer, corregir, verificar",
    "steps": [
        {
            "order": 1,
            "tool": "lsp_manager",
            "action": "diagnostics",
            "params": {"path": "ruta/archivo_problema.py"},
            "note": "Detectá errores de sintaxis, tipo y estilo",
        },
        {
            "order": 2,
            "tool": "file_manager",
            "action": "read",
            "params": {"path": "ruta/archivo_problema.py"},
            "note": "Leé el archivo con errores para entender el código",
        },
        {"order": 3, "tool": "web_search", "action": "search", "note": "Buscar soluciones"},
    ],
}

OTRO_KIT = {
    "tool_kit": "git_flow",
    "description": "Flujo de commits",
    "steps": [
        {"order": 1, "tool": "web_search", "action": "search"},
        {"order": 2, "tool": "git_manager", "action": "commit"},
    ],
}


def test_format_kit_reads_current_schema():
    out = _format_kit(KIT)
    assert "DEBUG_CYCLE" in out
    assert "Ciclo de depuración" in out
    assert "lsp_manager" in out
    assert "diagnostics" in out
    assert "Detectá errores" in out


def test_format_kit_does_not_render_params():
    """Los params literales NO se renderizan (ruido; además traían claves erróneas)."""
    out = _format_kit(KIT)
    assert "ruta/archivo_problema.py" not in out
    assert "params" not in out.lower()


def test_format_kit_step_order_and_note():
    out = _format_kit(KIT)
    lines = [ln for ln in out.splitlines() if ln.strip().startswith(("1.", "2.", "3."))]
    assert len(lines) == 3
    assert lines[0].strip().startswith("1.")
    assert "💡" in out


@pytest.fixture
def kits_dir(tmp_path, monkeypatch):
    d = tmp_path / "kits"
    d.mkdir()
    (d / "debug_cycle.yaml").write_text(yaml.safe_dump(KIT), encoding="utf-8")
    (d / "git_flow.yaml").write_text(yaml.safe_dump(OTRO_KIT), encoding="utf-8")
    monkeypatch.setattr(loop_mod, "_KITS_DIR", d)
    return d


def test_filter_allowlist_without_matches_returns_empty(kits_dir):
    """Allowlist sin herramientas de ningún kit ⇒ 0 kits."""
    assert _load_tool_kits(["pdf_read"]) == ""


def test_filter_allowlist_includes_only_matching_kits(kits_dir):
    """Allowlist file_manager incluye solo kits con pasos de file_manager."""
    out = _load_tool_kits(["file_manager"])
    assert "DEBUG_CYCLE" in out
    assert "GIT_FLOW" not in out


def test_none_allowlist_loads_all_kits(kits_dir):
    """allowed_tools=None ⇒ cargar todos los YAMLs."""
    out = _load_tool_kits(None)
    assert "DEBUG_CYCLE" in out
    assert "GIT_FLOW" in out


def test_malformed_kit_skipped(kits_dir):
    (kits_dir / "roto.yaml").write_text("steps: [ { tool: }", encoding="utf-8")
    out = _load_tool_kits(None)
    assert "DEBUG_CYCLE" in out


def test_real_repo_kits_render_with_full_allowlist():
    """Golden contra los YAMLs reales del repo."""
    out = _load_tool_kits(
        ["lsp_manager", "diff_editor", "test_runner", "git_manager", "file_manager"]
    )
    assert "CODE_QUALITY" in out
    assert "DEBUG_CYCLE" in out
