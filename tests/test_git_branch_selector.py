"""Tests del selector de rama git (servicio + botón en top bar).

v1: solo ramas locales, checkout bloqueado si el árbol está dirty.
Repos temporales REALES (GitPython) con paths stub-eados a nivel del
módulo consumidor (git_service.paths) — lección NV-L8/monkeypatch.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
gitpython = pytest.importorskip("git")

from typing import cast  # noqa: E402

from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from desktop.services import git_service  # noqa: E402


def _qapp() -> QApplication:
    return cast(QApplication, QApplication.instance() or QApplication([]))


class _StubPaths:
    """paths mínimo: memory_dir → tmp; normalize passthrough."""

    def __init__(self, base):
        self._base = base

    def memory_dir(self, workspace: str):
        return self._base / "memory" / workspace

    def normalize_project_root(self, project_root: str | None):
        from core.constants import PROJECTS_DIR_NAME

        prefix = f"{PROJECTS_DIR_NAME}/"
        if project_root and not project_root.startswith(prefix):
            return f"{prefix}{project_root}"
        return project_root


@pytest.fixture()
def git_env(tmp_path, monkeypatch):
    """Workspace aislado: paths stub + un repo real code_projects/repo."""
    stub = _StubPaths(tmp_path)
    monkeypatch.setattr(git_service, "paths", stub)
    ws = "ws_git_test"
    repo_dir = stub.memory_dir(ws) / "code_projects" / "repo"
    repo_dir.mkdir(parents=True)
    repo = gitpython.Repo.init(repo_dir)
    (repo_dir / "a.txt").write_text("v1\n")
    repo.index.add(["a.txt"])
    repo.index.commit("init")
    repo.create_head("feature")
    return ws, "code_projects/repo", repo_dir


# ── Servicio ──


@pytest.mark.asyncio
async def test_is_git_repo_true_y_false(git_env):
    ws, root, _ = git_env
    assert git_service.is_git_repo(ws, root) is True
    assert git_service.is_git_repo(ws, "code_projects/no_existe") is False
    assert git_service.is_git_repo(ws, None) is False


@pytest.mark.asyncio
async def test_list_branches_current_y_locales(git_env):
    ws, root, _ = git_env
    data = await git_service.list_branches(ws, root)
    assert data is not None
    assert data["current"] == "master" or data["current"] == "main"
    assert "feature" in data["local"]
    assert all(not b.startswith("origin/") for b in data["local"]), "solo locales"


@pytest.mark.asyncio
async def test_list_branches_sin_repo_da_none(git_env):
    ws, _, _ = git_env
    assert await git_service.list_branches(ws, "code_projects/no_existe") is None


@pytest.mark.asyncio
async def test_checkout_limpio_cambia_de_rama(git_env):
    ws, root, repo_dir = git_env
    ok, msg = await git_service.checkout_branch(ws, root, "feature")
    assert ok, msg
    assert gitpython.Repo(repo_dir).active_branch.name == "feature"
    assert "✅" in msg


@pytest.mark.asyncio
async def test_checkout_dirty_bloqueado(git_env):
    ws, root, repo_dir = git_env
    # modifica un archivo TRACKED → dirty
    (repo_dir / "a.txt").write_text("v2 sin commitear\n")
    ok, msg = await git_service.checkout_branch(ws, root, "feature")
    assert not ok
    assert "sin commitear" in msg
    assert gitpython.Repo(repo_dir).active_branch.name != "feature", "no cambió"


@pytest.mark.asyncio
async def test_checkout_rama_inexistente_rechazado(git_env):
    ws, root, repo_dir = git_env
    ok, msg = await git_service.checkout_branch(ws, root, "no_existe")
    assert not ok
    assert "no existe" in msg


# ── GUI: botón ⑂ en top bar ──


def test_branch_btn_oculto_sin_proyecto():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    assert not m._branch_btn.isVisibleTo(m)
    m._render_branch_menu(None)
    assert not m._branch_btn.isVisibleTo(m)


def test_branch_btn_render_menu_con_check_en_activa():
    from desktop.maestro_tab import SessionPane

    _qapp()
    m = SessionPane()
    m._render_branch_menu({"current": "main", "local": ["dev", "feature", "main"]})
    assert m._branch_btn.isVisibleTo(m)
    assert m._branch_btn.text() == "⑂ main"
    menu = m._branch_btn.menu()
    assert isinstance(menu, QMenu)
    labels = [a.text() for a in menu.actions()]
    assert any(a.text().startswith("✓") and "main" in a.text() for a in menu.actions())
    # la activa va disabled; el resto clicable
    activa = next(a for a in menu.actions() if "main" in a.text() and a.text().startswith("✓"))
    assert not activa.isEnabled()
    assert any(a.isEnabled() for a in menu.actions()), "hay ramas seleccionables"
    # acción de refresco (ramas creadas fuera de la app — snapshot stale)
    refresh = next((a for a in menu.actions() if "Refrescar" in a.text()), None)
    assert refresh is not None and refresh.isEnabled()
