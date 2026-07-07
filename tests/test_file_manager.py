# tests/test_file_manager.py
import pytest

import tools.file_manager as fm
from tools.file_manager import FileManager


@pytest.mark.asyncio
async def test_write_and_read(tmp_path):
    """Escribe un archivo y luego lo lee."""
    fm.SAFE_BASE = tmp_path
    await FileManager.execute("write", path="test.txt", content="Hola mundo")
    result = await FileManager.execute("read", path="test.txt")
    assert result == "Hola mundo"


@pytest.mark.asyncio
async def test_write_with_project_root_normalization(tmp_path):
    """El prefijo completo project_root se elimina del path."""
    fm.SAFE_BASE = tmp_path
    await FileManager.execute(
        "write",
        path="code_projects/miapp/app.py",
        content="print('ok')",
        project_root="code_projects/miapp",
    )
    # El archivo debe crearse en tmp_path/main/code_projects/miapp/app.py
    final_file = tmp_path / "main" / "code_projects" / "miapp" / "app.py"
    assert final_file.exists()


@pytest.mark.asyncio
async def test_write_with_project_name_normalization(tmp_path):
    """El prefijo con el nombre del proyecto también se elimina."""
    fm.SAFE_BASE = tmp_path
    await FileManager.execute(
        "write",
        path="miapp/app.py",
        content="print('ok')",
        project_root="code_projects/miapp",
    )
    final_file = tmp_path / "main" / "code_projects" / "miapp" / "app.py"
    assert final_file.exists()


@pytest.mark.asyncio
async def test_syntax_validation_rejects_bad_code(tmp_path):
    """Devuelve error de sintaxis y NO escribe el archivo."""
    fm.SAFE_BASE = tmp_path
    result = await FileManager.execute(
        "write",
        path="script.py",
        content="def foo(:",
    )
    assert result.startswith("❌ Error de sintaxis")
    assert not (tmp_path / "main" / "script.py").exists()


@pytest.mark.asyncio
async def test_file_not_found_raises(tmp_path):
    """Leer un archivo inexistente lanza FileNotFoundError."""
    fm.SAFE_BASE = tmp_path
    with pytest.raises(FileNotFoundError):
        await FileManager.execute("read", path="no_existe.txt")


@pytest.mark.asyncio
async def test_wrapper_infers_write_when_content_without_action(tmp_path):
    """DeepSeek a veces omite 'action'; con content presente debe ESCRIBIR (no leer)."""
    fm.SAFE_BASE = tmp_path
    result = await fm.file_manager_tool(path="inferido.py", content="x = 1", workspace="main")
    assert "escrito correctamente" in result
    assert (tmp_path / "main" / "inferido.py").read_text() == "x = 1"


@pytest.mark.asyncio
async def test_wrapper_infers_read_when_no_content_no_action(tmp_path):
    """Sin action y sin content → leer el archivo."""
    fm.SAFE_BASE = tmp_path
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "leeme.txt").write_text("contenido")
    result = await fm.file_manager_tool(path="leeme.txt", workspace="main")
    assert result == "contenido"


def test_is_modifying_action_infers_write_without_action():
    """El loop debe reconocer file_manager con content (sin action) como modificación."""
    from orchestration.loop import _is_modifying_action

    assert _is_modifying_action("file_manager", {"path": "x.py", "content": "y"}) is True
    assert _is_modifying_action("file_manager", {"action": "write", "path": "x.py"}) is True
    assert _is_modifying_action("file_manager", {"action": "read", "path": "x.py"}) is False
    assert _is_modifying_action("file_manager", {"path": "x.py"}) is False


@pytest.mark.asyncio
async def test_read_directory_returns_listing(tmp_path):
    """read sobre un directorio devuelve el listado en vez de FileNotFoundError."""
    fm.SAFE_BASE = tmp_path
    (tmp_path / "main").mkdir(parents=True, exist_ok=True)
    (tmp_path / "main" / "a.py").write_text("x = 1")
    (tmp_path / "main" / "sub").mkdir()
    result = await fm.file_manager_tool(action="read", path=".", workspace="main")
    assert "a.py" in result
    assert "sub/" in result
