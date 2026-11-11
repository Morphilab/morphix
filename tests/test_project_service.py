"""clone_project: clonación git en code_projects."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from desktop.services.project_service import (
    clone_project,
    create_project,
    normalize_project_name,
    project_dir,
)


@pytest.fixture(autouse=True)
def _isolated_workspace(tmp_path, monkeypatch):
    """Redirige memory_dir a tmp para no tocar el workspace real."""
    from core.path_resolver import paths

    monkeypatch.setattr(
        paths, "memory_dir", lambda ws=None: tmp_path / "mem" / (ws or "main"), raising=False
    )
    return tmp_path


def test_clone_project_success_counts_files():
    run = MagicMock(returncode=0)

    def fake_clone(*a, **k):
        d = project_dir("repo1")
        (d / "sub").mkdir(parents=True)
        (d / "a.py").write_text("x")
        (d / "sub" / "b.py").write_text("y")
        return run

    with patch.object(subprocess, "run", side_effect=fake_clone) as run_mock:
        ok, message = clone_project("https://host/repo.git", "repo1")

    assert ok is True
    assert "2 archivos" in message
    args = run_mock.call_args.args[0]
    assert args[0] == "git" and args[1] == "clone"
    assert "--depth" in args and "1" in args
    assert "https://host/repo.git" in args


def test_clone_project_existing_dst_rejected_without_git():
    create_project("dup")
    with patch.object(subprocess, "run") as run_mock:
        ok, message = clone_project("https://host/x.git", "dup")
    assert ok is False
    assert "existe" in message.lower()
    run_mock.assert_not_called()


def test_clone_project_failure_cleans_partial_and_redacts_credentials():
    run = MagicMock(returncode=128, stdout="", stderr="fatal: https://user:s3cret@host/x.git")
    (project_dir("broken")).parent.mkdir(parents=True, exist_ok=True)

    def fake_clone(*a, **k):
        # simula clon parcial creado antes de fallar
        d = project_dir("broken")
        d.mkdir(parents=True, exist_ok=True)
        (d / ".git").write_text("partial")
        return run

    with patch.object(subprocess, "run", side_effect=fake_clone):
        ok, message = clone_project("https://user:s3cret@host/x.git", "broken")

    assert ok is False
    assert "s3cret" not in message, f"credencial filtrada en error: {message}"
    assert not project_dir("broken").exists(), "no limpió el clon parcial"


def test_clone_project_timeout_cleans_up():
    (project_dir("slow").parent).mkdir(parents=True, exist_ok=True)

    def fake_clone(*a, **k):
        project_dir("slow").mkdir(parents=True, exist_ok=True)
        raise subprocess.TimeoutExpired(cmd="git", timeout=120)

    with patch.object(subprocess, "run", side_effect=fake_clone):
        ok, message = clone_project("https://host/slow.git", "slow")

    assert ok is False
    assert "timeout" in message.lower() or "120" in message
    assert not project_dir("slow").exists()


def test_clone_project_invalid_name_rejected():
    ok, message = clone_project("https://host/x.git", "")
    assert ok is False


def test_clone_project_real_local_repo(tmp_path):
    """Integración real: clona un repo local creado con git init."""
    origin = tmp_path / "origin_repo"
    env = ["-c", "user.email=t@t", "-c", "user.name=T"]
    subprocess.run(["git", "init", "-q", str(origin)], check=True)
    (origin / "README.md").write_text("hola")
    subprocess.run(["git", *env, "add", "."], cwd=origin, check=True)
    subprocess.run(["git", *env, "commit", "-qm", "init"], cwd=origin, check=True)

    name = normalize_project_name(origin.name)
    assert name is not None
    ok, message = clone_project(str(origin), name)
    assert ok is True, message
    dst = project_dir(name)
    assert (dst / "README.md").read_text() == "hola"


# ── delete_project: borrado con las tres garantías ──────────────────────


class TestDeleteProject:
    def _seed(self, name: str, n_files: int = 3) -> None:
        from desktop.services.project_service import create_project, project_dir

        create_project(name)
        d = project_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        (d / "src").mkdir(exist_ok=True)
        for i in range(n_files):
            (d / "src" / f"f{i}.py").write_text(f"# {i}\n" * 10)

    def test_borra_el_directorio_y_reporta(self):
        from desktop.services.project_service import delete_project, project_dir

        self._seed("victima")
        ok, msg = delete_project("victima")
        assert ok, msg
        assert msg == "victima"
        assert not project_dir("victima").exists()

    def test_stats_para_el_dialogo_de_confirmacion(self):
        from desktop.services.project_service import project_stats

        self._seed("medido", n_files=2)
        files, size = project_stats("medido")
        assert files == 2
        assert size > 0
        assert project_stats("inexistente") is None

    def test_nombre_invalido_rechazado(self):
        from desktop.services.project_service import delete_project

        ok, msg = delete_project("../escape")
        assert not ok
        assert "inválido" in msg or "invalid" in msg.lower()

    def test_inexistente_rechazado(self):
        from desktop.services.project_service import delete_project

        ok, msg = delete_project("fantasma")
        assert not ok
        assert "No existe" in msg

    def test_run_activo_bloquea_el_borrado(self):
        """Regla simple y sin carreras: con cualquier flujo corriendo no se
        borra nada — aunque el run use otro proyecto."""
        from core.workspaces import begin_workflow_run, end_workflow_run
        from desktop.services.project_service import delete_project

        self._seed("con_run")
        token = begin_workflow_run()
        try:
            ok, msg = delete_project("con_run")
            assert not ok
            assert "activo" in msg
        finally:
            end_workflow_run(token)
        # liberado el run, el borrado procede
        ok, msg = delete_project("con_run")
        assert ok, msg
