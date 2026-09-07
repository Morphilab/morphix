# tests/test_diff_editor.py
"""Tests de seguridad y funcionalidad para diff_editor."""

from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_create_action_timeout_kills_git_diff(tmp_path):
    """git diff sin respuesta no debe colgar: timeout + kill del subproceso."""
    target = tmp_path / "app.py"
    target.write_text("x = 1\n")

    with patch("tools.diff_editor.paths.memory_dir", return_value=tmp_path):
        with patch("tools.diff_editor.asyncio.create_subprocess_exec") as mock_exec:
            proc_mock = AsyncMock()
            proc_mock.communicate = AsyncMock(side_effect=TimeoutError)
            proc_mock.wait = AsyncMock()
            proc_mock.kill = MagicMock()
            mock_exec.return_value = proc_mock

            result = await _diff_editor_tool(
                file_path="app.py",
                action="create",
                workspace="test_ws",
            )

            proc_mock.kill.assert_called_once()
            assert result["success"] is False


@pytest.mark.asyncio
async def test_concurrent_external_modification_is_detected(tmp_path, monkeypatch):
    """Si el archivo cambia entre read y write, el diff se rechaza en vez
    de pisar la edición externa (lost-update)."""
    import asyncio as _aio

    from tools.diff_editor import _diff_editor_tool

    ws = tmp_path / "ws"
    proj = ws / "proj"
    proj.mkdir(parents=True)
    target = proj / "app.py"
    target.write_text("x = 1\n", encoding="utf-8")

    import core.path_resolver as pr

    monkeypatch.setattr(pr.paths, "memory_dir", staticmethod(lambda w=None: ws))

    real_to_thread = _aio.to_thread

    async def spy_to_thread(fn, *a, **k):
        res = await real_to_thread(fn, *a, **k)
        # escritor externo justo DESPUÉS del read del diff-editor
        if getattr(fn, "__name__", "") == "read_text":
            await real_to_thread(target.write_text, "EXTERNAL EDIT\n", encoding="utf-8")
        return res

    monkeypatch.setattr("tools.diff_editor.asyncio.to_thread", spy_to_thread)

    diff = "--- a/app.py\n+++ b/app.py\n" "@@ -1,1 +1,2 @@\n" " x = 1\n+print('hola')\n"
    r = await _diff_editor_tool(
        file_path="app.py", diff_content=diff, action="apply", workspace="ws", project_root="proj"
    )
    assert r["success"] is False, r
    assert "concurrentemente" in r["output"]
    assert target.read_text(encoding="utf-8") == "EXTERNAL EDIT\n", "pisó la edición externa"


# ═══════════ project_root absoluto y escapes vía symlink ═══════════


def _patch_memory_dir(monkeypatch, ws):
    import core.path_resolver as pr

    monkeypatch.setattr(pr.paths, "memory_dir", staticmethod(lambda w=None: ws))


@pytest.mark.asyncio
async def test_absolute_project_root_rejected(tmp_path, monkeypatch):
    """Project_root ABSOLUTO se rechaza: pathlib reemplazaría la base y
    permitiría escribir fuera del workspace."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.py").write_text("x = 1\n", encoding="utf-8")
    _patch_memory_dir(monkeypatch, ws)

    result = await _diff_editor_tool(
        file_path="f.py",
        diff_content="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        action="apply",
        workspace="ws",
        project_root=str(outside),
    )

    assert result["success"] is False, f"escribió fuera del workspace: {result}"
    assert "relativo" in result["output"].lower()
    assert (outside / "f.py").read_text(encoding="utf-8") == "x = 1\n", "escribió fuera"


@pytest.mark.asyncio
async def test_symlinked_project_root_rejected(tmp_path, monkeypatch):
    """Project_root relativo que es SYMLINK fuera del workspace: el
    containment debe hacerse contra la base ya resuelta."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "projlink").symlink_to(outside)
    _patch_memory_dir(monkeypatch, ws)

    result = await _diff_editor_tool(
        file_path="f.py",
        diff_content="--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        action="apply",
        workspace="ws",
        project_root="projlink",
    )

    assert result["success"] is False, f"symlink escapó del workspace: {result}"
    assert "inseguro" in result["output"].lower() or "fuera" in result["output"].lower()
    assert (outside / "f.py").read_text(encoding="utf-8") == "x = 1\n", "escribió fuera"


@pytest.mark.asyncio
async def test_deep_relative_traversal_still_blocked(tmp_path, monkeypatch):
    """C2 regresión: escape relativo profundo con '../' sigue bloqueado."""
    ws = tmp_path / "ws"
    ws.mkdir()
    _patch_memory_dir(monkeypatch, ws)

    result = await _diff_editor_tool(
        file_path="../../../../../../../etc/passwd",
        diff_content="+test",
        action="apply",
        workspace="ws",
        project_root=None,
    )

    assert result["success"] is False
    assert "inseguro" in result["output"].lower() or "fuera" in result["output"].lower()


@pytest.mark.asyncio
async def test_symlink_inside_workspace_rejected(tmp_path, monkeypatch):
    """C2 regresión: symlink dentro del workspace apuntando fuera → rechazado
    por resolve()+containment contra la raíz del workspace."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "link").symlink_to(outside)
    _patch_memory_dir(monkeypatch, ws)

    result = await _diff_editor_tool(
        file_path="link/secret.py",
        diff_content="--- a/secret.py\n+++ b/secret.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
        action="apply",
        workspace="ws",
        project_root=None,
    )

    assert result["success"] is False
    assert "inseguro" in result["output"].lower() or "fuera" in result["output"].lower()
    assert (outside / "secret.py").read_text(encoding="utf-8") == "x = 1\n", "escribió fuera"


@pytest.mark.asyncio
async def test_action_is_required_no_silent_apply(tmp_path, monkeypatch):
    """'action' es obligatorio — sin el kwarg → TypeError (fail-closed);
    jamás se ejecuta la rama apply por un default implícito."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "f.py").write_text("x = 1\n", encoding="utf-8")
    _patch_memory_dir(monkeypatch, ws)

    with pytest.raises(TypeError):
        await _diff_editor_tool(file_path="f.py", workspace="ws")

    assert (ws / "f.py").read_text(encoding="utf-8") == "x = 1\n", "escribió sin action"


@pytest.mark.asyncio
async def test_relative_project_root_create_still_works(tmp_path, monkeypatch):
    """C2 regresión: project_root RELATIVO legítimo ('docs') sigue funcionando."""
    ws = tmp_path / "ws"
    docs = ws / "docs"
    docs.mkdir(parents=True)
    (docs / "f.py").write_text("x = 1\n", encoding="utf-8")
    _patch_memory_dir(monkeypatch, ws)

    with patch("tools.diff_editor.asyncio.create_subprocess_exec") as mock_exec:
        proc_mock = AsyncMock()
        proc_mock.returncode = 0
        proc_mock.communicate = AsyncMock(return_value=(b"--- a/f.py\n+++ b/f.py\n+x = 2\n", b""))
        mock_exec.return_value = proc_mock

        out = await _diff_editor_tool(
            file_path="f.py",
            action="create",
            workspace="ws",
            project_root="docs",
        )

    assert out["success"] is True, out
    assert "+x = 2" in str(out.get("output", ""))
