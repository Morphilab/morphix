# tests/test_tools_registry.py
import pytest


@pytest.fixture
def clean_registry():
    """ToolsRegistry limpio para tests."""
    from tools.registry import ToolsRegistry

    registry = ToolsRegistry()
    registry._tools = {}
    return registry


def test_register_tool(clean_registry):
    """Registrar una tool funciona con el decorador."""

    @clean_registry.register("test_tool")
    async def test_tool(x: int) -> dict:
        return {"result": x * 2}

    assert "test_tool" in clean_registry.list_tools()


@pytest.mark.asyncio
async def test_get_tool_registered(clean_registry):
    """Obtener una tool registrada devuelve la función."""

    @clean_registry.register("my_tool")
    async def my_tool():
        return "ok"

    tool_fn = clean_registry.get_tool("my_tool")
    assert tool_fn is not None
    result = await tool_fn()
    assert result == "ok"


def test_get_tool_not_found(clean_registry):
    """Tool no registrada devuelve None."""
    result = clean_registry.get_tool("no_existe")
    assert result is None


def test_list_tools_empty():
    """Listar tools en un registro vacío."""
    from tools.registry import ToolsRegistry

    registry = ToolsRegistry()
    assert registry.list_tools() == {}


def test_register_twice_overwrites(clean_registry):
    """Registrar misma tool dos veces sobrescribe."""

    @clean_registry.register("dup")
    async def first():
        return "first"

    @clean_registry.register("dup")
    async def second():
        return "second"

    tools = list(clean_registry.list_tools().keys())
    assert "dup" in tools
