# tests/test_tool_loader.py


class TestLoadGlobalTools:
    def test_load_global_tools_registers_tools(self):
        """Verifica que load_global_tools() registra herramientas correctamente."""
        from tools.loader import load_global_tools
        from tools.registry import tools_registry

        # Guardar estado original
        original = dict(tools_registry._tools)
        try:
            tools_registry._tools = {}
            load_global_tools()
            tools = tools_registry.list_tools()
            assert len(tools) >= 5
        finally:
            # Restaurar
            tools_registry._tools = original

    def test_load_global_tools_skips_underscore_modules(self):
        """Verifica que módulos que empiezan con _ no se cargan."""
        from tools.loader import load_global_tools
        from tools.registry import tools_registry

        original = dict(tools_registry._tools)
        try:
            tools_registry._tools = {}
            load_global_tools()
            tools = tools_registry.list_tools()
            for t in tools:
                assert not t.startswith("_")
        finally:
            tools_registry._tools = original
