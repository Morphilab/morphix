"""Diff_editor honesto — hunks verificados, git real, blanks y backups."""

from unittest.mock import patch

import pytest

from tools.diff_editor import _apply_patch_lines, _diff_editor_tool

# ═══════════ verificación de hunks antes de borrar ═══════════


def test_misaligned_hunk_is_rejected():
    """Si las líneas old del hunk no coinciden con el disco → rechazar."""
    original = ["def a():\n", "    return 1\n", "def b():\n", "    return 2\n"]
    # El hunk dice que en línea 1 está 'return 99' pero el disco tiene 'return 1'
    diff = (
        "--- a/f.py\n+++ b/f.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def a():\n"
        "-    return 99\n"
        "+def a():\n"
        "+    return 100\n"
    )
    assert _apply_patch_lines(original, diff) is None


def test_aligned_hunk_applies():
    """Hunk correcto se aplica igual que antes."""
    original = ["def a():\n", "    return 1\n"]
    diff = (
        "--- a/f.py\n+++ b/f.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def a():\n"
        "-    return 1\n"
        "+def a():\n"
        "+    return 2\n"
    )
    result = _apply_patch_lines(original, diff)
    assert result == ["def a():\n", "    return 2\n"]


# ═══════════ líneas de contexto vacías ═══════════


def test_blank_context_lines_preserved():
    """Contexto vacío sin prefijo espacio NO se descarta silenciosamente."""
    original = ["a = 1\n", "\n", "b = 2\n"]
    diff = (
        "--- a/f.py\n+++ b/f.py\n"
        "@@ -1,3 +1,3 @@\n"
        " a = 1\n"
        "\n"  # blank context SIN prefijo (formato laxo común)
        "-b = 2\n"
        "+b = 3\n"
    )
    result = _apply_patch_lines(original, diff)
    assert result is not None
    assert result[1] == "\n", f"línea en blanco perdida: {result}"
    assert result[2] == "b = 3\n"


# ═══════════ backup en change_tracker antes de escribir ═══════════


@pytest.mark.asyncio
async def test_apply_creates_undo_backup(tmp_path):
    """Aplicar un diff guarda backup en change_tracker (paridad con file_manager)."""
    from core.change_tracker import ChangeTracker

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "f.py").write_text("x = 1\n", encoding="utf-8")

    saved: list[str] = []
    with (
        patch("tools.diff_editor.paths") as mock_paths,
        patch.object(
            ChangeTracker, "save_before_write", side_effect=lambda p: saved.append(p) or "/bk"
        ),
        patch("agents.audit.log_operation"),
    ):
        mock_paths.memory_dir.return_value = tmp_path
        await _diff_editor_tool(
            file_path="f.py",
            diff_content=("--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"),
            action="apply",
            workspace="ws",
            project_root="proj",
        )

    assert saved == ["f.py"], f"no se guardó backup: {saved}"
    assert (proj / "f.py").read_text(encoding="utf-8") == "x = 2\n"


# ═══════════ git honesto en action=create ═══════════


@pytest.mark.asyncio
async def test_create_without_git_repo_reports_honestly(tmp_path):
    """Repo SIN git init → error honesto, no success '(sin cambios)'."""
    proj = tmp_path / "plain"
    proj.mkdir()
    (proj / "f.py").write_text("x = 1\n", encoding="utf-8")

    with patch("tools.diff_editor.paths") as mock_paths:
        mock_paths.memory_dir.return_value = tmp_path
        out = await _diff_editor_tool(
            file_path="f.py",
            action="create",
            workspace="ws",
            project_root="plain",
        )

    assert out.get("success") is False, f"diagnóstico falso: {out}"
    assert "git" in str(out.get("output", "")).lower()


@pytest.mark.asyncio
async def test_create_in_real_git_repo_returns_diff(tmp_path):
    """Con repo válido y cambios reales, create retorna el diff (comportamiento sano)."""
    pytest.importorskip("git")
    from git import Repo

    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / "f.py").write_text("x = 1\n", encoding="utf-8")
    repo = Repo.init(proj)
    repo.index.add(["f.py"])
    repo.index.commit("init")
    (proj / "f.py").write_text("x = 2\n", encoding="utf-8")

    with patch("tools.diff_editor.paths") as mock_paths:
        mock_paths.memory_dir.return_value = tmp_path
        out = await _diff_editor_tool(
            file_path="f.py",
            action="create",
            workspace="ws",
            project_root="repo",
        )

    assert out.get("success") is True, out
    assert "+x = 2" in str(out.get("output", ""))
