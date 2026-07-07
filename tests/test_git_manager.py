# tests/test_git_manager.py
import pytest

from tools.git_manager import GitManager


@pytest.fixture
def temp_memory(tmp_path, monkeypatch):
    """Redirige Path('memory') a un directorio temporal vía path_resolver."""
    fake_memory = tmp_path / "memory"
    fake_memory.mkdir()

    import core.path_resolver as pr

    monkeypatch.setattr(pr, "MEMORY_BASE", tmp_path / "memory")
    monkeypatch.setattr(pr.paths, "memory_dir", lambda ws: tmp_path / "memory" / ws)
    monkeypatch.setattr(pr.paths, "memory_base", lambda: tmp_path / "memory")
    yield tmp_path / "memory"


@pytest.mark.asyncio
async def test_init_creates_repo(temp_memory):
    """Inicializa un repositorio Git en el directorio del proyecto."""
    await GitManager.execute(
        "init",
        workspace="main",
        project_root="code_projects/miapp",
    )
    repo_path = temp_memory / "main" / "code_projects" / "miapp"
    assert (repo_path / ".git").is_dir()


@pytest.mark.asyncio
async def test_add_and_commit_after_init(temp_memory):
    """Añade y commitea un archivo en un repo ya inicializado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    # Crear un archivo en el directorio del proyecto
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")
    # Add y commit
    add_result = await GitManager.execute(
        "add", workspace="main", project_root="code_projects/miapp"
    )
    assert "Archivos añadidos" in add_result
    commit_result = await GitManager.execute(
        "commit",
        message="commit inicial",
        workspace="main",
        project_root="code_projects/miapp",
    )
    assert "Commit realizado" in commit_result


@pytest.mark.asyncio
async def test_commit_without_init_fails(temp_memory):
    """Commit sin inicializar repo devuelve error."""
    result = await GitManager.execute(
        "commit",
        message="fallará",
        workspace="main",
        project_root="code_projects/miapp",
    )
    assert "No hay un repositorio Git inicializado" in result


@pytest.mark.asyncio
async def test_missing_project_root_error(temp_memory):
    """Si no se especifica project_root, devuelve error."""
    result = await GitManager.execute("init", workspace="main")
    assert "git_manager necesita 'project_root'" in result


@pytest.mark.asyncio
async def test_commit_rejects_error_message(temp_memory):
    """Commit con mensaje de error del sistema debe ser rechazado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")
    await GitManager.execute("add", workspace="main", project_root="code_projects/miapp")

    result = await GitManager.execute(
        "commit",
        message="❌ Rate limit excedido. Intenta de nuevo en unos segundos.",
        workspace="main",
        project_root="code_projects/miapp",
    )
    assert "no válido" in result


@pytest.mark.asyncio
async def test_commit_accepts_valid_message(temp_memory):
    """Commit con mensaje válido debe ser aceptado."""
    await GitManager.execute("init", workspace="main", project_root="code_projects/miapp")
    project_dir = temp_memory / "main" / "code_projects" / "miapp"
    (project_dir / "test.txt").write_text("contenido")
    await GitManager.execute("add", workspace="main", project_root="code_projects/miapp")

    result = await GitManager.execute(
        "commit",
        message="feat: agregar endpoint JWT",
        workspace="main",
        project_root="code_projects/miapp",
    )
    assert "Commit realizado" in result
