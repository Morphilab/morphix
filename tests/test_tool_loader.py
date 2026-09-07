# tests/test_tool_loader.py


class TestLoadGlobalTools:
    def test_load_global_tools_registers_tools(self):
        """load_global_tools() deja ≥12 herramientas registradas en la sesión.

        Los módulos persisten en sys.modules durante la sesión (carga
        única); el arranque en frío completo lo cubre el arranque real de la app.
        """
        from tools.loader import load_global_tools
        from tools.registry import tools_registry

        load_global_tools()
        tools = tools_registry.list_tools()
        assert len(tools) >= 12, f"herramientas registradas: {sorted(tools)}"


def test_unload_removes_registered_name_not_filename_stem(tmp_path, monkeypatch):
    """Una tool de workspace cuyo @register usa un nombre distinto al
    filename debe desregistrarse por su NOMBRE REGISTRADO, no por el stem."""

    from tools import loader
    from tools.registry import ToolsRegistry

    reg = ToolsRegistry()
    ws = "ws_alias_probe"
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "mi_pdf.py").write_text(
        "from tools.registry import tools_registry as _r\n"
        "@_r.register('alias_probe_unique')\n"
        "def alias_probe_unique(**kw):\n"
        "    return {'success': True}\n",
        encoding="utf-8",
    )

    class _Paths:
        def workspace_tools_dir(self, w):
            return tools_dir

    monkeypatch.setattr(loader, "paths", _Paths())
    # registry aislado para el test: parchear el import dentro del módulo tool
    monkeypatch.setattr(loader, "_imported_module_names", lambda: set())
    monkeypatch.setattr(loader, "_cleanup_module", lambda full: None)
    # el archivo importa tools.registry global; redirigir registro a reg aislado
    import sys
    import types

    fake_tools_reg = types.ModuleType("tools_registry_isolated")
    fake_tools_reg.tools_registry = reg
    sys.modules["tools_registry_isolated"] = fake_tools_reg
    (tools_dir / "mi_pdf.py").write_text(
        "from tools_registry_isolated import tools_registry as _r\n"
        "@_r.register('alias_probe_unique')\n"
        "def alias_probe_unique(**kw):\n"
        "    return {'success': True}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.registry.tools_registry", reg, raising=False)

    loader._workspace_modules.clear()
    loader.load_workspace_tools(ws)

    assert reg.get_tool("alias_probe_unique") is not None, "la tool no quedó registrada"

    loader.unload_workspace_tools()

    assert (
        reg.get_tool("alias_probe_unique") is None
    ), "ORCH-M5 REGRESIÓN: unregister usó el stem 'mi_pdf' en vez del nombre registrado"
