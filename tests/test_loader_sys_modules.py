"""El loader registra los módulos en sys.modules ANTES de ejecutarlos.

Antes: exec sin registrar → cada tool se ejecutaba DOS veces (loader +
import canónico), con singletons duplicados (ToolOrchestrator, etc.).
"""

import importlib
import sys


def test_load_global_tools_registers_in_sys_modules():
    from tools.loader import load_global_tools

    load_global_tools()

    # Nombres de ARCHIVO, el alias del registro puede diferir (code_exec≠code_execution)
    for expected in ("tools.code_execution", "tools.bash_manager", "tools.diff_editor"):
        assert expected in sys.modules, f"NV-L8: {expected} no registrado tras load_global_tools()"


def test_canonical_import_reuses_loaded_module():
    """import canónico y carga por spec deben producir EL MISMO objeto módulo."""
    from tools.loader import load_global_tools

    load_global_tools()
    mod = importlib.import_module("tools.test_runner")
    assert sys.modules["tools.test_runner"] is mod


def test_workspace_modules_also_registered(tmp_path):
    from tools.loader import _import_module_from_file

    tool_file = tmp_path / "mi_tool.py"
    tool_file.write_text("VALUE = 42\n", encoding="utf-8")
    name = "workspaces.ws_test.tools.mi_tool"

    assert _import_module_from_file(name, tool_file) is True
    assert sys.modules.get(name) is not None
    assert importlib.import_module(name).VALUE == 42

    # limpieza
    del sys.modules[name]
