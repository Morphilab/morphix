# tests/test_code_search.py
"""Tests para la herramienta de búsqueda de código."""

import pytest


@pytest.mark.asyncio
async def test_code_search_invalid_regex(tmp_path):
    """Verifica que un regex inválido retorna error descriptivo."""
    from unittest.mock import patch

    from tools.code_search import _code_search_tool

    (tmp_path / "dummy.py").write_text("test")

    with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
        result = await _code_search_tool(
            pattern="[invalid",
            workspace="main",
        )
        assert "regex inválido" in result.lower()


@pytest.mark.asyncio
async def test_code_search_path_traversal_blocked():
    """Verifica que rutas fuera del workspace se bloqueen."""
    from tools.code_search import _code_search_tool

    result = await _code_search_tool(
        pattern="test",
        path="../../etc",
        workspace="test_ws",
    )
    assert "fuera del workspace" in result


@pytest.mark.asyncio
async def test_code_search_directory_not_found():
    """Verifica error cuando el directorio no existe."""
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from tools.code_search import _code_search_tool

    with patch("core.path_resolver.paths.memory_dir") as mock_mem:
        mock_mem.return_value = Path(tempfile.gettempdir())
        result = await _code_search_tool(
            pattern="test",
            path="nonexistent_dir_xyz",
            workspace="main",
        )
        assert "no encontrado" in result.lower()


@pytest.mark.asyncio
async def test_code_search_finds_pattern(tmp_path):
    """Verifica que encuentra un patrón en archivos reales."""
    from unittest.mock import patch

    from tools.code_search import _code_search_tool

    (tmp_path / "app.py").write_text("def hello():\n    return 'world'\n")

    with patch("core.path_resolver.paths.memory_dir", return_value=tmp_path):
        result = await _code_search_tool(
            pattern="def hello",
            path=".",
            workspace="main",
        )
        assert "hello" in result
        assert "app.py" in result
