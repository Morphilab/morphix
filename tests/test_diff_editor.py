# tests/test_diff_editor.py
"""Tests de seguridad y funcionalidad para diff_editor."""

import pytest

from tools.diff_editor import _apply_patch_lines, _diff_editor_tool


@pytest.mark.asyncio
async def test_path_traversal_blocked(tmp_path):
    """Verifica que rutas con '../' fuera del workspace se bloqueen."""
    result = await _diff_editor_tool(
        file_path="../../etc/passwd",
        diff_content="+test",
        action="apply",
        workspace="test_ws",
        project_root=None,
    )

    assert result["success"] is False
    assert "inseguro" in result["output"].lower() or "fuera" in result["output"].lower()


@pytest.mark.asyncio
async def test_path_is_alias_for_file_path():
    """El parámetro 'path' produce el mismo bloqueo de seguridad que 'file_path'."""
    r1 = await _diff_editor_tool(
        file_path="../../etc/passwd",
        diff_content="+test",
        action="apply",
        workspace="test_ws",
    )
    r2 = await _diff_editor_tool(
        path="../../etc/passwd",
        diff_content="+test",
        action="apply",
        workspace="test_ws",
    )
    assert r1 == r2
    assert r1["success"] is False
    assert "inseguro" in r1["output"].lower() or "fuera" in r1["output"].lower()


@pytest.mark.asyncio
async def test_apply_missing_file(tmp_path):
    """Verifica error al intentar aplicar diff a un archivo inexistente."""
    result = await _diff_editor_tool(
        file_path="app.py",
        diff_content="+test",
        action="apply",
        workspace="test_ws",
        project_root=None,
    )

    assert result["success"] is False
    assert "no encontrado" in result["output"].lower()


class TestApplyPatchLines:
    def test_add_line(self):
        original = ["line1\n"]
        diff = "@@ -1,1 +1,2 @@\n line1\n+line2\n"
        result = _apply_patch_lines(original, diff)
        assert result == ["line1\n", "line2\n"]

    def test_remove_line(self):
        original = ["line1\n", "line2\n"]
        diff = "@@ -1,2 +1,1 @@\n line1\n-line2\n"
        result = _apply_patch_lines(original, diff)
        assert result == ["line1\n"]

    def test_replace_line(self):
        original = ["hello\n"]
        diff = "@@ -1,1 +1,1 @@\n-hello\n+world\n"
        result = _apply_patch_lines(original, diff)
        assert result == ["world\n"]

    def test_unchanged_context_preserved(self):
        original = ["keep1\n", "change\n", "keep2\n"]
        diff = "@@ -1,3 +1,3 @@\n keep1\n-change\n+changed\n keep2\n"
        result = _apply_patch_lines(original, diff)
        assert result == ["keep1\n", "changed\n", "keep2\n"]

    def test_empty_diff_returns_none(self):
        original = ["line1\n"]
        result = _apply_patch_lines(original, "")
        assert result is None


@pytest.mark.asyncio
async def test_content_is_alias_for_diff_content():
    """El parámetro 'content' funciona como alias de 'diff_content'."""
    r1 = await _diff_editor_tool(
        file_path="nonexistent.py",
        diff_content=None,
        content="+test",
        action="apply",
        workspace="test_ws",
    )
    r2 = await _diff_editor_tool(
        file_path="nonexistent.py",
        diff_content="+test",
        content="",
        action="apply",
        workspace="test_ws",
    )

    assert r1 == r2
    assert r1["success"] is False
    assert "no encontrado" in r1["output"].lower()


@pytest.mark.asyncio
async def test_content_empty_with_diff_content_works():
    """content vacío con diff_content válido debe funcionar (backward compat)."""
    result = await _diff_editor_tool(
        file_path="nonexistent.py",
        diff_content="+test",
        content="",
        action="apply",
        workspace="test_ws",
    )
    assert result["success"] is False
    assert "no encontrado" in result["output"].lower()


@pytest.mark.asyncio
async def test_both_content_empty_requires_diff():
    """Ambos content y diff_content vacíos/nulos deben fallar."""
    result = await _diff_editor_tool(
        file_path="app.py",
        diff_content=None,
        content="",
        action="apply",
        workspace="test_ws",
    )
    assert result["success"] is False
    assert "diff_content" in result["output"].lower() or "requerido" in result["output"].lower()
