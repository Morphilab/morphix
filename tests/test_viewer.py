"""Tests del visor standalone (viewer/) + tool file_view + servicio GUI.

Las funciones puras de viewer.py se testean sin Qt.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from viewer.viewer import looks_binary, pick_mode, sanitize_html

# ── 4.1 Funciones puras de viewer.py ─────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.md", "markdown"),
        ("a.markdown", "markdown"),
        ("a.html", "html"),
        ("a.htm", "html"),
        ("a.pdf", "pdf"),
        ("a.py", "text"),
        ("a.json", "text"),
        ("a", "text"),
        ("a.MD", "markdown"),  # case-insensitive
        ("a.PDF", "pdf"),
    ],
)
def test_pick_mode_dispatch(name, expected):
    assert pick_mode(Path(name)) == expected


def test_sanitize_html_strips_script_js_y_handlers():
    dirty = (
        "<p>hola</p><script>alert(1)</script>"
        '<a href="javascript:evil()">x</a>'
        "<img onerror='steal()' src='a.png'>"
    )
    clean = sanitize_html(dirty)
    assert "<script" not in clean
    assert "javascript:" not in clean
    assert "onerror" not in clean
    assert "[contenido bloqueado]" in clean
    assert "<p>hola</p>" in clean  # contenido legítimo intacto


def test_sanitize_html_neutraliza_frame_escape():
    assert "⟪" not in sanitize_html("⟪DATOS⟫")
    assert "DATOS" in sanitize_html("⟪DATOS⟫")


def test_looks_binary():
    assert looks_binary(b"ok\x00binary") is True
    assert looks_binary(b"texto normal " * 100) is False


def test_load_text_content_trunca_a_1mb(tmp_path):
    from viewer.viewer import load_text_content

    big = tmp_path / "big.txt"
    big.write_text("x" * 1_100_000, encoding="utf-8")
    content, mode = load_text_content(big)
    assert mode == "text"
    assert len(content) < 1_100_000
    assert "truncado" in content


# ── 4.2 Tool file_view ───────────────────────────────────────────────────


@pytest.fixture()
def tool():
    from tools.file_viewer import _file_view_tool

    return _file_view_tool


@pytest.fixture()
def paths_stub():
    """Stub de paths inyectado en tools.file_viewer — inmune al estado global
    del singleton (la suite completa deja sombras instancia/clase)."""
    from types import SimpleNamespace

    return SimpleNamespace(
        code_projects_dir=lambda ws, root=None: None,  # se sobreescribe por test
        viewer_script=lambda: Path("/no/existe/viewer.py"),
    )


@pytest.fixture(autouse=True)
def _inject_stub(paths_stub, monkeypatch):
    # IMPORTANTE: parchear el objeto módulo (no "tools.file_viewer.paths" por
    # string) — el loader spec-carga el módulo sin bindearlo al paquete padre,
    # y el resolve por string de monkeypatch falla en suite completa.
    import tools.file_viewer as fv_module

    monkeypatch.setattr(fv_module, "paths", paths_stub)


@pytest.mark.asyncio
async def test_file_view_sin_path(tool):
    out = await tool(path="")
    assert "requiere" in out


@pytest.mark.asyncio
async def test_file_view_headless_no_spawnea(tool, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with patch("tools.file_viewer.subprocess.Popen") as po:
        out = await tool(path="doc.md")
    assert "Sin display" in out
    po.assert_not_called()


@pytest.mark.asyncio
async def test_file_view_traversal_rechazado(tool, tmp_path, monkeypatch, paths_stub):
    proyecto = tmp_path / "proy"
    proyecto.mkdir()
    (proyecto / "doc.md").write_text("hola", encoding="utf-8")
    secreto = tmp_path / "secreto.md"
    secreto.write_text("top", encoding="utf-8")
    paths_stub.code_projects_dir = lambda ws, root=None: proyecto

    monkeypatch.setenv("DISPLAY", ":99")
    out = await tool(path="../secreto.md")
    assert "Acceso denegado" in out


@pytest.mark.asyncio
async def test_file_view_spawn_con_args_correctos(tool, tmp_path, monkeypatch, paths_stub):
    proyecto = tmp_path / "proy"
    proyecto.mkdir()
    (proyecto / "doc.md").write_text("hola", encoding="utf-8")
    script = tmp_path / "viewer.py"
    script.write_text("# fake", encoding="utf-8")
    paths_stub.code_projects_dir = lambda ws, root=None: proyecto
    paths_stub.viewer_script = lambda: script

    monkeypatch.setenv("DISPLAY", ":99")
    with patch("tools.file_viewer.subprocess.Popen") as po:
        out = await tool(path="doc.md")
    assert out.startswith("Abierto en el visor:")
    args = po.call_args.args[0]
    assert args[0] == sys.executable
    assert args[1] == str(script)
    assert args[2] == str((proyecto / "doc.md").resolve())


@pytest.mark.asyncio
async def test_file_view_archivo_inexistente(tool, tmp_path, monkeypatch, paths_stub):
    proyecto = tmp_path / "proy"
    proyecto.mkdir()
    paths_stub.code_projects_dir = lambda ws, root=None: proyecto
    monkeypatch.setenv("DISPLAY", ":99")
    out = await tool(path="no_esta.md")
    assert "no encontrado" in out


# ── 4.3 Servicio GUI ─────────────────────────────────────────────────────


def test_servicio_headless_no_spawnea(monkeypatch):
    from desktop.services import file_viewer_service as svc

    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    with patch.object(svc.subprocess, "Popen") as po:
        assert svc.open_in_viewer("x.md") is False
    po.assert_not_called()


def test_servicio_spawn(monkeypatch, tmp_path):
    from desktop.services import file_viewer_service as svc

    monkeypatch.setenv("DISPLAY", ":99")
    script = tmp_path / "viewer.py"
    script.write_text("# fake", encoding="utf-8")
    from types import SimpleNamespace

    monkeypatch.setattr(
        svc,
        "paths",
        SimpleNamespace(viewer_script=lambda: script, project_root=lambda: tmp_path),
    )
    with patch.object(svc.subprocess, "Popen") as po:
        assert svc.open_in_viewer("/abs/ruta.md") is True
    assert po.call_args.args[0][2] == "/abs/ruta.md"


def test_servicio_sin_script(monkeypatch, tmp_path):
    from desktop.services import file_viewer_service as svc

    monkeypatch.setenv("DISPLAY", ":99")
    from types import SimpleNamespace

    monkeypatch.setattr(
        svc,
        "paths",
        SimpleNamespace(viewer_script=lambda: tmp_path / "no.py", project_root=lambda: tmp_path),
    )
    assert svc.open_in_viewer("x.md") is False


# ── 4.4 Guard de templates ───────────────────────────────────────────────


def test_file_view_en_allowlists_de_templates():
    """file_view debe estar en developer/architect + development/coordinated."""
    import yaml

    dev_agent = yaml.safe_load(Path("templates/agents/developer.yaml").read_text())
    arch_agent = yaml.safe_load(Path("templates/agents/architect.yaml").read_text())
    dev_wf = yaml.safe_load(Path("templates/workflows/development.yaml").read_text())
    coord_wf = yaml.safe_load(Path("templates/workflows/coordinated.yaml").read_text())
    assert "file_view" in dev_agent["tools"]
    assert "file_view" in arch_agent["tools"]
    assert "file_view" in dev_wf["tools"]["allowed"]
    assert "file_view" in coord_wf["tools"]["allowed"]
