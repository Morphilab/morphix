# tests/test_git_manager_env.py — GitPython no debe heredar secretos
"""GitPython spawnea `git` heredando os.environ completo (el guard de bash/MCP
no lo cubre). Estos tests exigen que toda operación git pase por el allowlist
de build_child_env()."""

import os

import pytest

from tools.git_manager import GitManager


@pytest.fixture
def temp_memory(tmp_path, monkeypatch):
    fake_memory = tmp_path / "memory"
    fake_memory.mkdir()

    import core.path_resolver as pr

    monkeypatch.setattr(pr, "MEMORY_BASE", tmp_path / "memory")
    monkeypatch.setattr(pr.paths, "memory_dir", lambda ws: tmp_path / "memory" / ws)
    monkeypatch.setattr(pr.paths, "memory_base", lambda: tmp_path / "memory")
    yield tmp_path / "memory"


@pytest.fixture
def secret_in_env(monkeypatch):
    """Planta un secreto en os.environ como lo estaría en producción."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-secret-value")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    yield


@pytest.fixture
def env_capture(monkeypatch):
    """Intercepta TODO spawn de GitPython (funnel: git.cmd.Git.execute) y captura
    una copia de os.environ vista en cada invocación."""
    import git.cmd

    seen: list[dict[str, str]] = []
    real_execute = git.cmd.Git.execute

    def execute(self, *args, **kwargs):
        seen.append(dict(os.environ))
        return real_execute(self, *args, **kwargs)

    monkeypatch.setattr(git.cmd.Git, "execute", execute)
    yield seen


@pytest.mark.asyncio
async def test_add_routes_through_scrubbed_env(temp_memory, secret_in_env, env_capture):
    """Durante `git add` el entorno visto por el proceso es el allowlist, sin secretos,
    y os.environ queda restaurado al terminar la operación."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")

    env_capture.clear()
    await GitManager.execute("add", workspace="main", project_root="code_projects/miapp")

    assert env_capture, "git add nunca llegó a spawnear"
    for env in env_capture:
        assert "DEEPSEEK_API_KEY" not in env
        assert "DATABASE_URL" not in env
        assert env.get("PATH") == "/usr/local/bin:/usr/bin:/bin"
        assert env.get("HOME"), "HOME requerido por git (~/.gitconfig)"
    # Restauración tras la operación
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-test-secret-value"
    assert os.environ["DATABASE_URL"] == "postgresql://u:p@h/db"


@pytest.mark.asyncio
async def test_commit_show_diff_log_scrubbed(temp_memory, secret_in_env, env_capture):
    """Las demás rutas (commit/show/diff/log) también operan con entorno purgado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/app2")
    project_dir = temp_memory / "main" / "code_projects" / "app2"
    (project_dir / "a.txt").write_text("x")
    await GitManager.execute("add", workspace="main", project_root="code_projects/app2")

    env_capture.clear()
    res = await GitManager.execute(
        "commit", message="m", workspace="main", project_root="code_projects/app2"
    )
    assert isinstance(res, dict) and res["success"] is True
    await GitManager.execute(
        "show", ref_path="HEAD:a.txt", workspace="main", project_root="code_projects/app2"
    )
    await GitManager.execute("diff", workspace="main", project_root="code_projects/app2")
    await GitManager.execute("log", workspace="main", project_root="code_projects/app2")

    assert len(env_capture) >= 3, "show/diff/log deben spawnear git"
    for env in env_capture:
        assert "DEEPSEEK_API_KEY" not in env
        assert env.get("PATH") == "/usr/local/bin:/usr/bin:/bin"


@pytest.mark.asyncio
async def test_init_also_scrubbed(temp_memory, secret_in_env, env_capture):
    """Repo.init también spawnea git init — mismo allowlist."""
    env_capture.clear()
    await GitManager.execute("init", workspace="main", project_root="code_projects/app3")

    assert len(env_capture) == 1
    assert "DEEPSEEK_API_KEY" not in env_capture[0]
