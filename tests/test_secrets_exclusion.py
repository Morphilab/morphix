"""Secretos excluidos de auto-commit, export y Safety Net.

- Repo temporal con .env → add/commit NO lo incluye.
- El prompt de Safety Net no sugiere .env como target válido.
"""

import asyncio
from unittest.mock import patch

import pytest

from core.constants import SECRET_EXCLUDE_PATHSPEC


def test_constants_define_secret_pathspec():
    """La constante canónica existe y cubre .env/.pem/.key."""
    assert any("*.env" in p for p in SECRET_EXCLUDE_PATHSPEC)
    assert any("*.pem" in p for p in SECRET_EXCLUDE_PATHSPEC)
    assert any("*.key" in p for p in SECRET_EXCLUDE_PATHSPEC)


@pytest.mark.asyncio
async def test_git_add_excludes_env_from_commit(tmp_path):
    """auto_commit en repo temporal con .env no debe commitear el secreto."""
    pytest.importorskip("git")

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("DATABASE_URL=postgres://secret", encoding="utf-8")
    (proj / "app.py").write_text("print('hola')\n", encoding="utf-8")

    from tools.git_manager import GitManager

    with (
        patch("tools.git_manager.paths") as mock_paths,
        patch("agents.audit.log_operation"),
    ):
        mock_paths.memory_dir.return_value = tmp_path
        mock_paths.normalize_project_root.side_effect = lambda r: r
        await GitManager.execute(action="init", workspace="ws", project_root="proj")
        add_out = await GitManager.execute(
            action="add",
            workspace="ws",
            project_root="proj",
            excludes=SECRET_EXCLUDE_PATHSPEC,
        )
        commit_out = await GitManager.execute(
            action="commit", workspace="ws", project_root="proj", message="test: k6"
        )

    assert "añadidos" in add_out
    # '': commit retorna dict estructurado con el texto en 'output'
    assert isinstance(commit_out, dict) and commit_out["success"] is True

    from git import Repo

    repo = Repo(proj)
    committed = repo.head.commit
    blob_names = [b.path for b in committed.tree.traverse()]
    assert "app.py" in blob_names, f"app.py debe estar commiteado: {blob_names}"
    assert ".env" not in blob_names, f".env NO debe estar en el commit: {blob_names}"


def test_auto_commit_uses_secret_excludes(tmp_path):
    """core.git_operations.auto_commit pasa los excludes canónicos al add."""
    from core import git_operations

    calls: list[dict] = []

    async def fake_safe_tool_call(tool_name, parameters=None, **kwargs):
        calls.append(dict(parameters or {}))
        if tool_name == "git_manager" and parameters and parameters.get("action") == "commit":
            return {"output": "Commit realizado: test"}
        return {"output": "ok"}

    with patch.object(git_operations, "safe_tool_call", side_effect=fake_safe_tool_call):
        result = asyncio.run(git_operations.auto_commit(workspace="ws"))

    assert result["success"] is True
    add_calls = [c for c in calls if c.get("action") == "add"]
    assert add_calls, "no se llamó a git add"
    assert any(
        p.startswith(":(exclude)") for p in add_calls[0].get("excludes", [])
    ), f"add sin excludes de secretos: {add_calls[0]}"


@pytest.mark.asyncio
async def test_git_add_default_excludes_secrets(tmp_path):
    """Add SIN excludes explícitos NO staguea .env/.pem/.key/secrets/**."""
    pytest.importorskip("git")

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("DATABASE_URL=postgres://secret", encoding="utf-8")
    (proj / "server.pem").write_text("pem-data", encoding="utf-8")
    (proj / "api.key").write_text("key-data", encoding="utf-8")
    (proj / "secrets").mkdir()
    (proj / "secrets" / "token.txt").write_text("token", encoding="utf-8")
    (proj / "app.py").write_text("print('hola')\n", encoding="utf-8")

    from tools.git_manager import GitManager

    with (
        patch("tools.git_manager.paths") as mock_paths,
        patch("agents.audit.log_operation"),
    ):
        mock_paths.memory_dir.return_value = tmp_path
        mock_paths.normalize_project_root.side_effect = lambda r: r
        await GitManager.execute(action="init", workspace="ws", project_root="proj")
        await GitManager.execute(action="add", workspace="ws", project_root="proj")
        commit_out = await GitManager.execute(
            action="commit", workspace="ws", project_root="proj", message="test: sec-a2"
        )

    assert isinstance(commit_out, dict) and commit_out["success"] is True

    from git import Repo

    repo = Repo(proj)
    blob_names = [b.path for b in repo.head.commit.tree.traverse()]
    assert "app.py" in blob_names, f"app.py debe estar commiteado: {blob_names}"
    for secreto in (".env", "server.pem", "api.key", "secrets/token.txt"):
        assert secreto not in blob_names, f"{secreto} stagueado sin excludes: {blob_names}"


def test_git_manager_spec_documents_excludes():
    """El spec expone el parámetro 'excludes' para que el LLM lo conozca."""
    from tools.specs import TOOL_DEFINITIONS

    params = TOOL_DEFINITIONS["git_manager"].parameters
    assert "excludes" in params, "el spec de git_manager no documenta 'excludes'"
    assert params["excludes"]["type"] == "array"
