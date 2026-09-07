"""project_dir canónico — aggregator y file_manager resuelven IGUAL."""

from core.path_resolver import PathResolver


def test_project_dir_normalizes_prefix():
    a = PathResolver.project_dir("main", "lab")
    b = PathResolver.project_dir("main", "code_projects/lab")
    assert a == b, f"convenciones divergentes: {a} vs {b}"


def test_project_dir_dot_is_workspace_root():
    """'.' y None retornan la raíz del workspace."""
    p = PathResolver.project_dir("main", ".")
    assert p == PathResolver.memory_dir("main")


def test_aggregator_and_file_manager_resolve_same(tmp_path, monkeypatch):
    """El archivo que escribe file_manager ES el que lee el aggregator."""
    import asyncio

    import tools.file_manager as fmm

    monkeypatch.setattr("core.path_resolver.MEMORY_BASE", tmp_path)
    # FileManager cachea la base del workspace en su propio módulo
    monkeypatch.setattr(fmm, "SAFE_BASE", tmp_path)

    from tools.file_manager import FileManager

    out = asyncio.run(
        FileManager.execute(
            action="write",
            path="salida.py",
            content="x = 1\n",
            workspace="main",
            project_root="lab",
        )
    )
    assert "escrito" in str(out), out

    written = PathResolver.project_dir("main", "lab") / "salida.py"
    assert written.exists(), f"file_manager escribió en otra parte: {written} | out={out}"
